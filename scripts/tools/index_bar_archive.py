#!/usr/bin/env python3
"""补采指定日期的四指数5分钟原值，或只读检查盘中回执覆盖。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from providers.eastmoney_provider import EastmoneyProvider
from services.index_volume_monitor.bar_archive import load_archive, archive_fetched
from services.index_volume_monitor.core import calendar_basis
from services.index_volume_monitor.rules import INDEX_NAMES
from services.index_volume_monitor.service import DEFAULT_REPORT_ROOT
from services.intraday_monitor.guards import shanghai_now


def _opening_rows(item, day):
    if item["code"] not in INDEX_NAMES or item.get("status") != "complete":
        return None
    rows = [r for r in item["source_bars"] if r["trade_time"].startswith(day + " ")]
    expected = ["09:35", "09:40", "09:45", "09:50", "09:55", "10:00"]
    if [r["trade_time"][11:] for r in rows] != expected:
        raise ValueError("当日10点原值不等于六根已闭合的早盘5分钟K线")
    return rows


def recover_opening(root, day, previous, end, input_by):
    evidence, errors = [], []
    for path in sorted((root / day).glob("1000-*.json")):
        try:
            raw = path.read_bytes()
            report = json.loads(raw)
            env = report["opening_environment"]
            if (env["source"] != "eastmoney:index_5min" or env["trade_date"] != day
                    or env["previous_trade_date"] != previous or env["teacher_note_id"] != 1178
                    or report["captured_at"] != env["captured_at"]):
                raise ValueError("盘中回执来源或日期不符")
            when = datetime.fromisoformat(env["captured_at"])
            if (not when.tzinfo or shanghai_now(when).date() != end.date()
                    or not time(10, 1) <= shanghai_now(when).time() < time(10, 6)):
                raise ValueError("盘中回执采集时刻不在10点观察窗口")
            if not isinstance(env["items"], list):
                raise ValueError("盘中回执缺少按指数记录")
        except (OSError, TypeError, ValueError, KeyError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        results = {}
        duplicates = set()
        for pos, item in enumerate(env["items"]):
            try:
                rows = _opening_rows(item, day)
                if rows is not None:
                    code = item["code"]
                    if code in results or code in duplicates:
                        duplicates.add(code)
                        results.pop(code, None)
                        raise ValueError("同一指数在回执中重复")
                    results[code] = rows, None
            except (AttributeError, TypeError, ValueError, KeyError) as exc:
                errors.append(f"{path.name} item[{pos}]: {exc}")
        if results:
            origin = {"source_receipt": path.name, "source_sha256": hashlib.sha256(raw).hexdigest()}
            evidence.append(archive_fetched(root / "ohlcv-5m", results, previous=None, end=end,
                            captured_at=env["captured_at"], input_by=input_by, origin=origin))
    return evidence, errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--input-by", help="补采请求者")
    parser.add_argument("--coverage", action="store_true", help="只读统计盘中各槽最后回执")
    parser.add_argument("--start-date", type=date.fromisoformat, help="覆盖统计起始日，默认与--date相同")
    parser.add_argument("--local-only", action="store_true", help="仅用已有10点原始证据")
    opts = parser.parse_args()
    if opts.coverage:
        if opts.local_only:
            parser.error("--local-only仅用于补采")
        from services.index_volume_monitor.coverage import receipt_coverage
        try:
            result = receipt_coverage(DEFAULT_REPORT_ROOT, opts.start_date or opts.date, opts.date)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "ok" else 2
    if opts.start_date:
        parser.error("--start-date只用于--coverage")
    if not opts.input_by or not opts.input_by.strip():
        parser.error("补采需要显式传入非空--input-by")
    day = opts.date.isoformat()
    end = shanghai_now(datetime.combine(opts.date, time(15)))
    if shanghai_now() < end:
        parser.error("仅接受已收盘日期")
    status, previous = calendar_basis(day)
    if status != "complete":
        print(json.dumps({"trade_date": day, "status": status}, ensure_ascii=False))
        return 2
    local, errors = recover_opening(DEFAULT_REPORT_ROOT, day, previous, end, opts.input_by)
    summary = {"trade_date": day, "local_receipts": len(local), "local_errors": errors}
    if opts.local_only:
        path = DEFAULT_REPORT_ROOT / "ohlcv-5m" / f"{day}.json"
        try:
            saved = load_archive(path, day)
            final = ({**{k: saved[k] for k in ("status", "valid_count", "expected_full_day")},
                      "path": str(path)} if saved else {"status": "no_evidence"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            final = {"status": "archive_error", "error": str(exc), "path": str(path)}
    else:
        def fetch(code):
            result = EastmoneyProvider().get_index_intraday_bars(code, day, day, period_minutes=5)
            if not result.success or result.source != "eastmoney:index_5min":
                return code, ([], result.error or "指数5分钟来源不符")
            return code, (result.data, None)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = dict(pool.map(fetch, INDEX_NAMES))
        summary["provider_errors"] = {c: err for c, (_, err) in results.items() if err}
        final = archive_fetched(DEFAULT_REPORT_ROOT / "ohlcv-5m", results, previous=None,
                                end=end, captured_at=shanghai_now().isoformat(),
                                input_by=opts.input_by.strip())[day]
    print(json.dumps({**summary, **final}, ensure_ascii=False))
    return 0 if final["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
