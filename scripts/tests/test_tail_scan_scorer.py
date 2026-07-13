import sqlite3

import pytest

from services.tail_scan import industry_logic
from services.tail_scan import pk as tail_pk
from services.tail_scan import scorer


@pytest.fixture
def industry_logic_stub():
    return {"calls": [], "result": None, "error": None}


@pytest.fixture(autouse=True)
def _stub_industry_logic(monkeypatch, industry_logic_stub):
    """所有 scorer 测试隔离主营 provider、本地证据表和慧博文件。"""
    def fake_builder(conn, registry, candidates, **kwargs):
        industry_logic_stub["calls"].append({"candidates": candidates, **kwargs})
        if industry_logic_stub["error"] is not None:
            raise industry_logic_stub["error"]
        if industry_logic_stub["result"] is not None:
            return industry_logic_stub["result"]
        industry_map = kwargs.get("industry_map") or {}
        return {
            row["code"]: {
                "sw_l2": (industry_map.get(row["code"]) or {}).get("sw_l2", ""),
                "business_summary": "",
                "product_names": [],
                "business_source": "",
                "business_status": "missing",
                "industry_position": "",
                "catalyst_evidence": [],
                "catalyst_status": "none",
            }
            for row in candidates
        }

    monkeypatch.setattr(industry_logic, "build_industry_logic_map", fake_builder)


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
    def __init__(self, data=None, error=None, *, source="fake", fetched_at=""):
        self.data, self.error = data, error
        self.success = error is None and data is not None
        self.source = source
        self.fetched_at = fetched_at


def _membership_result(*concepts, status="ok", source="fake:memberships", fetched_at=""):
    return _R(
        {"stocks": {"600001.SH": {"status": status, "concepts": list(concepts)}}},
        source=source,
        fetched_at=fetched_at,
    )


def _membership(name, member_count, concept_code):
    return {
        "concept_code": concept_code,
        "name": name,
        "member_count": member_count,
    }


class _Reg:
    """全维度降级 mock（概念/大势/行业映射全失败）。"""
    def call(self, cap, *a):
        if cap == "get_stock_daily_range":
            return _R([{"trade_date": "2026-07-10", "close": 10.0, "high": 10.2,
                        "amount": 1e5, "pct_chg": 3.0}])  # amount 单位=千元
        if cap in {
            "is_trade_day", "get_concept_moneyflow_ths",
            "get_stock_concept_memberships", "get_stock_sw_industry_map",
        }:
            return _R(error="源失败")
        raise AssertionError(f"unexpected capability: {cap}")


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
        if cap == "is_trade_day":
            return _R(a[0] == "2026-07-10")
        if cap == "get_concept_moneyflow_ths":
            return _R([
                {"name": "AI算力", "net_amount_yi": 5.0, "company_num": 100},
                *[
                    {"name": f"热概念{i}", "net_amount_yi": 5.0 - i,
                     "company_num": 20 + i}
                    for i in range(1, 8)
                ],
            ])
        if cap == "get_stock_concept_memberships":
            return _membership_result(
                _membership("AI算力", 100, "885001.TI"),
                _membership("存储芯片", 180, "885002.TI"),
            )
        if cap == "get_stock_sw_industry_map":
            return _R({"600001.SH": {"name": "测试股", "sw_l2": "半导体"}})
        if cap == "get_stock_daily_range":
            return _R([{"trade_date": "2026-07-10", "close": 10.0, "high": 10.2,
                        "amount": 1e5, "pct_chg": 3.0}])
        raise AssertionError(f"unexpected capability: {cap}")


def test_build_fact_cards_positive_hits(monkeypatch):
    """正向命中：坐实 in_main_sector/in_hot_concept/大势 三个子信号可真为真
    （若 index_name/行业映射/读表任一接线错，本测试会红——审查要求的回归护栏）。"""
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    conn = _mk_conn()
    conn.execute("CREATE TABLE market_timing_signal (trade_date TEXT, index_code TEXT, "
                 "index_name TEXT, change_pct REAL, bottom_phase TEXT)")
    conn.execute("INSERT INTO market_timing_signal VALUES "
                 "('2026-07-10','000001.SH','上证指数',0.8,'confirmed')")
    cards = scorer.build_fact_cards(conn, _RegPos(), _scan1(), params={"date": "2026-07-13"})
    c = cards[0]
    assert c["in_main_sector"] is True
    assert c["in_hot_concept"] is True and c["concept_names"] == ["AI算力"]
    assert c["stock_concept_names"] == ["AI算力", "存储芯片"]
    assert c["stock_concept_total"] == 2
    assert c["index_status"] == "ok" and "上证指数" in c["index_context"]


