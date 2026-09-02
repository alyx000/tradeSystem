"""低价股赚钱效应历史补采、归档更新与趋势图生成。

历史补采只更新既有 ``post-market.yaml.raw_data.low_price_effect``，并通过
``sync_daily_market_to_db`` 复用盘后 YAML -> SQLite 标准双写链路；不触发持仓、
关注池、监管派生或任何推送。
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DAILY_DIR = PROJECT_ROOT / "daily"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "data" / "reports" / "low-price-effect"
TZ = ZoneInfo("Asia/Shanghai")


def select_trade_dates(conn, end_date: str, days: int) -> list[str]:
    """从本地交易日历精确选取截至 ``end_date`` 的最近 N 个开放日。"""
    datetime.strptime(end_date, "%Y-%m-%d")
    if days < 1 or days > 250:
        raise ValueError("days 必须在 1..250 之间")
    rows = conn.execute(
        """
        SELECT date
        FROM trade_calendar
        WHERE is_open = 1 AND date <= ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (end_date, days),
    ).fetchall()
    dates = [str(row[0]) for row in reversed(rows)]
    if len(dates) != days:
        raise RuntimeError(
            f"交易日历覆盖不足：截至 {end_date} 仅找到 {len(dates)}/{days} 个开放日"
        )
    return dates


def _result_section(result: Any, label: str) -> dict[str, Any]:
    """把涨跌停 DataResult 归一为 analyzer 所需的 count/stocks/source 结构。"""
    source = str(getattr(result, "source", "") or "")
    if not getattr(result, "success", False):
        return {
            "error": str(getattr(result, "error", "") or f"{label}来源失败"),
            "_source": source,
        }
    data = deepcopy(getattr(result, "data", None))
    if isinstance(data, list):
        data = {"count": len(data), "stocks": data}
    if not isinstance(data, dict):
        return {"error": f"{label}事实结构无效", "_source": source}
    stocks = data.get("stocks")
    if stocks is None and int(data.get("count") or 0) == 0:
        stocks = []
        data["stocks"] = stocks
    if isinstance(stocks, list):
        data.setdefault("count", len(stocks))
    data["_source"] = source
    return data


def collect_date_snapshot(registry, trade_date: str) -> dict[str, Any]:
    """只调用低价股统计所需的四个日期键控接口。"""
    from analyzers.low_price_effect import collect_low_price_effect

    try:
        st_result = registry.call("get_stock_st", trade_date)
        limit_up_result = registry.call("get_limit_up_list", trade_date)
        limit_down_result = registry.call("get_limit_down_list", trade_date)
        return collect_low_price_effect(
            registry,
            trade_date,
            stock_st_result=st_result,
            limit_up_section=_result_section(limit_up_result, "涨停"),
            limit_down_section=_result_section(limit_down_result, "跌停"),
        )
    except Exception as exc:  # noqa: BLE001 - 外部来源异常必须状态化，不能中断其余日期。
        return {
            "status": "source_failed",
            "trade_date": trade_date,
            "error": f"历史补采调用异常:{exc}",
            "gaps": [],
        }


def load_post_market_envelope(
    trade_date: str,
    *,
    daily_dir: Path = DEFAULT_DAILY_DIR,
) -> tuple[Path, dict[str, Any]]:
    """读取并校验目标日既有盘后信封；缺失时拒绝凭空构造。"""
    path = Path(daily_dir) / trade_date / "post-market.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"盘后归档不存在:{path}")
    with path.open(encoding="utf-8") as handle:
        envelope = yaml.safe_load(handle) or {}
    if not isinstance(envelope, dict):
        raise ValueError(f"盘后归档不是对象:{path}")
    archived_date = str(envelope.get("date") or "")
    if archived_date and archived_date != trade_date:
        raise ValueError(
            f"盘后归档日期错位:{archived_date} != {trade_date} ({path})"
        )
    raw_data = envelope.get("raw_data")
    if not isinstance(raw_data, dict):
        raise ValueError(f"盘后归档缺少 raw_data 对象:{path}")
    return path, envelope


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _sync_daily_market(
    trade_date: str,
    envelope: dict[str, Any],
    sync_fn: Callable[[str, dict[str, Any]], bool] | None,
) -> bool:
    if sync_fn is None:
        from db.dual_write import sync_daily_market_to_db

        sync_fn = sync_daily_market_to_db
    return bool(sync_fn(trade_date, envelope))


