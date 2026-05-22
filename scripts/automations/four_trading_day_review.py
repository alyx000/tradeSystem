from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_TZ = ZoneInfo("Asia/Shanghai")


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


def _to_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None


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


def _merge_actions(day_rows: list[dict]) -> str:
    return _format_actions(_group_trade_actions(day_rows))


def _row_key_time(r: dict) -> tuple:
    return (r.get("biz_date") or "", r.get("exec_time") or "", r.get("id") or 0)


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

    window_rows = _run_json(
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

    fifo_start = (run_date - timedelta(days=180)).isoformat()
    fifo_rows = _run_json(
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
    fifo_truncated = len(fifo_rows) >= limit

    theses = _run_json(
        ["python3", "scripts/main.py", "db", "thesis-list", "--account", account, "--json"]
    ) or []
    thesis_by_id = {t.get("id"): t for t in theses if t.get("id") is not None}

    # data freshness
    latest_trade_date = None
    latest_imported_at = None
    latest_import_run_id = None
    latest_source_archive_path = None
    if fifo_rows:
        dates = [r.get("biz_date") for r in fifo_rows if r.get("biz_date")]
        latest_trade_date = max(dates) if dates else None
        imported_parsed = [(_parse_dt(r.get("imported_at")), r) for r in fifo_rows if r.get("imported_at")]
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
    pl_ratio = (avg_win / abs(avg_loss)) if (avg_win is not None and avg_loss) else None
    profit_factor = (sum(wins) / abs(sum(losses))) if losses else (None if not wins else float("inf"))
    max_win = max(wins) if wins else None
    max_loss = min(losses) if losses else None

    loss_concentration = None
    if losses:
        abs_losses = sorted([abs(x) for x in losses], reverse=True)
        total_abs_loss = sum(abs_losses)
        loss_concentration = (abs_losses[0] / total_abs_loss) if total_abs_loss else None

    rows_current = [r for r in window_rows if r.get("biz_date") in current4]
    thesis_nonnull = [r for r in rows_current if r.get("thesis_id") is not None]
    thesis_coverage = (len(thesis_nonnull) / len(rows_current)) if rows_current else None

    need_thesis_review = 0
    for e in realized_events_current:
        tid = e.get("sell_thesis_id")
        t = thesis_by_id.get(tid) if tid is not None else None
        status = (t.get("status") if t else None)
        if tid is None or status in ("closed", "archived"):
            need_thesis_review += 1

    # unclosed clues: remaining lots that were bought in current window
    unclosed_clues = []
    for (acct, stock), lots in positions.items():
        if any(l.buy_date in current4 for l in lots):
            unclosed_clues.append({"stock_code": stock, "shares": sum(l.shares for l in lots)})
    unclosed_clue_n = len(unclosed_clues)

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
                ["盈亏比", f"{pl_ratio:.2f}" if pl_ratio is not None else "-", "avg_win / |avg_loss|"],
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

    lines.append("## 未闭环的新买入/剩余券商流水仓位线索")
    lines.append(unclosed_md)
    lines.append("")

    lines.append("## 交易思路对照")
    lines.append(thesis_md)
    lines.append("")

    lines.append("## 样本限制")
    lines.append("- [事实] 本报告以券商成交流水为唯一事实层，按指定交易日窗口聚合。")
    lines.append("- [事实] 已实现盈亏为 FIFO 近似：优先使用 net_amount；缺失时用 amount 与 total_fees 近似，可能与券商口径存在偏差。")
    if fifo_truncated:
        lines.append(f"- [事实] FIFO 历史读取达到 {limit} 条上限，存在被截断风险；若被截断，已实现盈亏/未闭环线索等可能失真。")
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
                summary = "\n".join(
                    [
                        f"### 最近4个交易日交易复盘摘要（{current_start}~{current_end}）",
                        "",
                        f"- 成交笔数：{int(current_agg['n'])}（买{int(current_agg['buy_n'])}/卖{int(current_agg['sell_n'])}）",
                        f"- 净现金流(近似)：{_format_money(current_agg['net_cashflow'])}",
                        f"- 近似已实现净盈亏：{_format_money(current_realized_total)}",
                        f"- 胜率/盈亏比/PF：{(f'{win_rate*100:.1f}%' if win_rate is not None else '-')}/{(f'{pl_ratio:.2f}' if pl_ratio is not None else '-')}/{('∞' if profit_factor==float('inf') else (f'{profit_factor:.2f}' if isinstance(profit_factor,float) else '-'))}",
                        f"- 最大盈利/最大亏损：{_format_money(max_win)}/{_format_money(max_loss)}",
                        f"- 未闭环线索数：{unclosed_clue_n}",
                        "",
                        f"本地完整报告：{report_path}",
                    ]
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
