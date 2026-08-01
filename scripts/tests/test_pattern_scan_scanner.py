"""形态篇 scanner 端到端单测，mock registry 隔离外网。

漏斗：主线板块 → 板块内全量（剔 ST）→ 逐票拉 OHLCV + 复权因子 → 前复权 → 四条件共振。
构造的 bars 一律走 apply_qfq（factors 全 1.0 → ratio=1 不改价），保证测的是真实调用链。
"""
from __future__ import annotations

import sqlite3
import threading
from types import SimpleNamespace

from db.migrate import migrate
from services.volume_concentration import repo as vc_repo

DATE = "2026-06-09"


def _bars(
    n: int = 150,
    *,
    start: float = 10.0,
    today_amount: float = 1.0e5,
    yang_vol: float = 3000.0,
    yin_vol: float = 400.0,
    end_date: str = DATE,
    trend: float = 1.0,
) -> list[dict]:
    """缓涨 + 阳放阴缩节奏的合规序列：每 4 根 = 阳阳阳阴，单日涨幅 1% 远低于涨停。

    trend=1.0 为上行（四条件全成立）；trend=-1.0 翻转为下行（用于构造不满足的对照）。
    日期只需单调递增且末根=end_date，用序号回推自然日即可（不进交易日历）。
    """
    import datetime

    end = datetime.date.fromisoformat(end_date)
    bars: list[dict] = []
    price = start
    for i in range(n):
        is_yin = (i % 4) == 3
        rate = (0.995 if is_yin else 1.01)
        if trend < 0:
            rate = (1.005 if is_yin else 0.99)
        open_px = price
        close_px = price * rate
        date = (end - datetime.timedelta(days=(n - 1 - i))).isoformat()
        bars.append({
            "trade_date": date,
            "open": open_px,
            "close": close_px,
            "high": max(open_px, close_px),
            "low": min(open_px, close_px),
            "pre_close": open_px,
            "pct_chg": (rate - 1) * 100,
            "vol": yin_vol if is_yin else yang_vol,
            "amount": today_amount if i == n - 1 else 5.0e4,
        })
        price = close_px
    return bars


def _factors(bars: list[dict], value: float = 1.0) -> list[dict]:
    return [{"trade_date": b["trade_date"], "adj_factor": value} for b in bars]


class _Registry:
    def __init__(self, bars_by_code: dict[str, list[dict]], *, factors_by_code=None,
                 factor_fail: set[str] | None = None):
        self.bars_by_code = bars_by_code
        self.factors_by_code = factors_by_code or {}
        self.factor_fail = factor_fail or set()

    def call(self, name: str, *args, **kwargs):
        if name == "get_stock_sw_industry_map":
            return SimpleNamespace(success=True, data={
                "600001.SH": {"name": "主线形态A", "sw_l2": "半导体"},
                "600002.SH": {"name": "主线形态B", "sw_l2": "半导体"},
                "600003.SH": {"name": "非主线", "sw_l2": "银行"},
                "600005.SH": {"name": "ST风险", "sw_l2": "半导体"},
            })
        if name == "get_stock_daily_range":
            code = args[0].split(".")[0]
            return SimpleNamespace(success=True, data=self.bars_by_code.get(code, []))
        if name == "get_stock_adj_factor_range":
            code = args[0].split(".")[0]
            if code in self.factor_fail:
                return SimpleNamespace(success=False, data=None)
            if code in self.factors_by_code:
                return SimpleNamespace(success=True, data=self.factors_by_code[code])
            return SimpleNamespace(success=True, data=_factors(self.bars_by_code.get(code, [])))
        if name == "get_ths_member":
            return SimpleNamespace(success=True, data=[])
        if name == "get_concept_moneyflow_ths":
            return SimpleNamespace(success=True, data=[])
        raise AssertionError(f"unexpected provider call: {name}")


def _conn_with_main_sector() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    migrate(conn)
    vc_repo.save_concentration(conn, {
        "date": DATE,
        "top_n": 20,
        "total_amount_billion": 1000,
        "sector_summary": [
            {"industry": "半导体", "amount_billion": 120},
            {"industry": "银行", "amount_billion": 10},
        ],
        "stocks": [],
        "source": {"provider": "pytest"},
    })
    return conn


def _run(registry, conn=None, top_k: int = 1):
    from services.pattern_scan import scanner
    conn = conn or _conn_with_main_sector()
    try:
        return scanner.run_daily(conn, registry, DATE, top_k=top_k)
    finally:
        conn.close()


