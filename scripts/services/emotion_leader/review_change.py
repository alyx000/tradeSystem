"""复盘 HTML 的情绪变化五段卡：只读同日/T-1 日报与盘后归档。"""
from __future__ import annotations

import json
import math
from datetime import date, timedelta
from html import escape
from pathlib import Path
from statistics import mean, median
from typing import Mapping

import yaml

from services.emotion_leader import tracking


STAGE_LABELS = (
    "退潮加剧",
    "冰点",
    "冰点后首次修复",
    "修复延续",
    "高低切分化",
    "情绪加速",
    "数据不足",
)
SIGNAL_LABELS = ("增强", "中性", "减弱", "未计算")
NEGATIVE_FEEDBACK = {"负反馈", "跌停负反馈"}
POSITIVE_FEEDBACK = {"弱正反馈", "强正反馈"}


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: object) -> int | None:
    number = _number(value)
    if number is None or number < 0 or not number.is_integer():
        return None
    return int(number)


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _strict_previous_open_day(
    payload: Mapping[str, object], report_date: str
) -> tuple[str | None, str | None]:
    rows = payload.get("tracking_calendar")
    if not isinstance(rows, list):
        return None, "缺少完整交易日历"
    calendar: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            return None, "交易日历存在非法行"
        day = str(row.get("date") or "")
        is_open = row.get("is_open")
        if day in calendar or is_open not in (0, 1):
            return None, "交易日历日期重复或状态非法"
        try:
            date.fromisoformat(day)
        except ValueError:
            return None, "交易日历日期非法"
        calendar[day] = int(is_open)
    if calendar.get(report_date) != 1:
        return None, "报告日未被完整日历确认为开放日"
    prior = [day for day, is_open in calendar.items() if day < report_date and is_open]
    if not prior:
        return None, "无严格上一开放日"
    previous = max(prior)
    cursor = date.fromisoformat(previous)
    end = date.fromisoformat(report_date)
    while cursor <= end:
        if cursor.isoformat() not in calendar:
            return None, "上一开放日至报告日之间存在日历缺口"
        cursor += timedelta(days=1)
    return previous, None


def _feedback_label(item: Mapping[str, object]) -> str:
    explicit = str(item.get("feedback_type") or "")
    if explicit in {
        "跌停负反馈",
        "负反馈",
        "平盘",
        "强正反馈",
        "弱正反馈",
        "未计算",
        "停牌",
    }:
        return explicit
    state = str(item.get("current_state") or "")
    pct = _number(item.get("today_pct_chg"))
    if state == "跌停":
        return "跌停负反馈"
    if pct is None:
        return "未计算"
    if pct < 0:
        return "负反馈"
    if pct == 0:
        return "平盘"
    if state == "涨停" or pct >= 5:
        return "强正反馈"
    return "弱正反馈"


def _top_core_rows(payload: Mapping[str, object]) -> list[dict[str, object]] | None:
    try:
        view = tracking.tracking_view(dict(payload))
    except (KeyError, TypeError, ValueError):
        return None
    rows = view.get("current")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return None
    return rows[:12]


def _current_lookup(payload: Mapping[str, object]) -> dict[str, dict[str, object]] | None:
    rows: list[object] = []
    for key in ("active", "archived"):
        value = payload.get(key)
        if not isinstance(value, list):
            return None
        rows.extend(value)
    result: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        code = str(row.get("code") or "").upper()
        if not code or code in result:
            return None
        result[code] = row
    return result


