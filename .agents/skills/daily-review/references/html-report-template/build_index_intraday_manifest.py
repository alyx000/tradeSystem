#!/usr/bin/env python3
"""只读生成六指数 15/30/60 分钟 K 线结构 sidecar。

数据固定来自 pytdx ``get_index_bars``；每个指数、每个周期都必须以目标日
15:00 为末根，缺一项即降级为 partial/failed。脚本不写数据库、计划层或关注池。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import date as date_type, datetime
from pathlib import Path
from typing import Mapping, Sequence


SCHEMA = "index-intraday-structure-v1"
GENERATOR = "build_index_intraday_manifest.py"
EXPECTED_ROWS = 18
FETCH_COUNT = 800
MAX_FETCH_PAGES = 64
INDEXES = (
    ("shanghai", "上证", 1, "000001", ""),
    ("shenzhen", "深成", 0, "399001", ""),
    ("chinext", "创业板", 0, "399006", ""),
    ("star50", "科创50", 1, "000688", ""),
    ("cni2000-proxy", "国证2000代理", 0, "399303", "中证2000近似代理"),
    ("average-price", "平均股价", 1, "880003", "通达信平均股价"),
)
PERIODS = (
    ("15m", 1, 400),
    ("30m", 2, 300),
    ("60m", 3, 240),
)


class ManifestBuildError(RuntimeError):
    pass


def _valid_date(value: str) -> bool:
    try:
        date_type.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _default_servers() -> tuple[tuple[str, int], ...]:
    scripts_dir = _repo_root() / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from providers.tdx_provider import _DEFAULT_SERVERS

    return tuple((str(host), int(port)) for host, port in _DEFAULT_SERVERS)


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ManifestBuildError(f"{field} 不是有效数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ManifestBuildError(f"{field} 不是有效数值") from exc
    if not math.isfinite(number):
        raise ManifestBuildError(f"{field} 不是有限数值")
    return number


def _normalize_bars(raw_bars: Sequence[Mapping[str, object]]) -> list[dict]:
    rows: dict[str, dict] = {}
    for raw in raw_bars:
        if not isinstance(raw, Mapping) or not {"datetime", "open", "high", "low", "close"} <= set(raw):
            raise ManifestBuildError("分钟K线缺少时间或完整OHLC字段")
        raw_time = str(raw["datetime"] or "").strip()
        try:
            stamp = datetime.fromisoformat(raw_time).strftime("%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ManifestBuildError("分钟K线时间无效") from exc
        bar = {"datetime": stamp, **{key: _finite(raw[key], key) for key in ("open", "high", "low", "close")}}
        if (min(bar[key] for key in ("open", "high", "low", "close")) <= 0 or
                bar["high"] < max(bar["open"], bar["close"]) or
                bar["low"] > min(bar["open"], bar["close"])):
            raise ManifestBuildError("分钟K线OHLC范围非法")
        # Pagination may overlap exactly; conflicting facts must never overwrite.
        if stamp in rows and rows[stamp] != bar:
            raise ManifestBuildError("同时间分钟K线冲突")
        rows[stamp] = bar
    return [rows[key] for key in sorted(rows)]


def _ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1.0 - alpha) * result[-1])
    return result


def _macd(closes: Sequence[float]) -> tuple[list[float], list[float], list[float]]:
    fast = _ema(closes, 12)
    slow = _ema(closes, 26)
    dif = [left - right for left, right in zip(fast, slow)]
    dea = _ema(dif, 9)
    hist = [left - right for left, right in zip(dif, dea)]
    return dif, dea, hist


def _bars_since_cross(dif: Sequence[float], dea: Sequence[float], kind: str) -> int | None:
    for index in range(len(dif) - 1, 0, -1):
        previous = dif[index - 1] - dea[index - 1]
        current = dif[index] - dea[index]
        if kind == "golden" and previous < 0 <= current:
            return len(dif) - 1 - index
        if kind == "dead" and previous > 0 >= current:
            return len(dif) - 1 - index
    return None


def _position(close: float, moving_average: float) -> str:
    if math.isclose(close, moving_average, rel_tol=1e-10, abs_tol=1e-10):
        return "equal"
    return "above" if close > moving_average else "below"


def _bottom_divergence(
    bars: Sequence[Mapping[str, object]], dif: Sequence[float]
) -> tuple[bool, list[str]]:
    start = max(2, len(bars) - 30)
    end = len(bars) - 2
    pivots: list[int] = []
    for index in range(start, end):
        low = float(bars[index]["low"])
        neighbors = [
            float(bars[other]["low"])
            for other in (index - 2, index - 1, index + 1, index + 2)
        ]
        if low < min(neighbors):
            pivots.append(index)
    if len(pivots) < 2:
        return False, []
    first, second = pivots[-2:]
    matched = (
        float(bars[second]["low"]) < float(bars[first]["low"])
        and dif[second] > dif[first]
    )
    dates = [str(bars[first]["datetime"]), str(bars[second]["datetime"])]
    return matched, dates if matched else []


def calculate_row(
    bars: Sequence[Mapping[str, object]],
    *,
    report_date: str,
    index_id: str,
    index_name: str,
    market: int,
    code: str,
    proxy_note: str,
    period: str,
    category: int,
    requested_count: int,
    source_node: str,
) -> dict:
    normalized = _normalize_bars(bars)
    expected_last = f"{report_date} 15:00"
    normalized = [row for row in normalized if row["datetime"] <= expected_last]
    if len(normalized) < requested_count:
        raise ManifestBuildError(
            f"{index_name}/{period} 样本不足：{len(normalized)}/{requested_count}"
        )
    normalized = normalized[-requested_count:]
    if normalized[-1]["datetime"] != expected_last:
        raise ManifestBuildError(
            f"{index_name}/{period} 末根 {normalized[-1]['datetime']}，不是 {expected_last}"
        )

    closes = [float(row["close"]) for row in normalized]
    dif, dea, hist = _macd(closes)
    moving_averages = {
        window: sum(closes[-window:]) / window for window in (5, 10, 20)
    }
    recent = normalized[-30:]
    swing_high = max(recent, key=lambda row: (float(row["high"]), row["datetime"]))
    swing_low = min(recent, key=lambda row: (float(row["low"]), row["datetime"]))
    divergence, divergence_dates = _bottom_divergence(normalized, dif)
    current_hist = hist[-1]
    previous_hist = hist[-2]
    if math.isclose(abs(current_hist), abs(previous_hist), rel_tol=1e-8, abs_tol=1e-10):
        momentum = "flat"
    elif abs(current_hist) < abs(previous_hist):
        momentum = "contracting"
    else:
        momentum = "expanding"
    close_value = round(closes[-1], 6)
    ma_values = {
        window: round(moving_averages[window], 6) for window in (5, 10, 20)
    }
    dif_value = round(dif[-1], 6)
    dea_value = round(dea[-1], 6)
    hist_value = round(current_hist, 6)
    if dif_value > 0 and dea_value > 0:
        axis = "above"
    elif dif_value < 0 and dea_value < 0:
        axis = "below"
    else:
        axis = "mixed"

    return {
        "index_id": index_id,
        "index_name": index_name,
        "tdx_market": market,
        "tdx_code": code,
        "proxy_note": proxy_note,
        "period": period,
        "category": category,
        "requested_count": requested_count,
        "sample_count": len(normalized),
        "last_bar_at": normalized[-1]["datetime"],
        "close": close_value,
        "ma5": ma_values[5],
        "ma10": ma_values[10],
        "ma20": ma_values[20],
        "close_vs_ma5": _position(close_value, ma_values[5]),
        "close_vs_ma10": _position(close_value, ma_values[10]),
        "close_vs_ma20": _position(close_value, ma_values[20]),
        "dif": dif_value,
        "dea": dea_value,
        "hist": hist_value,
        "macd_axis": axis,
        "bars_since_golden_cross": _bars_since_cross(dif, dea, "golden"),
        "bars_since_dead_cross": _bars_since_cross(dif, dea, "dead"),
        "hist_momentum": momentum,
        "swing_high": round(float(swing_high["high"]), 6),
        "swing_high_at": str(swing_high["datetime"]),
        "swing_low": round(float(swing_low["low"]), 6),
        "swing_low_at": str(swing_low["datetime"]),
        "bottom_divergence": divergence,
        "bottom_divergence_points": divergence_dates,
        "source_node": source_node,
    }


def _fetch_bars_through(
    api: object,
    *,
    category: int,
    market: int,
    code: str,
    report_date: str,
    requested_count: int,
) -> list[Mapping[str, object]]:
    """分页回溯，直到目标日末根及其固定样本窗均已覆盖。"""

    expected_last = f"{report_date} 15:00"
    collected: dict[str, Mapping[str, object]] = {}
    for page in range(MAX_FETCH_PAGES):
        start = page * FETCH_COUNT
        batch = api.get_index_bars(category, market, code, start, FETCH_COUNT)
        if not batch:
            break
        before = len(collected)
        for bar in _normalize_bars(batch):
            stamp = bar["datetime"]
            if stamp in collected and collected[stamp] != bar:
                raise ManifestBuildError("跨页同时间分钟K线冲突")
            collected[stamp] = bar
        normalized = [collected[key] for key in sorted(collected)]
        eligible = [row for row in normalized if row["datetime"] <= expected_last]
        if (
            len(eligible) >= requested_count
            and eligible[-1]["datetime"] == expected_last
        ):
            return list(collected.values())
        if len(batch) < FETCH_COUNT or len(collected) == before:
            break
    return list(collected.values())


def build_manifest(
    report_date: str,
    *,
    servers: Sequence[tuple[str, int]] | None = None,
    timeout: float = 6.0,
    visual_context: dict | None = None,
) -> dict:
    if not _valid_date(report_date):
        raise ValueError("report_date 必须是有效 YYYY-MM-DD")
    scripts_path = str(_repo_root() / 'scripts')
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    from services.review_visual_context import validate_visual_context
    context = validate_visual_context(visual_context, report_date)
    if context['status'] != 'complete':
        return dict(_failure_manifest(report_date, '交易日历未核验：'+context['reason']), visual_context=context)
    fact_date = context['fact_date']
    try:
        from pytdx.hq import TdxHq_API
    except ImportError as exc:
        raise ManifestBuildError("pytdx 未安装") from exc

    pending = {
        (index_id, period): (index_id, name, market, code, note, period, category, count)
        for index_id, name, market, code, note in INDEXES
        for period, category, count in PERIODS
    }
    rows: dict[tuple[str, str], dict] = {}
    errors: dict[tuple[str, str], str] = {}
    connected = False
    for host, port in (servers or _default_servers()):
        api = TdxHq_API(heartbeat=True)
        try:
            if not api.connect(host, port, time_out=timeout):
                continue
            connected = True
            for key, item in list(pending.items()):
                index_id, name, market, code, note, period, category, count = item
                try:
                    bars = _fetch_bars_through(
                        api,
                        category=category,
                        market=market,
                        code=code,
                        report_date=fact_date,
                        requested_count=count,
                    )
                    if not bars:
                        raise ManifestBuildError("返回空 K 线")
                    rows[key] = calculate_row(
                        bars,
                        report_date=fact_date,
                        index_id=index_id,
                        index_name=name,
                        market=market,
                        code=code,
                        proxy_note=note,
                        period=period,
                        category=category,
                        requested_count=count,
                        source_node=f"{host}:{port}",
                    )
                    pending.pop(key)
                    errors.pop(key, None)
                except Exception as exc:
                    errors[key] = str(exc)
            if not pending:
                break
        except Exception:
            continue
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    ordered_rows = [
        rows[(index_id, period)]
        for index_id, *_ in INDEXES
        for period, *_ in PERIODS
        if (index_id, period) in rows
    ]
    error_messages = [
        f"{key[0]}/{key[1]}: {errors.get(key, '所有节点均未返回有效数据')}"
        for key in pending
    ]
    if not connected and not error_messages:
        error_messages = ["所有通达信行情服务器连接失败"]
    status = "complete" if len(ordered_rows) == EXPECTED_ROWS else (
        "partial" if ordered_rows else "failed"
    )
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "report_date": report_date,
        "as_of": fact_date,
        "visual_context": context,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "complete": status == "complete",
        "source": "pytdx:get_index_bars",
        "index_count": len(INDEXES),
        "periods": [item[0] for item in PERIODS],
        "expected_rows": EXPECTED_ROWS,
        "row_count": len(ordered_rows),
        "rows": ordered_rows,
        "errors": error_messages,
    }


def _failure_manifest(report_date: str, message: str) -> dict:
    return {
        "schema": SCHEMA,
        "generator": GENERATOR,
        "report_date": report_date,
        "as_of": report_date,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": "failed",
        "complete": False,
        "source": "pytdx:get_index_bars",
        "index_count": len(INDEXES),
        "periods": [item[0] for item in PERIODS],
        "expected_rows": EXPECTED_ROWS,
        "row_count": 0,
        "rows": [],
        "errors": [message],
    }


def _atomic_write(payload: dict, output: Path) -> Path:
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_name, destination)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return destination


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report_date")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trade-db", type=Path, default=_repo_root()/"data/trade.db")
    parser.add_argument("--timeout", type=float, default=6.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        sys.path.insert(0, str(_repo_root()/'scripts'))
        from services.review_visual_context import load_visual_context
        context = load_visual_context(args.trade_db, args.report_date)
        payload = build_manifest(args.report_date, timeout=args.timeout, visual_context=context)
    except ValueError as exc:
        print(f"FAIL [usage] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        payload = _failure_manifest(args.report_date, str(exc))
    path = _atomic_write(payload, args.output)
    print(
        f"OK {path} status={payload['status']} "
        f"rows={payload['row_count']}/{payload['expected_rows']}"
    )
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
