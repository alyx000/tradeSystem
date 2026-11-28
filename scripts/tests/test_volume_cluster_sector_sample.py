import json
import sqlite3
from copy import deepcopy

import pytest

from analyzers import sector_increment
from services.trend_leader import observations, volume_cluster


DAY, PREV = "2026-11-27", "2026-11-26"


def partial_block():
    ids = [dict(ts_code=f"{600000+i:06}.SH", list_date="20000101") for i in range(100)]
    industries = {r["ts_code"]: {"sw_l2": "银行" if i < 50 else "电子"} for i, r in enumerate(ids)}
    def quotes(day, skip, amount):
        return [dict(ts_code=r["ts_code"], trade_date=day, amount=amount)
                for i, r in enumerate(ids) if i != skip]
    return sector_increment.calculate(quotes(DAY, 1, 200000), quotes(PREV, 2, 100000),
                                      ids, industries, DAY, PREV, min_count=2)


def read_sector(block, envelope):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_market(date, raw_data)")
    conn.execute("INSERT INTO daily_market VALUES(?,?)", (DAY, json.dumps(envelope)))
    conn.execute("CREATE TABLE raw_interface_payloads(id,interface_name,biz_date,target_date,provider,status,row_count,payload_json)")
    for index, d in enumerate((PREV, DAY)):
        code = block["coverage"][d]["missing_codes"][0]
        rows = [dict(ts_code=code, trade_date=d.replace("-", ""), suspend_type="S",
                     suspend_timing=None if d == PREV else "09:30-10:30")]
        payload = dict(interface_name="regulatory_suspend", biz_date=d, provider="tushare:suspend_d",
                       params={"trade_date": d.replace("-", "")}, rows=rows)
        conn.execute("INSERT INTO raw_interface_payloads VALUES(?,?,?,?,?,?,?,?)",
                     (index+1, "regulatory_suspend", d, d, "tushare:suspend_d", "success", 1, json.dumps(payload)))
    try:
        return observations.load_sector(conn, DAY)
    finally:
        conn.close()


def test_same_day_sector_forms_fixed_sample_without_approving_full_sector():
    block = partial_block()
    loaded = read_sector(block, {"date": DAY, "raw_data": {"sector_increment": block}})
    assert loaded["status"] == "partial"
    candidate = dict(code="600000", name="测试", industry="银行")
    result = volume_cluster.collect(DAY, [candidate], {}, loaded, [])
    row = result["rows"][0]
    assert row["sector_support"]["supported"] is None
    sample = row["sector_sample"]
    assert sample["member_count"] == 48
    assert sample["previous_amount_yi"] == 48
    assert sample["amount_yi"] == 96
    assert result["coverage"]["sector_sample_available"] == 1
    assert result["coverage"]["sector_verified"] == 0
    assert result["status"] == "partial"
    assert result["sector_sample_coverage"][PREV]["verified_full_day"] == ["600002.SH"]
    assert result["sector_sample_coverage"][DAY]["unexplained"] == ["600001.SH"]
    output = "\n".join(volume_cluster.render_markdown(result, DAY))
    assert "共同有行情" in output and "全天停牌" in output
    corrupted = deepcopy(result)
    corrupted["rows"][0]["sector_sample"]["amount_yi"] = 9999
    assert volume_cluster.validate(corrupted, DAY)["status"] == "source_failed"


def test_nested_date_conflict_and_broken_nested_value_are_rejected():
    block = partial_block()
    assert read_sector(block, {"sector_increment": block})["status"] == "partial"
    assert read_sector(block, {"date": PREV, "raw_data": {"sector_increment": block}}) is None
    assert read_sector(block, {"raw_data": None, "sector_increment": block}) is None


def test_existing_partial_snapshot_keeps_its_original_shape_and_verifies():
    block = partial_block()
    candidate = dict(code="600000", name="测试", industry="银行")
    legacy = dict(candidates=[candidate], quotes={}, sector_block=block, upstream_gaps=[])
    old = volume_cluster.calculate(DAY, legacy)
    assert volume_cluster.validate(old, DAY) == old
    assert "sector_sample" not in old["rows"][0]
    assert "sector_sample_available" not in old["coverage"]


def test_empty_watch_list_does_not_turn_partial_source_complete():
    block = partial_block()
    fresh = volume_cluster.collect(DAY, [], {}, block, [])
    assert fresh["status"] == "partial"
    assert fresh["coverage"]["sector_sample_available"] == 0
    assert fresh["sector_sample_coverage"][DAY]["unexplained"] == ["600001.SH"]


@pytest.mark.parametrize("mutation", ["duplicate", "wrong_total", "wrong_issuer"])
def test_malformed_components_do_not_create_sample(mutation):
    block = partial_block()
    loaded = read_sector(block, {"sector_increment": block})
    parts = loaded["components"]
    if mutation == "duplicate":
        parts.append(deepcopy(parts[0]))
    elif mutation == "wrong_total":
        parts[0]["amount_yi"] += 50
    else:
        parts[0]["industry"] = "假行业"
    result = volume_cluster.collect(DAY, [dict(code="600000", name="测试", industry="银行")], {}, loaded, [])
    assert result["coverage"]["sector_verified"] == 0
    assert not result["rows"][0].get("sector_sample")
