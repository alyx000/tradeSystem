"""风险模块为组装器唯一生成；缺源可见，结果不冒充交易指令。"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from html import escape
from pathlib import Path
from statistics import mean

import yaml

from .collector import METHOD, failed
from .detectors import DAILY_BARS, LABELS, MINUTE_DAYS, PARAMETERS, SCHEMA, analyze, normalize, number


def validate(payload, report_date):
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA or payload.get("date") != report_date:
        raise ValueError("板块风险格式或日期不一致")
    if payload.get("parameters") != PARAMETERS or payload.get("status") not in {"complete", "partial", "source_failed", "skipped"}:
        raise ValueError("板块风险参数或状态非法")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) > 28:
        raise ValueError("板块风险行数非法")
    if not rows:
        if payload["status"] not in {"source_failed", "skipped"}:
            raise ValueError("空结果不能标为完成")
        return payload
    days = payload.get("expected_dates", [])
    if len(days) != DAILY_BARS or days != sorted(set(days)) or days[-1] != report_date:
        raise ValueError("板块风险日期窗口非法")
    for day in days:
        date.fromisoformat(day)
    market = payload.get("market", {})
    if market.get("status") == "complete":
        amounts = [number(x) for x in market.get("amount_thousand_yuan", [])]
        if market.get("dates") != days[-23:] or len(amounts) != 23 or min(amounts) <= 0:
            raise ValueError("大盘量能日期或数值不完整")
        ratios = [amounts[i]/mean(amounts[i-20:i]) for i in range(20, 23)]
        if (market.get("three_ratios") != [round(x, 4) for x in ratios]
                or market.get("ratio_to_prior20") != round(ratios[-1], 4)
                or market.get("sustained_increment") != all(x >= 1.1 for x in ratios)):
            raise ValueError("增量背景与成交额证据不一致")
    codes = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("板块证据行格式非法")
        code = row["code"]
        if code in codes or row.get("kind") not in {"industry", "concept"}:
            raise ValueError("板块身份重复或类型非法")
        codes.add(code)
        if row.get("status") == "source_failed":
            if row.get("level") != "missing_data":
                raise ValueError("失败板块不能有结论")
            continue
        raw_daily = [{**b, "ts_code": code, "trade_date": b["time"]} for b in row["daily_bars"]]
        daily = normalize(raw_daily, code, days)
        minutes = None
        if row.get("minute_bars"):
            stamps = [d+" "+t for d in days[-MINUTE_DAYS:] for t in ("10:30:00", "11:30:00", "14:00:00", "15:00:00")]
            raw_minutes = [{**b, "ts_code": code, "trade_time": b["time"]} for b in row["minute_bars"]]
            minutes = normalize(raw_minutes, code, stamps, minute=True)
        excluded = row.get("excluded_closed_dates", [])
        if any(x in days or not days[0] <= x <= days[-1] for x in excluded):
            raise ValueError("不得排除开放日或范围外行情")
        if row.get("status") != ("complete" if minutes and not excluded else "partial") or row.get("minute_status") != ("complete" if minutes else "source_failed"):
            raise ValueError("分钟覆盖与状态不一致")
        computed = analyze(daily, minutes)
        if any(row.get(k) != v for k, v in computed.items()):
            raise ValueError("板块风险结论与K线证据不一致")
    expected_status = "source_failed" if all(r["status"] == "source_failed" for r in rows) else (
        "partial" if payload.get("gaps") or any(r["status"] != "complete" for r in rows) else "complete")
    if payload["status"] != expected_status:
        raise ValueError("板块风险总状态不一致")
    return payload


def load(daily_root, reports_root, report_date):
    """同日信封优先；更晚且证据覆盖不退化的同日补采可恢复，绝不跨日。"""
    envelope_payload = None
    envelope_error = None
    try:
        path = Path(daily_root)/report_date/"post-market.yaml"
        envelope = yaml.safe_load(path.read_text()) if path.is_file() else {}
        raw = envelope.get("raw_data", {}) if isinstance(envelope, dict) else {}
        if isinstance(raw, dict) and "sector_adjustment_risk" in raw:
            if str(envelope.get("date")) != report_date:
                raise ValueError("盘后信封日期错位")
            envelope_payload = validate(raw["sector_adjustment_risk"], report_date)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
        envelope_error = "同日盘后信封缺失或证据校验失败"
    try:
        standalone = validate(json.loads((Path(reports_root)/f"{report_date}.json").read_text()), report_date)
    except (OSError, ValueError, TypeError, KeyError):
        standalone = None
    if envelope_payload is None:
        if standalone is not None:
            return {**standalone, "recovery_note": envelope_error} if envelope_error else standalone
        return failed(report_date, "同日板块风险报告缺失或证据校验失败")
    if standalone is not None:
        def covered(p, minute=False):
            return {r["code"] for r in p["rows"] if
                    r.get("minute_status") == "complete"} if minute else {
                        r["code"] for r in p["rows"] if r["status"] != "source_failed"}
        try:
            newer = datetime.fromisoformat(standalone["generated_at"]) > datetime.fromisoformat(envelope_payload["generated_at"])
        except (ValueError, TypeError, KeyError):
            newer = False
        # 不能仅按行数替换：须保留原先有效的具体板块及分钟证据。
        preserves = (covered(standalone) >= covered(envelope_payload)
                     and covered(standalone, True) >= covered(envelope_payload, True))
        # 同集合的新行情修正也必须到达正式HTML。对比已验证的实际证据，
        # 不能仅靠generated_at变化刷新，也不能仅靠新增代码才允许纠正。
        changed = any(standalone.get(key) != envelope_payload.get(key)
                      for key in ("rows", "market", "selection", "gaps"))
        if newer and preserves and changed:
            prior = "；".join(envelope_payload.get("gaps", [])) or envelope_payload["status"]
            return {**standalone, "recovery_note": "采用同日较新补采恢复证据；原盘后状态："+prior}
    return envelope_payload


def render(payload, report_date):
    try:
        payload = validate(payload, report_date)
    except (ValueError, TypeError, KeyError):
        payload = failed(report_date, "板块风险证据无效，未判断")
    rows = payload["rows"]
    valid = [r for r in rows if r["status"] != "source_failed"]
    counts = Counter(r["level"] for r in valid)
    status = payload["status"]
    gaps = list(dict.fromkeys(payload.get("gaps", [])+[g for r in rows for g in r.get("gaps", [])]))
    summary = (f'覆盖 {len(valid)}/{len(rows)} 个活跃板块；'
               f'多周期信号 {counts["confirmed"]}，日线加强 {counts["daily_break"]}，动能预警 {counts["warning"]}。'
               f'60分钟完整 {sum(r.get("minute_status") == "complete" for r in rows)}/{len(rows)}，缺失不能解释为无风险。') if rows else "未取得可用板块风险数据，未判断。"
    if status == "skipped":
        summary = "休市日，未计算板块调整风险。"
    fragment = [f'<div data-sector-adjustment-risk="v1" data-as-of="{report_date}" data-source-status="{status}">',
                '<h3>活跃板块调整风险</h3>', f'<p>[判断] {summary}</p>']
    if payload.get("recovery_note"):
        fragment.append('<p>[事实] '+escape(payload["recovery_note"])+'</p>')
    market = payload.get("market") or {}
    # 量能背景仅作为解释，不降低已出现的破位信号等级。
    if market.get("status") == "complete":
        ratio = market.get("ratio_to_prior20")
        if isinstance(ratio, (int, float)):
            fragment.append(f'<p>[事实] 沪深合计成交额／前20日合计均额 {ratio:.2f} 倍；[判断] 持续增量代理：{"成立，背离可能延后兑现" if market.get("sustained_increment") else "未成立，不等于必然下跌"}。</p>')
    else:
        fragment.append('<p>[事实] 两市量能背景缺失，增量例外未计算。</p>')
    fragment.append(f'<details class="evidence" data-as-of="{report_date}" data-items="{max(1, len(rows))}" data-source="同日板块风险快照"><summary>逐板块证据、缺口与判断口径（{max(1, len(rows))} 项）</summary>')
    fragment.append(f'<p>[事实] {escape(payload.get("selection", {}).get("definition", "活跃榜缺失"))}</p>')
    order = {key: i for i, key in enumerate(LABELS)}
    if rows:
        fragment.append('<div class="table-wrap"><table><thead><tr><th>板块／口径</th><th>风险判断</th><th>上涨前提</th><th>日线证据</th><th>60分钟／缺口</th></tr></thead><tbody>')
        for r in sorted(rows, key=lambda r: (order.get(r["level"], 99), r["kind"], r["code"])):
            rise = f'{r["rise_pct"]:.1f}% / 峰值距今{r["peak_age"]}日' if "rise_pct" in r else "未计算"
            daily = "、".join(x for flag, x in [(r.get("daily_divergence"), "双峰背离"), (r.get("hist_weakening"), "红柱衰减"), (r.get("bearish_engulfing"), "放量阴包阳")] if flag) or ("本规则未触发" if r["status"] != "source_failed" else "未计算")
            minute = "、".join(x for flag, x in [(r.get("minute_divergence"), "顶背离"), (r.get("minute_break"), "支撑代理跌破")] if flag)
            minute = minute or ("本规则未触发" if r.get("minute_status") == "complete" else "未核验")
            if r.get("gaps"):
                minute += "；"+"；".join(r["gaps"])
            cells = [r["name"]+" / "+("申万二级" if r["kind"] == "industry" else "同花顺概念")+" / "+r["code"], LABELS[r["level"]], rise, daily, minute]
            fragment.append('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in cells)+'</tr>')
        fragment.append('</tbody></table></div>')
    fragment.append('<p>'+escape(METHOD)+'</p><p>[判断] 这是调整风险观察，不是已确定的顶部或买卖指令；未触发不代表安全。量能背景不覆盖已发生的破位事实。</p>')
    if gaps:
        fragment.append('<p>[事实] 数据缺口：'+escape('；'.join(gaps))+'</p>')
    fragment.append('</details></div>')
    return '\n'.join(fragment), gaps