def test_build_fact_cards_wires_industry_logic_once(industry_logic_stub, monkeypatch):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    industry_logic_stub["result"] = {
        "600001.SH": {
            "sw_l2": "",  # builder 空值须回退已有申万行业映射
            "business_summary": "晶圆制造设备",
            "product_names": ["刻蚀机", "薄膜设备"],
            "business_source": "tushare.stock_company",
            "business_status": "ok",
            "industry_position": "半导体产业链企业，核心产品包括刻蚀机、薄膜设备",
            "catalyst_evidence": [{
                "kind": "huibo_stock", "label": "研报观点·个股催化",
                "date": "2026-07-12", "source": "测试研报", "text": "新增产线验证",
            }],
            "catalyst_status": "exact",
        }
    }
    cards = scorer.build_fact_cards(
        _mk_conn(), _RegPos(), _scan1(),
        params={
            "date": "2026-07-13", "industry_logic_lookback": 20,
            "huibo_summary_dir": "/tmp/never-read-by-stub",
        },
    )
    assert len(industry_logic_stub["calls"]) == 1
    call = industry_logic_stub["calls"][0]
    assert call["scan_date"] == "2026-07-13"
    assert call["lookback_days"] == 20
    assert call["huibo_dir"] == "/tmp/never-read-by-stub"
    assert call["concept_map"] == {"600001": ["AI算力", "存储芯片"]}
    assert call["industry_map"]["600001.SH"]["sw_l2"] == "半导体"
    assert cards[0]["business_summary"] == "晶圆制造设备"
    assert cards[0]["product_names"] == ["刻蚀机", "薄膜设备"]
    assert cards[0]["sw_l2"] == "半导体"
    assert cards[0]["catalyst_status"] == "exact"


def test_prev_date_failure_still_fetches_current_memberships(
    industry_logic_stub, monkeypatch
):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda registry, date: None)
    calls = []

    class Registry:
        def call(self, capability, *args):
            calls.append((capability, args))
            if capability == "get_stock_concept_memberships":
                return _membership_result(
                    _membership("当前归属", 30, "885010.TI"),
                    source="tushare:ths_member:by_stock",
                    fetched_at="2026-07-13T14:40:00",
                )
            if capability == "get_stock_sw_industry_map":
                return _R({"600001.SH": {"sw_l2": "半导体"}})
            raise AssertionError(f"unexpected capability: {capability}")

    card = scorer.build_fact_cards(
        _mk_conn(), Registry(), _scan1(), params={"date": "2026-07-13"}
    )[0]

    assert [capability for capability, _args in calls] == [
        "get_stock_concept_memberships",
        "get_stock_sw_industry_map",
    ]
    assert card["stock_concept_names"] == ["当前归属"]
    assert card["stock_concept_status"] == "ok"
    assert card["stock_concept_source"] == "tushare:ths_member:by_stock"
    assert card["stock_concept_snapshot_at"] == "2026-07-13T14:40:00"
    assert card["concept_names"] == []
    assert card["concept_status"] == "source_failed"
    assert industry_logic_stub["calls"][0]["concept_map"] == {
        "600001": ["当前归属"]
    }