def _core_transitions(
    previous: Mapping[str, object],
    current: Mapping[str, object],
    report_date: str,
) -> dict[str, object]:
    top = _top_core_rows(previous)
    lookup = _current_lookup(current)
    if top is None or lookup is None:
        return {"status": "source_failed", "rows": [], "missing": []}
    rows: list[dict[str, object]] = []
    missing: list[str] = []
    for prior in top:
        code = str(prior.get("code") or "").upper()
        name = str(prior.get("name") or code)
        current_row = lookup.get(code)
        prior_pct = _number(prior.get("today_pct_chg"))
        prior_label = _feedback_label(prior)
        current_pct = _number(current_row.get("today_pct_chg")) if current_row else None
        current_label = _feedback_label(current_row) if current_row else "未计算"
        metric_as_of = str(current_row.get("metric_as_of") or "") if current_row else ""
        metric_status = str(current_row.get("metric_status") or "") if current_row else ""
        if (
            prior_pct is None
            or prior_label in {"未计算", "停牌"}
            or current_pct is None
            or current_label in {"未计算", "停牌"}
            or metric_status != "ok"
            or (metric_as_of and metric_as_of != report_date)
        ):
            missing.append(f"{name} {code}")
            continue
        transition = "other"
        if prior_label in NEGATIVE_FEEDBACK and current_label in POSITIVE_FEEDBACK:
            transition = "repaired"
        elif prior_label in NEGATIVE_FEEDBACK and current_label in NEGATIVE_FEEDBACK:
            transition = "continued_negative"
        elif prior_label in POSITIVE_FEEDBACK and current_label in NEGATIVE_FEEDBACK:
            transition = "positive_to_negative"
        rows.append(
            {
                "code": code,
                "name": name,
                "previous_pct": prior_pct,
                "previous_label": prior_label,
                "current_pct": current_pct,
                "current_label": current_label,
                "transition": transition,
            }
        )
    return {
        "status": "complete" if not missing else "partial",
        "rows": rows,
        "missing": missing,
        "base_count": len(top),
    }


def _normalize_promotion(raw: object, day: str) -> dict[str, object] | None:
    if not isinstance(raw, Mapping) or str(raw.get("trade_date") or "") != day:
        return None
    result: dict[str, object] = {"prev_date": str(raw.get("prev_date") or "")}
    for key in ("first_to_second", "second_to_third"):
        item = raw.get(key)
        if not isinstance(item, Mapping):
            return None
        base = _count(item.get("base"))
        promoted = _count(item.get("promoted"))
        rate = _number(item.get("rate"))
        if (
            base is None
            or promoted is None
            or promoted > base
            or rate is None
            or not math.isclose(rate, round(promoted / base, 3) if base else 0.0, abs_tol=0.0005)
        ):
            return None
        result[key] = {"base": base, "promoted": promoted, "rate": rate}
    return result


def _load_market_snapshot(path: Path, day: str) -> dict[str, object] | None:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(payload, Mapping) or str(payload.get("date") or "") != day:
        return None
    raw = payload.get("raw_data")
    if not isinstance(raw, Mapping):
        return None
    limit_up = raw.get("limit_up")
    limit_down = raw.get("limit_down")
    style = raw.get("style_factors")
    if not isinstance(limit_up, Mapping) or not isinstance(limit_down, Mapping) or not isinstance(style, Mapping):
        return None
    values = {
        "limit_up": _count(limit_up.get("count_ex_st")),
        "limit_down": _count(limit_down.get("count_ex_st")),
        "continuation": _count(limit_up.get("consecutive_board_count_ex_st")),
        "highest": _count(limit_up.get("highest_board_ex_st")),
        "broken_rate": _number(limit_up.get("broken_rate_pct_ex_st")),
    }
    if any(value is None for value in values.values()):
        return None
    values["first"] = int(values["limit_up"]) - int(values["continuation"])
    if int(values["first"]) < 0:
        return None
    ladder = limit_up.get("board_ladder_ex_st")
    if not isinstance(ladder, Mapping):
        return None
    mid_count = 0
    for height in (2, 3, 4):
        names = ladder.get(height, ladder.get(str(height), []))
        if not isinstance(names, list):
            return None
        mid_count += len(names)
    promotion = _normalize_promotion(style.get("promotion"), day)
    if promotion is None:
        return None
    return {"date": day, **values, "mid_count": mid_count, "promotion": promotion}


