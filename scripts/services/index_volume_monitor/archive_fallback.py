"""从同一指数、同一东财5分钟来源找回本次观察之前已经取得的同窗金额。"""
from datetime import datetime, timedelta

from .bar_archive import load_archive
from .core import compare_window, money
from .rules import INDEX_NAMES


class ArchiveFallback:
    def __init__(self, root, as_of):
        self.root = root
        self.as_of = as_of
        self.days = {}

    def _read(self, day):
        if day not in self.days:
            path = self.root / f"{day}.json"
            try:
                archive = load_archive(path, day)
                if (archive.get("source") != "eastmoney:index_5min" or archive.get("amount_unit") != "CNY"
                        or archive.get("timezone") != "Asia/Shanghai" or archive.get("bar_time") != "收盘时刻"):
                    archive = {}
                self.days[day] = archive
            except (OSError, TypeError, ValueError, AttributeError):
                self.days[day] = {}
        return self.days[day]

    def compare(self, code, period, end, previous, live, primary_error, rule):
        labels = [(end-timedelta(minutes=offset)).strftime("%H:%M") for offset in range(0, period, 5)]
        live_rows = {row["trade_time"]: row for row in live}
        rows, evidence = [], []
        for day in (previous, end.date().isoformat()):
            archive = self._read(day)
            item = archive.get("indices", {}).get(code, {})
            for label in labels:
                stamp = f"{day} {label}"
                if label in item.get("conflicts", {}):
                    conflict = item["conflicts"][label]
                    try:
                        recorded = datetime.fromisoformat(conflict["captured_at"])
                    except (TypeError, KeyError, ValueError):
                        recorded = None
                    if recorded is None or recorded.tzinfo is None or recorded <= self.as_of:
                        raise ValueError(f"同源日档原值有冲突：{stamp}")
                saved = item.get("bars", {}).get(label)
                captured = None
                if saved and item.get("name") == INDEX_NAMES[code]:
                    taken = datetime.fromisoformat(saved["captured_at"])
                    closed = datetime.fromisoformat(stamp).replace(tzinfo=end.tzinfo)
                    if taken.tzinfo is not None and closed <= taken <= self.as_of:
                        captured = taken
                if stamp in live_rows:
                    row = live_rows[stamp]
                    if captured and money(row["amount"]) != money(saved["amount"]):
                        raise ValueError(f"实时与同源日档金额冲突：{stamp}")
                else:
                    if captured is None:
                        raise ValueError(f"无当时可用的同源日档：{stamp}")
                    row = {"ts_code": code, "trade_time": stamp, "period_minutes": 5,
                           "amount": saved["amount"], "amount_unit": "CNY"}
                    evidence.append({"trade_time": stamp, "captured_at": captured.isoformat(),
                                     "amount": str(money(saved["amount"])), "amount_unit": "CNY",
                                     "archive": f"ohlcv-5m/{day}.json"})
                rows.append(row)
        result = compare_window(code, period, end, previous, rows, rule=rule)
        result["data_origin"] = {"kind": "archive_only" if len(evidence) == len(rows) else "live_and_archive",
                                 "primary_error": primary_error, "archive_rows": evidence}
        return result
