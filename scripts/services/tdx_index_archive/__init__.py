"""两条通达信专有指数的原生5分钟OHLC，与四宽基东财档案分别保留。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time
import fcntl
from pathlib import Path
from uuid import uuid4

from services.index_volume_monitor.bar_archive import ArchiveSpec, archive_fetched, load_archive, ENDS
from services.index_volume_monitor.core import calendar_basis, windows_at
from services.intraday_monitor.guards import shanghai_now
from services.intraday_monitor.state import save_state


NAMES = {"880003.TDX": "通达信平均股价", "880823.TDX": "通达信微盘股"}
SPEC = ArchiveSpec("tdx-index-ohlc-5m-v1", "tdx:index_5min", NAMES, ("open","high","low","close"), {
    "request_market": 1, "request_category": 0, "source_time": "pytdx原生datetime；上海收盘日期及时间为日档和K线键",
    "identity_basis": "逐代码调用get_index_bars；协议不回显标的，回显身份若存在须与请求一致",
}, store_source_time=True)
ROOT = Path(__file__).resolve().parents[3] / "data/reports/tdx-index-5m"


def _fetch(provider, code, days, end):
    try:
        result = provider.get_tdx_index_5m(code, days, end.strftime("%H:%M"))
        if not result.success or result.source != SPEC.source:
            raise ValueError(result.error or "通达信5分钟来源不符")
        rows = result.data
        nodes = {row["node"] for row in rows}
        if len(nodes) != 1 or not all(nodes):
            raise ValueError("通达信5分钟节点身份不唯一")
        return ((rows, None), {"node": nodes.pop(), "market": 1, "requested_code": code[:6], "category": 0},
                result.note)
    except (AttributeError, ValueError, TypeError, KeyError, OSError) as exc:
        return ([], str(exc)), {}, None


def _attempt(provider, root, day, previous, end, clock, input_by, dry_run):
    days = (previous, day) if previous is not None else (day,)
    with ThreadPoolExecutor(max_workers=len(NAMES)) as pool:
        futures = {code: pool.submit(_fetch, provider, code, days, end) for code in NAMES}
        fetched = {code: future.result() for code, future in futures.items()}
    captured_at = shanghai_now(clock()).isoformat()
    results = {code: pair for code, (pair, origin, note) in fetched.items()}
    origins = {code: origin for code, (pair, origin, note) in fetched.items()}
    receipt = {
        "source": SPEC.source, "trade_date": day, "cutoff": end.isoformat(), "captured_at": captured_at,
        "input_by": input_by, "previous_trade_date": previous,
        "errors": {code: error for code, (_, error) in results.items() if error},
        "requests": {
            code: {"market": 1, "category": 0, "code": code[:6], **origins[code],
                   "provider_note": fetched[code][2],
                   "returned_rows_by_date": {
                       d: sum(row["trade_time"].startswith(d + " ") for row in results[code][0]) for d in days
                   }} for code in NAMES
        }, "pushed": False,
    }
    expected = {d: len(ENDS) if d != day else sum(t <= end.strftime("%H:%M") for t in ENDS)
                for d in days}
    receipt["attempt_coverage"] = {}
    for d in days:
        by_code = {code: request["returned_rows_by_date"][d] for code, request in receipt["requests"].items()}
        checked = sum(by_code.values())
        receipt["attempt_coverage"][d] = {
            "expected_per_index": expected[d], "rows_by_index": by_code,
            "status": ("complete" if all(n == expected[d] for n in by_code.values())
                       else "partial" if checked else "source_failed"),
        }
    if dry_run:
        receipt["status"] = "dry_run"
        return receipt
    archives = archive_fetched(root, results, previous=previous, end=end, captured_at=captured_at,
                               input_by=input_by, spec=SPEC, per_code_origin=origins)
    receipt["archives"] = archives
    if any(item["status"] == "archive_error" for item in archives.values()):
        receipt["status"] = "archive_error"
    else:
        # 本槽充分与当天48根完整分别表述。历史已存的K线不能掩盖本次请求失败。
        archived = load_archive(root / f"{day}.json", day, SPEC)
        covered = [not s["missing_due"] and not s["conflicts"] for s in archived["indices"].values()]
        attempts = receipt["attempt_coverage"]
        receipt["status"] = ("source_failed" if attempts[day]["status"] == "source_failed"
                             else "complete" if all(covered) and all(a["status"] == "complete" for a in attempts.values())
                             else "partial")
    path = root / "attempts" / day / f"{end:%H%M}-{uuid4().hex}.json"
    save_state(path, receipt)
    return {**receipt, "receipt_path": str(path)}


def run_archive(provider, *, input_by, day=None, dry_run=False, root=ROOT, db_path=None, clock=shanghai_now):
    """实时只认既有收盘槽；显式日期只认已收盘开放日，不发通知。"""
    if not isinstance(input_by, str) or not input_by.strip():
        return {"status":"invalid_request", "error":"input_by不可为空"}
    now = shanghai_now(clock())
    historical = day is not None
    if historical:
        end = shanghai_now(datetime.combine(date.fromisoformat(day), time(15)))
        if now < end:
            return {"status":"not_closed"}
    else:
        windows = windows_at(now, periods=(5,))
        if not windows:
            return {"status":"outside_slot"}
        end = windows[0][1]
        day = end.date().isoformat()
    status, previous = calendar_basis(day, db_path)
    if status != "complete":
        return {"status":status}
    prior = None if historical else previous
    if dry_run:
        return _attempt(provider, root, day, prior, end, clock, input_by.strip(), True)
    try:
        root.mkdir(parents=True, exist_ok=True)
        # 防止相邻180秒轮询同时取同一槽；收盘补取与实时日档合并也受逐日锁保护。
        with (root / ".collection.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not historical:
                existing = load_archive(root / f"{day}.json", day, SPEC)
                due = [stamp for stamp in ENDS if stamp <= end.strftime("%H:%M")]
                if existing and all(all(stamp in item["bars"] and stamp not in item["conflicts"] for stamp in due)
                                    for item in existing["indices"].values()):
                    return {"status":"already_checked", "trade_date":day, "cutoff":end.isoformat()}
            return _attempt(provider, root, day, prior, end, clock, input_by.strip(), False)
    except BlockingIOError:
        return {"status":"already_running"}
    except (ValueError, OSError, TypeError, KeyError) as exc:
        return {"status":"archive_error", "error":str(exc)}