def _valid_height_event(raw: object, report_date: str) -> Mapping[str, object] | None:
    if not isinstance(raw, Mapping):
        return None
    status = str(raw.get("status") or "")
    lookback = _count(raw.get("lookback_open_days"))
    current = _count(raw.get("current_max_height"))
    previous = _count(raw.get("previous_max_height"))
    if (
        raw.get("source_status") != "complete"
        or status not in {"triggered", "none"}
        or str(raw.get("as_of") or "") != report_date
        or lookback != 20
        or current is None
        or previous is None
    ):
        return None
    if status == "triggered" and (current < 2 or current <= previous):
        return None
    if status == "none" and current >= 2 and current > previous:
        return None
    return raw


def _load_board_feedback(path: Path, report_date: str) -> dict[str, object]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {"status": "source_failed", "rows": [], "errors": ["断板反馈归档不可读"]}
    raw = payload.get("raw_data") if isinstance(payload, Mapping) else None
    block = raw.get("board_break_feedback") if isinstance(raw, Mapping) else None
    if not isinstance(block, Mapping) or str(block.get("outcome_date") or "") != report_date:
        return {"status": "source_failed", "rows": [], "errors": ["断板反馈日期或结构无效"]}
    status = str(block.get("status") or "source_failed")
    if status == "empty":
        if _count(block.get("sample_count")) == 0:
            return {"status": "empty", "rows": [], "sample_count": 0, "errors": []}
        status = "source_failed"
    if status not in {"ok", "partial"}:
        return {"status": "source_failed", "rows": [], "errors": ["断板反馈来源失败"]}
    details = block.get("details")
    sample_count = _count(block.get("sample_count"))
    if not isinstance(details, list) or sample_count is None or sample_count <= 0 or len(details) != sample_count:
        return {"status": "source_failed", "rows": [], "errors": ["断板反馈样本无法对账"]}
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in details:
        if not isinstance(item, Mapping):
            return {"status": "source_failed", "rows": [], "errors": ["断板反馈明细非法"]}
        code = str(item.get("code") or "").split(".")[0]
        name = str(item.get("name") or "").strip()
        open_pct = _number(item.get("feedback_open_pct"))
        close_pct = _number(item.get("feedback_close_pct"))
        if len(code) != 6 or not code.isdigit() or code in seen or not name or open_pct is None or close_pct is None:
            return {"status": "source_failed", "rows": [], "errors": ["断板反馈逐股身份或涨幅非法"]}
        seen.add(code)
        rows.append(
            {
                "code": code,
                "name": name,
                "open_pct": open_pct,
                "close_pct": close_pct,
                "outcome": str(item.get("outcome") or "未计算"),
            }
        )
    opens = [float(row["open_pct"]) for row in rows]
    closes = [float(row["close_pct"]) for row in rows]
    return {
        "status": status,
        "rows": rows,
        "sample_count": sample_count,
        "open_up_rate": sum(value > 0 for value in opens) / sample_count,
        "close_up_rate": sum(value > 0 for value in closes) / sample_count,
        "open_median": median(opens),
        "close_median": median(closes),
        "open_mean": mean(opens),
        "close_mean": mean(closes),
        "relimit_count": _count(block.get("relimit_count")),
        "limit_down_count": _count(block.get("limit_down_count")),
        "errors": [str(item) for item in block.get("errors", []) if str(item)],
    }


