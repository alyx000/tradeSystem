"""覆盖闭合网格、重放修复、矛盾和故障隔离。"""
from copy import deepcopy
from datetime import datetime
import json

import pytest

from services.index_volume_monitor.bar_archive import ENDS, archive_fetched
from services.index_volume_monitor.rules import INDEX_NAMES
from tools.index_bar_archive import recover_opening


TODAY = "2026-11-27"
PREV = "2026-11-26"
FIRST = next(iter(INDEX_NAMES))


def quotes():
    return {code: ([
        {"ts_code": code, "trade_time": f"{day} {end}", "period_minutes": 5,
         "open": "100.05", "high": "101", "low": "99", "close": "100.20",
         "volume": "2.00", "amount": "12.30", "amount_unit": "CNY"}
        for day in (PREV, TODAY) for end in ENDS
    ], None) for code in INDEX_NAMES}


def save(tmp, rows, at="10:00", input_by="test"):
    end = datetime.fromisoformat(f"{TODAY}T{at}:00+08:00")
    return archive_fetched(tmp, rows, previous=PREV, end=end, captured_at=end.isoformat(), input_by=input_by)


def day(path):
    return json.loads((path / f"{TODAY}.json").read_text())


def test_live_rollup_across_lunch_and_no_future_bars(tmp_path):
    rows = quotes()
    result = save(tmp_path, rows)
    assert result[PREV]["valid_count"] == 192 and result[TODAY]["valid_count"] == 24
    archived = day(tmp_path)
    index = archived["indices"][FIRST]
    assert list(index["bars"]) == list(ENDS[:6])
    assert index["missing_due"] == [] and index["status"] == "partial"
    assert index["bars_30m"] == {"10:00": {"open":"100.05","close":"100.2","high":"101",
                                          "low":"99","amount":"73.8","volume":"12"}}
    before = (tmp_path / f"{TODAY}.json").read_bytes()
    save(tmp_path, rows, input_by="another")
    assert (tmp_path / f"{TODAY}.json").read_bytes() == before
    result = save(tmp_path, rows, "15:00")
    assert result[TODAY]["status"] == "complete"
    assert list(day(tmp_path)["indices"][FIRST]["bars_30m"]) == list(ENDS[5::6])


def test_missing_retried_without_overwriting_verified_rows(tmp_path):
    rows = quotes()
    full = deepcopy(rows)
    rows[FIRST][0].pop(49)
    save(tmp_path, rows, "15:00")
    first = day(tmp_path)["indices"][FIRST]
    assert first["missing_due"] == [ENDS[1]] and len(first["bars_30m"]) == 7
    save(tmp_path, full, "15:00", input_by="retry")
    first = day(tmp_path)["indices"][FIRST]
    assert first["bars"][ENDS[1]]["input_by"] == "retry" and first["bars"][ENDS[0]]["input_by"] == "test"


@pytest.mark.parametrize("dirty", ["unit","identity","duplicate","negative_volume","bad_ohlc","off_grid"])
def test_invalid_index_cannot_cross_contaminate_others(tmp_path, dirty):
    rows = quotes()
    r = rows[FIRST][0][48]
    if dirty == "duplicate":
        rows[FIRST][0].append(dict(r))
    else:
        key, value = {"unit":("amount_unit","USD"), "identity":("ts_code","other"),
                      "negative_volume":("volume","-1"), "bad_ohlc":("low","102"),
                      "off_grid":("trade_time", TODAY + " 12:30")}[dirty]
        r[key] = value
    result = save(tmp_path, rows)
    assert result[TODAY]["valid_count"] == 18
    idx = day(tmp_path)["indices"][FIRST]
    assert idx["missing_due"] == list(ENDS[:6]) and idx["source_error"]


