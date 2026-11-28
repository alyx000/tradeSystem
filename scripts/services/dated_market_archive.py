"""读取有目标日回执和文件SHA的已归档行情原响应。"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import hashlib
import io

from services.course_core_minutes import _PrimitiveUnpickler


def read_source(folder, manifest, day, file, method, source):
    path = folder / file
    if path.stat().st_size > 16_000_000:
        raise ValueError("日线对照来源体积异常")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if manifest["source_sha256"].get(str(path.resolve())) != sha:
        raise ValueError("日线对照来源摘要不匹配")
    receipt = next((r for r in manifest["source_receipts"] if r.get("capability") == method
                    and r.get("source") == source and r.get("args") == [day]
                    and r.get("success") is True and not r.get("error") and r.get("fetched_at")), None)
    if not receipt:
        raise ValueError("日线对照来源回执缺失")
    stamp = datetime.fromisoformat(receipt["fetched_at"])
    tz = ZoneInfo("Asia/Shanghai")
    if stamp.tzinfo is None or stamp > datetime.now(tz):
        raise ValueError("日线对照取数时刻无时区或晚于当前")
    earliest = datetime.combine(date.fromisoformat(day), time(15), tz)
    if method == "get_market_daily_quotes" and stamp < earliest:
        raise ValueError("当日收盘行情回执早于市场收盘")
    rows = _PrimitiveUnpickler(io.BytesIO(raw)).load()
    return rows, dict(path=str(path), sha256=sha, source=source, captured_at=receipt["fetched_at"])