def _stage_and_signals(
    current: Mapping[str, object],
    previous: Mapping[str, object],
    transitions: Mapping[str, object],
    board: Mapping[str, object],
    height_event: object,
) -> tuple[str, str, str]:
    rows = transitions.get("rows")
    if not isinstance(rows, list) or transitions.get("status") == "source_failed":
        return "数据不足", "未计算", "未计算"
    board_status = str(board.get("status") or "source_failed")
    if board_status not in {"ok", "partial", "empty"}:
        return "数据不足", "未计算", "未计算"
    repaired = sum(row.get("transition") == "repaired" for row in rows if isinstance(row, Mapping))
    negative = sum(
        row.get("transition") in {"continued_negative", "positive_to_negative"}
        for row in rows
        if isinstance(row, Mapping)
    )
    stop_score = 0
    stop_score += 1 if int(current["limit_down"]) < int(previous["limit_down"]) else -1 if int(current["limit_down"]) > int(previous["limit_down"]) else 0
    stop_score += 1 if repaired > 0 else -1 if negative > repaired else 0
    if board_status != "empty":
        close_mean = float(board["close_mean"])
        open_mean = float(board["open_mean"])
        close_up_rate = float(board["close_up_rate"])
        stop_score += 1 if close_mean > open_mean and close_up_rate >= 0.5 else -1 if close_mean < 0 and close_up_rate < 0.5 else 0
    stop_signal = "增强" if stop_score >= 2 else "减弱" if stop_score <= -2 else "中性"

    height_complete = isinstance(height_event, Mapping)
    height_triggered = height_complete and height_event.get("status") == "triggered"
    current_p = current["promotion"]
    previous_p = previous["promotion"]
    quality_improved = (
        float(current_p["second_to_third"]["rate"])
        >= float(previous_p["second_to_third"]["rate"])
        and float(current["broken_rate"]) < float(previous["broken_rate"])
    )
    mid_expanded = int(current["mid_count"]) > int(previous["mid_count"])
    if not height_complete:
        new_cycle_signal = "未计算"
    elif height_triggered and mid_expanded and quality_improved:
        new_cycle_signal = "增强"
    else:
        weak_flags = (
            int(current["highest"]) < int(previous["highest"]),
            int(current["mid_count"]) < int(previous["mid_count"]),
            float(current["broken_rate"]) > float(previous["broken_rate"]),
            float(current_p["second_to_third"]["rate"])
            < float(previous_p["second_to_third"]["rate"]),
        )
        new_cycle_signal = "减弱" if sum(weak_flags) >= 2 else "中性"

    stressed_previous = (
        int(previous["limit_down"]) >= max(int(previous["limit_up"]), 20)
        or float(previous["broken_rate"]) >= 35
    )
    if stop_signal == "减弱":
        stage = "退潮加剧"
    elif (
        int(current["limit_down"]) >= max(int(current["limit_up"]), 20)
        and float(current["broken_rate"]) >= 35
    ):
        stage = "冰点"
    elif stop_signal == "增强" and stressed_previous:
        stage = "冰点后首次修复"
    elif stop_signal == "增强" and new_cycle_signal == "增强":
        stage = "情绪加速"
    elif stop_signal == "增强":
        stage = "修复延续"
    else:
        stage = "高低切分化"
    return stage, stop_signal, new_cycle_signal


def _observed_count(rows: list, source_status: object) -> str:
    if source_status in {"complete", "ok", "empty"}:
        return f"{len(rows)}只"
    if source_status == "partial" and rows:
        return f"已核验{len(rows)}只（部分覆盖）"
    return "未计算（部分覆盖）" if source_status == "partial" else "未计算"


def _empty_observation(source_status: object) -> str:
    if source_status in {"complete", "ok", "empty"}:
        return "正常计算后为0"
    return "未计算（部分覆盖）" if source_status == "partial" else "未计算"