def test_conflicting_bar_and_corrupt_archive_preserved(tmp_path):
    rows = quotes()
    save(tmp_path, rows)
    rows[FIRST][0][48]["amount"] = "24.98"
    save(tmp_path, rows)
    first = day(tmp_path)["indices"][FIRST]
    assert first["bars"][ENDS[0]]["amount"] == "12.3" and first["conflicts"][ENDS[0]]["amount"] == "24.98"
    assert first["valid_count"] == 5 and not first["bars_30m"]
    path = tmp_path / f"{TODAY}.json"
    path.write_text(path.read_text().replace("eastmoney:index_5min","other:source"))
    before = path.read_bytes()
    assert save(tmp_path, rows)[TODAY]["status"] == "archive_error"
    assert path.read_bytes() == before


def test_recover_actual_window_rejects_extra_day_rows(tmp_path):
    path = tmp_path / TODAY
    path.mkdir()
    rows = quotes()
    end = datetime.fromisoformat(TODAY + "T15:00:00+08:00")
    at = TODAY + "T10:02:00+08:00"
    items = [{"code":code, "status":"complete", "source_bars":data[48:54]} for code,(data, _) in rows.items()]
    items[0]["source_bars"].append(rows[FIRST][0][54])
    (path / "1000-example.json").write_text(json.dumps({
        "captured_at":at, "opening_environment":{"source":"eastmoney:index_5min","trade_date":TODAY,
        "previous_trade_date":PREV,"teacher_note_id":1178,"captured_at":at,"items":items}}))
    (path / "1000-broken.json").write_text("{invalid")
    result, errors = recover_opening(tmp_path,TODAY,PREV,end,"pytest")
    assert len(errors) == 2
    assert result[0][TODAY]["valid_count"] == 18 and day(tmp_path / "ohlcv-5m")["status"] == "partial"


def test_concurrent_disjoint_writers_merge_the_same_day(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    rows = quotes()
    with ThreadPoolExecutor(max_workers=4) as workers:
        jobs = [workers.submit(save, tmp_path, {code: payload}, "15:00") for code, payload in rows.items()]
        for job in jobs:
            job.result()
    assert day(tmp_path)["status"] == "complete" and day(tmp_path)["valid_count"] == 192


def test_replay_queries_only_missing_indices_and_preserves_earlier_values(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from tools import index_bar_archive as cli

    rows = quotes()
    codes = list(INDEX_NAMES)
    limited = {code: (data[:-1] if code in codes[:2] else data, err) for code, (data, err) in rows.items()}
    save(tmp_path / "ohlcv-5m", limited, "15:00")
    monkeypatch.setattr(cli, "DEFAULT_REPORT_ROOT", tmp_path)
    monkeypatch.setattr(cli, "calendar_basis", lambda _: ("complete", PREV))
    monkeypatch.setattr(sys, "argv", ["index_bar_archive.py", "--date", TODAY, "--input-by", "USER"])
    calls = []
    def request(self, code, start, end, *, period_minutes):
        calls.append(code)
        assert (start, end, period_minutes) == (TODAY, TODAY, 5)
        if code == codes[0]:
            raise RuntimeError("index unavailable")
        data = deepcopy(rows[code][0])
        data[48]["amount"] = "999"
        return SimpleNamespace(success=True, source="eastmoney:index_5min", data=data)
    monkeypatch.setattr(cli.EastmoneyProvider, "get_index_intraday_bars", request)
    assert cli.main() == 2
    assert sorted(calls) == sorted(codes[:2])
    archived = day(tmp_path / "ohlcv-5m")["indices"]
    assert archived[codes[0]]["valid_count"] == 47
    second = archived[codes[1]]
    assert second["valid_count"] == 48 and not second["conflicts"]
    assert second["bars"]["09:35"]["amount"] == "12.3"
    assert second["bars"]["15:00"]["acquisition"] == "post_close_replay"
    assert second["bars"]["15:00"]["input_by"] == "USER"
    # A later retry asks only the index that is still missing a bar.
    calls.clear()
    assert cli.main() == 2 and calls == [codes[0]]
