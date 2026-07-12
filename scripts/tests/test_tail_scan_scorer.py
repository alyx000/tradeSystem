import sqlite3

from services.tail_scan import scorer


def _card(**kw):
    base = {"code": "600001.SH", "name": "x", "in_main_sector": False,
            "in_hot_concept": False, "teacher_hit": False, "rank_in_pool": 5,
            "first_surge": False, "ma_above": False, "broke_high": False,
            "is_limit_up": False, "close_pos": 0.5}
    base.update(kw)
    return base


def test_score_rewards_main_sector_and_concept():
    strong = _card(in_main_sector=True, in_hot_concept=True, teacher_hit=True,
                   rank_in_pool=1, first_surge=True, ma_above=True,
                   broke_high=True, is_limit_up=True, close_pos=1.0)
    weak = _card()
    scored = scorer.score_all([weak, strong])
    assert scored[0]["code"] == strong["code"]        # 强票排前
    assert scored[0]["total"] > scored[1]["total"]
    assert scored[0]["rank_score"] == 1


def test_score_all_stable_tiebreak_by_code():
    a = _card(code="600009.SH")
    b = _card(code="600008.SH")
    scored = scorer.score_all([a, b])
    assert [c["code"] for c in scored] == ["600008.SH", "600009.SH"]  # 同分裸码字典序


class _R:
    def __init__(self, data=None, error=None):
        self.data, self.error = data, error
        self.success = error is None and data is not None


class _Reg:
    """全维度降级 mock（概念/大势/行业映射全失败）。"""
    def call(self, cap, *a):
        if cap == "get_stock_daily_range":
            return _R([{"trade_date": "2026-07-10", "close": 10.0, "high": 10.2,
                        "amount": 1e5, "pct_chg": 3.0}])  # amount 单位=千元
        return _R(error="源失败")   # concept / sw_industry_map 均降级


def _mk_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE teacher_notes (id INTEGER, date TEXT, title TEXT, "
                 "core_view TEXT, key_points TEXT, sectors TEXT)")
    conn.execute("CREATE TABLE daily_volume_concentration (date TEXT)")  # 空 → 主线降级
    return conn


def _scan1():
    return {"candidates": [{"code": "600001.SH", "name": "测试股", "pct_chg": 8.0,
                            "price": 12.0, "amount_yi": 25.0, "is_limit_up": False,
                            "close_pos": 0.8, "amplitude": 5.0}]}


def test_build_fact_cards_degrades_without_crash():
    cards = scorer.build_fact_cards(_mk_conn(), _Reg(), _scan1(), params={"date": "2026-07-13"})
    assert len(cards) == 1
    assert cards[0]["concept_status"] == "source_failed"
    assert cards[0]["index_status"] == "missing"    # market_timing_signal 表不存在 → 降级
    assert cards[0]["in_hot_concept"] is False
    assert cards[0]["in_main_sector"] is False       # 行业映射失败 → 主线降级


class _RegPos:
    """全维度命中 mock：概念(index_name)/申万行业映射/日线齐全。"""
    def call(self, cap, *a):
        if cap == "get_concept_moneyflow_ths":
            return _R([{"name": "AI算力", "net_amount_yi": 5.0}])
        if cap == "get_ths_member":
            return _R([{"con_code": "600001", "index_name": "AI算力"}])
        if cap == "get_stock_sw_industry_map":
            return _R({"600001.SH": {"name": "测试股", "sw_l2": "半导体"}})
        if cap == "get_stock_daily_range":
            return _R([{"trade_date": "2026-07-10", "close": 10.0, "high": 10.2,
                        "amount": 1e5, "pct_chg": 3.0}])
        return _R([])


def test_build_fact_cards_positive_hits(monkeypatch):
    """正向命中：坐实 in_main_sector/in_hot_concept/大势 三个子信号可真为真
    （若 index_name/行业映射/读表任一接线错，本测试会红——审查要求的回归护栏）。"""
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    conn = _mk_conn()
    conn.execute("CREATE TABLE market_timing_signal (trade_date TEXT, index_code TEXT, "
                 "index_name TEXT, change_pct REAL, bottom_phase TEXT)")
    conn.execute("INSERT INTO market_timing_signal VALUES "
                 "('2026-07-11','000001.SH','上证指数',0.8,'confirmed')")
    cards = scorer.build_fact_cards(conn, _RegPos(), _scan1(), params={"date": "2026-07-13"})
    c = cards[0]
    assert c["in_main_sector"] is True
    assert c["in_hot_concept"] is True and c["concept_names"] == ["AI算力"]
    assert c["index_status"] == "ok" and "上证指数" in c["index_context"]