def build(
    current_report: Mapping[str, object] | None,
    report_date: str,
    *,
    emotion_dir: str | Path | None,
    daily_dir: str | Path | None,
) -> dict[str, object]:
    """构建五段卡数据；不补采、不写盘。"""

    # 断板反馈有独立同日来源，不因生命周期或基线缺失而丢弃有效事实。
    board = (
        _load_board_feedback(Path(daily_dir) / report_date / "post-market.yaml", report_date)
        if daily_dir is not None
        else {"status": "source_failed", "rows": [], "errors": []}
    )

    def missing(reason: str, *, previous_date: str = "", transitions=None) -> dict[str, object]:
        return {
            "date": report_date,
            "previous_date": previous_date,
            "status": "source_failed",
            "stage": "数据不足",
            "stop_signal": "未计算",
            "new_cycle_signal": "未计算",
            "facts": [],
            "transitions": transitions or {"status": "source_failed", "rows": [], "missing": []},
            "board": board,
            "errors": [reason],
        }

    if not isinstance(current_report, Mapping) or current_report.get("date") != report_date:
        return missing("同日情绪核心日报不存在或错日")
    if current_report.get("status") not in {"ok", "partial"}:
        return missing("同日情绪核心日报状态无效或 source_failed")
    if not isinstance(current_report.get("source_errors"), list):
        return missing("同日情绪核心日报 source_errors 结构无效")
    previous_date, calendar_error = _strict_previous_open_day(current_report, report_date)
    if previous_date is None:
        return missing(calendar_error or "严格上一开放日未计算")
    if emotion_dir is None or daily_dir is None:
        return missing("未提供情绪日报或盘后归档目录", previous_date=previous_date)
    previous_report = _read_json(Path(emotion_dir) / f"{previous_date}.json")
    if not previous_report or previous_report.get("date") != previous_date:
        return missing("无可比基线", previous_date=previous_date)
    if previous_report.get("status") not in {"ok", "partial"}:
        return missing("严格上一开放日情绪日报状态无效或 source_failed", previous_date=previous_date)

    transitions = _core_transitions(previous_report, current_report, report_date)
    current_market = _load_market_snapshot(Path(daily_dir) / report_date / "post-market.yaml", report_date)
    previous_market = _load_market_snapshot(Path(daily_dir) / previous_date / "post-market.yaml", previous_date)
    if current_market is None or previous_market is None:
        return missing("T/T-1 盘后市场事实缺失或无法对账", previous_date=previous_date, transitions=transitions)
    if current_market["promotion"].get("prev_date") != previous_date:
        return missing("当日晋级事实未绑定严格 T-1", previous_date=previous_date)
    height_event = _valid_height_event(current_report.get("height_breakthrough"), report_date)
    stage, stop_signal, new_cycle_signal = _stage_and_signals(
        current_market,
        previous_market,
        transitions,
        board,
        height_event,
    )

    transition_rows = transitions.get("rows") if isinstance(transitions.get("rows"), list) else []
    repaired = [row for row in transition_rows if row.get("transition") == "repaired"]
    continued = [row for row in transition_rows if row.get("transition") == "continued_negative"]
    reversed_rows = [row for row in transition_rows if row.get("transition") == "positive_to_negative"]
    board_rows = board.get("rows") if isinstance(board.get("rows"), list) else []
    weak_to_strong = [row for row in board_rows if row.get("open_pct", 0) < 0 < row.get("close_pct", 0)]
    still_weak = [row for row in board_rows if row.get("close_pct", 0) < 0]
    current_p = current_market["promotion"]
    previous_p = previous_market["promotion"]
    height = current_report.get("height_breakthrough")
    if height_event is not None:
        height_fact = (
            f"近{height_event['lookback_open_days']}个开放日高度比较="
            f"{'已突破' if height_event.get('status') == 'triggered' else '未突破'}，"
            f"当前{height_event['current_max_height']}板、此前"
            f"{height_event['previous_max_height']}板"
        )
    else:
        height_reason = (
            str(height.get("reason") or "高度比较来源不完整")
            if isinstance(height, Mapping)
            else "高度比较未计算"
        )
        height_fact = f"近20个开放日高度比较=未计算（{height_reason}）"

    board_fact = "断板反馈=未计算"
    if board.get("status") in {"ok", "partial"}:
        relimit = _count(board.get("relimit_count"))
        limit_down = _count(board.get("limit_down_count"))
        board_fact = (
            f"断板样本{_count(board.get('sample_count')) or 0}只，开盘/收盘上涨率"
            f"{float(board['open_up_rate']):.1%}/{float(board['close_up_rate']):.1%}，"
            f"涨幅中位数{float(board['open_median']):+.2f}%/{float(board['close_median']):+.2f}%，"
            f"再次涨停{'未计算' if relimit is None else relimit}只、"
            f"跌停{'未计算' if limit_down is None else limit_down}只"
        )
    elif board.get("status") == "empty":
        board_fact = "断板反馈样本正常计算后为0"
    facts = [
        f"涨停{previous_market['limit_up']}→{current_market['limit_up']}，跌停{previous_market['limit_down']}→{current_market['limit_down']}，炸板率{previous_market['broken_rate']:.1f}%→{current_market['broken_rate']:.1f}%",
        f"首板{previous_market['first']}→{current_market['first']}，二板及以上{previous_market['continuation']}→{current_market['continuation']}，最高板{previous_market['highest']}→{current_market['highest']}",
        f"首板→二板{previous_p['first_to_second']['rate']:.1%}→{current_p['first_to_second']['rate']:.1%}，二板→三板{previous_p['second_to_third']['rate']:.1%}→{current_p['second_to_third']['rate']:.1%}",
        f"旧核心修复{_observed_count(repaired, transitions.get('status'))}、继续负反馈{_observed_count(continued, transitions.get('status'))}、由正转负{_observed_count(reversed_rows, transitions.get('status'))}；断板低开转收涨{_observed_count(weak_to_strong, board.get('status'))}；{board_fact}",
    ]
    errors = []
    if transitions.get("status") == "source_failed":
        errors.append("T-1前12只核心反馈来源失败，未计算")
    elif transitions.get("status") != "complete":
        errors.append(f"T-1前12只核心有{len(transitions.get('missing') or [])}只反馈未计算")
    if board.get("status") == "partial":
        errors.append("断板反馈为 partial")
    elif board.get("status") == "source_failed":
        errors.append("断板反馈 source_failed")
    if current_report.get("status") == "partial" or previous_report.get("status") == "partial":
        errors.append("T/T-1 情绪核心日报至少一日为 partial")
    if height_event is None:
        errors.append("20开放日高度比较未计算")
    source_status = "complete" if not errors else "partial"
    return {
        "date": report_date,
        "previous_date": previous_date,
        "status": source_status,
        "stage": stage,
        "stop_signal": stop_signal,
        "new_cycle_signal": new_cycle_signal,
        "height_fact": height_fact,
        "facts": facts,
        "current_market": current_market,
        "previous_market": previous_market,
        "transitions": transitions,
        "board": {**board, "weak_to_strong": weak_to_strong, "still_weak": still_weak},
        "errors": errors,
        "current_report_status": str(current_report.get("status") or "source_failed"),
        "previous_report_status": str(previous_report.get("status") or "source_failed"),
        "source_error_count": len(current_report.get("source_errors") or []),
    }


