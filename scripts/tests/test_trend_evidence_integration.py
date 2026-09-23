import json
import sqlite3
from types import SimpleNamespace
import pytest

from db.schema import init_schema
from services.trend_leader import scanner, pool, renderer, research_evidence as E
from analyzers import sector_increment as S
from tests.test_trend_leader_observations import bars


@pytest.mark.parametrize("enveloped", [False, True])
def test_enrichment_keeps_pool_rules_and_rerun_count(enveloped):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    day = "2026-07-30"
    quote = lambda d, amounts: [dict(ts_code=c, trade_date=d, amount=a*1e5) for c, a in zip(["600000.SH", "000001.SZ"], amounts)]
    identities = [dict(ts_code=c, list_date="20000101") for c in ["600000.SH", "000001.SZ"]]
    mapping = {"600000.SH": {"sw_l2": "半导体"}, "000001.SZ": {"sw_l2": "银行"}}
    block = S.calculate(quote(day, [160, 90]), quote("2026-07-29", [100, 100]), identities, mapping,
                        day, "2026-07-29", min_count=2)
    from db.dual_write import _extract_market_row
    from db.queries import upsert_daily_market
    source = {"sector_increment": block}
    envelope = {"date": day, "raw_data": source} if enveloped else source
    upsert_daily_market(conn, _extract_market_row(day, envelope))
    conn.commit()
    pool.record(conn, code="600000", name="测试证券", sw_l2="半导体", first_limit_date="2026-07-28", date="2026-07-29")

    class Registry:
        calls = []

        def call(self, method, *args):
            self.calls.append(method)
            values = {
                "get_limit_up_list": {"stocks": []}, "get_stock_sw_industry_map": mapping,
                "get_market_daily_changes": [], "get_stock_daily_range": bars(),
                "get_stock_adj_factor_range": [dict(trade_date=b["trade_date"], adj_factor=1) for b in bars()],
                "get_stock_holder_numbers": [dict(ts_code="600000.SH", ann_date=a, end_date=e, holder_num=n)
                                             for a, e, n in [("20260430", "20260331", 1000), ("20260729", "20260630", 900)]],
                "get_income_history": [],
            }
            return SimpleNamespace(data=values.get(method), success=method in values, source="test")

    reg = Registry()
    for iteration in range(2):
        result = scanner.run_daily(conn, reg, day, main_line="l2")
        row = pool.get_active(conn, "600000")
        assert row["days_in_pool"] == 2
        assert result["entered"] == [] and result["exited"] == []
        assert result["launch_pullbacks"][0]["state"] == "pullback_observed"
        assert row["last_signal"]["launch_pullback"]["launch_date"] == "2026-07-28"
        assert row["last_signal"]["research_evidence"]["holders"]["latest"]["value"] == 900
        assert "放量后缩量回踩成立" in renderer.render_daily(conn, result)
    assert reg.calls.count("get_stock_daily_range") == 2  # 新观察复用行情，不多取一次。
    assert reg.calls.count("get_stock_holder_numbers") == 2
    conn.close()


@pytest.mark.parametrize("factor_status", ["source_failed", "invalid", "short"])
def test_factor_failure_keeps_cause_and_pool_state(factor_status):
    from services.trend_leader import review_evidence as R
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row; init_schema(conn)
    day = "2026-07-30"
    pool.record(conn, code="600000", name="测试证券", sw_l2="半导体", first_limit_date="2026-07-28", date="2026-07-29")
    price_bars = bars()[-29:] if factor_status == "short" else bars()
    class Registry:
        def call(self, method, *args):
            data = {"get_limit_up_list": {"stocks": []}, "get_market_daily_changes": [],
                    "get_stock_sw_industry_map": {"600000.SH": {"sw_l2": "半导体"}},
                    "get_stock_daily_range": price_bars,
                    "get_stock_adj_factor_range": [] if factor_status == "invalid" else [dict(trade_date=b["trade_date"], adj_factor=1) for b in price_bars]}
            ok = method in data and not (method == "get_stock_adj_factor_range" and factor_status == "source_failed")
            return SimpleNamespace(success=ok, data=data.get(method), source="test", error=None if ok else "fixture factor outage")
    result = scanner.run_daily(conn, Registry(), day, main_line="l2")
    obs = result["launch_pullbacks"][0]
    assert obs["status"] == "partial" and obs["state"] == "missing_data"
    assert pool.get_active(conn, "600000")["days_in_pool"] == 2
    assert not result["entered"] and not result["exited"]
    snapshot = R.build(result, "test report")
    assert snapshot["status"] == "partial"
    if factor_status == "source_failed":
        assert "source_failed" in obs["reason"] and "fixture factor outage" in obs["reason"]
        assert any("get_stock_adj_factor_range[600000]" in gap for gap in snapshot["gaps"])
        assert "fixture factor outage" in R.render(snapshot, day)[0]
    elif factor_status == "invalid":
        assert "覆盖校验未通过" in obs["reason"] and result["data_errors"]
    else:
        assert "不足30根" in obs["reason"] and not result["source_errors"]
    conn.close()


def test_evidence_card_coverage_cap_is_visible(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    for i in range(14):
        pool.record(conn, code=str(600000+i), name="测试", sw_l2="行业", first_limit_date="2026-07-28", date="2026-07-29")
    class Registry:
        def call(self, method, *args):
            return SimpleNamespace(success=method == "get_limit_up_list", data={"stocks": []} if method == "get_limit_up_list" else None)
    calls = []
    monkeypatch.setattr(E, "collect", lambda reg, code, day: calls.append(code) or dict(code=code, trade_date=day, status="partial"))
    result = scanner.run_daily(conn, Registry(), "2026-07-30", main_line="l2")
    assert len(calls) == E.MAX_CARDS
    assert result["research_coverage"]["not_collected"] == ["600012", "600013"]
    assert all(r["last_seen_date"] == "2026-07-29" for r in pool.list_pool(conn))  # 缺行情不能靠新证据推进日期。
    conn.close()
