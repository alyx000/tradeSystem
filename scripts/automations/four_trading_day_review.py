from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
import json
import math
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_TZ = ZoneInfo("Asia/Shanghai")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _run_json(cmd: list[str]) -> Any:
    p = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\nstderr: {p.stderr.strip()}")
    out = p.stdout.strip()
    return json.loads(out) if out else None


def _format_money(x: float | None) -> str:
    return f"{x:,.2f}" if x is not None else "-"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def _to_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        value = float(x)
    except Exception:
        return None
    return value if math.isfinite(value) else None


def _row_cash_components(r: dict) -> tuple[str, float, float, float | None, float, float]:
    direction = (r.get("direction") or "").lower()
    amount = _to_float(r.get("amount")) or 0.0
    fees = _to_float(r.get("total_fees"))
    if fees is None:
        fees = sum(
            (_to_float(r.get(k)) or 0.0)
            for k in [
                "commission",
                "stamp_duty",
                "transfer_fee",
                "exchange_fee",
                "regulatory_fee",
                "other_fees",
            ]
        )
    net_amount = _to_float(r.get("net_amount"))
    if direction == "buy":
        buy_cost = abs(net_amount) if net_amount is not None else amount + fees
        sell_rev = 0.0
    elif direction == "sell":
        sell_rev = net_amount if net_amount is not None else max(amount - fees, 0.0)
        buy_cost = 0.0
    else:
        buy_cost = 0.0
        sell_rev = 0.0
    return direction, amount, fees, net_amount, buy_cost, sell_rev


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    out = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def _db_uri_readonly() -> str:
    return "file:" + str((PROJECT_ROOT / "data" / "trade.db").resolve()) + "?mode=ro"


def _try_get_last_n_trade_days(n: int, as_of: date) -> list[str] | None:
    try:
        conn = sqlite3.connect(_db_uri_readonly(), uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT date FROM trade_calendar WHERE date<=? AND is_open=1 ORDER BY date DESC LIMIT ?",
            (as_of.isoformat(), n),
        ).fetchall()
        conn.close()
        days = [r["date"] for r in rows]
        if len(days) < n:
            return None
        return days[::-1]
    except Exception:
        return None


