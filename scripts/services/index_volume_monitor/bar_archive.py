"""复用已请求的东方财富行情，按事实日归档闭合5分钟量价和可回放的30分钟聚合。"""
import fcntl
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from services.intraday_monitor.state import save_state
from .core import money
from .rules import INDEX_NAMES


SCHEMA = "index-ohlcv-5m-v1"
FIELDS = ("open", "high", "low", "close", "volume", "amount")
ENDS = tuple(
    (datetime(2000, 1, 1, hour, minute) + timedelta(minutes=offset)).strftime("%H:%M")
    for hour, minute in ((9, 30), (13, 0))
    for offset in range(5, 121, 5)
)


@dataclass(frozen=True)
class ArchiveSpec:
    schema: str
    source: str
    names: dict
    fields: tuple
    metadata: dict
    store_source_time: bool = False


EASTMONEY = ArchiveSpec(SCHEMA, "eastmoney:index_5min", INDEX_NAMES, FIELDS, {
    "amount_unit": "CNY", "volume_unit": "eastmoney_native_same_index",
})


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def load_archive(path, day, spec=EASTMONEY):
    if not path.exists():
        return {}
    saved = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(saved, dict) or saved.pop("sha256", None) != _digest(saved)
            or saved.get("schema") != spec.schema or saved.get("source") != spec.source or saved.get("trade_date") != day):
        raise ValueError("旧指数5分钟归档校验失败，保留原文件")
    return saved


def _extract(rows, code, day, cutoff, spec):
    selected = {}
    for row in rows:
        stamp = row["trade_time"]
        if not stamp.startswith(day + " "):
            continue
        end = stamp[11:]
        if (end not in ENDS or row.get("ts_code") != code or row.get("period_minutes") != 5
                or "amount" in spec.fields and row.get("amount_unit") != "CNY"):
            raise ValueError("指数5分钟身份、收盘网格或单位不符")
        if end > cutoff:
            continue
        if end in selected:
            raise ValueError(f"{stamp} 同批存在重复K线")
        values = {key: money(row.get(key)) for key in spec.fields}
        if not (0 < values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]):
            raise ValueError(f"{stamp} OHLC值非法")
        selected[end] = {key: format(value.normalize(), "f") for key, value in values.items()}
    return selected


def _aggregate(bars, conflicts, fields):
    blocks = {}
    for offset in range(0, len(ENDS), 6):
        ends = ENDS[offset:offset + 6]
        if any(end not in bars or end in conflicts for end in ends):
            continue
        group = [bars[end] for end in ends]
        blocks[ends[-1]] = {
            "open": group[0]["open"], "close": group[-1]["close"],
            "high": max((row["high"] for row in group), key=Decimal),
            "low": min((row["low"] for row in group), key=Decimal),
            **{key: str(sum(Decimal(row[key]) for row in group))
               for key in ("volume", "amount") if key in fields},
        }
    return blocks


def _merge_day(old, day, cutoff, results, captured_at, input_by, origin, spec, per_code_origin, fill_missing):
    through = max(cutoff, old.get("observed_through", ""))
    due = [end for end in ENDS if end <= through]
    indices = {}
    for code, name in spec.names.items():
        prior = old.get("indices", {}).get(code, {})
        bars = dict(prior.get("bars", {}))
        conflicts = dict(prior.get("conflicts", {}))
        error = prior.get("source_error")
        if code in results:
            rows, error = results[code]
            if not error:
                try:
                    new = _extract(rows, code, day, cutoff, spec)
                    for end, values in new.items():
                        provenance = {"captured_at": captured_at, "input_by": input_by, **origin,
                                      **per_code_origin.get(code, {})}
                        if spec.store_source_time:
                            provenance["source_time"] = f"{day} {end}"
                        if end not in bars:
                            bars[end] = {**values, **provenance}
                        elif not fill_missing and end not in conflicts and any(bars[end][key] != values[key] for key in spec.fields):
                            conflicts[end] = {**values, **provenance}
                except (AttributeError, TypeError, KeyError, ValueError) as exc:
                    error = str(exc)
        missing = [end for end in due if end not in bars]
        complete = len(bars) == len(ENDS) and not conflicts
        indices[code] = {
            "name": name, "bars": bars, "bars_30m": _aggregate(bars, conflicts, spec.fields),
            "conflicts": conflicts, "missing_due": missing,
            "status": "complete" if complete else "partial" if bars else "source_failed",
            "valid_count": len(bars) - len(conflicts), "expected_full_day": len(ENDS),
            "source_error": (error or prior.get("source_error")) if missing else None,
        }
    valid = sum(item["valid_count"] for item in indices.values())
    return {
        "schema": spec.schema, "trade_date": day, "source": spec.source,
        "timezone": "Asia/Shanghai", "bar_time": "收盘时刻", **spec.metadata, "observed_through": through,
        "status": "complete" if all(item["status"] == "complete" for item in indices.values())
        else "partial" if valid else "source_failed",
        "valid_count": valid, "expected_full_day": len(ENDS) * len(spec.names), "indices": indices,
    }


def archive_fetched(root, results, *, previous, end, captured_at, input_by, origin=None, spec=EASTMONEY,
                    per_code_origin=None, fill_missing=False):
    """日期由调用方已通过的严格SSE日历给出。只从最新完成槽及更早的K线取证。"""
    receipts = {}
    days = [(end.date().isoformat(), end.strftime("%H:%M"))]
    if previous is not None:
        days.insert(0, (previous, "15:00"))
    for day, cutoff in days:
        path = root / f"{day}.json"
        try:
            root.mkdir(parents=True, exist_ok=True)
            with path.with_suffix(".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                old = load_archive(path, day, spec)
                data = _merge_day(old, day, cutoff, results, captured_at, input_by, origin or {}, spec,
                                  per_code_origin or {}, fill_missing)
                if data != old:
                    save_state(path, {**data, "sha256": _digest(data)})
            receipts[day] = {key: data[key] for key in ("status", "valid_count", "expected_full_day")}
            receipts[day]["path"] = str(path)
        except (TypeError, OSError, ValueError, KeyError) as exc:
            receipts[day] = {"status": "archive_error", "error": str(exc), "path": str(path)}
    return receipts