class TestFunnel:
    def test_matches_mainline_candidates_sorted_by_amount(self):
        registry = _Registry({
            "600001": _bars(today_amount=1.6e5),
            "600002": _bars(today_amount=2.4e5),
        })
        result = _run(registry)
        assert result["status"] == "ok"
        assert [c["code"] for c in result["candidates"]] == ["600002", "600001"]

    def test_non_mainline_sector_excluded(self):
        registry = _Registry({"600001": _bars(), "600003": _bars(today_amount=9.9e5)})
        result = _run(registry)
        assert [c["code"] for c in result["candidates"]] == ["600001"]
        assert result["rejects"]["not_main_sector"] == 1

    def test_st_excluded_before_fetching(self):
        registry = _Registry({"600001": _bars(), "600005": _bars(today_amount=9.9e5)})
        result = _run(registry)
        assert [c["code"] for c in result["candidates"]] == ["600001"]
        assert result["rejects"]["st_or_delist"] == 1

    def test_two_workers_overlap_stocks_without_changing_call_count(self):
        """两只股票的网络等待并行；每票仍只取一次行情和一次复权因子。"""
        class _ConcurrentRegistry(_Registry):
            def __init__(self):
                super().__init__({
                    "600001": _bars(today_amount=1.6e5),
                    "600002": _bars(today_amount=2.4e5),
                })
                self.barrier = threading.Barrier(2)
                self.calls: list[tuple[str, str]] = []
                self.lock = threading.Lock()

            def call(self, name, *args, **kwargs):
                if name in {"get_stock_daily_range", "get_stock_adj_factor_range"}:
                    code = args[0].split(".")[0]
                    with self.lock:
                        self.calls.append((name, code))
                    if name == "get_stock_daily_range":
                        self.barrier.wait(timeout=2)
                return super().call(name, *args, **kwargs)

        registry = _ConcurrentRegistry()
        result = _run(registry)

        assert [candidate["code"] for candidate in result["candidates"]] == ["600002", "600001"]
        assert sorted(registry.calls) == [
            ("get_stock_adj_factor_range", "600001"),
            ("get_stock_adj_factor_range", "600002"),
            ("get_stock_daily_range", "600001"),
            ("get_stock_daily_range", "600002"),
        ]

    def test_no_mainline_returns_empty_without_fetching(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        migrate(conn)  # 无 volume_concentration 记录 → 无主线
        registry = _Registry({"600001": _bars()})
        result = _run(registry, conn=conn)
        assert result["status"] == "ok"
        assert result["candidates"] == []
        assert result["rejects"] == {"no_main_sector": 1}


class TestConceptBranch:
    """概念分支必须真正进入候选宇宙。

    `judge_mainline(use_llm=False)` 只出申万二级、概念图恒空（门2 high），
    故 scanner 自建机械分支；若回退成依赖 judgment.main_concepts，下面两个用例会红。
    """

    class _WithConcept(_Registry):
        def call(self, name, *args, **kwargs):
            if name == "get_concept_moneyflow_ths":
                return SimpleNamespace(success=True, data=[
                    {"name": "CPO", "net_amount": 8_000_000_000},
                ])
            if name == "get_ths_member":
                return SimpleNamespace(success=True, data=[
                    {"con_code": "600003.SH", "index_name": "CPO"},
                ])
            return super().call(name, *args, **kwargs)

    def test_stock_outside_l2_included_via_hot_concept(self):
        """600003 属银行（非主线申万二级），只因命中热概念 CPO 才应入池。"""
        registry = self._WithConcept({"600003": _bars()})
        result = _run(registry)
        assert [c["code"] for c in result["candidates"]] == ["600003"]
        assert result["candidates"][0]["branch_concepts"] == ["CPO"]

    def test_main_concepts_reported_in_payload(self):
        """报告里的「主线概念分支」必须反映机械分支结果，不能恒空。"""
        result = _run(self._WithConcept({"600003": _bars()}))
        assert result["mainline"]["main_concepts"] == ["CPO"]

    def test_concept_flow_failure_degrades_to_l2_only(self):
        """概念资金流失败只降级为纯申万二级，不打掉整批扫描。"""
        class _NoFlow(_Registry):
            def call(self, name, *args, **kwargs):
                if name == "get_concept_moneyflow_ths":
                    return SimpleNamespace(success=False, data=None)
                return super().call(name, *args, **kwargs)

        result = _run(_NoFlow({"600001": _bars()}))
        assert result["status"] == "ok"
        assert [c["code"] for c in result["candidates"]] == ["600001"]
        assert "concept_flow" in result["source_errors"]


class TestRejectReasons:
    def test_downtrend_rejected_as_ma_not_aligned(self):
        registry = _Registry({"600001": _bars(trend=-1.0)})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["ma_not_aligned"] == 1

    def test_stale_last_bar_rejected(self):
        """末根不是目标日 → 不得当作当日形态成立（陈旧数据守卫）。"""
        registry = _Registry({"600001": _bars(end_date="2026-06-05")})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["stale_last_bar"] == 1

    def test_short_history_rejected_as_insufficient(self):
        """样本不足必须与「形态不满足」分开计数。"""
        registry = _Registry({"600001": _bars(n=60)})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["insufficient_history"] == 1
        assert result["rejects"]["ma_not_aligned"] == 0

    def test_accelerated_candidate_rejected(self):
        """近 20 日出现涨停 → 空间已透支，即使前三条形态成立也落选。"""
        bars = _bars()
        bars[-3]["pct_chg"] = 10.5
        registry = _Registry({"600001": bars})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["already_accelerated"] == 1

    def test_weak_yang_volume_rejected(self):
        """节奏完全翻转（阳线缩量、阴线放量）→ 无资金进场证据。

        注意不能用「阳线量略高于阴线量」来构造：阳线占窗口 3/4，均量线本身被阳线
        拉高，略高仍会站上。必须让阴线量高于阳线量，均量线才会抬到阳线之上。
        """
        registry = _Registry({"600001": _bars(yang_vol=400.0, yin_vol=3000.0)})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["yang_volume_weak"] == 1


class TestQfq:
    def test_missing_factors_marks_qfq_failed_not_match(self):
        """复权因子取不到 → 整票剔除，绝不退回未复权硬算。"""
        registry = _Registry({"600001": _bars()}, factor_fail={"600001"})
        result = _run(registry)
        assert result["candidates"] == []
        assert result["rejects"]["qfq_failed"] == 1
        assert "600001" in result["data_errors"]

    def test_open_is_adjusted_so_yin_yang_survives_ex_dividend(self):
        """除权日 open 必须与 close 同坐标系。

        factors 在窗口中段跳变；若只复权 close 而不复权 open，阳线会被判成阴线，
        节奏统计随之失真。这里用「复权后仍命中」证明 open 走了同一 ratio。
        """
        bars = _bars()
        factors = _factors(bars)
        for f in factors[: len(factors) // 2]:
            f["adj_factor"] = 0.5   # 前半段因子折半 → ratio=0.5
        registry = _Registry({"600001": bars}, factors_by_code={"600001": factors})
        result = _run(registry)
        assert [c["code"] for c in result["candidates"]] == ["600001"]


class TestSourceFailure:
    def test_all_universe_fetch_failed_is_source_failed(self):
        """全宇宙取数失败是链路故障，不能报成「今日无候选」。"""
        registry = _Registry({})
        result = _run(registry)
        assert result["status"] == "source_failed"
        assert "stock_daily_range_or_adj_factor_or_stale" in result["source_errors"]

    def test_all_stale_bars_is_source_failed_not_empty_pool(self):
        """Tushare 滞后时每票都「成功但末根是上一交易日」→ 必须 source_failed。

        只查 bar_missing/qfq_failed 会让整批陈旧数据以 ok + 今日无命中 正常推出去（门2 high）。
        """
        registry = _Registry({
            "600001": _bars(end_date="2026-06-05"),
            "600002": _bars(end_date="2026-06-05"),
        })
        result = _run(registry)
        assert result["status"] == "source_failed"
        assert result["rejects"]["stale_last_bar"] == 2

    def test_empty_sw_map_is_source_failed(self):
        """空映射表是 provider 故障，与「板块内确实没票」不可区分 → fail-closed。"""
        class _EmptySw(_Registry):
            def call(self, name, *args, **kwargs):
                if name == "get_stock_sw_industry_map":
                    return SimpleNamespace(success=True, data={})
                return super().call(name, *args, **kwargs)

        result = _run(_EmptySw({"600001": _bars()}))
        assert result["status"] == "source_failed"
        assert "sw_map" in result["source_errors"]

    def test_mainline_matching_no_stock_is_source_failed(self):
        """主线非空却匹配不到任何票 → 板块名口径错位，不是「今日无候选」。"""
        class _OtherSectors(_Registry):
            def call(self, name, *args, **kwargs):
                if name == "get_stock_sw_industry_map":
                    return SimpleNamespace(success=True, data={
                        "600009.SH": {"name": "全是别的板块", "sw_l2": "银行"},
                    })
                return super().call(name, *args, **kwargs)

        result = _run(_OtherSectors({"600001": _bars()}))
        assert result["status"] == "source_failed"
        assert "mainline_coverage_empty" in result["source_errors"]

    def test_sw_map_failure_is_source_failed(self):
        class _NoSw(_Registry):
            def call(self, name, *args, **kwargs):
                if name == "get_stock_sw_industry_map":
                    return SimpleNamespace(success=False, data=None)
                return super().call(name, *args, **kwargs)

        result = _run(_NoSw({"600001": _bars()}))
        assert result["status"] == "source_failed"
        assert "sw_map" in result["source_errors"]

    def test_partial_failure_still_ok(self):
        """部分票取数失败不应升级成整体失败。"""
        registry = _Registry({"600001": _bars()}, factor_fail={"600002"})
        registry.bars_by_code["600002"] = _bars()
        result = _run(registry)
        assert result["status"] == "ok"
        assert [c["code"] for c in result["candidates"]] == ["600001"]
        assert result["rejects"]["qfq_failed"] == 1


def test_progress_logged_per_stock(caplog):
    import logging
    registry = _Registry({"600001": _bars()})
    with caplog.at_level(logging.INFO):
        _run(registry)
    assert any("候选宇宙" in r.message or "候选宇宙" in str(r.msg) for r in caplog.records)