def _load_thesis_reviews() -> dict[int, dict]:
    """只读读取 thesis_review 全表，返回 {thesis_id: row_dict}。

    与 _try_get_last_n_trade_days 同样的容错风格：fresh DB / 表不存在 /
    PROJECT_ROOT 指向无 DB 的临时目录时，整体兜底返回 {}，绝不抛。
    """
    conn = None
    try:
        conn = sqlite3.connect(_db_uri_readonly(), uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM thesis_review").fetchall()
        return {int(r["thesis_id"]): dict(r) for r in rows}
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()


def _normalize_stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.split(".", 1)[0] if text else ""


def _execution_semantic_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """跨导入批次识别同一券商成交，用于追踪该事实首次被系统知晓的时间。"""
    trade_no_text = str(row.get("broker_trade_no") or "").strip().upper()
    trade_no = trade_no_text.lstrip("0") or ("0" if trade_no_text else "")
    contract_no = str(row.get("broker_contract_no") or "").strip().upper()
    identity = (
        ("trade", trade_no)
        if trade_no
        else (("contract", contract_no) if contract_no else ("row", row.get("id")))
    )
    price = _to_float(row.get("price"))
    amount = _to_float(row.get("amount"))
    shares = _to_float(row.get("shares"))
    return (
        str(row.get("biz_date") or ""),
        str(row.get("exec_time") or ""),
        _normalize_stock_code(row.get("stock_code") or row.get("stock_code_raw")),
        str(row.get("direction") or "").lower(),
        int(round(shares * 1_000_000)) if shares is not None else None,
        int(round(price * 1_000_000)) if price is not None else None,
        int(round(amount * 100)) if amount is not None else None,
        identity,
    )


def _load_holding_snapshots(days: set[str]) -> dict[str, dict[str, Any]]:
    """只读加载指定交易日的盘后持仓快照，保留逐股行情失败成员。"""
    requested = sorted({str(day) for day in days if day})
    if not requested:
        return {}
    conn = None
    try:
        conn = sqlite3.connect(_db_uri_readonly(), uri=True)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in requested)
        rows = conn.execute(
            f"SELECT date, raw_data FROM daily_market WHERE date IN ({placeholders})",
            requested,
        ).fetchall()
        raw_execution_rows = conn.execute(
            """
            SELECT *
              FROM broker_executions
             WHERE account_id = 'default'
               AND biz_date <= ?
               AND imported_at IS NOT NULL
               AND (
                    COALESCE(is_void, 0) = 0
                    OR void_reason = 'semantic_duplicate_exact'
               )
             ORDER BY imported_at ASC, biz_date ASC
            """,
            (requested[-1],),
        ).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            conn.close()

    first_seen_by_key: dict[tuple[Any, ...], str] = {}
    for execution_row in raw_execution_rows:
        execution = dict(execution_row)
        imported_at = str(execution.get("imported_at") or "")
        if _parse_dt(imported_at) is None:
            continue
        key = _execution_semantic_key(execution)
        if key not in first_seen_by_key:
            first_seen_by_key[key] = imported_at
    active_execution_rows: list[dict[str, Any]] = []
    for execution_row in raw_execution_rows:
        execution = dict(execution_row)
        if int(execution.get("is_void") or 0) != 0:
            continue
        first_seen = first_seen_by_key.get(_execution_semantic_key(execution))
        if first_seen:
            execution["imported_at"] = first_seen
        active_execution_rows.append(execution)
    try:
        execution_rows = _collapse_split_plus_summary_rows(active_execution_rows)
    except RuntimeError:
        return {}

    snapshots: dict[str, dict[str, Any]] = {}
    for row in rows:
        trade_date = str(row["date"])
        try:
            envelope = json.loads(row["raw_data"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshots[trade_date] = {
                "status": "malformed",
                "reason": "raw_data_json_invalid",
                "members": {},
            }
            continue
        if not isinstance(envelope, dict):
            snapshots[trade_date] = {
                "status": "malformed",
                "reason": "raw_data_not_object",
                "members": {},
            }
            continue
        envelope_date = str(envelope.get("date") or "")
        if envelope_date and envelope_date != trade_date:
            snapshots[trade_date] = {
                "status": "malformed",
                "reason": f"snapshot_date_mismatch:{envelope_date}",
                "members": {},
            }
            continue
        generated_at = str(envelope.get("generated_at") or "").strip()
        try:
            generated_dt = datetime.fromisoformat(
                generated_at.replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            generated_dt = None
        if generated_dt is not None:
            if generated_dt.tzinfo is None:
                generated_local = generated_dt.replace(tzinfo=RUN_TZ)
            else:
                generated_local = generated_dt.astimezone(RUN_TZ)
        else:
            generated_local = None
        generated_date = (
            generated_local.date().isoformat() if generated_local is not None else None
        )
        generated_after_close = bool(
            generated_local is not None
            and (generated_local.hour, generated_local.minute) >= (15, 0)
        )
        if generated_date != trade_date or not generated_after_close:
            if generated_date is None:
                reason = "generated_at_missing_or_invalid"
            elif generated_date != trade_date:
                reason = f"generated_date_mismatch:{generated_date}"
            else:
                reason = f"generated_before_market_close:{generated_local:%H:%M:%S}"
            snapshots[trade_date] = {
                "status": "malformed",
                "reason": reason,
                "members": {},
            }
            continue
        late_execution = None
        for execution_row in execution_rows:
            biz_date = str(execution_row["biz_date"] or "")
            if not biz_date or biz_date > trade_date:
                continue
            imported_at = _parse_dt(str(execution_row["imported_at"] or ""))
            if imported_at is None:
                continue
            imported_local = imported_at.replace(tzinfo=timezone.utc).astimezone(
                RUN_TZ
            )
            if imported_local > generated_local:
                late_execution = (biz_date, imported_local)
                break
        if late_execution is not None:
            late_biz_date, late_imported_at = late_execution
            snapshots[trade_date] = {
                "status": "stale",
                "reason": (
                    f"late_execution_import:{late_biz_date}:"
                    f"{late_imported_at.isoformat(timespec='seconds')}"
                ),
                "members": {},
            }
            continue
        raw_members = envelope.get("holdings_data")
        if not isinstance(raw_members, list):
            snapshots[trade_date] = {
                "status": "missing",
                "reason": "holdings_data_missing",
                "members": {},
            }
            continue

        members: dict[str, dict[str, Any]] = {}
        duplicate_codes: set[str] = set()
        invalid_members = 0
        for item in raw_members:
            if not isinstance(item, dict):
                invalid_members += 1
                continue
            code = _normalize_stock_code(item.get("code") or item.get("stock_code"))
            if not code:
                invalid_members += 1
                continue
            if code in members:
                duplicate_codes.add(code)
                continue
            error = str(item.get("error") or "")
            shares = _to_float(item.get("shares"))
            if shares is not None and shares <= 0:
                invalid_members += 1
            elif shares is None and not error:
                invalid_members += 1
            members[code] = {
                "stock_code": code,
                "stock_name": str(item.get("name") or item.get("stock_name") or ""),
                "shares": shares,
                "change_pct": (
                    None if error else _to_float(item.get("change_pct"))
                ),
                "error": error,
            }
        reasons: list[str] = []
        if duplicate_codes:
            reasons.append("duplicate_codes:" + ",".join(sorted(duplicate_codes)))
        if invalid_members:
            reasons.append(f"invalid_members:{invalid_members}")
        snapshots[trade_date] = {
            "status": "partial" if reasons else "success",
            "reason": ";".join(reasons) or None,
            "members": members,
        }
    return snapshots


def _trade_day_index(days: list[str]) -> dict[str, int]:
    return {d: i for i, d in enumerate(days)}


def _group_trade_actions(day_rows: list[dict]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for r in day_rows:
        direction = (r.get("direction") or "").lower()
        stock = r.get("stock_code") or r.get("stock_code_raw") or ""
        if not stock or direction not in ("buy", "sell"):
            continue
        key = (direction, stock)
        if key not in grouped:
            grouped[key] = {
                "account_id": r.get("account_id"),
                "biz_date": r.get("biz_date"),
                "exec_time": r.get("exec_time"),
                "id": r.get("id") or 0,
                "stock_code": stock,
                "stock_code_raw": r.get("stock_code_raw"),
                "stock_name": r.get("stock_name"),
                "direction": direction,
                "direction_raw": r.get("direction_raw"),
                "shares": 0.0,
                "amount": 0.0,
                "total_fees": 0.0,
                "net_amount": 0.0,
                "_all_net_amount": True,
                "_thesis_ids": set(),
            }
        agg = grouped[key]
        agg["shares"] += _to_float(r.get("shares")) or 0.0
        agg["amount"] += _to_float(r.get("amount")) or 0.0
        _, _, fees, net_amount, _, _ = _row_cash_components(r)
        agg["total_fees"] += fees
        if net_amount is None:
            agg["_all_net_amount"] = False
        else:
            agg["net_amount"] += net_amount
        if r.get("stock_name"):
            agg["stock_name"] = r.get("stock_name")
        if r.get("thesis_id") is not None:
            agg["_thesis_ids"].add(r.get("thesis_id"))

    actions: list[dict[str, Any]] = []
    for action in grouped.values():
        thesis_ids = action.pop("_thesis_ids")
        action["thesis_id"] = next(iter(thesis_ids)) if len(thesis_ids) == 1 else None
        if not action.pop("_all_net_amount"):
            action["net_amount"] = None
        actions.append(action)
    return sorted(actions, key=lambda r: ((r.get("direction") or ""), r.get("stock_code") or ""))


def _format_actions(actions: list[dict[str, Any]]) -> str:
    if not actions:
        return "无券商成交流水"
    parts: list[str] = []
    for action in actions:
        direction = action.get("direction") or ""
        stock = action.get("stock_code") or action.get("stock_code_raw") or ""
        shares = _to_float(action.get("shares")) or 0.0
        amount = _to_float(action.get("amount")) or 0.0
        vwap = (amount / shares) if shares else None
        name = action.get("stock_name") or ""
        name_part = f" {name}" if name else ""
        parts.append(f"{direction} {stock}{name_part} {shares:.0f}@{vwap:.2f}" if vwap is not None else f"{direction} {stock}{name_part} {shares:.0f}")
    return "；".join(parts) if parts else "无券商成交流水"


def _format_ratio(x: float | None) -> str:
    if x is None:
        return "-"
    if x == float("inf"):
        return "∞"
    return f"{x:.2f}"


def _format_pct(x: float | None) -> str:
    return f"{x * 100:.1f}%" if x is not None else "-"


def _format_signed_money(x: float | None) -> str:
    if x is None:
        return "-"
    prefix = "+" if x > 0 else ""
    return f"{prefix}{x:,.2f}"


def _short_date(s: str | None) -> str:
    if not s or len(s) < 10:
        return s or "-"
    return s[5:10]


def _event_brief(event: dict[str, Any] | None) -> str:
    if not event:
        return "-"
    stock_name = event.get("stock_name") or ""
    name_part = f" {stock_name}" if stock_name else ""
    thesis_id = event.get("sell_thesis_id")
    thesis_part = f"思路#{thesis_id}" if thesis_id is not None else "无思路"
    return (
        f"{_short_date(event.get('biz_date'))} {event.get('stock_code')}{name_part} "
        f"{_format_signed_money(float(event.get('realized_pnl') or 0.0))}（{thesis_part}）"
    )


def _change_note(name: str, delta: float) -> str:
    direction = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
    explanations = {
        "成交笔数": "看交易频率是否收缩。",
        "买入金额": "看试错强度是否变化。",
        "卖出金额": "看回收资金是否减少。",
        "净现金流": "结合留仓判断仓位变化。",
        "交易费用": "跟随交易活跃度变化。",
        "近似已实现净盈亏": "回到具体标的复盘。",
    }
    return f"{name}{direction}{_format_signed_money(delta)}｜{explanations.get(name, '仅作短样本线索。')}"


def _format_actions_compact(actions: list[dict[str, Any]], *, limit: int = 2) -> str:
    if not actions:
        return "无流水"
    parts: list[str] = []
    for action in actions[:limit]:
        direction = "买" if (action.get("direction") or "").lower() == "buy" else "卖"
        stock = action.get("stock_code") or action.get("stock_code_raw") or "-"
        stock_name = action.get("stock_name") or ""
        shares = _to_float(action.get("shares")) or 0.0
        name_part = f" {stock_name}" if stock_name else ""
        parts.append(f"{direction} {stock}{name_part} {shares:.0f}股")
    if len(actions) > limit:
        parts.append(f"等{len(actions)}项")
    return "；".join(parts)


def _normalize_anomaly_reason(reason: str | None) -> str:
    if not reason:
        return "待核查"
    if "缺少" in reason:
        return "缺少思路"
    if "closed" in reason or "archived" in reason:
        return "思路已关闭"
    return reason.replace("thesis", "思路")


def _format_anomaly_line(thesis_anomaly_rows: list[dict[str, Any]]) -> str:
    if not thesis_anomaly_rows:
        return "思路核查：无"
    unique: dict[tuple[str, str], None] = {}
    for row in thesis_anomaly_rows:
        stock = str(row.get("stock_code") or "-")
        reason = _normalize_anomaly_reason(row.get("reason"))
        unique[(stock, reason)] = None
    examples = "；".join(f"{stock}:{reason}" for stock, reason in list(unique.keys())[:3])
    suffix = f"（{examples}）" if examples else ""
    return f"思路核查：{len(unique)}项/{len(thesis_anomaly_rows)}行{suffix}"


def _review_question_lines(
    *,
    realized_events_current: list[dict[str, Any]],
    thesis_anomaly_rows: list[dict[str, Any]],
    same_day_flip_n: int,
    next_trade_day_flip_n: int,
) -> list[str]:
    questions: list[str] = []
    for event in sorted(
        realized_events_current,
        key=lambda e: abs(float(e.get("realized_pnl") or 0.0)),
        reverse=True,
    )[:3]:
        stock_name = event.get("stock_name") or ""
        name_part = f" {stock_name}" if stock_name else ""
        pnl = float(event.get("realized_pnl") or 0.0)
        questions.append(
            f"- {_short_date(event.get('biz_date'))} {event.get('stock_code')}{name_part}："
            f"{_format_signed_money(pnl)}，核对止盈/止损与仓位执行。"
        )
    if thesis_anomaly_rows:
        questions.append("- 思路核查：确认是否需要补复盘或重新归因。")
    if same_day_flip_n or next_trade_day_flip_n:
        questions.append(f"- 快进快出：同日{same_day_flip_n}/隔日{next_trade_day_flip_n}，核对是否由主线变化触发。")
    if not questions:
        questions.append("- 本期无闭环卖出：优先确认数据新鲜度和未闭环线索。")
    return questions[:4]


def _build_dingtalk_summary(
    *,
    current_start: str,
    current_end: str,
    current_agg: dict[str, Any],
    prev_agg: dict[str, Any] | None,
    current_day_summaries: list[dict[str, Any]],
    realized_by_day: dict[str, float],
    realized_events_current: list[dict[str, Any]],
    current_realized_total: float,
    realized_prev_total: float,
    win_rate: float | None,
    pl_ratio: float | None,
    profit_factor: float | None,
    max_win: float | None,
    max_loss: float | None,
    unclosed_clue_n: int,
    unclosed_clues: list[dict[str, Any]],
    thesis_anomaly_rows: list[dict[str, Any]],
    same_day_flip_n: int,
    next_trade_day_flip_n: int,
    open_check_n: int,
    unreviewed_n: int,
    deviation_n: int,
    weak_sell_summary: dict[str, Any],
    report_path: Path,
) -> str:
    change_lines: list[str] = []
    if prev_agg:
        change_candidates: list[tuple[str, float]] = [
            ("成交笔数", float(current_agg["n"]) - float(prev_agg["n"])),
            ("买入金额", float(current_agg["buy_amount"]) - float(prev_agg["buy_amount"])),
            ("卖出金额", float(current_agg["sell_amount"]) - float(prev_agg["sell_amount"])),
            ("交易费用", float(current_agg["fees"]) - float(prev_agg["fees"])),
            ("近似已实现净盈亏", current_realized_total - realized_prev_total),
        ]
        change_lines = [
            f"- {_change_note(name, delta)}"
            for name, delta in sorted(change_candidates, key=lambda x: abs(x[1]), reverse=True)[:3]
        ]
    if not change_lines:
        change_lines = ["- 样本不足：缺少上一组 4 个交易日可比窗口。"]

    day_lines = []
    for summary in current_day_summaries:
        day = summary["day"]
        realized = realized_by_day.get(day, 0.0)
        actions = _format_actions_compact(summary["actions"])
        day_lines.append(
            f"- {day}：买{int(summary['buy_n'])}/卖{int(summary['sell_n'])}；"
            f"已实现盈亏{_format_signed_money(realized)}；{actions}"
        )

    win_event = max(
        (e for e in realized_events_current if float(e.get("realized_pnl") or 0.0) > 1e-9),
        key=lambda e: float(e.get("realized_pnl") or 0.0),
        default=None,
    )
    loss_event = min(
        (e for e in realized_events_current if float(e.get("realized_pnl") or 0.0) < -1e-9),
        key=lambda e: float(e.get("realized_pnl") or 0.0),
        default=None,
    )

    unclosed_codes = ",".join(
        str(c["stock_code"]) for c in sorted(unclosed_clues, key=lambda x: -float(x.get("shares") or 0.0))[:5]
    )
    review_lines = _review_question_lines(
        realized_events_current=realized_events_current,
        thesis_anomaly_rows=thesis_anomaly_rows,
        same_day_flip_n=same_day_flip_n,
        next_trade_day_flip_n=next_trade_day_flip_n,
    )

    lines = [
        f"### 4日复盘｜{current_start}~{current_end}",
        "",
        "### 核心",
        f"- 笔数 {int(current_agg['n'])}（买{int(current_agg['buy_n'])}/卖{int(current_agg['sell_n'])}）",
        f"- 已实现盈亏 {_format_signed_money(current_realized_total)}｜胜率 {_format_pct(win_rate)}｜盈亏比 {_format_ratio(pl_ratio)}｜PF {_format_ratio(profit_factor)}",
        f"- 未闭环 {unclosed_clue_n}｜快进快出 同{same_day_flip_n}/隔{next_trade_day_flip_n}",
        "",
        "### 逐日节奏",
        *day_lines,
        "",
        "### Top 盈亏",
        f"- 最大盈利：{_event_brief(win_event)}",
        f"- 最大亏损：{_event_brief(loss_event)}",
        "",
        "### 重点变化",
        *change_lines,
        "",
        "### 需核查",
        f"- 未闭环：{unclosed_clue_n}" + (f"（{unclosed_codes}）" if unclosed_codes else ""),
        f"- 快进快出：同日{same_day_flip_n} / 隔日{next_trade_day_flip_n}",
        f"- {_format_anomaly_line(thesis_anomaly_rows)}",
        "",
        "### 失效与纪律",
        f"- 持仓自查 {open_check_n} 项｜未复盘 {unreviewed_n}｜偏离/低分 {deviation_n}",
        (
            f"- 卖弱顺序：卖出选择 {weak_sell_summary['actions']}｜"
            f"可判 {weak_sell_summary['comparable']}｜"
            f"最弱优先 {weak_sell_summary['aligned']}｜"
            f"有更弱未先卖 {weak_sell_summary['weaker_retained']}｜"
            f"不适用 {weak_sell_summary['not_applicable']}｜"
            f"缺数 {weak_sell_summary['unknown']}｜"
            f"流水{'完整' if weak_sell_summary['history_complete'] else '截断'}"
        ),
        "",
        "### 复盘问题",
        *review_lines,
        "",
        f"本地完整报告：{report_path}",
    ]
    return "\n".join(lines)


def _merge_actions(day_rows: list[dict]) -> str:
    return _format_actions(_group_trade_actions(day_rows))


def _row_key_time(r: dict) -> tuple:
    return (r.get("biz_date") or "", r.get("exec_time") or "", r.get("id") or 0)


def _exec_time_seconds(value: Any) -> int | None:
    try:
        parsed = datetime.strptime(str(value or ""), "%H:%M:%S")
    except ValueError:
        return None
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def _normalize_trade_no(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text.lstrip("0") or ("0" if text else "")


def _money_cents(value: float | None) -> int | None:
    return int(round(value * 100)) if value is not None else None


def _canonical_summary_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    _, _, fees, _, buy_outflow, sell_inflow = _row_cash_components(row)
    price = _to_float(row.get("price"))
    shares = _to_float(row.get("shares"))
    balance_after = _to_float(row.get("balance_after"))
    return (
        str(row.get("biz_date") or ""),
        str(row.get("direction") or "").lower(),
        _normalize_stock_code(row.get("stock_code") or row.get("stock_code_raw")),
        int(round(shares * 1_000_000)) if shares is not None else None,
        int(round(price * 1_000_000)) if price is not None else None,
        _money_cents(_to_float(row.get("amount"))),
        (
            int(round(balance_after * 1_000_000))
            if balance_after is not None
            else None
        ),
        _money_cents(fees),
        _money_cents(buy_outflow),
        _money_cents(sell_inflow),
        str(row.get("currency") or "").upper(),
    )


def _merge_component_cash_fields(
    summary: dict[str, Any],
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    """把被折叠拆单的更完整现金字段带到汇总行；冲突时停止生成报告。"""
    merged = dict(summary)
    _, _, summary_fees, _, _, _ = _row_cash_components(merged)
    component_fees = sum(_row_cash_components(row)[2] for row in components)
    if summary_fees > 0 and component_fees > 0:
        if abs(summary_fees - component_fees) > 0.01:
            raise RuntimeError("split-summary duplicate cash fields conflict")
    elif component_fees > 0:
        merged["total_fees"] = component_fees

    summary_net = _to_float(merged.get("net_amount"))
    component_nets = [_to_float(row.get("net_amount")) for row in components]
    component_net = (
        sum(value for value in component_nets if value is not None)
        if component_nets and all(value is not None for value in component_nets)
        else None
    )
    if summary_net is not None and component_net is not None:
        if abs(summary_net - component_net) > 0.01:
            raise RuntimeError("split-summary duplicate cash fields conflict")
    elif summary_net is None and component_net is not None:
        merged["net_amount"] = component_net

    imported_candidates = [
        (parsed, raw)
        for row in [merged, *components]
        for raw in [str(row.get("imported_at") or "")]
        for parsed in [_parse_dt(raw)]
        if parsed is not None
    ]
    if imported_candidates:
        merged["imported_at"] = min(imported_candidates, key=lambda item: item[0])[1]
    return merged


def _collapse_split_plus_summary_rows(day_rows: list[dict]) -> list[dict]:
    """仅凭跨来源且成交号可关联的强证据折叠“拆分成交 + 汇总成交”。"""
    rows = [dict(row) for row in sorted(day_rows, key=_row_key_time)]
    component_buckets: dict[
        tuple[str, str, str, int],
        dict[str, list[Any]],
    ] = {}
    for component_index, component in enumerate(rows):
        if component.get("balance_after") is not None:
            continue
        component_time = _exec_time_seconds(component.get("exec_time"))
        component_price = _to_float(component.get("price"))
        component_code = _normalize_stock_code(
            component.get("stock_code") or component.get("stock_code_raw")
        )
        component_direction = str(component.get("direction") or "").lower()
        component_date = str(component.get("biz_date") or "")
        if (
            component_time is None
            or component_price is None
            or not component_code
            or component_direction not in ("buy", "sell")
            or not component_date
        ):
            continue
        bucket_key = (
            component_date,
            component_direction,
            component_code,
            int(round(component_price * 1_000_000)),
        )
        bucket = component_buckets.setdefault(
            bucket_key,
            {"times": [], "indexes": []},
        )
        bucket["times"].append(component_time)
        bucket["indexes"].append(component_index)

    plans: list[tuple[int, tuple[int, ...]]] = []
    for candidate_index, candidate in enumerate(rows):
        candidate_shares = _to_float(candidate.get("shares"))
        candidate_amount = _to_float(candidate.get("amount"))
        candidate_price = _to_float(candidate.get("price"))
        candidate_balance = _to_float(candidate.get("balance_after"))
        candidate_time = _exec_time_seconds(candidate.get("exec_time"))
        candidate_contract = str(candidate.get("broker_contract_no") or "").strip()
        candidate_trade_no = _normalize_trade_no(candidate.get("broker_trade_no"))
        candidate_run = str(candidate.get("import_run_id") or "").strip()
        candidate_source = str(candidate.get("source_file") or "").strip()
        candidate_code = _normalize_stock_code(
            candidate.get("stock_code") or candidate.get("stock_code_raw")
        )
        candidate_direction = str(candidate.get("direction") or "").lower()
        candidate_date = str(candidate.get("biz_date") or "")
        if (
            candidate.get("balance_after") is None
            or candidate_balance is None
            or candidate_balance < 0
            or candidate_shares is None
            or candidate_amount is None
            or candidate_price is None
            or candidate_time is None
            or not candidate_contract
            or not candidate_trade_no
            or not candidate_run
            or not candidate_source
            or not candidate_code
            or candidate_direction not in ("buy", "sell")
            or not candidate_date
        ):
            continue

        nearby_groups: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        bucket_key = (
            candidate_date,
            candidate_direction,
            candidate_code,
            int(round(candidate_price * 1_000_000)),
        )
        bucket = component_buckets.get(bucket_key) or {"times": [], "indexes": []}
        start = bisect_left(bucket["times"], candidate_time - 5)
        end = bisect_right(bucket["times"], candidate_time + 5)
        for component_index in bucket["indexes"][start:end]:
            component = rows[component_index]
            component_time = _exec_time_seconds(component.get("exec_time"))
            component_price = _to_float(component.get("price"))
            shares = _to_float(component.get("shares"))
            amount = _to_float(component.get("amount"))
            component_contract = str(
                component.get("broker_contract_no") or ""
            ).strip()
            component_run = str(component.get("import_run_id") or "").strip()
            component_source = str(component.get("source_file") or "").strip()
            if (
                component_time is None
                or abs(candidate_time - component_time) > 5
                or component_price is None
                or abs(component_price - candidate_price) > 1e-9
                or shares is None
                or amount is None
                or not component_contract
                or not component_run
                or not component_source
                or component_contract == candidate_contract
                or component_run == candidate_run
                or component_source == candidate_source
            ):
                continue
            nearby_groups[
                (component_contract, component_run, component_source)
            ].append(component_index)

        matches: list[tuple[int, ...]] = []
        for component_indexes in nearby_groups.values():
            components = [rows[index] for index in component_indexes]
            thesis_ids = {
                row.get("thesis_id")
                for row in [candidate, *components]
                if row.get("thesis_id") is not None
            }
            component_trade_nos = {
                _normalize_trade_no(component.get("broker_trade_no"))
                for component in components
            }
            if (
                not components
                or candidate_trade_no not in component_trade_nos
                or candidate_shares
                < max(_to_float(component.get("shares")) or 0.0 for component in components)
            ):
                continue
            component_shares = sum(
                _to_float(component.get("shares")) or 0.0 for component in components
            )
            component_amount = sum(
                _to_float(component.get("amount")) or 0.0 for component in components
            )
            if (
                abs(component_shares - candidate_shares) <= 1e-9
                and abs(component_amount - candidate_amount) <= 0.01
            ):
                if len(thesis_ids) > 1:
                    raise RuntimeError(
                        "split-summary duplicate has conflicting thesis_id"
                    )
                matches.append(tuple(component_indexes))
        if len(matches) > 1:
            raise RuntimeError("split-summary duplicate has multiple component matches")
        if not matches:
            continue
        plans.append((candidate_index, matches[0]))

    plan_groups: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for candidate_index, component_indexes in plans:
        plan_groups[tuple(sorted(component_indexes))].append(candidate_index)
    component_group_usage: dict[int, set[tuple[int, ...]]] = defaultdict(set)
    for component_indexes in plan_groups:
        for component_index in component_indexes:
            component_group_usage[component_index].add(component_indexes)
    if any(len(groups) > 1 for groups in component_group_usage.values()):
        raise RuntimeError("split-summary duplicate has overlapping component matches")

    removed: set[int] = set()
    for component_indexes, candidate_indexes in plan_groups.items():
        components = [rows[index] for index in component_indexes]
        normalized_candidates = {
            index: _merge_component_cash_fields(rows[index], components)
            for index in candidate_indexes
        }
        signatures = {
            _canonical_summary_signature(normalized_candidates[index])
            for index in candidate_indexes
        }
        thesis_ids = {
            row.get("thesis_id")
            for row in [*components, *(rows[index] for index in candidate_indexes)]
            if row.get("thesis_id") is not None
        }
        if len(signatures) != 1 or len(thesis_ids) > 1:
            raise RuntimeError("split-summary duplicate summaries conflict")

        candidate_index = min(
            candidate_indexes,
            key=lambda index: (
                0 if rows[index].get("thesis_id") is not None else 1,
                _row_key_time(rows[index]),
            ),
        )
        first_component = min(components, key=_row_key_time)
        collapsed = dict(normalized_candidates[candidate_index])
        collapsed["thesis_id"] = next(iter(thesis_ids)) if thesis_ids else None
        collapsed["exec_time"] = first_component.get("exec_time")
        collapsed["id"] = first_component.get("id")
        rows[candidate_index] = collapsed
        removed.update(component_indexes)
        removed.update(
            index for index in candidate_indexes if index != candidate_index
        )
    return sorted(
        (row for index, row in enumerate(rows) if index not in removed),
        key=_row_key_time,
    )


def _ordered_execution_blocks(day_rows: list[dict]) -> list[dict[str, Any]]:
    """按券商行序回放；仅合并可由合同号或同一成交时刻识别的相邻拆单。"""
    valid_rows: list[dict[str, Any]] = []
    time_keys: dict[str, set[tuple[str, str]]] = defaultdict(set)
    missing_time = False
    for row in _collapse_split_plus_summary_rows(day_rows):
        direction = str(row.get("direction") or "").lower()
        code = _normalize_stock_code(row.get("stock_code") or row.get("stock_code_raw"))
        shares = _to_float(row.get("shares"))
        if direction not in ("buy", "sell") or not code or shares is None or shares <= 0:
            continue
        valid_rows.append(row)
        action_key = (direction, code)
        exec_time = str(row.get("exec_time") or "")
        if exec_time:
            time_keys[exec_time].add(action_key)
        else:
            missing_time = True

    whole_day_ambiguous = missing_time and len(valid_rows) > 1
    ambiguous_times = sorted(
        exec_time for exec_time, keys in time_keys.items() if len(keys) > 1
    )
    first_ambiguous_time = ambiguous_times[0] if ambiguous_times else None
    blocks: list[dict[str, Any]] = []
    for row in valid_rows:
        direction = str(row.get("direction") or "").lower()
        code = _normalize_stock_code(row.get("stock_code") or row.get("stock_code_raw"))
        shares = _to_float(row.get("shares")) or 0.0
        exec_time = str(row.get("exec_time") or "")
        order_ambiguous = whole_day_ambiguous or (
            first_ambiguous_time is not None
            and bool(exec_time)
            and exec_time >= first_ambiguous_time
        )
        contract_no = str(row.get("broker_contract_no") or "").strip()
        key = (direction, code)
        previous = blocks[-1] if blocks else None
        same_identified_order = bool(
            previous
            and previous["_key"] == key
            and previous["order_ambiguous"] == order_ambiguous
            and (
                (contract_no and contract_no == previous["_contract_no"])
                or (exec_time and exec_time == previous["exec_time"])
            )
        )
        if same_identified_order:
            block = previous
            block["shares"] += shares
            if row.get("stock_name"):
                block["stock_name"] = row.get("stock_name")
            if row.get("balance_after") is not None:
                block["balance_after"] = _to_float(row.get("balance_after"))
            continue
        blocks.append(
            {
                "_key": key,
                "_contract_no": contract_no,
                "biz_date": row.get("biz_date"),
                "exec_time": row.get("exec_time"),
                "stock_code": code,
                "stock_name": row.get("stock_name") or "",
                "direction": direction,
                "shares": shares,
                "balance_after": _to_float(row.get("balance_after")),
                "order_ambiguous": order_ambiguous,
            }
        )
    for block in blocks:
        block.pop("_key", None)
        block.pop("_contract_no", None)
    return blocks


def _apply_execution_block(
    state: dict[str, dict[str, Any]],
    block: dict[str, Any],
) -> None:
    code = block["stock_code"]
    direction = block["direction"]
    shares = float(block.get("shares") or 0.0)
    balance_after = _to_float(block.get("balance_after"))
    member = state.get(code)
    if block.get("balance_conflict"):
        if member is None:
            state[code] = {
                "stock_code": code,
                "stock_name": block.get("stock_name") or "",
                "shares": None,
                "change_pct": None,
                "error": "balance_after_conflict",
                "presence_uncertain": True,
            }
        else:
            member["shares"] = None
            member["presence_uncertain"] = True
        return
    if direction == "buy":
        if member is None:
            member = {
                "stock_code": code,
                "stock_name": block.get("stock_name") or "",
                "shares": 0.0,
                "change_pct": None,
                "error": "same_day_buy_has_no_prior_holding_quote",
            }
            state[code] = member
        old_shares = _to_float(member.get("shares"))
        member["shares"] = (
            balance_after
            if balance_after is not None
            else ((old_shares + shares) if old_shares is not None else None)
        )
        return

    if member is None:
        if balance_after is None or balance_after > 1e-9:
            state[code] = {
                "stock_code": code,
                "stock_name": block.get("stock_name") or "",
                "shares": balance_after,
                "change_pct": None,
                "error": "sell_missing_from_prior_snapshot",
                "presence_uncertain": balance_after is None,
            }
        return
    old_shares = _to_float(member.get("shares"))
    remaining = (
        balance_after
        if balance_after is not None
        else (max(old_shares - shares, 0.0) if old_shares is not None else None)
    )
    if remaining is not None and remaining <= 1e-9:
        state.pop(code, None)
    else:
        member["shares"] = remaining
        member["presence_uncertain"] = remaining is None


def _balance_after_conflicts(
    state: dict[str, dict[str, Any]],
    block: dict[str, Any],
) -> bool:
    balance_after = _to_float(block.get("balance_after"))
    shares = _to_float(block.get("shares"))
    member = state.get(block.get("stock_code"))
    if member is None and block.get("direction") == "sell" and shares:
        return True
    if balance_after is None:
        return False
    old_shares = (
        _to_float(member.get("shares"))
        if member
        else (0.0 if block.get("direction") == "buy" else None)
    )
    if shares is None or old_shares is None:
        return False
    if block.get("direction") == "buy":
        expected = old_shares + shares
    else:
        if shares > old_shares + 1e-9:
            return True
        expected = old_shares - shares
    return abs(balance_after - expected) > 1e-9


def _weak_sell_result(
    *,
    block: dict[str, Any],
    prior_day: str | None,
    snapshot_status: str,
    snapshot_reason: str | None,
    history_complete: bool,
    replay_reliable: bool,
    state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    code = block["stock_code"]
    universe = {
        member_code: member
        for member_code, member in state.items()
        if _to_float(member.get("shares")) is None
        or (_to_float(member.get("shares")) or 0.0) > 1e-9
    }
    sold_member = universe.get(code)
    sold_change = _to_float(sold_member.get("change_pct")) if sold_member else None
    known_changes = {
        member_code: _to_float(member.get("change_pct"))
        for member_code, member in universe.items()
        if _to_float(member.get("change_pct")) is not None
    }
    unknown_codes = sorted(
        member_code for member_code in universe if member_code not in known_changes
    )
    uncertain_other_codes = sorted(
        member_code
        for member_code, member in universe.items()
        if member_code != code and member.get("presence_uncertain")
    )
    known_weaker = sorted(
        member_code
        for member_code, change in known_changes.items()
        if (
            sold_change is not None
            and not universe[member_code].get("presence_uncertain")
            and change < sold_change - 1e-9
        )
    )
    rank = (
        1 + sum(1 for change in known_changes.values() if change < sold_change - 1e-9)
        if sold_change is not None
        else None
    )
    pre_shares = _to_float(sold_member.get("shares")) if sold_member else None
    sold_shares = float(block.get("shares") or 0.0)
    balance_after = _to_float(block.get("balance_after"))
    if block.get("balance_conflict"):
        exit_scope = "未知"
    elif balance_after is not None:
        exit_scope = "全部" if balance_after <= 1e-9 else "部分"
    elif pre_shares is not None:
        exit_scope = "全部" if sold_shares >= pre_shares - 1e-9 else "部分"
    else:
        exit_scope = "未知"

    status = "unknown_missing_snapshot"
    if not history_complete or not replay_reliable:
        status = "partial_history"
    elif snapshot_status not in ("success",):
        status = "unknown_missing_snapshot"
    elif block.get("order_ambiguous"):
        status = "unknown_ambiguous_order"
    elif sold_member is None:
        status = "partial_history"
    elif uncertain_other_codes:
        status = "partial_history"
    elif len(universe) <= 1:
        status = "not_applicable"
    elif sold_change is None:
        status = "unknown_missing_quote"
    elif known_weaker:
        status = "known_weaker_retained"
    elif unknown_codes:
        status = "unknown_missing_quote"
    else:
        weakest = min(known_changes.values())
        weakest_codes = [
            member_code
            for member_code, change in known_changes.items()
            if abs(change - weakest) <= 1e-9
        ]
        status = "tied_weakest" if len(weakest_codes) > 1 else "sold_weakest"

    def member_label(member_code: str) -> str:
        member = universe.get(member_code) or {}
        name = str(member.get("stock_name") or "")
        change = _to_float(member.get("change_pct"))
        change_text = f"{change:+.2f}%" if change is not None else "缺行情"
        return f"{member_code}{(' ' + name) if name else ''}({change_text})"

    return {
        "biz_date": block.get("biz_date"),
        "exec_time": block.get("exec_time"),
        "stock_code": code,
        "stock_name": block.get("stock_name") or (sold_member or {}).get("stock_name") or "",
        "shares": sold_shares,
        "exit_scope": exit_scope,
        "prior_trade_day": prior_day,
        "snapshot_status": snapshot_status,
        "snapshot_reason": snapshot_reason,
        "position_count": len(universe),
        "sold_change_pct": sold_change,
        "rank": rank,
        "known_count": len(known_changes),
        "weaker_retained": [member_label(member_code) for member_code in known_weaker],
        "missing_quotes": [member_label(member_code) for member_code in unknown_codes],
        "uncertain_positions": [
            member_label(member_code) for member_code in uncertain_other_codes
        ],
        "balance_conflict": bool(block.get("balance_conflict")),
        "day_balance_conflict": False,
        "status": status,
    }


def _analyze_weak_sell_order(
    *,
    rows: list[dict],
    current_days: list[str],
    prior_day_by_day: dict[str, str],
    snapshots: dict[str, dict[str, Any]],
    history_complete: bool = True,
) -> list[dict[str, Any]]:
    """用严格上一交易日持仓涨跌幅，按真实成交顺序检查卖弱优先代理。"""
    rows_by_day: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        trade_date = str(row.get("biz_date") or "")
        if trade_date in current_days:
            rows_by_day[trade_date].append(row)

    results: list[dict[str, Any]] = []
    for trade_date in current_days:
        blocks = _ordered_execution_blocks(rows_by_day.get(trade_date, []))
        if not any(block["direction"] == "sell" for block in blocks):
            continue
        prior_day = prior_day_by_day.get(trade_date)
        snapshot = snapshots.get(prior_day or "") or {
            "status": "missing",
            "reason": (
                "strict_prior_trade_day_unavailable"
                if not prior_day
                else "daily_market_snapshot_missing"
            ),
            "members": {},
        }
        state = {
            code: dict(member)
            for code, member in (snapshot.get("members") or {}).items()
        }
        snapshot_status = str(snapshot.get("status") or "missing")
        replay_reliable = True
        day_balance_conflict = False
        day_result_start = len(results)
        for block in blocks:
            balance_conflict = (
                snapshot_status == "success"
                and _balance_after_conflicts(state, block)
            )
            if balance_conflict:
                block = {**block, "balance_conflict": True}
                day_balance_conflict = True
            if block["direction"] == "sell":
                results.append(
                    _weak_sell_result(
                        block=block,
                        prior_day=prior_day,
                        snapshot_status=snapshot_status,
                        snapshot_reason=(
                            str(snapshot.get("reason"))
                            if snapshot.get("reason")
                            else None
                        ),
                        history_complete=history_complete,
                        replay_reliable=replay_reliable and not balance_conflict,
                        state=state,
                    )
                )
            _apply_execution_block(state, block)
            if balance_conflict:
                replay_reliable = False
        if day_balance_conflict:
            for result in results[day_result_start:]:
                result["status"] = "partial_history"
                result["day_balance_conflict"] = True
    return results


def _summarize_weak_sell_checks(
    checks: list[dict[str, Any]],
    *,
    history_complete: bool = True,
) -> dict[str, Any]:
    aligned_statuses = {"sold_weakest", "tied_weakest"}
    comparable_statuses = aligned_statuses | {"known_weaker_retained"}
    return {
        "actions": len(checks),
        "comparable": sum(check.get("status") in comparable_statuses for check in checks),
        "aligned": sum(check.get("status") in aligned_statuses for check in checks),
        "weaker_retained": sum(
            check.get("status") == "known_weaker_retained" for check in checks
        ),
        "not_applicable": sum(
            check.get("status") == "not_applicable" for check in checks
        ),
        "unknown": sum(
            check.get("status") not in comparable_statuses | {"not_applicable"}
            for check in checks
        ),
        "history_complete": history_complete,
    }


def _weak_sell_status_label(status: str) -> str:
    return {
        "sold_weakest": "符合：唯一最弱",
        "tied_weakest": "符合：并列最弱",
        "known_weaker_retained": "需复核：有更弱持仓未先卖",
        "not_applicable": "不适用：卖出前仅1只",
        "unknown_missing_snapshot": "无法判断：前一交易日快照缺失/不完整",
        "unknown_missing_quote": "无法判断：持仓行情不完整",
        "unknown_ambiguous_order": "无法判断：成交先后不明确",
        "partial_history": "无法判断：持仓回放不完整",
    }.get(status, f"无法判断：{status or '未知状态'}")


def _render_weak_sell_checks(
    checks: list[dict[str, Any]],
    *,
    history_complete: bool = True,
) -> str:
    if not checks:
        if not history_complete:
            return "（复盘窗口流水已截断，无法确认是否存在卖出动作）"
        return "（本期无卖出动作，不适用）"
    rows: list[list[str]] = []
    for check in checks:
        sold_change = _to_float(check.get("sold_change_pct"))
        rank = check.get("rank")
        position_count = int(check.get("position_count") or 0)
        known_count = int(check.get("known_count") or 0)
        details: list[str] = []
        if check.get("weaker_retained"):
            details.append("更弱：" + "；".join(check["weaker_retained"]))
        if check.get("missing_quotes"):
            details.append("缺行情：" + "；".join(check["missing_quotes"]))
        if check.get("uncertain_positions"):
            details.append("持仓去留不明：" + "；".join(check["uncertain_positions"]))
        if check.get("balance_conflict"):
            details.append("持仓基线或券商成交后余额与流水矛盾")
        elif check.get("day_balance_conflict"):
            details.append("当日其他流水暴露持仓基线/余额矛盾，整日回放不可靠")
        if check.get("snapshot_status") not in (None, "success"):
            snapshot_reason = str(check.get("snapshot_reason") or "原因未记录")
            details.append(
                f"前收盘快照 {check.get('snapshot_status')}：{snapshot_reason}"
            )
        rows.append(
            [
                f"{check.get('biz_date') or '-'} {check.get('exec_time') or '-'}",
                _md_cell(
                    f"{check.get('stock_code') or '-'} "
                    f"{check.get('stock_name') or ''}".strip()
                ),
                str(check.get("exit_scope") or "未知"),
                str(check.get("prior_trade_day") or "-"),
                str(position_count),
                f"{sold_change:+.2f}%" if sold_change is not None else "-",
                f"{rank}/{known_count}（已知）" if rank is not None and known_count else "-",
                _md_cell("；".join(details) or "-"),
                _weak_sell_status_label(str(check.get("status") or "")),
            ]
        )
    return md_table(
        [
            "卖出时间",
            "卖出标的",
            "全部/部分",
            "强弱快照日",
            "卖出前持仓数",
            "卖出票快照涨跌",
            "弱势排名",
            "更弱未卖/缺失与歧义",
            "代理结论",
        ],
        rows,
    )


def _md_cell(s: Any) -> str:
    """把自由文本安全放进 Markdown 表格单元格：竖线/换行会破坏表格结构。"""
    text = "-" if s is None else str(s)
    return text.replace("|", "｜").replace("\n", " ").strip() or "-"


def _held_days(opened_at: str | None, run_date: date) -> int | None:
    od = _parse_date(opened_at)
    return (run_date - od).days if od else None


def _stock_label(t: dict) -> str:
    return _md_cell(f"{t.get('stock_code') or '-'} {t.get('stock_name') or ''}".strip())


def _render_failure_self_check(open_theses: list[dict], run_date: date) -> str:
    """持仓失效自查清单：列出 open 状态 thesis 的失效条件/止损/目标价，供人工核对。

    不取行情、不机械判定文本型 failure_condition 是否触发——保持报告事实层纯度。
    """
    if not open_theses:
        return "（当前无 open 状态 thesis，无需自查）"
    # 预计算持仓天数：排序与渲染共用，避免对同一 opened_at 解析两次。
    with_days = [(t, _held_days(t.get("opened_at"), run_date)) for t in open_theses]
    rows: list[list[str]] = []
    for t, days in sorted(with_days, key=lambda x: (x[1] if x[1] is not None else -1), reverse=True):
        stop = _to_float(t.get("stop_loss"))
        target = _to_float(t.get("target_price"))
        pos = _to_float(t.get("planned_position_pct"))
        rows.append(
            [
                f"#{t.get('id')}",
                _stock_label(t),
                str(t.get("opened_at") or "-"),
                str(days) if days is not None else "-",
                _md_cell(t.get("failure_condition")),
                _format_money(stop) if stop is not None else "-",
                _format_money(target) if target is not None else "-",
                f"{pos:.0f}%" if pos is not None else "-",
                _md_cell(t.get("entry_reason")),
            ]
        )
    return md_table(
        ["思路#", "股票", "开仓日", "持仓天数(自然日)", "失效条件", "止损", "目标价", "计划仓位%", "开仓逻辑"],
        rows,
    )


_EP_LABEL = {0: "偏离", 1: "按计划", 2: "未执行"}


def _render_discipline_review(
    *,
    relevant_closed: list[dict],
    unreviewed: list[dict],
    deviation: list[tuple[dict, dict]],
    backlog_n: int,
) -> list[str]:
    """纪律执行回顾：基于 thesis_review，回顾本期相关已平仓交易的复盘缺口与偏离。"""
    if not relevant_closed and backlog_n == 0:
        return ["（本期无已平仓 thesis 需复盘）"]

    out: list[str] = []
    out.append("### 未复盘（已平仓缺 thesis_review）")
    if unreviewed:
        out.append(
            md_table(
                ["思路#", "股票", "平仓日", "失效条件"],
                [
                    [
                        f"#{t.get('id')}",
                        _stock_label(t),
                        str(t.get("closed_at") or "-"),
                        _md_cell(t.get("failure_condition")),
                    ]
                    for t in unreviewed
                ],
            )
        )
        out.append("- [判断] 上述已平仓交易尚未复盘，建议补 `python3 main.py db thesis-review`。")
    else:
        out.append("（本期相关已平仓 thesis 均已复盘）")

    out.append("")
    out.append("### 复盘标记偏离/未执行/低分")
    if deviation:
        rows: list[list[str]] = []
        for t, rev in deviation:
            score = rev.get("discipline_score")
            pnl = _to_float(rev.get("realized_pnl_amount"))
            rows.append(
                [
                    f"#{t.get('id')}",
                    _stock_label(t),
                    _EP_LABEL.get(rev.get("executed_as_planned"), str(rev.get("executed_as_planned"))),
                    str(rev.get("exit_trigger") or "-"),
                    str(score) if score is not None else "-",
                    _format_signed_money(pnl) if pnl is not None else "-",
                    _md_cell(rev.get("lessons")),
                ]
            )
        out.append(
            md_table(
                ["思路#", "股票", "执行情况", "exit_trigger", "纪律分", "已实现盈亏", "教训"],
                rows,
            )
        )
    else:
        out.append("（无偏离/未执行/低分标记）")

    if backlog_n > 0:
        out.append("")
        out.append(f"- [判断] 另有 {backlog_n} 笔历史已平仓 thesis 仍未复盘（不在本期窗口）。")
    return out


@dataclass
class Lot:
    shares: float
    cost_total: float
    buy_date: str
    thesis_id: Optional[int]


def generate(*, run_date: date, account: str, limit: int, push: bool) -> dict[str, Any]:
    run_date_str = run_date.isoformat()

    sample_limit_note = None
    last8 = _try_get_last_n_trade_days(8, run_date)
    if last8 is None:
        lookback_start = (run_date - timedelta(days=21)).isoformat()
        rows = _run_json(
            [
                "python3",
                "scripts/main.py",
                "executions",
                "list",
                "--from",
                lookback_start,
                "--to",
                run_date_str,
                "--account",
                account,
                "--limit",
                str(limit),
                "--json",
            ]
        ) or []
        uniq = sorted(
            {
                r.get("biz_date")
                for r in rows
                if r.get("biz_date") and r.get("biz_date") <= run_date_str
            }
        )
        if len(uniq) < 4:
            raise RuntimeError("交易日历不可用且近21自然日流水不足以推断4日窗口")
        current4 = uniq[-4:]
        prev4 = uniq[-8:-4] if len(uniq) >= 8 else []
        sample_limit_note = "交易日历不可用，本次按有券商流水日期近似"
    else:
        current4 = last8[-4:]
        prev4 = last8[-8:-4]

    current_start, current_end = current4[0], current4[-1]
    prev_start, prev_end = (prev4[0], prev4[-1]) if prev4 else (None, None)
    trade_day_idx = _trade_day_index(last8) if last8 else {}
    prior_day_by_day = (
        {last8[i]: last8[i - 1] for i in range(1, len(last8))}
        if last8
        else {}
    )
    weak_snapshot_days = {
        prior_day_by_day[trade_day]
        for trade_day in current4
        if trade_day in prior_day_by_day
    }
    holding_snapshots = _load_holding_snapshots(weak_snapshot_days)
    if account != "default":
        holding_snapshots = {
            snapshot_day: {
                "status": "missing",
                "reason": "holdings_snapshot_is_not_account_scoped",
                "members": {},
            }
            for snapshot_day in weak_snapshot_days
        }

    window_raw_rows = _run_json(
        [
            "python3",
            "scripts/main.py",
            "executions",
            "list",
            "--from",
            prev_start or current_start,
            "--to",
            current_end,
            "--account",
            account,
            "--limit",
            str(limit),
            "--json",
        ]
    ) or []
    window_truncated = len(window_raw_rows) >= limit
    window_rows = _collapse_split_plus_summary_rows(window_raw_rows)

    fifo_start = (run_date - timedelta(days=180)).isoformat()
    fifo_raw_rows = _run_json(
        [
            "python3",
            "scripts/main.py",
            "executions",
            "list",
            "--from",
            fifo_start,
            "--to",
            current_end,
            "--account",
            account,
            "--limit",
            str(limit),
            "--json",
        ]
    ) or []
    fifo_truncated = len(fifo_raw_rows) >= limit
    fifo_rows = _collapse_split_plus_summary_rows(fifo_raw_rows)

    theses = _run_json(
        ["python3", "scripts/main.py", "db", "thesis-list", "--account", account, "--json"]
    ) or []
    thesis_by_id = {t.get("id"): t for t in theses if t.get("id") is not None}

    # data freshness
    latest_trade_date = None
    latest_imported_at = None
    latest_import_run_id = None
    latest_source_archive_path = None
    if fifo_raw_rows:
        dates = [r.get("biz_date") for r in fifo_raw_rows if r.get("biz_date")]
        latest_trade_date = max(dates) if dates else None
        imported_parsed = [
            (_parse_dt(r.get("imported_at")), r)
            for r in fifo_raw_rows
            if r.get("imported_at")
        ]
        imported_parsed = [x for x in imported_parsed if x[0] is not None]
        if imported_parsed:
            latest_dt, latest_row = max(imported_parsed, key=lambda x: x[0])
            latest_imported_at = latest_dt.strftime("%Y-%m-%d %H:%M:%S")
            latest_import_run_id = latest_row.get("import_run_id")
            latest_source_archive_path = latest_row.get("source_archive_path")

    rows_by_date: dict[str, list[dict]] = defaultdict(list)
    for r in window_rows:
        d = r.get("biz_date")
        if d:
            rows_by_date[d].append(r)

    def compute_day_summary(day: str) -> dict[str, Any]:
        day_rows = rows_by_date.get(day, [])
        actions = _group_trade_actions(day_rows)
        buys = [r for r in actions if (r.get("direction") or "").lower() == "buy"]
        sells = [r for r in actions if (r.get("direction") or "").lower() == "sell"]
        buy_amount = 0.0
        sell_amount = 0.0
        fees = 0.0
        for r in day_rows:
            direction, amount, fee, net_amount, buy_cost, sell_rev = _row_cash_components(r)
            fees += fee
            if direction == "buy":
                buy_amount += amount
            elif direction == "sell":
                sell_amount += amount
        net_cashflow = sell_amount - buy_amount - fees
        return {
            "day": day,
            "n": len(actions),
            "buy_n": len(buys),
            "sell_n": len(sells),
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "fees": fees,
            "net_cashflow": net_cashflow,
            "rows": sorted(day_rows, key=_row_key_time),
            "actions": actions,
        }

    current_day_summaries = [compute_day_summary(d) for d in current4]
    prev_day_summaries = [compute_day_summary(d) for d in prev4] if prev4 else []

    def agg_summaries(summaries: list[dict[str, Any]]) -> dict[str, float]:
        return {
            "n": float(sum(s["n"] for s in summaries)),
            "buy_n": float(sum(s["buy_n"] for s in summaries)),
            "sell_n": float(sum(s["sell_n"] for s in summaries)),
            "buy_amount": float(sum(s["buy_amount"] for s in summaries)),
            "sell_amount": float(sum(s["sell_amount"] for s in summaries)),
            "fees": float(sum(s["fees"] for s in summaries)),
            "net_cashflow": float(sum(s["net_cashflow"] for s in summaries)),
        }

    current_agg = agg_summaries(current_day_summaries)
    prev_agg = agg_summaries(prev_day_summaries) if prev_day_summaries else None

    # FIFO realized for both windows
    fifo_actions_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fifo_rows:
        d = r.get("biz_date")
        if d:
            fifo_actions_by_date[d].append(r)
    fifo_sorted: list[dict[str, Any]] = []
    for day in sorted(fifo_actions_by_date):
        fifo_sorted.extend(_group_trade_actions(fifo_actions_by_date[day]))
    positions: dict[tuple[str, str], list[Lot]] = defaultdict(list)
    realized_events_current: list[dict[str, Any]] = []
    realized_events_prev: list[dict[str, Any]] = []
    unmatched_sells_current: list[dict[str, Any]] = []

    for r in fifo_sorted:
        direction = (r.get("direction") or "").lower()
        stock = r.get("stock_code") or r.get("stock_code_raw") or ""
        if not stock or direction not in ("buy", "sell"):
            continue
        shares = _to_float(r.get("shares")) or 0.0
        if shares <= 0:
            continue
        _, amount, fees, net_amount, buy_cost, sell_rev = _row_cash_components(r)
        key = (r.get("account_id") or account, stock)
        if direction == "buy":
            positions[key].append(
                Lot(
                    shares=shares,
                    cost_total=buy_cost,
                    buy_date=r.get("biz_date") or "",
                    thesis_id=r.get("thesis_id"),
                )
            )
            continue

        remaining = shares
        rev_per_share = (sell_rev / shares) if shares > 0 else 0.0
        matched_cost = 0.0
        matched_shares = 0.0
        buy_thesis_ids = set()
        matched_buy_dates: list[str] = []
        while remaining > 1e-9 and positions[key]:
            lot = positions[key][0]
            take = min(lot.shares, remaining)
            cost_part = lot.cost_total * (take / lot.shares)
            lot.shares -= take
            lot.cost_total -= cost_part
            if lot.shares <= 1e-9:
                positions[key].pop(0)
            remaining -= take
            matched_cost += cost_part
            matched_shares += take
            if lot.buy_date:
                matched_buy_dates.append(lot.buy_date)
            if lot.thesis_id is not None:
                buy_thesis_ids.add(lot.thesis_id)

        if remaining > 1e-9:
            if r.get("biz_date") in current4:
                unmatched_sells_current.append({"row": r, "unmatched_shares": remaining})
            continue

        realized = (rev_per_share * matched_shares) - matched_cost
        ev = {
            "biz_date": r.get("biz_date"),
            "stock_code": stock,
            "stock_name": r.get("stock_name"),
            "shares": matched_shares,
            "realized_pnl": realized,
            "sell_thesis_id": r.get("thesis_id"),
            "buy_thesis_ids": sorted(buy_thesis_ids),
            "buy_dates": sorted(set(matched_buy_dates)),
        }
        if ev["biz_date"] in current4:
            realized_events_current.append(ev)
        if ev["biz_date"] in prev4:
            realized_events_prev.append(ev)

    realized_by_day: dict[str, float] = defaultdict(float)
    for e in realized_events_current:
        realized_by_day[e["biz_date"]] += float(e["realized_pnl"])
    current_realized_total = sum(realized_by_day.get(d, 0.0) for d in current4)
    realized_prev_total = sum(float(e["realized_pnl"]) for e in realized_events_prev)

    pnls = [float(e["realized_pnl"]) for e in realized_events_current]
    wins = [p for p in pnls if p > 1e-9]
    losses = [p for p in pnls if p < -1e-9]
    win_rate = (len(wins) / len(pnls)) if pnls else None
    avg_win = (sum(wins) / len(wins)) if wins else None
    avg_loss = (sum(losses) / len(losses)) if losses else None
    # 与 profit_factor 同口径：全胜无亏损 → ∞（而非 -）；全亏无盈利 → 0.0；无样本 → None。
    pl_ratio = (
        (avg_win / abs(avg_loss)) if (wins and losses)
        else (float("inf") if wins else (0.0 if losses else None))
    )
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (None if not wins else float("inf"))
    max_win = max(wins) if wins else None
    max_loss = min(losses) if losses else None

    loss_concentration = None
    if losses:
        abs_losses = sorted([abs(x) for x in losses], reverse=True)
        total_abs_loss = sum(abs_losses)
        loss_concentration = (abs_losses[0] / total_abs_loss) if total_abs_loss else None

    rows_current = [r for r in window_rows if r.get("biz_date") in current4]
    weak_sell_checks = _analyze_weak_sell_order(
        rows=rows_current,
        current_days=current4,
        prior_day_by_day=prior_day_by_day,
        snapshots=holding_snapshots,
        history_complete=not window_truncated,
    )
    weak_sell_summary = _summarize_weak_sell_checks(
        weak_sell_checks,
        history_complete=not window_truncated,
    )
    thesis_nonnull = [r for r in rows_current if r.get("thesis_id") is not None]
    thesis_coverage = (len(thesis_nonnull) / len(rows_current)) if rows_current else None

    need_thesis_review = 0
    for e in realized_events_current:
        tid = e.get("sell_thesis_id")
        t = thesis_by_id.get(tid) if tid is not None else None
        status = (t.get("status") if t else None)
        if tid is None or status in ("closed", "archived"):
            need_thesis_review += 1

    # quick in/out heuristics (by trade-day distance using calendar indices when available)
    same_day_flip_n = 0
    next_trade_day_flip_n = 0
    for e in realized_events_current:
        sell_day = e.get("biz_date")
        buy_days = e.get("buy_dates") or []
        if not sell_day or not buy_days:
            continue
        if sell_day in buy_days:
            same_day_flip_n += 1
        latest_buy_day = max(buy_days)
        if sell_day in trade_day_idx and latest_buy_day in trade_day_idx:
            if trade_day_idx[sell_day] - trade_day_idx[latest_buy_day] == 1:
                next_trade_day_flip_n += 1

    # unclosed clues: remaining lots that were bought in current window
    unclosed_clues = []
    for (acct, stock), lots in positions.items():
        if any(l.buy_date in current4 for l in lots):
            unclosed_clues.append({"stock_code": stock, "shares": sum(l.shares for l in lots)})
    unclosed_clue_n = len(unclosed_clues)

    # same-day switch heuristic: both buy and sell happen, with different stock sets
    same_day_switch_days: list[dict[str, Any]] = []
    for s in current_day_summaries:
        bought = {a.get("stock_code") for a in s["actions"] if (a.get("direction") or "").lower() == "buy" and a.get("stock_code")}
        sold = {a.get("stock_code") for a in s["actions"] if (a.get("direction") or "").lower() == "sell" and a.get("stock_code")}
        if bought and sold and bought != sold:
            same_day_switch_days.append({"day": s["day"], "bought": sorted(bought), "sold": sorted(sold)})

    # thesis anomalies in current rows
    thesis_anomaly_rows: list[dict[str, Any]] = []
    for r in rows_current:
        tid = r.get("thesis_id")
        if tid is None:
            thesis_anomaly_rows.append({"biz_date": r.get("biz_date"), "stock_code": r.get("stock_code"), "direction": r.get("direction"), "reason": "缺少 thesis_id"})
            continue
        t = thesis_by_id.get(tid) or {}
        if (t.get("status") or "") in ("closed", "archived"):
            thesis_anomaly_rows.append(
                {"biz_date": r.get("biz_date"), "stock_code": r.get("stock_code"), "direction": r.get("direction"), "reason": f"thesis 状态为 {t.get('status')}"}
            )

    # rhythm lines (merged)
    rhythm_lines = []
    for s in current_day_summaries:
        day = s["day"]
        realized = realized_by_day.get(day, 0.0)
        merged_actions = _format_actions(s["actions"])
        rhythm_lines.append(
            f"- {day}：买入{s['buy_n']} 卖出{s['sell_n']}；买入金额{_format_money(s['buy_amount'])} "
            f"卖出金额{_format_money(s['sell_amount'])}；净现金流(近似){_format_money(s['net_cashflow'])}；"
            f"当日闭环盈亏(近似){_format_money(realized)}；主要动作：{merged_actions}"
        )

    # trend table (merged)
    trend_rows = []
    for s in current_day_summaries:
        day = s["day"]
        realized = realized_by_day.get(day, 0.0)
        trend_rows.append(
            [
                day,
                str(int(s["buy_n"])),
                str(int(s["sell_n"])),
                _format_money(s["buy_amount"]),
                _format_money(s["sell_amount"]),
                _format_money(s["net_cashflow"]),
                _format_money(realized),
                _format_actions(s["actions"]),
            ]
        )
    trend_table = md_table(
        ["交易日", "买入笔数", "卖出笔数", "买入金额", "卖出金额", "净现金流(近似)", "当日闭环盈亏(近似)", "主要动作"],
        trend_rows,
    )

    # compare table (keep explanation empty; human to fill)
    compare_metrics = [
        ("成交笔数", current_agg["n"], prev_agg["n"] if prev_agg else None),
        ("买入金额", current_agg["buy_amount"], prev_agg["buy_amount"] if prev_agg else None),
        ("卖出金额", current_agg["sell_amount"], prev_agg["sell_amount"] if prev_agg else None),
        ("净现金流(近似)", current_agg["net_cashflow"], prev_agg["net_cashflow"] if prev_agg else None),
        ("交易费用", current_agg["fees"], prev_agg["fees"] if prev_agg else None),
        ("近似已实现净盈亏", current_realized_total, realized_prev_total if prev4 else None),
        ("胜率(按卖出笔)", win_rate, None),
        ("盈亏比(按卖出笔)", pl_ratio, None),
        ("Profit Factor(按卖出笔)", profit_factor if isinstance(profit_factor, float) else None, None),
        ("平均盈利", avg_win, None),
        ("平均亏损", avg_loss, None),
        ("最大单笔盈利", max_win, None),
        ("最大单笔亏损", max_loss, None),
        ("亏损集中度(Top1/总亏损)", loss_concentration, None),
        ("未闭环线索数", float(unclosed_clue_n), None),
        ("需核查 thesis_review 的闭环交易数(启发式)", float(need_thesis_review), None),
    ]
    cmp_rows = []
    for name, cur, prev in compare_metrics:
        def fmt(v: Any) -> str:
            if v is None:
                return "-"
            if v == float("inf"):
                return "∞"
            if isinstance(v, float):
                return _format_money(v) if abs(v) >= 1 else f"{v:.4f}"
            return str(v)

        if prev is None:
            cmp_rows.append([name, fmt(cur), "样本不足", "样本不足", "样本不足", ""])
        else:
            d = float(cur) - float(prev)
            direction = "上升" if d > 0 else ("下降" if d < 0 else "持平")
            cmp_rows.append([name, fmt(cur), fmt(prev), fmt(d), direction, ""])
    compare_table = md_table(["指标", "本期(4日)", "上期(4日)", "变化量", "方向", "需要关注的解释"], cmp_rows)

    # thesis cross reference table
    thesis_refs: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "rows": 0,
            "stocks": set(),
            "status": None,
            "trade_mode": None,
            "sector": None,
            "failure_condition": None,
            "planned_position_pct": None,
        }
    )
    for r in rows_current:
        tid = r.get("thesis_id")
        if tid is None:
            continue
        t = thesis_by_id.get(tid) or {}
        ref = thesis_refs[int(tid)]
        ref["rows"] += 1
        if r.get("stock_code"):
            ref["stocks"].add(r.get("stock_code"))
        ref["status"] = t.get("status")
        ref["trade_mode"] = t.get("trade_mode")
        ref["sector"] = t.get("sector")
        ref["failure_condition"] = t.get("failure_condition")
        ref["planned_position_pct"] = t.get("planned_position_pct")

    thesis_md = (
        md_table(
            ["thesis_id", "本期相关流水笔数", "相关股票", "trade_mode", "sector", "planned_position_pct", "failure_condition", "status"],
            [
                [
                    str(tid),
                    str(ref["rows"]),
                    ",".join(sorted(ref["stocks"])) or "-",
                    str(ref["trade_mode"] or "-"),
                    str(ref["sector"] or "-"),
                    str(ref["planned_position_pct"] if ref["planned_position_pct"] is not None else "-"),
                    str(ref["failure_condition"] or "-"),
                    str(ref["status"] or "-"),
                ]
                for tid, ref in sorted(thesis_refs.items())
            ],
        )
        if thesis_refs
        else "（本期无 thesis_id 关联流水）"
    )

    unclosed_md = (
        md_table(
            ["stock_code", "剩余未闭环线索 shares(近似)"],
            [[c["stock_code"], f"{c['shares']:.0f}"] for c in sorted(unclosed_clues, key=lambda x: -x["shares"])],
        )
        if unclosed_clues
        else "（本期无新增未闭环线索）"
    )

    # report path (allowed write)
    report_dir = PROJECT_ROOT / "tmp" / "daily-trade-reviews"
    report_path = report_dir / f"{run_date_str}-four-trading-day-review.md"
    report_dir.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# 最近 4 个交易日交易复盘（运行日 {run_date_str}，Asia/Shanghai）")
    lines.append("")
    lines.append("## 本次范围与数据来源")
    lines.append(f"- [事实] 本期窗口（最近 4 个A股交易日）：{current_start} ~ {current_end}")
    lines.append(
        f"- [事实] 上期对照窗口（上一组 4 个A股交易日）：{prev_start} ~ {prev_end}"
        if prev4
        else "- [事实] 上期对照窗口：样本不足"
    )
    lines.append(
        "- [事实] 券商成交流水读取命令：python3 scripts/main.py executions list --from <date> --to <date> --account default --limit 10000 --json"
    )
    lines.append(
        "- [事实] 交易思路中间层读取命令：python3 scripts/main.py db thesis-list --account default --json"
    )
    lines.append(
        "- [事实] 卖弱顺序检查读取严格上一交易日 daily_market.raw_data.holdings_data，"
        "并按券商 exec_time / id 正序回放当日成交。"
    )
    if sample_limit_note:
        lines.append(f"- [事实] 样本限制：{sample_limit_note}")
    lines.append("")

    lines.append("## 数据新鲜度检查")
    lines.append(f"- [事实] 最近成交日期（biz_date max）：{latest_trade_date or '-'}")
    lines.append(f"- [事实] 最近导入时间 imported_at（max）：{latest_imported_at or '-'}")
    lines.append(f"- [事实] 最近 import_run_id：{latest_import_run_id or '-'}")
    lines.append(f"- [事实] 最近 source_archive_path：{latest_source_archive_path or '-'}")
    if latest_trade_date and latest_trade_date < current_end:
        lines.append(
            f"- [判断] 最近成交日期早于报告截止日 {current_end}：这可能是最近交易日无成交，也可能是数据尚未导入；不直接假设缺失。"
        )
    lines.append("")

    lines.append("## 交易事实摘要")
    summary_rows = [
        [
            f"本期 {current_start}~{current_end}",
            str(int(current_agg["n"])),
            str(int(current_agg["buy_n"])),
            str(int(current_agg["sell_n"])),
            _format_money(current_agg["buy_amount"]),
            _format_money(current_agg["sell_amount"]),
            _format_money(current_agg["fees"]),
            _format_money(current_agg["net_cashflow"]),
        ]
    ]
    if prev_agg:
        summary_rows.append(
            [
                f"上期 {prev_start}~{prev_end}",
                str(int(prev_agg["n"])),
                str(int(prev_agg["buy_n"])),
                str(int(prev_agg["sell_n"])),
                _format_money(prev_agg["buy_amount"]),
                _format_money(prev_agg["sell_amount"]),
                _format_money(prev_agg["fees"]),
                _format_money(prev_agg["net_cashflow"]),
            ]
        )
    lines.append(
        md_table(
            ["窗口", "成交笔数", "买入笔数", "卖出笔数", "买入金额", "卖出金额", "交易费用", "净现金流(近似)"],
            summary_rows,
        )
    )
    lines.append("")

    lines.append("## 按交易日拆分节奏")
    lines += rhythm_lines
    lines.append("")

    lines.append("## 核心指标变化趋势")
    lines.append("### 当前 4 个交易日日度趋势表")
    lines.append(trend_table)
    lines.append("")
    lines.append("### 本期 vs 上期 环比表")
    lines.append(compare_table)
    lines.append("")

    lines.append("## 已闭环交易表现表")
    lines.append(
        md_table(
            ["指标", "值", "说明"],
            [
                ["近似已实现净盈亏", _format_money(current_realized_total), "按 FIFO 匹配卖出笔近似计算"],
                ["胜率(按卖出笔)", f"{win_rate*100:.1f}%" if win_rate is not None else "-", "realized_pnl>0 计胜"],
                ["平均盈利", _format_money(avg_win) if avg_win is not None else "-", ""],
                ["平均亏损", _format_money(avg_loss) if avg_loss is not None else "-", ""],
                ["盈亏比", _format_ratio(pl_ratio), "avg_win / |avg_loss|"],
                [
                    "Profit Factor",
                    ("∞" if profit_factor == float("inf") else (f"{profit_factor:.2f}" if isinstance(profit_factor, float) else "-")),
                    "sum(win)/|sum(loss)|",
                ],
                ["最大单笔盈利", _format_money(max_win) if max_win is not None else "-", ""],
                ["最大单笔亏损", _format_money(max_loss) if max_loss is not None else "-", ""],
                ["亏损集中度", f"{loss_concentration*100:.1f}%" if loss_concentration is not None else "-", "Top1亏损 / 总亏损(绝对值)"],
            ],
        )
    )
    lines.append("")

    lines.append("## 收益集中度与风险点")
    lines.append(
        md_table(
            ["指标", "值", "说明"],
            [
                ["亏损集中度(Top1/总亏损)", f"{loss_concentration*100:.1f}%" if loss_concentration is not None else "-", "越高表示亏损更集中"],
                ["同日快进快出次数(按卖出笔)", str(same_day_flip_n), "卖出匹配到同日买入"],
                ["隔日快进快出次数(按卖出笔)", str(next_trade_day_flip_n), "卖出匹配到上一个交易日买入(需交易日历)"],
                ["同日切仓日数(启发式)", str(len(same_day_switch_days)), "同日既买又卖且股票集合不同"],
                ["thesis_id 覆盖率(按流水行)", f"{thesis_coverage*100:.1f}%" if thesis_coverage is not None else "-", "本期流水行 thesis_id 非空比例"],
                ["需核查 thesis_review 的闭环交易数(启发式)", str(need_thesis_review), "卖出 thesis 缺失或状态已关闭"],
            ],
        )
    )
    if same_day_switch_days:
        lines.append("")
        lines.append("### 同日切仓日明细(启发式)")
        for x in same_day_switch_days:
            lines.append(f"- {x['day']}：卖出 {','.join(x['sold'])}；买入 {','.join(x['bought'])}")
    lines.append("")

    lines.append("## 是否优先卖出最弱个股")
    lines.append(
        "- [事实] 计算口径：以卖出日前一交易日收盘快照的当日涨跌幅作为相对强弱代理，"
        "数值越低视为越弱；相邻拆单，以及经跨来源与成交号关联确认的“拆分+汇总”重复行，"
        "合并为一次卖出选择。"
    )
    lines.append(
        "- [判断] 这是无前视的“前一收盘日弱势代理”，不等于成交时刻强弱，也不覆盖"
        "止损、失效、止盈、流动性等计划性退出理由；出现“有更弱未先卖”时需结合原计划复核。"
    )
    lines.append(
        _render_weak_sell_checks(
            weak_sell_checks,
            history_complete=not window_truncated,
        )
    )
    lines.append("")

    lines.append("## 值得重点复盘的 3-6 个问题")
    focus_questions: list[str] = []
    big_pnls = sorted(realized_events_current, key=lambda e: abs(float(e.get("realized_pnl") or 0.0)), reverse=True)
    for e in big_pnls[:3]:
        pnl = float(e.get("realized_pnl") or 0.0)
        focus_questions.append(
            f"{e.get('biz_date')} {e.get('stock_code')} 已闭环盈亏(近似){_format_money(pnl)}：对应 thesis_id={e.get('sell_thesis_id') or '-'}；是否与 thesis 的 failure_condition / planned_position_pct 一致？"
        )
    if same_day_flip_n or next_trade_day_flip_n:
        focus_questions.append("本期存在快进快出：同日/隔日次数偏高时，是否反映了主线不清、止损/止盈执行不一致或追涨杀跌？")
    if thesis_anomaly_rows:
        focus_questions.append("本期存在 thesis 异常流水（缺 thesis_id 或 thesis 已关闭仍交易）：是否需要补齐 thesis_review / 重新归因？")
    if unmatched_sells_current:
        focus_questions.append("本期存在卖出无法在近180日历史中完成 FIFO 匹配：是否意味着更早买入未覆盖、导入不全或标的代码映射异常？")
    if weak_sell_summary["weaker_retained"]:
        focus_questions.append(
            f"本期有 {weak_sell_summary['weaker_retained']} 次卖出时仍有上一交易日表现更弱的持仓未先卖："
            "请核对是否由止损、失效、止盈或流动性等计划性原因触发。"
        )
    if not weak_sell_summary["history_complete"]:
        focus_questions.append(
            "卖弱顺序所需的复盘窗口流水已达到读取上限，无法确认卖出动作是否完整；"
            "需扩大流水上限后再判。"
        )
    elif weak_sell_summary["unknown"]:
        focus_questions.append(
            f"卖弱顺序检查有 {weak_sell_summary['unknown']} 次因持仓快照、行情或成交顺序不完整而无法判断，"
            "缺失样本不计入符合率。"
        )
    if not focus_questions:
        focus_questions.append("本期无成交或无闭环卖出：重点复盘是否遵守了交易窗口节奏、是否有计划外下单。")
    for q in focus_questions[:6]:
        lines.append(f"- [判断] {q}")
    lines.append("")

    lines.append("## 需要用户补充确认的问题")
    confirm_qs: list[str] = []
    if latest_trade_date and latest_trade_date < current_end:
        confirm_qs.append(
            f"报告截止日为 {current_end}，但最近成交日期为 {latest_trade_date}：请确认 {latest_trade_date}~{current_end} 是否确实无成交，或是否尚未导入。"
        )
    if thesis_anomaly_rows:
        confirm_qs.append("请确认本期 thesis 异常流水是否应补齐 thesis_id（或说明其不需要归入任何 thesis）。")
    if unmatched_sells_current:
        confirm_qs.append("请确认是否需要扩大 FIFO 历史窗口（当前为近180日），以覆盖更早的买入成本。")
    confirm_qs.append("如需更精确的已实现盈亏口径，请确认是否以券商对账单/资金流水口径为准（本报告为 FIFO 近似）。")
    for q in confirm_qs[:6]:
        lines.append(f"- [判断] {q}")
    lines.append("")

    lines.append("## 未闭环的新买入/剩余券商流水仓位线索")
    lines.append(unclosed_md)
    lines.append("")

    lines.append("## 交易思路对照")
    lines.append(thesis_md)
    lines.append("")

    open_theses = [t for t in theses if (t.get("status") or "") == "open"]
    lines.append("## 持仓失效自查清单（需人工核对）")
    lines.append(
        "- [判断] 以下为当前 open 状态的交易思路（不限本期窗口），请逐条人工核对失效条件/止损/目标价是否已触发；"
        "本清单不取行情、不替代你的判断。"
    )
    lines.append(_render_failure_self_check(open_theses, run_date))
    lines.append("")

    # 纪律执行回顾：本期相关已平仓 thesis（窗口内有流水 或 closed_at 落窗口）对照 thesis_review
    reviews = _load_thesis_reviews()
    window_thesis_ids = set(thesis_refs.keys())
    relevant_closed: list[dict] = []
    relevant_ids: set[int] = set()
    for t in theses:
        if (t.get("status") or "") != "closed" or t.get("id") is None:
            continue
        closed_at = t.get("closed_at")
        in_window = bool(closed_at and current_start <= closed_at <= current_end)
        if int(t["id"]) in window_thesis_ids or in_window:
            relevant_closed.append(t)
            relevant_ids.add(int(t["id"]))
    unreviewed: list[dict] = []
    deviation: list[tuple[dict, dict]] = []
    for t in relevant_closed:
        rev = reviews.get(int(t["id"]))
        if rev is None:
            unreviewed.append(t)
            continue
        score = rev.get("discipline_score")
        if rev.get("executed_as_planned") in (0, 2) or (score is not None and score <= 2):
            deviation.append((t, rev))
    backlog_unreviewed_n = sum(
        1
        for t in theses
        if (t.get("status") or "") == "closed"
        and t.get("id") is not None
        and int(t["id"]) not in reviews
        and int(t["id"]) not in relevant_ids
    )
    lines.append("## 纪律执行回顾")
    lines.append(
        "- [判断] 以下基于 thesis_review 纪律记录，回顾本期相关已平仓交易的复盘缺口与偏离。"
    )
    lines += _render_discipline_review(
        relevant_closed=relevant_closed,
        unreviewed=unreviewed,
        deviation=deviation,
        backlog_n=backlog_unreviewed_n,
    )
    lines.append("")

    lines.append("## 样本限制")
    lines.append(
        "- [事实] 交易动作与盈亏以券商成交流水为事实层；"
        "卖弱顺序另使用严格上一交易日的 daily_market 持仓行情快照。"
    )
    lines.append("- [事实] 已实现盈亏为 FIFO 近似：优先使用 net_amount；缺失时用 amount 与 total_fees 近似，可能与券商口径存在偏差。")
    lines.append(
        "- [事实] 卖弱顺序只比较严格上一交易日持仓快照中的当日涨跌幅；"
        "快照缺失、快照非当日收盘后生成、快照后补录了更早成交、任一持仓行情缺失或"
        "成交先后不明确时均不判符合。"
    )
    lines.append(
        "- [判断] 卖弱顺序结论是盘前已知信息的相对强弱代理，不是具体卖出建议；"
        "若要核验成交时刻强弱，需要保存同一时刻的全组合分钟行情快照。"
    )
    if fifo_truncated:
        lines.append(f"- [事实] FIFO 历史读取达到 {limit} 条上限，存在被截断风险；若被截断，已实现盈亏/未闭环线索等可能失真。")
    if window_truncated:
        lines.append(
            f"- [事实] 复盘窗口流水读取达到 {limit} 条上限；"
            "卖弱顺序统一标记为持仓回放不完整，不判符合。"
        )
    if sample_limit_note:
        lines.append(f"- [事实] {sample_limit_note}。")
    if unmatched_sells_current:
        lines.append(f"- [事实] FIFO 匹配存在 {len(unmatched_sells_current)} 笔本期卖出无法在近 { (run_date - timedelta(days=180)).isoformat() } 起的历史中找到足够买入匹配，可能是更早历史或导入不全。")
    lines.append("- [判断] “需核查 thesis_review 的闭环交易数”为启发式提示：仅基于 thesis_id 是否缺失/状态是否已关闭，不能替代人工核查。")

    report_md = "\n".join(lines) + "\n"
    report_path.write_text(report_md, encoding="utf-8")

    push_ok = False
    push_err = None
    if push:
        try:
            from scripts.pushers.dingtalk_pusher import DingTalkPusher

            pusher = DingTalkPusher({})
            if not pusher.initialize():
                push_err = "DingTalk 未启用（缺少 DINGTALK_WEBHOOK_TOKEN/SECRET）"
            else:
                summary = _build_dingtalk_summary(
                    current_start=current_start,
                    current_end=current_end,
                    current_agg=current_agg,
                    prev_agg=prev_agg,
                    current_day_summaries=current_day_summaries,
                    realized_by_day=realized_by_day,
                    realized_events_current=realized_events_current,
                    current_realized_total=current_realized_total,
                    realized_prev_total=realized_prev_total,
                    win_rate=win_rate,
                    pl_ratio=pl_ratio,
                    profit_factor=profit_factor if isinstance(profit_factor, float) else None,
                    max_win=max_win,
                    max_loss=max_loss,
                    unclosed_clue_n=unclosed_clue_n,
                    unclosed_clues=unclosed_clues,
                    thesis_anomaly_rows=thesis_anomaly_rows,
                    same_day_flip_n=same_day_flip_n,
                    next_trade_day_flip_n=next_trade_day_flip_n,
                    open_check_n=len(open_theses),
                    unreviewed_n=len(unreviewed),
                    deviation_n=len(deviation),
                    weak_sell_summary=weak_sell_summary,
                    report_path=report_path,
                )
                push_ok = pusher.send_markdown("最近4个交易日交易复盘", summary)
                if not push_ok:
                    push_err = "DingTalk 推送失败（见本地运行日志；已脱敏）"
        except Exception as e:
            push_ok = False
            push_err = f"DingTalk 推送异常: {type(e).__name__}: {str(e)[:200]}"

        status_md = "\n\n---\n\n## 钉钉推送状态\n"
        status_md += "- [事实] 推送结果：成功\n" if push_ok else f"- [事实] 推送结果：失败\n- [事实] 失败原因：{push_err or '-'}\n"
        report_path.write_text(report_md + status_md, encoding="utf-8")

    return {
        "report_path": str(report_path),
        "push_ok": push_ok,
        "push_err": push_err,
        "current_days": current4,
        "current_window": [current_start, current_end],
        "prev_window": [prev_start, prev_end] if prev4 else None,
        "weak_sell_summary": weak_sell_summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="生成最近4个交易日交易复盘（只读业务数据）")
    parser.add_argument("--account", default="default")
    parser.add_argument("--limit", type=int, default=10000)
    parser.add_argument("--date", dest="run_date", help="运行日(Asia/Shanghai) YYYY-MM-DD，默认取当前日期")
    parser.add_argument("--push", action="store_true", help="推送钉钉短摘要（需要环境变量凭据）")
    args = parser.parse_args()

    if args.run_date:
        run_date = datetime.strptime(args.run_date, "%Y-%m-%d").date()
    else:
        run_date = datetime.now(RUN_TZ).date()

    result = generate(run_date=run_date, account=args.account, limit=args.limit, push=bool(args.push))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