def test_membership_metadata_survives_industry_lookup_for_reranking_without_leak(
    industry_logic_stub, monkeypatch
):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda registry, date: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, top_k: (set(), False))
    monkeypatch.setattr(scorer, "_index_context", lambda conn, date: ("", "missing"))
    industry_logic_stub["result"] = {
        "600001.SH": {
            "sw_l2": "电子化学品",
            "business_summary": "狭义气体研发生产",
            "product_names": ["狭义气体"],
            "business_source": "tushare:stock_company",
            "business_status": "ok",
            "industry_position": "电子化学品产业链企业，核心产品包括狭义气体",
            "catalyst_evidence": [],
            "catalyst_status": "none",
        }
    }
    calls = []

    class Registry:
        def call(self, capability, *args):
            calls.append((capability, args))
            if capability == "get_stock_concept_memberships":
                return _membership_result(
                    _membership("狭义气体", 200, "885100.TI"),
                    _membership("宽泛标签", 10, "885200.TI"),
                    _membership("重复代码别名", 1, "885100.TI"),
                    _membership("狭义气体", 2, "885300.TI"),
                    source="tushare:ths_member:by_stock",
                    fetched_at="2026-07-13T14:40:01",
                )
            if capability == "get_concept_moneyflow_ths":
                return _R([
                    {"name": "其它热概念", "net_amount_yi": 1,
                     "company_num": 20}
                ])
            if capability == "get_stock_sw_industry_map":
                return _R({"600001.SH": {"sw_l2": "电子化学品"}})
            if capability == "get_stock_daily_range":
                return _R([])
            raise AssertionError(f"unexpected capability: {capability}")

    card = scorer.build_fact_cards(
        _mk_conn(), Registry(), _scan1(),
        params={"date": "2026-07-13", "concept_top_m": 1},
    )[0]

    assert [capability for capability, _args in calls].count(
        "get_stock_concept_memberships"
    ) == 1
    assert all(capability != "get_ths_member" for capability, _args in calls)
    assert industry_logic_stub["calls"][0]["concept_map"] == {
        "600001": ["宽泛标签", "狭义气体"]
    }
    assert card["stock_concept_names"] == ["狭义气体", "宽泛标签"]
    assert card["stock_concept_total"] == 2
    assert card["stock_concept_source"] == "tushare:ths_member:by_stock"
    assert card["stock_concept_snapshot_at"] == "2026-07-13T14:40:01"
    assert "stock_concept_memberships" not in card
    assert "stock_concept_memberships" not in tail_pk._payload(card, card)["A"]


def test_complete_memberships_do_not_change_coarse_score():
    base = _card(in_hot_concept=False, stock_concept_names=[])
    enriched = {
        **base,
        "stock_concept_names": ["存储芯片", "第三代半导体"],
        "stock_concept_total": 2,
        "stock_concept_status": "ok",
    }
    assert scorer._coarse_score(base) == scorer._coarse_score(enriched)


def test_build_fact_cards_industry_logic_failure_keeps_card(industry_logic_stub, monkeypatch):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    industry_logic_stub["error"] = RuntimeError("产业逻辑整批失败")
    cards = scorer.build_fact_cards(_mk_conn(), _RegPos(), _scan1(), params={"date": "2026-07-13"})
    assert len(cards) == 1
    assert cards[0]["sw_l2"] == "半导体"
    assert cards[0]["business_summary"] == ""
    assert cards[0]["product_names"] == []
    assert cards[0]["business_status"] == "source_failed"
    assert cards[0]["catalyst_evidence"] == []
    assert cards[0]["catalyst_status"] == "source_failed"


def test_build_fact_cards_normalizes_malformed_industry_logic_row(industry_logic_stub, monkeypatch):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    industry_logic_stub["result"] = {
        "600001.SH": {
            "sw_l2": {"伪造": "行业"},
            "business_summary": ["伪造主营"],
            "product_names": [{"name": "伪造产品"}, " 合法\n产品 ", 7],
            "business_source": 123,
            "business_status": "ready",
            "industry_position": {"伪造": "位置"},
            "catalyst_evidence": [
                {"kind": "huibo_stock", "label": "研报观点·个股催化",
                 "date": "2026-07-12", "source": "研报", "text": "合法证据"},
                "畸形证据",
            ],
            "catalyst_status": "ready",
        }
    }
    cards = scorer.build_fact_cards(_mk_conn(), _RegPos(), _scan1(), params={"date": "2026-07-13"})
    card = cards[0]
    assert card["sw_l2"] == "半导体"
    assert card["business_summary"] == ""
    assert card["product_names"] == []
    assert card["business_source"] == ""
    assert card["industry_position"] == "半导体相关企业"
    assert card["business_status"] == "source_failed"
    assert card["catalyst_evidence"] == []
    assert card["catalyst_status"] == "source_failed"
    for key in ("sw_l2", "business_summary", "business_source", "industry_position"):
        assert isinstance(card[key], str)


