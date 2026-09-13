"""复盘反馈趋势：只读归档，严格开放日脊柱，不补采或写业务数据。"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from html import escape
from pathlib import Path
from statistics import mean, median

import yaml

from services.emotion_leader.suspensions import is_verified_suspension, reconcile_report_file


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _count(value):
    number = _number(value)
    return int(number) if number is not None and number >= 0 and number.is_integer() else None


def _read(path, *, yaml_file=False):
    try:
        text = path.read_text(encoding="utf-8")
        payload = yaml.load(text, Loader=yaml.CSafeLoader) if yaml_file else json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, yaml.YAMLError):
        return {}


def _calendar(db_path, report_date):
    """22 个开放日含最早反馈点的 T-2；自然日缺口不能被 SQL 跳过。"""
    if db_path is None or not Path(db_path).is_file():
        return []
    try:
        with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as conn:
            rows = conn.execute(
                "SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date<=? "
                "ORDER BY date DESC LIMIT 120", (report_date,),
            ).fetchall()
        if not rows or rows[0][0] != report_date:
            return []
        expected = date.fromisoformat(report_date)
        opened = []
        for day, is_open in rows:
            if day != expected.isoformat() or is_open not in (0, 1):
                return []
            if is_open:
                opened.append(day)
            if len(opened) == 22:
                return list(reversed(opened))
            expected -= timedelta(days=1)
    except (sqlite3.Error, ValueError, TypeError):
        pass
    return []


def _board_point(payload, days):
    point = {"date": days[2], "status": "missing-data", "sample": None, "total": None, "values": {}}
    if str(payload.get("date")) != days[2]:
        return point
    raw = payload.get("raw_data")
    block = raw.get("board_break_feedback") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return point
    status = block.get("status")
    point["status"] = status if status in ("ok", "partial", "source_failed", "empty") else "source_failed"
    if status not in ("ok", "partial", "empty"):
        return point
    if [block.get(k) for k in ("source_connected_date", "break_date", "outcome_date")] != days:
        point["status"] = "source_failed"
        return point
    counts = [_count(block.get(k)) for k in ("sample_count", "break_count", "break_candidate_count", "connected_count")]
    rows = block.get("details")
    if any(n is None for n in counts) or counts != sorted(counts) or not isinstance(rows, list) or len(rows) != counts[0]:
        point["status"] = "source_failed"
        return point
    n, total, candidates, _ = counts
    point.update(sample=n, total=total)
    if n == 0:
        point["status"] = "empty" if status in ("ok", "empty") and total == 0 and block.get("empty_reason") in ("no_connected_candidates", "no_board_breaks") else "partial"
        return point
    codes = set()
    opens, closes = [], []
    for row in rows:
        if not isinstance(row, dict):
            point["status"] = "source_failed"
            return point
        code = str(row.get("code") or "").split(".")[0]
        op, cl = _number(row.get("feedback_open_pct")), _number(row.get("feedback_close_pct"))
        height = _count(row.get("previous_height"))
        if len(code) != 6 or not code.isdigit() or code in codes or op is None or cl is None or height is None or height < 2:
            point["status"] = "source_failed"
            return point
        codes.add(code)
        opens.append(op)
        closes.append(cl)
    # 直接从完整逐股明细重算，避免旧汇总精度和百分数/小数口径漂移。
    point["values"] = {"open": mean(opens), "close": mean(closes), "median": median(closes),
                       "up_rate": sum(v > 0 for v in closes) / n * 100}
    point["status"] = "ok" if status == "ok" and n == total == candidates else "partial"
    return point


def _core_point(payload, day):
    point = {"date": day, "status": "missing-data", "sample": None, "total": None, "values": {}}
    if payload.get("date") != day:
        return point
    status = payload.get("status")
    point["status"] = status if status in ("ok", "partial", "source_failed") else "source_failed"
    if status not in ("ok", "partial"):
        return point
    rows, summary, promoted = payload.get("active"), payload.get("summary"), payload.get("promoted_today")
    if not isinstance(rows, list) or not isinstance(summary, dict) or not isinstance(promoted, list):
        point["status"] = "source_failed"
        return point
    n = _count(summary.get("active_count"))
    keys = ("today_limit_up_count", "today_limit_down_count", "new_peak_count")
    values = [_count(summary.get(k)) for k in keys]
    if n != len(rows) or any(v is None or v > n for v in values) or values[0] + values[1] > n:
        point["status"] = "source_failed"
        return point
    if any(not isinstance(r, dict) or not r.get("code") for r in rows + promoted):
        point["status"] = "source_failed"
        return point
    codes = {r["code"] for r in rows}
    if len(codes) != n or len({r["code"] for r in promoted}) != len(promoted) or any(r["code"] not in codes or r.get("promoted_date") != day for r in promoted):
        point["status"] = "source_failed"
        return point
    if values != [sum(r.get("current_state") == "涨停" for r in rows),
                  sum(r.get("current_state") == "跌停" for r in rows),
                  sum(r.get("new_peak_today") is True for r in rows)]:
        point["status"] = "source_failed"
        return point
    valid = sum(r.get("metric_status") == "ok" for r in rows)
    suspended = sum(is_verified_suspension(r, day) for r in rows)
    point["suspended"] = suspended
    point["missing"] = n - valid - suspended
    point["notes"] = [f"{r.get('name', '')} {r['code']} 全天停牌（原始记录 #{r['suspension_evidence']['raw_payload_id']}）" for r in rows if is_verified_suspension(r, day)]
    coverage = payload.get("coverage") or {}
    complete_history = isinstance(coverage, dict) and _count(coverage.get("expected_open_days")) not in (None, 0) and coverage.get("expected_open_days") == coverage.get("loaded_limit_days")
    if not complete_history:
        point["notes"].append(f"历史涨停覆盖 {coverage.get('loaded_limit_days', '—')}/{coverage.get('expected_open_days', '—')} 日" if isinstance(coverage, dict) else "历史覆盖未知")
    if point["missing"]:
        point["notes"].append("指标不可算：" + "、".join(f"{r.get('name', '')} {r['code']}" for r in rows if r.get("metric_status") != "ok" and not is_verified_suspension(r, day)))
    point.update(sample=valid, total=n, values=dict(zip(("limit_up", "limit_down", "peak"), values)))
    point["values"]["promoted"] = len(promoted)
    if n > 0 and valid == 0:
        point["values"].pop("peak")
    point["status"] = "ok" if status == "ok" and valid + suspended == n and complete_history else "partial"
    return point


def load_feedback_trends(daily_dir, emotion_dir, db_path, report_date):
    days = _calendar(db_path, report_date)
    result = {"board": [], "core": [], "calendar_status": "ok" if days else "missing-data"}
    for i in range(2, len(days)):
        day = days[i]
        result["board"].append(_board_point(_read(Path(daily_dir) / day / "post-market.yaml", yaml_file=True), days[i-2:i+1]))
        result["core"].append(_core_point(reconcile_report_file(_read(Path(emotion_dir) / f"{day}.json"), db_path, day), day))
    return result


def _fmt(value):
    return "—" if value is None else f"{value:.2f}".rstrip("0").rstrip(".")


def _panel(points, series, unit, panel_id, *, compact=False, axis_max=None):
    colors = {"open": "#c04b3c", "close": "#2779ae", "median": "#8b63b5",
              "up_rate": "#c04b3c", "limit_up": "#c04b3c", "limit_down": "#2779ae",
              "peak": "#8b63b5", "promoted": "#29826b"}
    values = [p["values"][key] for p in points for key, _ in series if key in p["values"]]
    lo, hi = min([0, *values]), max([1, *values])
    if series == [("up_rate", "收涨率")]:
        lo, hi = 0, 100
    if axis_max is not None:
        lo, hi = 0, axis_max
    left, right, bottom, chart_height = (44, 432, 166, 120) if compact else (64, 860, 204, 148)
    x = lambda i: left + (right-left) * i / max(1, len(points) - 1)
    y = lambda v: bottom - chart_height * (v-lo) / (hi-lo)
    viewbox = "0 0 470 208" if compact else "0 0 920 270"
    parts = [f'<svg viewBox="{viewbox}" role="img" aria-label="{escape(panel_id)}">']
    ticks = {lo, 0, hi, (lo+hi)/2} if compact else {lo, 0, hi}
    for tick in sorted(ticks):
        parts.append(f'<line x1="{left}" x2="{right}" y1="{y(tick):.1f}" y2="{y(tick):.1f}" stroke="currentColor" opacity=".18"/><text x="{left-8}" y="{y(tick)+4:.1f}" text-anchor="end">{_fmt(tick)}{unit}</text>')
    for j, (key, label) in enumerate(series):
        color = colors[key]
        parts.append(f'<text x="{left+j*195}" y="24" fill="{color}">{escape(label)}</text>')
        previous = None
        for i, point in enumerate(points):
            value = point["values"].get(key)
            if value is None:
                previous = None
                continue
            cx, cy = x(i), y(value)
            if previous is not None:
                # 有值的 partial 点可展示观测走势，但不能伪装成完整来源；缺值仍清空 previous。
                line_status = "ok" if previous[2] == point["status"] == "ok" else "partial"
                dash = ' stroke-dasharray="5 4"' if line_status == "partial" else ""
                parts.append(f'<line data-trend-line="{key}" data-line-status="{line_status}" x1="{previous[0]:.1f}" y1="{previous[1]:.1f}" x2="{cx:.1f}" y2="{cy:.1f}" stroke="{color}" stroke-width="2"{dash}/>')
            filled = color if point["status"] == "ok" else "var(--paper, white)"
            title = f'{point["date"]} {label} {_fmt(value)}{unit}；样本 {point["sample"]}/{point["total"]}；{point["status"]}'
            radius = 2.8 if compact else 4
            parts.append(f'<circle data-trend-point="{key}" data-date="{point["date"]}" data-status="{point["status"]}" aria-label="{escape(title)}" cx="{cx:.1f}" cy="{cy:.1f}" r="{radius}" fill="{filled}" stroke="{color}" stroke-width="1.8"/>')
            previous = (cx, cy, point["status"])
    for i, point in enumerate(points):
        label = "无样本" if point["status"] == "empty" else "缺"
        if point.get("sample") == 0 and point.get("suspended", 0) > 0 and point.get("suspended") == point.get("total"):
            label = "停牌"
        if not any(key in point["values"] for key, _ in series):
            parts.append(f'<text x="{x(i):.1f}" y="{bottom-chart_height/2:.1f}" text-anchor="middle">{label}</text>')
        if i in {0, len(points)//3, len(points)*2//3, len(points)-1}:
            parts.append(f'<text x="{x(i):.1f}" y="{bottom+26}" text-anchor="middle">{point["date"][5:]}</text>')
        if not compact:
            parts.append(f'<text x="{x(i):.1f}" y="254" text-anchor="middle">{point["sample"] if point["sample"] is not None else "—"}</text>')
    if not compact:
        parts.append('<text x="12" y="254">样本</text>')
    parts.append('</svg>')
    return "".join(parts)


def render_feedback_trends(payload, report_date):
    payload = payload or {}
    fragments, gaps = [], []
    specs = (
        ("board", "断板次日反馈趋势", [[("open", "开盘均值"), ("close", "收盘均值"), ("median", "收盘中位数")], [("up_rate", "收涨率")]], "%",
         "T-2 非ST连板≥2 → T-1 有交易且不再涨停 → T 开盘/收盘反馈；采用反馈日行情昨收口径（含除权除息处理）。样本为有效反馈数，分母为确认断板数。"),
        ("core", "情绪核心反馈趋势", [[("limit_up", "涨停核心")], [("limit_down", "跌停核心")], [("peak", "触及区间高点")], [("promoted", "新增核心")]], "只",
         "读取当日全部活跃情绪核心，非页面前12只；新增核心为当日升格，非连板晋级率。样本为指标可算数，分母为活跃核心数；已核实全天停牌单列且不补零；触及区间高点含持平。每日成员变化，计数不代表固定组合收益。"),
    )
    for kind, title, panels, unit, definition in specs:
        points = payload.get(kind) or []
        status = "complete" if points and all(p["status"] in ("ok", "empty") for p in points) else "partial"
        valid = sum(bool(p["values"]) for p in points)
        marker = f'data-{kind}-feedback-trend'
        if not points:
            fragments.append(f'<p {marker}="missing-data" data-as-of="{report_date}" data-source-status="missing-data">[事实] {title}：历史数据或完整交易日历不足，未计算。</p>')
            gaps.append(f"{title}：历史数据或完整交易日历不足")
            continue
        if status != "complete":
            gaps.append(f'{title}：{sum(p["status"] not in ("ok", "empty") for p in points)}/{len(points)} 日为部分覆盖或缺失')
        columns = [item for panel in panels for item in panel]
        rows = "".join(f'<tr data-source-date="{p["date"]}"><td>{p["date"]}</td><td>{escape(p["status"])}</td><td>{p["sample"] if p["sample"] is not None else "—"}/{p["total"] if p["total"] is not None else "—"}</td>' + "".join(f'<td>{_fmt(p["values"].get(k))}</td>' for k, _ in columns) + (f'<td>{p.get("suspended", "—")}</td><td>{p.get("missing", "—")}</td><td>{escape("；".join(p.get("notes", [])) or ("当日情绪报告失败，无法确定核心名单" if p["status"] == "source_failed" else "—"))}</td>' if kind == 'core' else '') + '</tr>' for p in points)
        compact = kind == "core"
        core_max = max(4, math.ceil(max((v for p in points for v in p["values"].values()), default=0)/4)*4) if compact else None
        charts = "".join('<div class="feedback-panel">' + _panel(points, panel, unit, title + " / " + "、".join(label for _, label in panel), compact=compact, axis_max=core_max) + '</div>' for panel in panels)
        grid_class = " feedback-small-multiples" if compact else ""
        sample_note = ""
        if compact:
            samples = [p["sample"] for p in points if p["sample"] is not None]
            sample_note = f'<p class="feedback-sample-note">四图纵轴一致；有效样本每日 {_fmt(min(samples))}～{_fmt(max(samples))} 只，逐日数量与覆盖见下方明细。</p>' if samples else '<p>有效样本数未计算。</p>'
        suspension_note = ""
        if compact:
            latest = points[-1]
            if latest.get("suspended"):
                suspension_note = '<p class="feedback-sample-note">[事实] ' + escape(f"{latest['date']}：活跃 {latest['total']} 只，有效行情 {latest['sample']} 只，全天停牌 {latest['suspended']} 只。") + '完整表示应有数据齐全；停牌不计入行情样本，名称与证据见下方明细。</p>'
        fragments.append(f'''<figure class="feedback-trend" {marker}="v1" data-as-of="{report_date}" data-source-status="{status}">
<figcaption><strong>{title}</strong><span>{points[0]["date"]} 至 {points[-1]["date"]} · {valid}/{len(points)} 日有值 · {status}</span></figcaption>
<p>[事实] {definition}</p>
<p class="feedback-line-legend"><span class="feedback-solid-key">实线＋实心点：完整</span><span class="feedback-dashed-key">虚线＋空心点：部分覆盖的观测值</span><span>缺值才断线，不补零</span></p>
<div class="feedback-plots{grid_class}">{charts}</div>{sample_note}{suspension_note}
<details class="evidence" data-as-of="{report_date}" data-items="{len(points)}" data-evidence-kind="{kind}-feedback-trend"><summary>查看逐日数值与覆盖（{len(points)} 项）</summary><div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr><th>交易日</th><th>状态</th><th>样本/分母</th>{''.join(f'<th>{label}（{unit}）</th>' for _, label in columns)}{"<th>停牌</th><th>指标缺失</th><th>核验证据</th>" if compact else ""}</tr></thead><tbody>{rows}</tbody></table></div></div></details></figure>''')
    return "\n".join(fragments), gaps