def _pct(value: object) -> str:
    number = _number(value)
    return "未计算" if number is None else f"{number:+.2f}%"


def _signal_class(label: str) -> str:
    return {"增强": "positive", "减弱": "negative", "中性": "neutral"}.get(label, "missing")


def render_html(payload: Mapping[str, object] | None, report_date: str) -> tuple[str, str | None]:
    """返回固定五段 HTML 与可选 ops 缺口。"""

    if not isinstance(payload, Mapping) or payload.get("date") != report_date:
        payload = {
            "date": report_date,
            "previous_date": "",
            "status": "source_failed",
            "stage": "数据不足",
            "stop_signal": "未计算",
            "new_cycle_signal": "未计算",
            "facts": [],
            "transitions": {"rows": [], "missing": []},
            "board": {"status": "source_failed", "rows": []},
            "errors": ["情绪变化模块未计算"],
        }
    status = str(payload.get("status") or "source_failed")
    previous_date = str(payload.get("previous_date") or "")
    stage = str(payload.get("stage") or "数据不足")
    stop_signal = str(payload.get("stop_signal") or "未计算")
    cycle_signal = str(payload.get("new_cycle_signal") or "未计算")
    height_fact = str(payload.get("height_fact") or "近20个开放日高度比较=未计算")
    if stage not in STAGE_LABELS:
        stage = "数据不足"
    if stop_signal not in SIGNAL_LABELS:
        stop_signal = "未计算"
    if cycle_signal not in SIGNAL_LABELS:
        cycle_signal = "未计算"
    transitions = payload.get("transitions") if isinstance(payload.get("transitions"), Mapping) else {}
    transition_rows = transitions.get("rows") if isinstance(transitions.get("rows"), list) else []
    board = payload.get("board") if isinstance(payload.get("board"), Mapping) else {}
    board_rows = board.get("rows") if isinstance(board.get("rows"), list) else []
    repaired = [row for row in transition_rows if isinstance(row, Mapping) and row.get("transition") == "repaired"]
    continued = [row for row in transition_rows if isinstance(row, Mapping) and row.get("transition") == "continued_negative"]
    reversed_rows = [row for row in transition_rows if isinstance(row, Mapping) and row.get("transition") == "positive_to_negative"]
    weak_to_strong = [row for row in board_rows if isinstance(row, Mapping) and _number(row.get("open_pct")) is not None and _number(row.get("close_pct")) is not None and float(row["open_pct"]) < 0 < float(row["close_pct"])]
    still_weak = [row for row in board_rows if isinstance(row, Mapping) and _number(row.get("close_pct")) is not None and float(row["close_pct"]) < 0]
    errors = [str(item) for item in payload.get("errors", []) if str(item)] if isinstance(payload.get("errors"), list) else []
    facts = [str(item) for item in payload.get("facts", []) if str(item)] if isinstance(payload.get("facts"), list) else []

    transition_names = lambda rows: "、".join(escape(str(row.get("name") or row.get("code") or "")) for row in rows[:5]) or _empty_observation(transitions.get("status"))
    board_names = lambda rows: "、".join(escape(str(row.get("name") or row.get("code") or "")) for row in rows[:5]) or _empty_observation(board.get("status"))
    fact_html = "".join(f"<li>[事实] {escape(item)}</li>" for item in facts[:4])
    if not fact_html:
        fact_html = "<li>[事实] T/T-1 可比事实未计算。</li>"
    transition_table_rows = "".join(
        f'<tr data-code="{escape(str(row.get("code") or ""))}" data-transition-kind="{escape(str(row.get("transition") or "other"))}">'
        f'<td>{escape(str(row.get("name") or "—"))}<br><small>{escape(str(row.get("code") or ""))}</small></td>'
        f'<td>{_pct(row.get("previous_pct"))} / {escape(str(row.get("previous_label") or "未计算"))}</td>'
        f'<td>{_pct(row.get("current_pct"))} / {escape(str(row.get("current_label") or "未计算"))}</td>'
        f'<td>{escape({"repaired": "负反馈修复", "continued_negative": "继续负反馈", "positive_to_negative": "由正转负"}.get(str(row.get("transition")), "其他"))}</td></tr>'
        for row in [*repaired, *continued, *reversed_rows]
    )
    board_table_rows = "".join(
        f'<tr data-code="{escape(str(row.get("code") or ""))}" data-board-transition="{"weak-to-strong" if row in weak_to_strong else "still-weak"}">'
        f'<td>{escape(str(row.get("name") or "—"))}<br><small>{escape(str(row.get("code") or ""))}</small></td>'
        f'<td>{_pct(row.get("open_pct"))}</td><td>{_pct(row.get("close_pct"))}</td><td>{escape(str(row.get("outcome") or "未计算"))}</td></tr>'
        for row in [*weak_to_strong, *still_weak]
    )
    sample_count = _count(board.get("sample_count"))
    sample_note = (
        "[判断] 断板样本少于5只，仅作当日观察。"
        if sample_count is not None and sample_count < 5
        else "[判断] 断板反馈仅代表当日固定样本，不外推为稳定胜率。"
    )
    transition_details = (
        f'<details class="evidence" data-as-of="{escape(report_date)}" data-items="{len(repaired) + len(continued) + len(reversed_rows)}" data-evidence-kind="emotion-change-core"><summary>辨识度个股负反馈修复明细（{len(repaired) + len(continued) + len(reversed_rows)}只）</summary><div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr><th>股票</th><th>T-1反馈</th><th>T反馈</th><th>变化</th></tr></thead><tbody>{transition_table_rows}</tbody></table></div></div></details>'
        if transition_table_rows
        else '<p class="note">[事实] 辨识度个股负反馈修复：' + (_empty_observation(transitions.get("status")) + "。") + "</p>"
    )
    board_details = (
        f'<details class="evidence" data-as-of="{escape(report_date)}" data-items="{len(weak_to_strong) + len(still_weak)}" data-evidence-kind="emotion-change-board"><summary>断板股盘中弱转强代表（{len(weak_to_strong) + len(still_weak)}只）</summary><div class="evidence-body"><div class="table-scroll-shell"><table><thead><tr><th>股票</th><th>开盘</th><th>收盘</th><th>结果</th></tr></thead><tbody>{board_table_rows}</tbody></table></div><p>{sample_note}</p></div></details>'
        if board_table_rows
        else '<p class="note">[事实] 断板股盘中弱转强：' + (_empty_observation(board.get("status")) + "。") + "</p>"
    )
    value = "v1" if status in {"complete", "partial"} and stage != "数据不足" else "missing-data"
    normalized_status = status if value == "v1" else "source_failed"
    error_text = "；".join(errors[:4]) or "无新增缺口"
    current_status = escape(str(payload.get("current_report_status") or "未计算"))
    previous_status = escape(str(payload.get("previous_report_status") or "未计算"))
    source_error_count = _count(payload.get("source_error_count"))
    source_error_attr = "unavailable" if source_error_count is None else str(source_error_count)
    fragment = f'''<div class="emotion-change" data-emotion-change="{value}" data-as-of="{escape(report_date)}"
    data-prev-date="{escape(previous_date)}" data-source-status="{escape(normalized_status)}"
    data-stage-label="{escape(stage)}" data-stop-signal="{escape(stop_signal)}"
    data-new-cycle-signal="{escape(cycle_signal)}" data-repair-count="{len(repaired) if transitions.get("status") in {"complete", "partial"} else "unavailable"}"
    data-continued-negative-count="{len(continued) if transitions.get("status") in {"complete", "partial"} else "unavailable"}" data-positive-to-negative-count="{len(reversed_rows) if transitions.get("status") in {"complete", "partial"} else "unavailable"}"
    data-board-sample-count="{"unavailable" if sample_count is None else sample_count}"
    data-source-error-count="{source_error_attr}">
  <h3>今日相对 T-1 的情绪变化</h3>
  <div class="template-grid emotion-change-grid">
    <div class="template-card"><h4>阶段结论</h4><p><strong>[判断] {escape(stage)}</strong></p><ul>{fact_html}</ul></div>
    <div class="template-card"><h4>止跌信号</h4><p data-signal-class="{_signal_class(stop_signal)}">[判断] {escape(stop_signal)}</p><p>[事实] 旧核心修复{_observed_count(repaired, transitions.get("status"))}；断板低开转收涨{_observed_count(weak_to_strong, board.get("status"))}。</p></div>
    <div class="template-card"><h4>新周期确认</h4><p data-signal-class="{_signal_class(cycle_signal)}">[判断] {escape(cycle_signal)}</p><p>[事实] {escape(height_fact)}；中间梯队、晋级率与封板质量按同日归档联合核验。</p></div>
    <div class="template-card"><h4>代表个股</h4><p>[事实] 修复：{transition_names(repaired)}；继续负反馈：{transition_names(continued)}；由正转负：{transition_names(reversed_rows)}。</p><p>[事实] 断板低开转收涨：{board_names(weak_to_strong)}；继续走弱：{board_names(still_weak)}。</p></div>
    <div class="template-card"><h4>数据状态</h4><p>[事实] T={current_status}；T-1（{escape(previous_date or '未计算')}）={previous_status}；断板反馈={escape(str(board.get('status') or 'source_failed'))}；source_errors={source_error_attr}。</p><p class="note">[事实] {escape(error_text)}；缺失不补0。</p></div>
  </div>
  {transition_details}
  {board_details}
</div>'''
    gap = None if normalized_status == "complete" else "情绪变化五段卡：" + error_text
    return fragment, gap