@pytest.mark.parametrize(
    "business_status,catalyst_status",
    [({}, []), ([], set()), (set(), {})],
)
def test_normalize_logic_row_rejects_unhashable_statuses_without_raising(
    business_status, catalyst_status
):
    row = {
        "sw_l2": "半导体", "business_summary": "设备", "product_names": [],
        "business_source": "资料", "business_status": business_status,
        "industry_position": "半导体设备企业", "catalyst_evidence": [],
        "catalyst_status": catalyst_status,
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["business_status"] == "source_failed"
    assert normalized["catalyst_status"] == "source_failed"
    assert normalized["catalyst_evidence"] == []


def _complete_evidence(**overrides):
    item = {
        "kind": "huibo_stock", "label": "研报观点·个股催化",
        "date": "2026-07-12", "source": "测试研报", "text": "新增产线验证",
    }
    item.update(overrides)
    return item


@pytest.mark.parametrize(
    "status,evidence",
    [
        ("exact", []),
        ("sector", [_complete_evidence(text="")]),
        ("exact", [_complete_evidence(source="")]),
        ("none", [_complete_evidence()]),
        ("source_failed", [_complete_evidence()]),
    ],
)
def test_normalize_logic_row_enforces_catalyst_status_evidence_invariant(status, evidence):
    row = {
        "sw_l2": "半导体", "business_summary": "设备", "product_names": [],
        "business_source": "资料", "business_status": "ok",
        "industry_position": "半导体设备企业", "catalyst_evidence": evidence,
        "catalyst_status": status,
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["catalyst_status"] == "source_failed"
    assert normalized["catalyst_evidence"] == []


def test_source_failed_business_payload_cannot_leak_row_claims(industry_logic_stub, monkeypatch):
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda conn, date, k: ({"半导体"}, False))
    industry_logic_stub["result"] = {
        "600001.SH": {
            "sw_l2": "伪造行业",
            "business_summary": "恶意主营已兑现",
            "product_names": ["恶意产品"],
            "business_source": "恶意来源",
            "business_status": "source_failed",
            "industry_position": "行业龙头必然受益",
            "catalyst_evidence": [],
            "catalyst_status": "none",
        }
    }
    card = scorer.build_fact_cards(
        _mk_conn(), _RegPos(), _scan1(), params={"date": "2026-07-13"}
    )[0]
    assert card["sw_l2"] == "半导体"
    assert card["business_summary"] == ""
    assert card["product_names"] == []
    assert card["business_source"] == ""
    assert card["industry_position"] == "半导体相关企业"
    payload = str(tail_pk._payload(card, card))
    for leaked in ("伪造行业", "恶意主营", "恶意产品", "恶意来源", "行业龙头"):
        assert leaked not in payload


@pytest.mark.parametrize(
    "evidence",
    [
        [_complete_evidence(kind="industry", label="老师观点·个股", date="not-a-date")],
        [_complete_evidence(date="2026-07-14")],
    ],
)
def test_normalize_logic_row_rejects_bad_kind_label_pair_or_future_date(evidence):
    row = {
        "sw_l2": "伪造行业", "business_summary": "设备", "product_names": [],
        "business_source": "资料", "business_status": "ok",
        "industry_position": "半导体设备企业", "catalyst_evidence": evidence,
        "catalyst_status": "exact",
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["sw_l2"] == "半导体"
    assert normalized["catalyst_status"] == "source_failed"
    assert normalized["catalyst_evidence"] == []


@pytest.mark.parametrize(
    "status,evidence",
    [
        ("exact", [_complete_evidence(kind="teacher_stock", label="老师观点·个股")]),
        ("sector", [_complete_evidence(kind="industry", label="事实·行业催化")]),
    ],
)
def test_normalize_logic_row_keeps_valid_exact_and_sector_evidence(status, evidence):
    row = {
        "sw_l2": "伪造行业", "business_summary": "设备", "product_names": [],
        "business_source": "资料", "business_status": "ok",
        "industry_position": "半导体设备企业", "catalyst_evidence": evidence,
        "catalyst_status": status,
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["sw_l2"] == "半导体"
    assert normalized["catalyst_status"] == status
    assert normalized["catalyst_evidence"] == evidence


def test_normalize_logic_row_preserves_report_only_marker():
    evidence = [
        _complete_evidence(
            kind="industry",
            label="事实·行业催化",
            pk_eligible=False,
        )
    ]
    row = {
        "sw_l2": "半导体",
        "business_summary": "设备",
        "product_names": [],
        "business_source": "资料",
        "business_status": "ok",
        "industry_position": "半导体设备企业",
        "catalyst_evidence": evidence,
        "catalyst_status": "sector",
    }

    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")

    assert normalized["catalyst_status"] == "sector"
    assert normalized["catalyst_evidence"] == evidence


def test_normalize_logic_row_preserves_bounded_report_and_pk_evidence_union():
    evidence = [
        _complete_evidence(kind="teacher_stock", label="老师观点·个股"),
        _complete_evidence(
            kind="industry",
            label="事实·行业催化",
            text="仅由当前概念衍生",
            pk_eligible=False,
        ),
        _complete_evidence(
            kind="industry",
            label="事实·行业催化",
            text="主营直接覆盖该行业",
        ),
    ]
    row = {
        "sw_l2": "半导体",
        "business_summary": "设备",
        "product_names": [],
        "business_source": "资料",
        "business_status": "ok",
        "industry_position": "半导体设备企业",
        "catalyst_evidence": evidence,
        "catalyst_status": "exact",
    }

    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")

    assert normalized["catalyst_evidence"] == evidence


@pytest.mark.parametrize("marker", ["false", 0, None, [], {}])
def test_normalize_logic_row_rejects_malformed_report_only_marker(marker):
    row = {
        "sw_l2": "半导体",
        "business_summary": "设备",
        "product_names": [],
        "business_source": "资料",
        "business_status": "ok",
        "industry_position": "半导体设备企业",
        "catalyst_evidence": [
            _complete_evidence(
                kind="industry",
                label="事实·行业催化",
                pk_eligible=marker,
            )
        ],
        "catalyst_status": "sector",
    }

    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")

    assert normalized["catalyst_status"] == "source_failed"
    assert normalized["catalyst_evidence"] == []


def test_normalize_logic_row_rejects_report_only_marker_on_direct_evidence():
    row = {
        "sw_l2": "半导体",
        "business_summary": "设备",
        "product_names": [],
        "business_source": "资料",
        "business_status": "ok",
        "industry_position": "半导体设备企业",
        "catalyst_evidence": [
            _complete_evidence(
                kind="teacher_stock",
                label="老师观点·个股",
                pk_eligible=False,
            )
        ],
        "catalyst_status": "exact",
    }

    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")

    assert normalized["catalyst_status"] == "source_failed"
    assert normalized["catalyst_evidence"] == []


def test_current_concept_only_evidence_changes_report_but_not_score_or_pk_payload():
    base_logic = scorer._normalize_logic_row(
        {
            "sw_l2": "燃气",
            "business_summary": "天然气销售",
            "product_names": [],
            "business_source": "公司资料",
            "business_status": "ok",
            "industry_position": "燃气领域企业，主营天然气销售",
            "catalyst_evidence": [],
            "catalyst_status": "none",
        },
        "燃气",
        "2026-07-13",
    )
    concept_logic = scorer._normalize_logic_row(
        {
            **base_logic,
            "catalyst_evidence": [
                {
                    "kind": "industry",
                    "label": "事实·行业催化",
                    "date": "2026-07-12",
                    "source": "行业笔记",
                    "text": "页岩气行业产量更新",
                    "pk_eligible": False,
                }
            ],
            "catalyst_status": "sector",
        },
        "燃气",
        "2026-07-13",
    )
    base_card = {**_card(), **base_logic, "stock_concept_names": []}
    concept_card = {
        **_card(),
        **concept_logic,
        "stock_concept_names": ["页岩气"],
    }

    assert concept_card["catalyst_evidence"]
    assert scorer._coarse_score(base_card) == scorer._coarse_score(concept_card)
    assert tail_pk._payload(base_card, base_card) == tail_pk._payload(
        concept_card, concept_card
    )


def test_normalize_logic_row_rejects_ok_row_without_business_or_products():
    row = {
        "sw_l2": "伪造行业", "business_summary": "", "product_names": [],
        "business_source": "tushare.stock_company", "business_status": "ok",
        "industry_position": "", "catalyst_evidence": [], "catalyst_status": "none",
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["business_status"] == "source_failed"
    assert normalized["business_summary"] == ""
    assert normalized["product_names"] == []
    assert normalized["business_source"] == ""
    assert normalized["industry_position"] == "半导体相关企业"
    assert normalized["sw_l2"] == "半导体"


def test_normalize_logic_row_keeps_ok_product_only_content():
    row = {
        "sw_l2": "伪造行业", "business_summary": "", "product_names": ["储能系统"],
        "business_source": "akshare.stock_zyjs_ths", "business_status": "ok",
        "industry_position": "电池产业链企业，核心产品包括储能系统",
        "catalyst_evidence": [], "catalyst_status": "none",
    }
    normalized = scorer._normalize_logic_row(row, "电池", "2026-07-13")
    assert normalized["business_status"] == "ok"
    assert normalized["business_summary"] == ""
    assert normalized["product_names"] == ["储能系统"]
    assert normalized["business_source"] == "akshare.stock_zyjs_ths"


@pytest.mark.parametrize(
    "overrides",
    [
        {"business_source": ""},
        {"business_summary": {"非法": "容器"}},
        {"product_names": "非法列表"},
    ],
)
def test_normalize_logic_row_ok_still_fails_closed_for_missing_source_or_bad_types(overrides):
    row = {
        "sw_l2": "伪造行业", "business_summary": "设备", "product_names": [],
        "business_source": "公司资料", "business_status": "ok",
        "industry_position": "半导体设备企业",
        "catalyst_evidence": [], "catalyst_status": "none",
        **overrides,
    }
    normalized = scorer._normalize_logic_row(row, "半导体", "2026-07-13")
    assert normalized["business_status"] == "source_failed"
    assert normalized["business_summary"] == ""
    assert normalized["product_names"] == []
    assert normalized["business_source"] == ""
    assert normalized["industry_position"] == "半导体相关企业"


def test_industry_logic_fields_do_not_change_coarse_score():
    base = _card(in_main_sector=True, teacher_hit=True, first_surge=True)
    enriched = {
        **base,
        "sw_l2": "半导体",
        "business_summary": "晶圆制造设备",
        "product_names": ["刻蚀机"],
        "business_source": "tushare.stock_company",
        "business_status": "ok",
        "industry_position": "半导体产业链企业，核心产品包括刻蚀机",
        "catalyst_evidence": [{"label": "研报观点·个股催化", "text": "验证"}],
        "catalyst_status": "exact",
    }
    assert scorer._coarse_score(enriched) == scorer._coarse_score(base)


def test_main_sectors_degrades_on_db_error(monkeypatch):
    """vc_repo 取数失败（DB 错/表缺失）不中断整批，_main_sectors 降级空集。"""
    monkeypatch.setattr(scorer.vc_repo, "get_concentration",
                       lambda conn, date: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")))
    monkeypatch.setattr(scorer.vc_repo, "get_recent_concentration",
                       lambda conn, date, n: (_ for _ in ()).throw(sqlite3.OperationalError("no such table")))
    cards = scorer.build_fact_cards(_mk_conn(), _Reg(), _scan1(), params={"date": "2026-07-13"})
    assert len(cards) == 1
    assert cards[0]["in_main_sector"] is False  # 降级无主线
    assert cards[0]["main_sector_degraded"] is True  # 标记降级
    assert cards[0]["main_sector_status"] == "missing"


def test_concepts_use_prev_trade_date(monkeypatch):
    """codex 门2 高危：概念资金流须用 T-1（否则盘中恒空、维度死）。"""
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda c, d, k: (set(), False))
    seen = {}

    class _R2:
        def call(self, cap, *a):
            if cap == "get_concept_moneyflow_ths":
                seen["date"] = a[0]
                return _R([{"name": "AI", "net_amount_yi": 1.0,
                            "company_num": 20}])
            if cap == "get_stock_concept_memberships":
                return _membership_result(status="missing")
            if cap == "get_stock_sw_industry_map":
                return _R({})
            if cap == "get_stock_daily_range":
                return _R([])
            raise AssertionError(f"unexpected capability: {cap}")

    scorer.build_fact_cards(
        _mk_conn(), _R2(), _scan1(),
        params={"date": "2026-07-13", "concept_top_m": 1},
    )
    assert seen["date"] == "2026-07-10"   # T-1，不是 2026-07-13


def test_concept_member_failed_status(monkeypatch):
    """资金流成功但当前逐票归属失败 → member_failed（非静默 ok）。"""
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda c, d, k: (set(), False))

    class _R3:
        def call(self, cap, *a):
            if cap == "get_concept_moneyflow_ths":
                return _R([{"name": "AI", "net_amount_yi": 1.0,
                            "company_num": 20}])
            if cap == "get_stock_concept_memberships":
                return _membership_result(status="source_failed")
            if cap == "get_stock_sw_industry_map":
                return _R({})
            if cap == "get_stock_daily_range":
                return _R([])
            raise AssertionError(f"unexpected capability: {cap}")

    cards = scorer.build_fact_cards(
        _mk_conn(), _R3(), _scan1(),
        params={"date": "2026-07-13", "concept_top_m": 1},
    )
    assert cards[0]["concept_status"] == "member_failed"


def test_history_source_failed_status_and_none_updays(monkeypatch):
    """codex 门2 中：历史行情失败 → history_status=source_failed 且 up_days=None（非伪装0）。"""
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda c, d, k: (set(), False))

    class _Rhist:
        def call(self, cap, *a):
            if cap == "get_stock_daily_range":
                return _R(error="行情源失败")      # 历史失败
            if cap == "get_stock_concept_memberships":
                return _membership_result(status="missing")
            if cap == "get_concept_moneyflow_ths":
                return _R(error="概念源失败")
            if cap == "get_stock_sw_industry_map":
                return _R({})
            raise AssertionError(f"unexpected capability: {cap}")

    cards = scorer.build_fact_cards(_mk_conn(), _Rhist(), _scan1(), params={"date": "2026-07-13"})
    assert cards[0]["history_status"] == "source_failed"
    assert cards[0]["up_days"] is None            # 不是 0
    assert cards[0]["first_surge"] is False        # None up_days 不触发


def test_index_context_bounded_by_ref_date(monkeypatch):
    """codex 门2 高危：大势信号须 ≤ T-1，不取未来/同日。"""
    monkeypatch.setattr(scorer, "_prev_trade_date", lambda reg, d: "2026-07-10")
    monkeypatch.setattr(scorer, "_main_sectors", lambda c, d, k: (set(), False))
    conn = _mk_conn()
    conn.execute("CREATE TABLE market_timing_signal (trade_date TEXT, index_code TEXT, "
                 "index_name TEXT, change_pct REAL, bottom_phase TEXT)")
    conn.execute("INSERT INTO market_timing_signal VALUES ('2026-07-13','X','未来指数',9.9,'')")  # 未来
    conn.execute("INSERT INTO market_timing_signal VALUES ('2026-07-10','Y','T1指数',0.5,'')")   # =prev

    class _Rreg:
        def call(self, cap, *a):
            if cap == "get_stock_sw_industry_map":
                return _R({})
            if cap == "get_stock_concept_memberships":
                return _membership_result(status="missing")
            if cap == "get_concept_moneyflow_ths":
                return _R(error="概念源失败")
            if cap == "get_stock_daily_range":
                return _R([])
            raise AssertionError(f"unexpected capability: {cap}")

    cards = scorer.build_fact_cards(conn, _Rreg(), _scan1(), params={"date": "2026-07-13"})
    assert "T1指数" in cards[0]["index_context"]    # 用了 ≤prev 的 07-10
    assert "未来指数" not in cards[0]["index_context"]  # 未来的 07-13 被排除
