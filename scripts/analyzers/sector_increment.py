"""全量沪深A股成交额增量归因。行业采用采集时申万二级快照，不代表历史成分。"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from html import escape
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from analyzers.low_price_effect import _finite_float

SCHEMA = "sector-increment-v1"
MIN_COUNT = 4000
MIN_COVERAGE = .98
MIN_INDUSTRY_COVERAGE = .99
NET_INCREMENT_FLOOR = .005  # 净增额低于昨日市场额0.5%不计算贡献率，工程稳定性闸门。
DEFINITION = "沪深A股（含ST、剔B股）；成交额非净流入；采集时申万二级快照同时归类两日；非历史成分回放"


def failed(day, reason):
    return dict(schema=SCHEMA, trade_date=day, status="source_failed", gaps=[str(reason)], rows=[])


def _code(value):
    text = str(value or "")
    if len(text) != 9 or text[6:] not in {".SH", ".SZ", ".BJ"} or not text[:6].isdigit():
        raise ValueError("证券代码或交易所非法")
    return text


def _eligible(code):
    return not code.endswith(".BJ") and not code.startswith(("200", "201", "900"))


def _day(value):
    text = str(value or "")
    return datetime.strptime(text, "%Y%m%d" if len(text) == 8 else "%Y-%m-%d").date().isoformat()


def calculate(today, previous, identities, industry_map, day, prev_day, *, min_count=MIN_COUNT):
    """保留两日各自全量成交额，显式列出非共同成分；不把缺行情补为0。"""
    base = dict(schema=SCHEMA, trade_date=day, previous_date=prev_day, definition=DEFINITION,
                industry_basis="current_snapshot", rows=[], gaps=[], coverage={})
    try:
        if _day(day) <= _day(prev_day):
            raise ValueError("比较日期非法")
        if not isinstance(identities, list) or not identities or not isinstance(industry_map, dict) or not industry_map:
            raise ValueError("证券宇宙或行业快照缺失")
        universe = {}
        for r in identities:
            c = _code(r["ts_code"])
            if c in universe:
                raise ValueError("证券宇宙重复")
            universe[c] = (_day(r["list_date"]), _day(r["delist_date"]) if r.get("delist_date") else None)
        quotes = []
        for rows, d in ((previous, prev_day), (today, day)):
            if not isinstance(rows, list) or not rows:
                raise ValueError("全市场日线为空")
            by_code = {}
            seen = set()
            for r in rows:
                c = _code(r["ts_code"])
                if c in seen or _day(r["trade_date"]) != d:
                    raise ValueError("日线重复或日期错位")
                seen.add(c)
                if not _eligible(c):
                    continue
                if c not in universe:
                    raise ValueError("行情无法匹配证券宇宙")
                listed, delisted = universe[c]
                if listed > d or (delisted and delisted <= d):
                    raise ValueError("行情上市/退市身份错位")
                amount = _finite_float(r.get("amount"))
                if amount is None or amount < 0:
                    raise ValueError("成交额缺失/非法，不能按0归因")
                by_code[c] = amount / 1e5  # daily.amount 千元 → 亿元
            expected = {c for c, (listed, delisted) in universe.items()
                        if _eligible(c) and listed <= d and (not delisted or delisted > d)}
            coverage = len(by_code) / len(expected) if expected else 0
            base["coverage"][d] = dict(quote_count=len(by_code), universe_count=len(expected),
                                       ratio=coverage, missing_codes=sorted(expected-set(by_code)))
            if len(by_code) < min_count or coverage < MIN_COVERAGE:
                raise ValueError("行情数量/证券宇宙覆盖不足")
            if expected-set(by_code):
                base["gaps"].append(f"{d}上市宇宙无行情{len(expected-set(by_code))}只（可能停牌，未补0）")
            quotes.append(by_code)
        prev, curr = quotes
        union, common = set(prev) | set(curr), set(prev) & set(curr)
        base["coverage"].update(common_ratio=len(common)/len(union),
                                today_only=sorted(set(curr)-set(prev)), previous_only=sorted(set(prev)-set(curr)))
        if len(common)/len(union) < MIN_COVERAGE:
            raise ValueError("两日共同证券覆盖不足")
        groups = defaultdict(lambda: [0., 0.])
        unknown = []
        components = []
        for c in sorted(union):
            entry = industry_map.get(c, {})
            industry = entry.get("sw_l2") if isinstance(entry, dict) else None
            if not isinstance(industry, str) or not industry.strip() or industry == "未分类":
                industry = "未分类"
                unknown.append(c)
            # 各日分别汇总；非共同成分不产生个股增量，名单已单列。
            if c in prev:
                groups[industry][0] += prev[c]
            if c in curr:
                groups[industry][1] += curr[c]
            components.append(dict(ts_code=c, industry=industry,
                                   previous_amount_yi=prev.get(c), amount_yi=curr.get(c)))
        base["components"] = components
        base["coverage"]["unclassified_codes"] = unknown
        if 1-len(unknown)/len(union) < MIN_INDUSTRY_COVERAGE:
            raise ValueError("行业映射覆盖不足99%")
        if unknown:
            base["gaps"].append(f"未分类{len(unknown)}只，保留未分类桶")
        p, t = sum(prev.values()), sum(curr.values())
        if min(p, t) <= 0:
            raise ValueError("市场成交额非正")
        delta = t-p
        contribution_ok = delta > 0 and delta/p >= NET_INCREMENT_FLOOR
        base["market"] = dict(previous_amount_yi=p, amount_yi=t, delta_yi=delta,
                              change_pct=delta/p*100, contribution_available=contribution_ok,
                              contribution_floor_pct=NET_INCREMENT_FLOOR*100)
        for name, (pa, ta) in groups.items():
            da, share = ta-pa, (ta/t-pa/p)*100
            base["rows"].append(dict(industry=name, previous_amount_yi=pa, amount_yi=ta,
                                     delta_yi=da, share_delta_pp=share,
                                     contribution_pct=da/delta*100 if contribution_ok else None))
        base["rows"].sort(key=lambda r: (-r["delta_yi"], r["industry"]))
        base["status"] = "partial" if base["gaps"] else "complete"
        return base
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {**base, "status": "source_failed", "gaps": base["gaps"]+[str(exc)], "rows": []}


def collect(registry, day, *, now=None, db_path=None):
    """复用盘后信封落盘；只读完整日历，不新增调度或业务表。"""
    try:
        now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.tzinfo is None:
            raise ValueError("运行时刻必须包含时区")
        now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        target = date.fromisoformat(day)
        if target > now.date():
            raise ValueError("未来日期")
        from db.connection import get_readonly_connection
        conn = get_readonly_connection(db_path)
        start = target-timedelta(days=20)
        try:
            calendar = dict(conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date BETWEEN ? AND ?",
                                         (start.isoformat(), day)).fetchall())
        finally:
            conn.close()
        if any(calendar.get((start+timedelta(days=i)).isoformat()) not in (0, 1) for i in range(21)):
            raise ValueError("交易日历缺失或不完整")
        if not calendar[day] or (target == now.date() and now.hour < 16):
            return dict(schema=SCHEMA, trade_date=day, status="skipped", rows=[], gaps=["休市或尚未收盘"])
        days = sorted(d for d, flag in calendar.items() if flag)
        if len(days) < 2:
            raise ValueError("缺少上一开放日")
        prev = days[-2]
        results = []
        for method, args in (("get_market_daily_quotes", (day,)), ("get_market_daily_quotes", (prev,)),
                             ("get_stock_universe_as_of", (day,)), ("get_stock_sw_industry_map", ())):
            r = registry.call(method, *args)
            if not r.success:
                raise ValueError(f"{method}来源失败:{r.error}")
            if method == "get_market_daily_quotes" and r.source != "tushare:daily":
                raise ValueError("成交额单位未核验，拒绝替代源")
            results.append(r)
        identity = results[2].data
        if prev[:7] != day[:7]:
            # as_of宇宙按月保留退市身份；跨月比较补上月末身份，避免漏掉假期内退市证券。
            previous_identity = registry.call("get_stock_universe_as_of", prev)
            if not previous_identity.success or not isinstance(previous_identity.data, list):
                raise ValueError("跨月比较的上期证券身份来源失败")
            merged = {r["ts_code"]: r for r in identity}
            if len(merged) != len(identity):
                raise ValueError("当期证券身份重复")
            seen_previous = set()
            for row in previous_identity.data:
                code = row["ts_code"]
                if code in seen_previous or (code in merged and merged[code] != row):
                    raise ValueError("跨月证券身份重复或冲突")
                seen_previous.add(code)
                merged[code] = row
            identity = list(merged.values())
            results.append(previous_identity)
        result = calculate(results[0].data, results[1].data, identity, results[3].data, day, prev)
        result["sources"] = [dict(source=r.source, fetched_at=getattr(r, "fetched_at", None)) for r in results]
        result["generated_at"] = now.isoformat()
        return result
    except Exception as exc:
        return failed(day, exc)


def render(block):
    """Markdown全量行业表，保留负贡献及大于100%的贡献。"""
    lines = [f"状态：{block.get('status', 'source_failed')}；{DEFINITION}"]
    lines.extend(f"- 缺口：{g}" for g in block.get("gaps", []))
    if block.get("status") not in {"complete", "partial"}:
        return lines
    m = block["market"]
    lines += [f"比较日：{block['previous_date']} → {block['trade_date']}；市场增额 {m['delta_yi']:+.2f} 亿元。",
              "贡献率仅在市场净增额≥昨日成交额0.5%时展示；可为负数或超过100%，不代表净资金流入。",
              "| 申万二级 | 成交额(亿) | 增额(亿) | 份额变化(pp) | 净增额贡献(%) |",
              "| --- | --- | --- | --- | --- |"]
    for r in block["rows"]:
        c = "未计算" if r["contribution_pct"] is None else f"{r['contribution_pct']:+.2f}"
        name = r["industry"].replace("|", "／").replace("\n", " ")
        lines.append(f"| {name} | {r['amount_yi']:.2f} | {r['delta_yi']:+.2f} | {r['share_delta_pp']:+.2f} | {c} |")
    return lines


def render_html(block, day):
    block = validate(block, day)
    body = f'<p>状态：{escape(block["status"])}；{escape(DEFINITION)}</p>'
    body += ''.join(f'<p>缺口：{escape(g)}</p>' for g in block.get("gaps", []))
    if block["status"] in {"complete", "partial"}:
        expanded_table = len(block["rows"]) > 10
        body += (f'<p>{escape(block["previous_date"])} → {escape(day)}；'
                 f'市场增额{block["market"]["delta_yi"]:+.2f}亿元；净增额不足昨日市场额0.5%时贡献率未计算。</p>'
                 )
        if expanded_table:
            top = '；'.join(f'{escape(r["industry"])} {r["delta_yi"]:+.2f}亿' for r in block["rows"][:3])
            body += (f'<p>成交额增额排序前三：{top}。</p>'
                     f'<details class="evidence" data-as-of="{escape(day)}" data-items="{len(block["rows"])}">'
                     f'<summary>全量{len(block["rows"])}个行业增额证据</summary>')
        body += ('<div style="overflow-x:auto"><table><thead><tr><th>申万二级</th><th>成交额(亿)</th>'
                 '<th>增额(亿)</th><th>份额变化(pp)</th><th>净增额贡献(%)</th></tr></thead><tbody>')
        for r in block["rows"]:
            c = "未计算" if r["contribution_pct"] is None else f'{r["contribution_pct"]:+.2f}'
            body += (f'<tr><td>{escape(r["industry"])}</td><td>{r["amount_yi"]:.2f}</td>'
                     f'<td>{r["delta_yi"]:+.2f}</td><td>{r["share_delta_pp"]:+.2f}</td><td>{c}</td></tr>')
        body += '</tbody></table></div>'
        if expanded_table:
            body += '</details>'
    return (f'<section data-sector-increment="{escape(day)}"><h3>板块增量归因 [事实·计算]</h3>{body}</section>',
            block.get("gaps", []) if block.get("status") != "complete" else [])


def validate(block, day):
    """展示/消费端复算总额、份额及贡献；损坏快照不能显示为完整事实。"""
    try:
        if (not isinstance(block, dict) or block.get("schema") != SCHEMA or block.get("trade_date") != day
                or block.get("status") not in {"complete", "partial", "source_failed", "skipped"}):
            raise ValueError("同日板块增量归因缺失/错位")
        if not isinstance(block.get("gaps"), list) or any(not isinstance(g, str) for g in block["gaps"]):
            raise ValueError("缺口结构非法")
        if block["status"] in {"source_failed", "skipped"}:
            return {**block, "rows": []}
        if _day(block["previous_date"]) >= day or not block["rows"]:
            raise ValueError("比较日期或行业表非法")
        m = block["market"]
        p, t = (_finite_float(m[k]) for k in ("previous_amount_yi", "amount_yi"))
        if p is None or t is None or min(p, t) <= 0:
            raise ValueError("市场额非法")
        close = lambda a, b: _finite_float(a) is not None and math.isclose(float(a), b, rel_tol=1e-9, abs_tol=1e-7)
        delta = t-p
        available = delta > 0 and delta/p >= NET_INCREMENT_FLOOR
        if not close(m["delta_yi"], delta) or m["contribution_available"] is not available:
            raise ValueError("市场增额与证据不符")
        names = set()
        for r in block["rows"]:
            if not isinstance(r["industry"], str) or r["industry"] in names:
                raise ValueError("行业名称重复/非法")
            names.add(r["industry"])
            pa, ta = (_finite_float(r[k]) for k in ("previous_amount_yi", "amount_yi"))
            if pa is None or ta is None or min(pa, ta) < 0:
                raise ValueError("行业成交额非法")
            if not close(r["delta_yi"], ta-pa) or not close(r["share_delta_pp"], (ta/t-pa/p)*100):
                raise ValueError("行业增额/份额与证据不符")
            if (available and not close(r["contribution_pct"], (ta-pa)/delta*100)) or (not available and r["contribution_pct"] is not None):
                raise ValueError("行业贡献与证据不符")
        if not close(sum(r["amount_yi"] for r in block["rows"]), t) or not close(sum(r["previous_amount_yi"] for r in block["rows"]), p):
            raise ValueError("行业合计与市场总额不符")
        if block["status"] == "complete" and block["gaps"]:
            raise ValueError("有缺口却标完整")
        return block
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return failed(day, exc)


def load_snapshot(daily_root, day):
    import yaml
    try:
        envelope = yaml.safe_load((Path(daily_root)/day/"post-market.yaml").read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or str(envelope.get("date")) != day:
            raise ValueError("盘后信封日期错位")
        return validate(envelope.get("raw_data", {}).get("sector_increment"), day)
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
        return failed(day, exc)
