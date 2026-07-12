from __future__ import annotations

from . import aggregator, collector, detector, repo


def run_daily(conn, registry, date: str, *, persist: bool = True, top_n: int = 10) -> dict:
    inputs = collector.collect_daily_inputs(registry, date)
    if inputs.get("status") != "ok":
        return {
            "status": "source_failed",
            "date": date,
            "market_count": 0,
            "new_high_count": 0,
            "sector_summary": [],
            "stocks": [],
            "source": inputs,
        }

    codes = [r["code"] for r in inputs["rows"] if r.get("code")]
    watermarks = repo.get_watermarks(conn, codes)
    detected = detector.detect_new_highs(inputs["rows"], watermarks, date)
    sectors = aggregator.aggregate_by_sector(detected["new_highs"])
    record = {
        "status": "ok",
        "date": date,
        "market_count": detected["market_count"],
        "new_high_count": len(detected["new_highs"]),
        "sector_summary": sectors,
        "stocks": detected["new_highs"],
        "source": {
            **inputs["source"],
            "skipped_count": detected["skipped_count"],
            "initialized_count": detected["initialized_count"],
            "top_n": top_n,
        },
    }

    if persist:
        repo.upsert_watermarks(conn, detected["watermark_updates"])
        repo.save_daily_stats(conn, record)
    return record


def run_trend(conn, date: str, *, days: int = 30) -> list[dict]:
    return repo.get_recent_stats(conn, date, days)


def run_backfill(conn, registry, dates: list[str], *, persist: bool = True, top_n: int = 10) -> dict:
    processed = []
    skipped = []
    for date in sorted(dates):
        record = run_daily(conn, registry, date, persist=persist, top_n=top_n)
        if record.get("status") == "ok":
            processed.append(date)
        else:
            skipped.append({"date": date, "source": record.get("source")})
    return {
        "processed_count": len(processed),
        "skipped_count": len(skipped),
        "processed_dates": processed,
        "skipped": skipped,
    }