def persist_date_snapshot(
    trade_date: str,
    snapshot: dict[str, Any],
    *,
    input_by: str,
    daily_dir: Path = DEFAULT_DAILY_DIR,
    sync_fn: Callable[[str, dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """原子更新单日 YAML，再走标准 dual-write 同步完整信封到 SQLite。"""
    if not input_by.strip():
        raise ValueError("input_by 不能为空")
    if str(snapshot.get("trade_date") or trade_date) != trade_date:
        raise ValueError("snapshot.trade_date 与目标日不一致")

    path, envelope = load_post_market_envelope(trade_date, daily_dir=daily_dir)
    stored = deepcopy(snapshot)
    stored["trade_date"] = trade_date
    stored["collection_receipt"] = {
        "mode": "historical_backfill",
        "input_by": input_by.strip(),
        "collected_at": datetime.now(TZ).isoformat(),
    }
    envelope["raw_data"]["low_price_effect"] = stored
    body = yaml.safe_dump(
        envelope,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    _atomic_text(path, body)

    db_synced = _sync_daily_market(trade_date, envelope, sync_fn)
    return {
        "status": "persisted" if db_synced else "yaml_only",
        "trade_date": trade_date,
        "source_status": stored.get("status"),
        "yaml_path": str(path),
        "db_synced": db_synced,
    }


_STATUS_RANK = {
    "source_failed": 0,
    "partial": 1,
    "complete": 2,
}


def backfill_history(
    registry,
    trade_dates: list[str],
    *,
    input_by: str,
    refetch: bool = False,
    daily_dir: Path = DEFAULT_DAILY_DIR,
    collect_fn: Callable[[Any, str], dict[str, Any]] = collect_date_snapshot,
    sync_fn: Callable[[str, dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """逐日补采；已有完整块默认幂等跳过，刷新失败时不降级覆盖旧事实。"""
    receipts: list[dict[str, Any]] = []
    for trade_date in trade_dates:
        try:
            _, envelope = load_post_market_envelope(trade_date, daily_dir=daily_dir)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            receipts.append({
                "status": "archive_failed",
                "trade_date": trade_date,
                "error": str(exc),
            })
            continue

        existing = envelope["raw_data"].get("low_price_effect")
        existing_status = (
            str(existing.get("status") or "") if isinstance(existing, dict) else ""
        )
        if existing_status == "complete" and not refetch:
            try:
                db_synced = _sync_daily_market(trade_date, envelope, sync_fn)
            except Exception as exc:  # noqa: BLE001 - 单日双写失败不能中断后续日期。
                receipts.append({
                    "status": "sync_failed",
                    "trade_date": trade_date,
                    "source_status": "complete",
                    "db_synced": False,
                    "error": str(exc),
                })
            else:
                receipts.append({
                    "status": "already_complete" if db_synced else "sync_failed",
                    "trade_date": trade_date,
                    "source_status": "complete",
                    "db_synced": db_synced,
                })
            continue

        snapshot = collect_fn(registry, trade_date)
        new_status = str(snapshot.get("status") or "source_failed")
        if (
            existing_status in _STATUS_RANK
            and _STATUS_RANK.get(new_status, -1) < _STATUS_RANK[existing_status]
        ):
            receipt = {
                "status": "fallback_preserved",
                "trade_date": trade_date,
                "source_status": new_status,
                "preserved_status": existing_status,
                "error": snapshot.get("error"),
            }
            try:
                receipt["db_synced"] = _sync_daily_market(
                    trade_date, envelope, sync_fn
                )
            except Exception as exc:  # noqa: BLE001 - 保留事实仍可继续处理其他日期。
                receipt["db_synced"] = False
                receipt["sync_error"] = str(exc)
            receipts.append(receipt)
            continue
        try:
            receipt = persist_date_snapshot(
                trade_date,
                snapshot,
                input_by=input_by,
                daily_dir=daily_dir,
                sync_fn=sync_fn,
            )
        except Exception as exc:  # noqa: BLE001 - YAML/SQLite 单日异常必须隔离。
            receipt = {
                "status": "write_failed",
                "trade_date": trade_date,
                "source_status": new_status,
                "error": str(exc),
            }
        receipts.append(receipt)
    return receipts


def _percent(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value) * 100
    except (TypeError, ValueError):
        return None
    return round(number, 2) if math.isfinite(number) else None


def build_trend_rows(
    trade_dates: list[str],
    *,
    daily_dir: Path = DEFAULT_DAILY_DIR,
) -> list[dict[str, Any]]:
    """从最终盘后归档重建趋势表；非 complete 行保留状态但数值不用于连线。"""
    rows: list[dict[str, Any]] = []
    for trade_date in trade_dates:
        try:
            _, envelope = load_post_market_envelope(trade_date, daily_dir=daily_dir)
            block = envelope["raw_data"].get("low_price_effect") or {}
        except (OSError, ValueError, yaml.YAMLError) as exc:
            rows.append({
                "trade_date": trade_date,
                "status": "missing",
                "gaps": str(exc),
            })
            continue
        if not isinstance(block, dict):
            rows.append({
                "trade_date": trade_date,
                "status": "missing",
                "gaps": "low_price_effect 结构无效",
            })
            continue
        status = str(block.get("status") or "missing")
        low = block.get("low_price") if isinstance(block.get("low_price"), dict) else {}
        market = (
            block.get("market_benchmark")
            if isinstance(block.get("market_benchmark"), dict)
            else {}
        )
        coverage = block.get("coverage") if isinstance(block.get("coverage"), dict) else {}
        raw_gaps = block.get("gaps") or []
        gaps = (
            [str(item) for item in raw_gaps]
            if isinstance(raw_gaps, list)
            else [str(raw_gaps)]
        )
        required_metrics = {
            "sample_count": low.get("sample_count"),
            "low_price_median_pct": low.get("pct_chg_median"),
            "market_median_pct": market.get("pct_chg_median"),
            "advance_rate": low.get("advance_rate"),
            "amount_share_pct": low.get("amount_share_pct"),
        }
        missing_metrics = [
            key for key, value in required_metrics.items() if value is None
        ]
        if status == "complete" and missing_metrics:
            status = "archive_invalid"
            gaps.append("complete 归档缺少趋势字段:" + ",".join(missing_metrics))
        row = {
            "trade_date": trade_date,
            "status": status,
            "sample_count": low.get("sample_count"),
            "low_price_median_pct": low.get("pct_chg_median"),
            "market_median_pct": market.get("pct_chg_median"),
            "median_excess_pp": low.get("median_excess_vs_market_pp"),
            "advance_rate_pct": _percent(low.get("advance_rate")),
            "amount_share_pct": low.get("amount_share_pct"),
            "strong_gain_rate_pct": _percent(low.get("strong_gain_rate")),
            "strong_loss_rate_pct": _percent(low.get("strong_loss_rate")),
            "limit_up_rate_pct": _percent(low.get("limit_up_rate")),
            "limit_down_rate_pct": _percent(low.get("limit_down_rate")),
            "unique_quote_count": coverage.get("unique_quote_count"),
            "gaps": " | ".join(gaps),
        }
        if status != "complete":
            # CSV 保留可审计的覆盖字段，但图表消费端只连接 complete 值。
            row["chart_eligible"] = False
        else:
            row["chart_eligible"] = True
        rows.append(row)
    return rows


_CSV_FIELDS = [
    "trade_date",
    "status",
    "chart_eligible",
    "sample_count",
    "low_price_median_pct",
    "market_median_pct",
    "median_excess_pp",
    "advance_rate_pct",
    "amount_share_pct",
    "strong_gain_rate_pct",
    "strong_loss_rate_pct",
    "limit_up_rate_pct",
    "limit_down_rate_pct",
    "unique_quote_count",
    "gaps",
]


def _chart_value(row: dict[str, Any], key: str) -> float:
    if row.get("status") != "complete" or row.get(key) is None:
        return math.nan
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return math.nan


def render_trend_chart(rows: list[dict[str, Any]], output_path: Path) -> None:
    """生成三联静态趋势图；缺失/失败状态用断线表达，不补零、不插值。"""
    if not rows:
        raise ValueError("趋势图至少需要 1 行数据")
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import font_manager, pyplot as plt

    preferred_fonts = ["PingFang SC", "Heiti TC", "Arial Unicode MS"]
    for font_name in preferred_fonts:
        try:
            font_manager.findfont(font_name, fallback_to_default=False)
        except ValueError:
            continue
        plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
        break
    plt.rcParams["axes.unicode_minus"] = False

    x = list(range(len(rows)))
    labels = [str(row["trade_date"])[5:] for row in rows]
    low_median = [_chart_value(row, "low_price_median_pct") for row in rows]
    market_median = [_chart_value(row, "market_median_pct") for row in rows]
    advance = [_chart_value(row, "advance_rate_pct") for row in rows]
    amount_share = [_chart_value(row, "amount_share_pct") for row in rows]

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    fig.patch.set_facecolor("#F7F8FA")
    for ax in axes:
        ax.set_facecolor("white")
        ax.grid(axis="y", color="#D9DEE7", linewidth=0.8, alpha=0.75)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0].plot(
        x,
        low_median,
        color="#2563EB",
        marker="o",
        linewidth=2.2,
        label="低价股中位涨跌幅",
    )
    axes[0].plot(
        x,
        market_median,
        color="#64748B",
        marker="s",
        linestyle="--",
        linewidth=1.8,
        label="全市场中位涨跌幅",
    )
    axes[0].axhline(0, color="#111827", linewidth=0.8, alpha=0.55)
    axes[0].set_ylabel("涨跌幅（%）")
    axes[0].legend(loc="upper left", ncol=2, frameon=False)

    axes[1].plot(
        x,
        advance,
        color="#0F766E",
        marker="o",
        linewidth=2.2,
    )
    axes[1].axhline(50, color="#94A3B8", linestyle="--", linewidth=1)
    axes[1].set_ylabel("上涨家数占比（%）")

    axes[2].plot(
        x,
        amount_share,
        color="#D97706",
        marker="D",
        linewidth=2.2,
    )
    axes[2].set_ylabel("成交额占比（%）")
    axes[2].set_xticks(x, labels, rotation=0)
    axes[2].set_xlabel("交易日（月-日）")

    incomplete = [
        f"{str(row['trade_date'])[5:]}:{row.get('status')}"
        for row in rows
        if row.get("status") != "complete"
    ]
    if incomplete:
        fig.text(
            0.99,
            0.015,
            "断线状态：" + "；".join(incomplete),
            ha="right",
            va="bottom",
            fontsize=8.5,
            color="#6B7280",
        )

    fig.suptitle(
        f"低价股赚钱效应趋势（最近{len(rows)}个交易日）",
        fontsize=17,
        fontweight="bold",
        y=0.99,
    )
    fig.text(
        0.5,
        0.945,
        "口径：当日未复权收盘价≤10元，剔除ST/退市与沪深B股；个股等权；仅 complete 点连线",
        ha="center",
        fontsize=10,
        color="#4B5563",
    )
    fig.text(
        0.01,
        0.015,
        "数据来源：本地 post-market.yaml 最终归档｜图中数值为客观统计，不构成交易建议",
        ha="left",
        va="bottom",
        fontsize=8.5,
        color="#6B7280",
    )
    fig.tight_layout(rect=(0.02, 0.05, 0.98, 0.92))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".png", dir=output_path.parent
    )
    os.close(fd)
    try:
        fig.savefig(tmp_name, format="png", dpi=160, bbox_inches="tight")
        os.replace(tmp_name, output_path)
    finally:
        plt.close(fig)
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def write_trend_artifacts(
    rows: list[dict[str, Any]],
    *,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> dict[str, str]:
    """原子输出 CSV/JSON/PNG；文件名绑定实际首尾交易日。"""
    if not rows:
        raise ValueError("趋势产物至少需要 1 行数据")
    report_dir = Path(report_dir)
    stem = f"trend-{rows[0]['trade_date']}-to-{rows[-1]['trade_date']}"
    csv_path = report_dir / f"{stem}.csv"
    json_path = report_dir / f"{stem}.json"
    png_path = report_dir / f"{stem}.png"

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    _atomic_text(csv_path, buffer.getvalue())
    _atomic_text(
        json_path,
        json.dumps(
            {
                "definition": {
                    "classification_basis": "当日未复权收盘价",
                    "low_price_max_yuan": 10,
                    "universe": "沪深北A股，剔除ST/退市与沪深B股",
                    "chart_rule": "仅 status=complete 的点连线；不补零、不插值",
                },
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    render_trend_chart(rows, png_path)
    return {
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "png_path": str(png_path),
    }
