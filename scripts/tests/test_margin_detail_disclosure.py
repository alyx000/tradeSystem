from copy import deepcopy
from contextlib import closing
import json

import pytest

from providers import margin_detail as detail
from db.connection import get_readonly_connection
from services.ingest_service import IngestService


DAY = "2026-11-26"


def row(code, amount=1):
    return {"ts_code": code, "trade_date": "20261126", "rzmre": amount}


def get(rows, *, step=2):
    offsets = []

    def query(name, **params):
        assert name == "margin_detail" and params["trade_date"] == "20261126" and params["limit"] == 6000
        start = params["offset"]
        offsets.append(start)
        return rows[start:start + step]

    return detail.fetch(query, DAY), offsets


def test_short_pages_include_funds_in_raw_and_only_stocks_in_ranking():
    rows = [row("600001.SH", 0), row("510050.SH", 12345), row("302132.SZ", 3), row("920001.BJ", 2)]
    result, offsets = get(rows)
    assert offsets == [0, 2, 4]
    assert json.loads(result.note)["pages"] == [2, 2, 0]
    assert detail.receipt_is_published(result.data, result.to_dict(), DAY)
    block = detail.summary(result.data, result.to_dict(), DAY)
    assert block["status"] == "published" and block["other_security_count"] == 1
    assert [r["ts_code"] for r in block["top_rzmre"]] == ["302132.SZ", "920001.BJ", "600001.SH"]


@pytest.mark.parametrize("bad", [
    {"ts_code": "600001.SZ"}, {"code": "000001.SZ"}, {"trade_date": "20261125"},
    {"ts_code": "600001"}, {"ts_code": "510050.BJ"}, {"ts_code":"159919.BJ"},
    {"rzmre": float("nan")}, {"rzmre": True}, {"rzmre": -1},
])
def test_invalid_row_never_becomes_empty_or_published(bad):
    got, _ = get([{**row("600001.SH"), **bad}])
    assert not got.success and got.data is None


def test_duplicate_page_and_failure_after_valid_page_leave_no_partial_counts():
    got = detail.fetch(lambda _, **kw: [row("600001.SH")], DAY)
    assert not got.success and got.data is None
    got = detail.fetch(lambda _, **kw: [row("600001.SH")] if kw["offset"] == 0 else None, DAY)
    assert not got.success and got.data is None


def test_date_and_exchange_scope_and_missing_fields():
    empty, _ = get([])
    pending = detail.summary(empty.data, empty.to_dict(), DAY)
    assert pending["trade_date"] is None and pending["count"] is None and pending["status"] == "pending"
    part, _ = get([row("600001.SH")])
    sh = detail.summary(part.data, part.to_dict(), DAY)
    assert sh["status"] == "partial" and sh["exchanges"] == {"SH": 1} and sh["top_rzmre"] is None
    full, _ = get([row("600001.SH"), row("920001.BJ"), {**row("302132.SZ", None), "rzye": 3}])
    s = detail.summary(full.data, full.to_dict(), DAY)
    assert s["status"] == "partial" and s["missing_stock_buy_amount"] == 1 and s["top_rzmre"] is None
    assert not detail.receipt_is_published(full.data, full.to_dict(), "2026-11-27")
    corrupted = deepcopy(full.data)
    corrupted[1]["ts_code"] = "000001.SZ"
    assert not detail.receipt_is_published(corrupted, full.to_dict(), DAY)


def test_render_distinct_requested_and_previous_disclosure_dates():
    empty, _ = get([])
    block = detail.summary(empty.data, empty.to_dict(), DAY)
    prior = {**block, "status": "published", "requested_date": "2026-11-25", "trade_date": "2026-11-25",
             "exchanges": {"SH": 2, "SZ": 1, "BJ": 1}, "stock_count": 3, "other_security_count": 1}
    lines = detail.report_lines({**block, "previous_disclosure": prior})
    assert "指定日逐证券明细 2026-11-26，尚无已披露记录" in lines[0]
    assert "上一开放日逐证券明细 2026-11-25" in lines[1] and "其他证券 1 只" in lines[1]


def test_late_and_invalid_receipt_times_are_explicit():
    result, _ = get([row("600001.SH")])
    meta = result.to_dict()
    for at in (None, "2026-11-26T20:00:00", "2026-11-26T13:30:00+08:00", "2026-11-25T18:00:00+08:00"):
        assert detail.summary(result.data, {**meta, "fetched_at": at}, DAY)["status"] == "source_failed"
    observed = "2026-11-27T09:02:00+08:00"
    lines = detail.report_lines(detail.summary(result.data, {**meta, "fetched_at":observed}, DAY))
    assert "事后取得于上海时间 2026-11-27 09:02" in lines[0]
    requests = []
    assert not detail.fetch(lambda *a, **k: requests.append(k), "2999-12-01").success and not requests


def test_canonical_keeps_richer_original_and_first_identical_receipt(tmp_path):
    class Registry:
        def supports(self, method):
            return method == "get_margin_detail"

        def call(self, method, day):
            return self.result

    registry = Registry()
    registry.providers = [registry]
    service = IngestService(str(tmp_path/"data.db"), registry)
    source = [{**row(c), "rzye": 20., "rqye": 0.} for c in ("600001.SH", "302132.SZ", "920001.BJ")]

    def submit(rows, time):
        registry.result = get(rows)[0]
        registry.result.fetched_at = f"2026-11-27T{time}+08:00"
        attempt = service.execute_interface("margin_detail", DAY, input_by="USER")
        with closing(get_readonly_connection(service.db_path)) as conn:
            stored = conn.execute("SELECT row_count,status,payload_json,source_meta_json,inserted_at FROM raw_interface_payloads").fetchone()
        return attempt["status"], tuple(stored)

    status, initial = submit(source,"09:00:00")
    assert status == "success"
    assert submit(list(reversed(source)),"09:01:00") == ("success",initial)
    assert submit([{**r,"rqye":None} for r in source],"09:02:00") == ("success",initial)
    assert submit([{**r,"rzmre":None} for r in source],"09:03:00") == ("partial",initial)
    assert submit(source[:1],"09:04:00") == ("partial",initial)
    assert submit([],"09:05:00") == ("empty",initial)
