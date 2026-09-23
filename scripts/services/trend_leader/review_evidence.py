"""按日归档的趋势证据：CLI 落盘，复盘组装器只读，不查当前池或重新选股。"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import math
import os
from pathlib import Path
import re
import tempfile

from services.trend_leader.observations import LABELS
from services.trend_leader.research_evidence import MAX_CARDS

SCHEMA = "trend-review-evidence-v1"
MARKER = "data-trend-review-evidence"


def missing(day, reason, status="missing-data"):
    return dict(schema=SCHEMA, trade_date=day, status=status, gaps=[reason], observations=[], cards=[])


def _date(value, limit):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value or value > limit:
        raise ValueError("证据日期非法或晚于观察日")


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("证据数值缺失或非法")
    return value


def _part(part, day, income=False):
    if part.get("status") not in {"complete", "partial", "source_failed"}:
        raise ValueError("户数/业绩状态非法")
    other = "prior_year" if income else "previous"
    for key in ("latest", other):
        p = part.get(key)
        if p is None:
            continue
        _date(p["end_date"], day); _date(p["ann_date"], day)
        if p["ann_date"] < p["end_date"]:
            raise ValueError("公告早于报告期")
        if income and (type(p.get("report_type")) is not int or p["report_type"] not in {1, 4}):
            raise ValueError("业绩必须使用合并累计报表口径")
        n = _number(p["value"])
        if not income and (n <= 0 or n != int(n)):
            raise ValueError("股东户数非法")
    current, prior = part.get("latest"), part.get(other)
    change_key = "profit_change_pct" if income else "change_pct"
    expected = None
    if current and prior:
        if income:
            if str(int(current["end_date"][:4])-1)+current["end_date"][4:] != prior["end_date"]:
                raise ValueError("业绩同比报告期错位")
        elif prior["end_date"] >= current["end_date"]:
            raise ValueError("户数对比期必须更早")
        if prior["value"] != 0:
            expected = (current["value"]-prior["value"])/abs(prior["value"])*100
    value = part.get(change_key)
    if expected is None:
        if value is not None or part["status"] == "complete":
            raise ValueError("缺对照却报完整变化率")
    elif value is None or not math.isclose(_number(value), expected, rel_tol=1e-9, abs_tol=1e-7):
        raise ValueError("户数/业绩变化率与原值不符")


def validate(payload, day):
    """日期、覆盖与原值对账。无效快照整体关闭，不能保留伪造的完整标题。"""
    try:
        _date(day, day)
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("trade_date") != day:
            raise ValueError("同日趋势复盘证据缺失或错日")
        if payload.get("status") in {"missing-data", "source_failed"}:
            return missing(day, "；".join(str(x) for x in payload.get("gaps", [])) or "趋势证据来源失败", payload["status"])
        observations, cards, coverage = payload["observations"], payload["cards"], payload["coverage"]
        if not isinstance(observations, list) or not isinstance(cards, list) or len(observations) > 500 or len(cards) > MAX_CARDS:
            raise ValueError("趋势证据数量非法")
        codes = []
        for row in observations:
            code = row["code"]
            if not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) or code in codes:
                raise ValueError("观察名单代码非法或重复")
            codes.append(code)
            if row.get("trade_date") != day or row.get("state") not in LABELS or row.get("status") not in {"complete", "partial"}:
                raise ValueError("个股观察日期或状态非法")
            if not isinstance(row.get("name"), str):
                raise ValueError("观察名称缺失")
            if row["state"] == "missing_data" and row["status"] != "partial":
                raise ValueError("缺行情却标完整")
            if row["state"] not in {"missing_data", "no_launch"} and not row.get("launch_date"):
                raise ValueError("启动观察缺少启动日")
            if row["state"] == "launched" and row.get("launch_date") != day:
                raise ValueError("当日启动日期错位")
            if row["state"] == "invalidated" and not row.get("invalidated_date"):
                raise ValueError("结构失效缺少失效日")
            support = row.get("sector_support")
            if support is None:
                if row["state"] != "missing_data":
                    raise ValueError("板块支持证据缺失")
            else:
                if support.get("trade_date") != day:
                    raise ValueError("板块支持日期错位")
                supported = support.get("supported")
                if support.get("status") == "complete":
                    delta = _number(support.get("delta_yi"))
                    share = _number(support.get("share_delta_pp"))
                    if type(supported) is not bool or supported != (delta > 0 and share > 0):
                        raise ValueError("板块支持与原值不符")
                elif support.get("status") != "missing_data" or supported is not None:
                    raise ValueError("板块支持状态非法")
                if row["status"] == "complete" and supported is None:
                    raise ValueError("板块支持未核验却标完整")
                required_support = {"pullback_observed": True, "support_weakened": False, "sector_unverified": None}
                if row["state"] in required_support and supported is not required_support[row["state"]]:
                    raise ValueError("观察结论与板块支持矛盾")
                if row["state"] in {"pullback_observed", "sector_unverified"} and not (
                        row.get("shrink_pullback") is True and row.get("near_ma5") is True):
                    raise ValueError("回踩结论缺少缩量与均线证据")
            for key in ("launch_date", "invalidated_date"):
                if row.get(key): _date(row[key], day)
            if row.get("launch_date") and row.get("invalidated_date") and row["invalidated_date"] < row["launch_date"]:
                raise ValueError("失效早于启动")
        card_codes = []
        for card in cards:
            if card["code"] not in codes or card["code"] in card_codes or card.get("trade_date") != day:
                raise ValueError("证据卡身份/日期错位")
            card_codes.append(card["code"])
            _part(card["holders"], day); _part(card["income"], day, income=True)
            statuses = [card[k]["status"] for k in ("holders", "income")]
            expected = "complete" if all(s == "complete" for s in statuses) else ("source_failed" if all(s == "source_failed" for s in statuses) else "partial")
            if card.get("status") != expected:
                raise ValueError("证据卡完整状态不符")
        absent = coverage["not_collected"]
        if any(type(coverage[k]) is not int for k in ("eligible", "collected")):
            raise ValueError("覆盖计数非法")
        if (coverage["eligible"] != len(codes) or coverage["collected"] != len(cards)
                or not isinstance(absent, list) or len(absent) != len(set(absent))
                or set(absent) != set(codes)-set(card_codes)):
            raise ValueError("证据卡覆盖与名单不符")
        if not isinstance(payload.get("source_gaps"), list): raise ValueError("来源缺口格式非法")
        gaps = list(payload["source_gaps"])
        if not all(isinstance(g, str) for g in gaps): raise ValueError("来源缺口非法")
        partial_codes = [r["code"] for r in observations if r["status"] != "complete"]
        if partial_codes: gaps.append("回踩/板块支持证据不完整："+"、".join(partial_codes))
        for c in cards:
            for k, label in (("holders", "股东户数"), ("income", "业绩")):
                if c[k]["status"] != "complete": gaps.append(c["code"]+label+"："+c[k]["status"])
        if absent: gaps.append("证据卡未覆盖："+"、".join(absent))
        expected = "partial" if gaps else "complete"
        if payload.get("status") != expected:
            raise ValueError("快照完整状态与覆盖不符")
        return {**payload, "gaps": gaps}
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
        return missing(day, str(exc), "source_failed")


def build(summary, markdown):
    day = summary["date"]
    required = ("launch_pullbacks", "research_cards", "research_coverage")
    if not all(k in summary for k in required):
        return missing(day, "扫描结果缺少新增证据", "source_failed")
    payload = dict(schema=SCHEMA, trade_date=day, generated_at=datetime.now(timezone.utc).isoformat(),
                   report_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
                   observations=deepcopy(summary["launch_pullbacks"]), cards=deepcopy(summary["research_cards"]),
                   coverage=deepcopy(summary["research_coverage"]),
                   source_gaps=[str(x) for key in ("source_errors", "data_errors") for x in summary.get(key, [])])
    payload["status"] = "partial" if (payload["source_gaps"] or payload["coverage"]["not_collected"]
        or any(r.get("status") != "complete" for r in payload["observations"]+payload["cards"])) else "complete"
    return validate(payload, day)


def write(root, day, payload):
    """仅由现有 daily CLI 调用；先序列化，再同目录原子替换。"""
    _date(day, day)
    payload = validate(payload, day)
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    path = root/f"{day}.review.json"
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False, suffix=".tmp") as f:
            tmp = f.name; f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp): os.unlink(tmp)
    return path


def load(root, day):
    try:
        _date(day, day)
        root = Path(root)
        payload = json.loads((root/f"{day}.review.json").read_text(encoding="utf-8"))
        checked = validate(payload, day)
        if checked["status"] in {"complete", "partial"}:
            digest = sha256((root/f"{day}.md").read_bytes()).hexdigest()
            if payload.get("report_sha256") != digest:
                return missing(day, "趋势快照与同日日报不匹配", "source_failed")
        return checked
    except FileNotFoundError:
        return missing(day, "同日趋势扫描证据尚未生成（通常21:30启动），不回退旧日或读取当前池")
    except (OSError, ValueError, TypeError) as exc:
        return missing(day, "趋势证据读取失败："+str(exc), "source_failed")


def structure(html):
    """提取官方模块完整标签/属性序列，避免纯文字对账放过折叠元数据漂移。"""
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.depth = 0; self.signature = []

        def handle_starttag(self, tag, attrs):
            if not self.depth and MARKER not in dict(attrs):
                return
            self.signature.append(("start", tag, tuple(sorted(attrs, key=lambda item: (item[0], item[1] or "")))))
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.depth += 1

        def handle_endtag(self, tag):
            if self.depth:
                self.signature.append(("end", tag)); self.depth -= 1

    parser = Parser(); parser.feed(html)
    return parser.signature


def render(payload, day):
    if payload is None:
        payload = missing(day, "同日趋势扫描证据尚未提供，不回退旧日或读取当前池")
    p = validate(payload, day)
    text = f'<div {MARKER}="{escape(day)}"><h3>放量回踩与股东户数、业绩证据</h3>'
    if p["status"] not in {"complete", "partial"}:
        return text+f'<p>[事实] {escape(p["status"])}：{escape("；".join(p["gaps"]))}。</p></div>', p["gaps"]
    obs, cards = p["observations"], p["cards"]
    counts = Counter(r["state"] for r in obs)
    labels = "；".join(f'{LABELS[k]}{v}只' for k,v in counts.items()) or "同日扫描无在池观察对象"
    text += f'<p>[判断] {escape(labels)}。[事实] 观察{len(obs)}只；证据卡{len(cards)}/{p["coverage"]["eligible"]}；状态{p["status"]}。趋势池观察不代表容量资格或交易动作。</p>'
    if not obs:
        return text+'</div>', p['gaps']
    items = len(obs)+len(cards)
    text += f'<details class="evidence" data-as-of="{escape(day)}" data-items="{items}" data-evidence-kind="trend-review"><summary>展开{items}项回踩观察与财务证据</summary><p>[事实] 同日趋势扫描归档；不读取当前池。前复权OHLC；户数与业绩均只引用观察日前可见公告。股东户数变化不证明机构持仓，已披露业绩不代表未来表现。</p>'
    if obs:
        text += '<div class="table-wrap"><table><thead><tr><th>代码/名称</th><th>启动日</th><th>观察 [判断]</th><th>状态 [事实]</th></tr></thead><tbody>'
        for r in obs:
            text += '<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in (r['code']+' '+r['name'],r.get('launch_date','未计算'),LABELS[r['state']],r['status']))+'</tr>'
        text += '</tbody></table></div>'
    names = {r['code']:r['name'] for r in obs}
    for c in cards:
        text += f'<h4>{escape(c["code"]+" "+names[c["code"]])}</h4>'
        for key,label,comparison,change in (("holders","股东户数","previous","change_pct"),("income","归母净利润累计（元）","prior_year","profit_change_pct")):
            part=c[key]; latest=part.get('latest'); prior=part.get(comparison)
            if not latest:
                detail=part.get('reason','有效数据不足')
            else:
                detail=f'{latest["value"]:,.0f}；截止{latest["end_date"]}，公告{latest["ann_date"]}'
                if prior: detail+=f'；对照{prior["value"]:,.0f}（截止{prior["end_date"]}，公告{prior["ann_date"]}）'
                v=part.get(change); detail+='；'+('同比' if key=='income' else '较上期')+('未计算' if v is None else f'{v:+.2f}%')
            if part.get('invalid_rows'): detail+=f'；无效记录{part["invalid_rows"]}行已排除'
            text += f'<p>[事实] {label}：{escape(detail)}；{escape(part["status"])}；来源{escape(str(part.get("source","未核验")))}。</p>'
    if p['gaps']: text+='<p>[事实] 缺口：'+escape('；'.join(p['gaps']))+'。</p>'
    return text+'</details></div>',p['gaps']
