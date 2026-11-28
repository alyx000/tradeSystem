"""按实现日给一字首开溢价补充样本口径，只读原回填和交易日历。"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from utils.trade_date import verified_previous_session


@dataclass(frozen=True)
class _Archive:
    generated_at: str | None
    has_feedback: bool
    receipt: tuple
    snapshot: dict | None


def _database_evidence(conn, first_day, last_day):
    # 投影小型回执和快照，无需向 Python 传输数百 MB 的逐股原始行情。
    # 一些早期信封含其它指标的 NaN。支持 JSON5 的 SQLite 可安全投影其中合规的目标字段。
    valid_json = "json_valid(raw_data, 2)" if sqlite3.sqlite_version_info >= (3, 45, 0) else "json_valid(raw_data)"
    rows = conn.execute(f"""
        SELECT date, CASE WHEN {valid_json} THEN json_type(raw_data, '$.premium_backfill') END,
        CASE WHEN {valid_json} THEN json_extract(raw_data,
            '$.date', '$.generated_at', '$.raw_data.date', '$.premium_backfill.prev_date',
            '$.premium_backfill.trade_date', '$.premium_backfill.yizi_first_open',
            '$.premium_backfill.yizi_continued', '$.premium_backfill.yizi_undetermined',
            '$.premium_backfill.first_board.count', '$.premium_backfill._coverage',
            '$.raw_data.style_factors.premium_snapshot.yizi_first_open'
        ) END FROM daily_market WHERE date BETWEEN ? AND ?
    """, (first_day, last_day))
    by_day = {}
    for day, feedback_type, data in rows:
        if not data:
            continue
        envelope, stamp, raw_day, start, end, group, continued, undetermined, first_count, coverage, snapshot = json.loads(data)
        if envelope != day or raw_day not in (None, day):
            continue
        by_day[day] = _Archive(stamp, feedback_type is not None,
                               (start, end, group, continued, undetermined, first_count, coverage), snapshot)
    return by_day


@lru_cache(maxsize=384)
def _archive_header(path, identity):
    # 旧双写遗漏回填时只读标准 YAML 的小型头部。下一次原子替换会得到新的缓存键。
    # 重排/超大头部留待核验，不为展示扫描整份原始行情。
    header = bytearray()
    with path.open("rb") as handle:
        for line in handle:
            if line.startswith(b"raw_data:"):
                break
            if len(header) + len(line) > 512 * 1024:
                return None
            header.extend(line)
    doc = yaml.load(bytes(header), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    if not isinstance(doc, dict) or not isinstance(doc.get("premium_backfill"), dict):
        return None
    block = doc["premium_backfill"]
    first = block.get("first_board")
    return (doc.get("date"), doc.get("generated_at"), block.get("prev_date"), block.get("trade_date"),
            block.get("yizi_first_open"), block.get("yizi_continued"), block.get("yizi_undetermined"),
            first.get("count") if isinstance(first, dict) else None, block.get("_coverage"))


def _file_receipt(directory, source, stamp):
    if directory is None or not stamp:
        return None
    path = directory / source / "post-market.yaml"
    try:
        stat = path.stat()
        header = _archive_header(path, (stat.st_ino, stat.st_size, stat.st_mtime_ns))
    except (OSError, ValueError, yaml.YAMLError):
        return None
    if header and header[:2] == (source, stamp):
        return header[2:]
    return None


def _classify(receipt, source, day, scalar, snapshot):
    if receipt is None:
        return "unverified", None, False
    start, end, group, continued, undetermined, first_count, coverage = receipt
    if start != source or end != day or not isinstance(group, dict):
        return "unverified", None, False
    count, median = group.get("count"), group.get("premium_median")
    if type(count) is not int or count < 0:
        return "unverified", None, False
    if count > 0:
        if type(median) not in (int, float) or not math.isfinite(median):
            return "unverified", None, False
    elif median is not None:
        return "unverified", None, False
    if scalar is not None and scalar != median:
        return "unverified", None, False
    if snapshot is not None and (not isinstance(snapshot, dict) or
            (snapshot.get("count"), snapshot.get("premium_median")) != (count, median)):
        return "unverified", None, False
    details = group.get("detail", [])
    if not isinstance(details, list) or not all(isinstance(item, dict) for item in details):
        return "unverified", None, False
    partial = coverage is not None and (not isinstance(coverage, dict) or coverage.get("status") != "complete")
    if undetermined is not None:
        partial = partial or type(undetermined) is not int or undetermined != 0
    if isinstance(continued, dict) and "count" in continued:
        rest = continued["count"]
        if (rest is not None and (type(rest) is not int or rest < 0)) or any(
                "t_opened" in item and item["t_opened"] is not True for item in details):
            return "unverified", None, False
        return "opened_only", count, partial
    # 旧版原回填无继续一字分组。新版按同一键写出零样本；结果日摘要则会省略零桶。
    # 只在已知旧版实现日范围、旧回填结构完整时认作旧口径。
    if (day <= "2026-07-27" and continued is None and undetermined is None
            and type(first_count) is int and first_count >= 0
            and (count == 0 or details) and all("t_opened" not in item for item in details)):
        return "legacy_unsplit", count, partial
    return "unverified", count, partial


def add_first_open_evidence(conn, rows):
    if not rows:
        return
    previous = {row["date"]: verified_previous_session(conn, row["date"]) for row in rows}
    first_day = rows[0]["date"]
    evidence = _database_evidence(conn, previous[first_day] or first_day, rows[-1]["date"])
    filename = next((row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main"), "")
    db = Path(filename)
    directory = db.parent.parent / "daily" if db.name == "trade.db" and db.parent.name == "data" else None
    last, last_basis = None, None
    for row in rows:
        day = row["date"]
        source = previous[day]
        src = evidence.get(source)
        current = evidence.get(day)
        receipt = None
        if src:
            # 若库内有回填，直接使用，日期或数值冲突不得切换来源回避。
            receipt = src.receipt if src.has_feedback else _file_receipt(directory, source, src.generated_at)
        basis, count, partial = _classify(receipt, source, day, row["premium_first_open"], current.snapshot if current else None)
        row.update(premium_first_open_basis=basis, premium_first_open_count=count, premium_first_open_partial=partial,
                   premium_first_open_break_before=last is not None and (source != last or basis != last_basis))
        last, last_basis = day, basis
