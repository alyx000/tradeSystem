"""scripts/tests/test_board_break_scanner.py

断板反包筛选层单测（Stage 1）：纯函数 mock，无外网。
覆盖：bare_code/主板前缀、filter_candidates 六类拒绝原因、enrich_with_today_bar
断板日 6% 边界与末根日期校验、run_daily 三源状态编排 + 空语义三分。
"""
from __future__ import annotations

import sqlite3

import pytest

from db.schema import init_schema
from services.board_break import constants as C, scanner
from services.volume_concentration import repo as vc_repo


def _lu(code, name="某票", lt=2, industry="计算机"):
    return {"code": code, "name": name, "limit_times": lt, "industry": industry}


class TestBareAndBoard:
    @pytest.mark.parametrize("raw,expect", [
        ("600000.SH", "600000"), ("000001.SZ", "000001"), ("600000", "600000"),
        (600000, "600000"),  # provider 降级可能给非字符串码（int），bare_code 须兜底不抛异常
    ])
    def test_bare_code(self, raw, expect):
        assert scanner.bare_code(raw) == expect

    @pytest.mark.parametrize("code,ok", [
        ("600001", True), ("601001", True), ("603001", True), ("605001", True),
        ("000001", True), ("001001", True), ("002001", True), ("003001", True),
        ("688001", False), ("300001", False), ("830001", False), ("430001", False), ("920001", False),
    ])
    def test_is_main_board(self, code, ok):
        assert scanner.is_main_board(code) is ok


class TestCoerceLimitTimes:
    def test_inf_coerced_to_none(self):
        # float("inf") 会让 int(inf) 抛 OverflowError；_coerce_limit_times 须优雅归 None
        assert scanner._coerce_limit_times(float("inf")) is None
        assert scanner._coerce_limit_times(float("-inf")) is None


class TestFilterCandidates:
    def test_lianban_boundary(self):
        prev = [_lu("600001.SH", lt=1), _lu("600002.SH", lt=2), _lu("600003.SH", lt=3)]
        cands, rejects = scanner.filter_candidates(prev, set(), set())
        assert {c["code"] for c in cands} == {"600002", "600003"}

    def test_still_limit_up_today_excluded(self):
        prev = [_lu("600002.SH", lt=2)]
        cands, rejects = scanner.filter_candidates(prev, {"600002"}, set())
        assert cands == [] and rejects["still_limit_up"] == 1

    def test_limit_down_excluded(self):
        prev = [_lu("600002.SH", lt=2)]
        cands, rejects = scanner.filter_candidates(prev, set(), {"600002"})
        assert cands == [] and rejects["limit_down"] == 1

    @pytest.mark.parametrize("st_name", ["ST某某", "*ST某某", "SST某某", "S*ST某某", "退市某某", "某某退"])
    def test_st_variants_excluded(self, st_name):
        prev = [_lu("600002.SH", name=st_name, lt=2)]
        cands, rejects = scanner.filter_candidates(prev, set(), set())
        assert cands == [] and rejects["st"] == 1

    def test_non_main_board_excluded(self):
        prev = [_lu("688001.SH", lt=2)]
        cands, rejects = scanner.filter_candidates(prev, set(), set())
        assert cands == [] and rejects["non_main_board"] == 1

    @pytest.mark.parametrize("dirty", [None, "abc", float("nan"), float("inf")])
    def test_dirty_limit_times(self, dirty):
        prev = [_lu("600002.SH", lt=dirty)]
        cands, rejects = scanner.filter_candidates(prev, set(), set())
        assert cands == [] and rejects["dirty_limit_times"] == 1

    def test_string_number_limit_times_ok(self):
        prev = [_lu("600002.SH", lt="3")]
        cands, _ = scanner.filter_candidates(prev, set(), set())
        assert cands[0]["limit_times"] == 3


class TestEnrichWithTodayBar:
    def _fetch(self, bars_by_code):
        def fetch(code, start, end):
            return bars_by_code.get(code)
        return fetch

    @pytest.mark.parametrize("pct,kept", [(5.99, True), (6.0, True), (6.01, False), (-3.0, True)])
    def test_pct_boundary_and_green_kept(self, pct, kept):
        cands = [{"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机"}]
        bars = {"600002": [{"trade_date": "2026-07-04", "close": 10.0, "pct_chg": pct}]}
        out, rejects = scanner.enrich_with_today_bar(cands, self._fetch(bars), date="2026-07-04")
        assert (len(out) == 1) is kept
        if kept:
            assert out[0]["ref_price"] == 10.6

    def test_missing_or_stale_bar_rejected(self):
        cands = [{"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机"},
                 {"code": "600003", "name": "y", "limit_times": 2, "industry": "计算机"}]
        bars = {"600003": [{"trade_date": "2026-07-03", "close": 10.0, "pct_chg": 1.0}]}  # 末根非 T
        out, rejects = scanner.enrich_with_today_bar(cands, self._fetch(bars), date="2026-07-04")
        assert out == [] and rejects["bar_missing"] == 2

    def test_real_contract_dashed_trade_date_not_misjudged_bar_missing(self):
        """揭露性用例：真实 provider（get_stock_daily_range）契约 trade_date 为带横杠
        "YYYY-MM-DD"（见 tushare_provider.py 归一化 + test_tushare_daily_range.py 钉死），
        而非 "YYYYMMDD" 紧凑格式。旧实现内部 _compact_date 把 date 转紧凑格式去比对
        带横杠的真实 trade_date，恒不相等 → 候选恒被误判 bar_missing → 生产环境永远空清单。

        本用例在旧代码上必须 RED（bar_missing 被误计、候选被误剔）；修复后须 GREEN。
        """
        cands = [{"code": "600002", "name": "x", "limit_times": 2, "industry": "计算机"}]
        bars = {"600002": [{"trade_date": "2026-07-04", "close": 10.0, "pct_chg": 3.0}]}
        out, rejects = scanner.enrich_with_today_bar(cands, self._fetch(bars), date="2026-07-04")
        assert len(out) == 1
        assert rejects["bar_missing"] == 0


class _FakeResult:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.source = data, error, "fake"

    @property
    def success(self) -> bool:
        # 与真实 providers.base.DataResult.success 语义对齐：无错误即成功
        return not self.error


def _make_registry(*, prev_rows=None, prev_ok=True, today_ok=True, down_ok=True, range_result=None):
    """通用 fake registry 工厂，供 TestRunDailySourceStates / TestEmptySemantics 三处复用。

    prev_rows: T-1 涨停榜行（默认单票 lt=2，满足 D1 门槛）；
    prev_ok/today_ok/down_ok: 三源各自是否返回 error（模拟 source_failed）；
    range_result: get_stock_daily_range 的返回（默认 T 日单根 3.0% bar），
                  可传 _FakeResult(error=...) 模拟日线缺失。
    """
    prev_rows = prev_rows if prev_rows is not None else [_lu("600002.SH", lt=2)]
    range_result = range_result if range_result is not None else _FakeResult(
        [{"trade_date": "2026-07-04", "close": 10.0, "pct_chg": 3.0}]
    )

    class R:
        def call(self, method, *a, **k):
            if method == "get_limit_up_list":
                date = a[0]
                if date == "2026-07-03":
                    return _FakeResult({"stocks": prev_rows}) if prev_ok else _FakeResult(error="x")
                return _FakeResult({"stocks": []}) if today_ok else _FakeResult(error="x")
            if method == "get_limit_down_list":
                return _FakeResult({"stocks": []}) if down_ok else _FakeResult(error="x")
            if method == "get_stock_daily_range":
                return range_result
            return _FakeResult(error="unknown")
    return R()


class TestRunDailySourceStates:
    def _registry(self, prev_ok=True, today_ok=True, down_ok=True):
        return _make_registry(prev_ok=prev_ok, today_ok=today_ok, down_ok=down_ok)

    def test_any_core_source_failed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        for kw in ({"prev_ok": False}, {"today_ok": False}, {"down_ok": False}):
            result = scanner.run_daily(None, self._registry(**kw), "2026-07-04")
            assert result["status"] == "source_failed"
            # source_failed 与 ok 两分支返回 dict 形状对称：都须含 empty_kind 键
            assert result["empty_kind"] is None

    def test_today_limit_up_empty_error_treated_as_failed(self, monkeypatch):
        # get_limit_up_list 空 DataFrame 返 error：T 日按 source_failed 处理（spec 数据流）
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        result = scanner.run_daily(None, self._registry(today_ok=False), "2026-07-04")
        assert result["status"] == "source_failed"


class TestJsonSafeContract:
    def test_run_daily_result_json_serializable(self, monkeypatch):
        """run_daily 对外结果必须可 json.dumps(main_sectors 为 sorted list 非 set)——门2 R1 契约测试。"""
        import json
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        monkeypatch.setattr(scanner, "_main_sectors", lambda conn, date, top_k: ({"计算机", "传媒"}, False))
        reg = _make_registry()
        result = scanner.run_daily(None, reg, "2026-07-04")
        dumped = json.dumps(result, ensure_ascii=False)
        assert isinstance(result["main_sectors"], list)
        assert result["main_sectors"] == sorted(result["main_sectors"])
        assert "计算机" in dumped

    def test_source_failed_result_json_serializable(self, monkeypatch):
        import json
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        reg = _make_registry(prev_ok=False)
        result = scanner.run_daily(None, reg, "2026-07-04")
        json.dumps(result, ensure_ascii=False)
        assert result["main_sectors"] == []


class TestEmptySemantics:
    """源状态三分之一：源都 ok 时，入口候选（D1 连板>=2）是否存在决定空语义归属。

    T-1 无连板>=2（全场 lt<min）→ source_ok_empty（源本身自然没有候选，非规则剔除）；
    有入口票（lt>=2）但被后续规则（ST/主板/仍涨停/跌停）全剔 → rule_filtered_empty；
    有最终候选 → status ok 且 empty_kind 为 None。
    """

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        init_schema(c)
        return c

    def _registry(self, prev_rows):
        return _make_registry(prev_rows=prev_rows)

    def test_source_ok_empty(self, monkeypatch, conn):
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        prev_rows = [_lu("600002.SH", lt=1)]  # 全场未达连板门槛
        result = scanner.run_daily(conn, self._registry(prev_rows), "2026-07-04")
        assert result["status"] == "ok"
        assert result["empty_kind"] == "source_ok_empty"

    def test_rule_filtered_empty(self, monkeypatch, conn):
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        prev_rows = [_lu("600002.SH", name="ST某某", lt=2)]  # 达连板门槛但被 ST 规则剔除
        result = scanner.run_daily(conn, self._registry(prev_rows), "2026-07-04")
        assert result["status"] == "ok"
        assert result["empty_kind"] == "rule_filtered_empty"

    def test_has_candidates_empty_kind_none(self, monkeypatch, conn):
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        prev_rows = [_lu("600002.SH", lt=2)]
        result = scanner.run_daily(conn, self._registry(prev_rows), "2026-07-04")
        assert result["status"] == "ok"
        assert result["empty_kind"] is None
        assert len(result["candidates"]) == 1

    def test_all_bar_missing_escalates_to_source_failed(self, monkeypatch, conn):
        """入口候选存在且全部因 bar_missing 被剔 → 升级 source_failed（门2 S1 R2 契约）。

        原「固化」为 rule_filtered_empty 的行为被 codex R2 推翻：行情源整体挂掉时
        静默渲染成"规则过滤完"是高成本静默空结果。全 bar_missing = 大概率源故障，fail-safe。
        """
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        registry = _make_registry(
            prev_rows=[_lu("600002.SH", lt=2)],
            range_result=_FakeResult(error="日线缺失"),  # fetch_range 返回 []
        )

        result = scanner.run_daily(conn, registry, "2026-07-04")
        assert result["status"] == "source_failed"
        assert "stock_daily_range" in result["failed_sources"]
        assert result["rejects"]["bar_missing"] > 0
        import json
        json.dumps(result, ensure_ascii=False)  # JSON-safe 契约同样适用

    def test_partial_bar_missing_stays_ok(self, monkeypatch, conn):
        """部分票缺 bar 仍按单票降级：status=ok，缺 bar 票计 bar_missing，其余正常入选。"""
        monkeypatch.setattr(scanner, "_prev_trade_date", lambda registry, d: "2026-07-03")
        bars_ok = [{"trade_date": "2026-07-04", "close": 10.0, "pct_chg": 3.0}]
        calls = {"n": 0}

        class _R:
            def call(self, method, *a, **k):
                if method == "get_limit_up_list":
                    if a[0] == "2026-07-03":
                        return _FakeResult({"stocks": [_lu("600002.SH", lt=2), _lu("600003.SH", lt=2)]})
                    return _FakeResult({"stocks": []})
                if method == "get_limit_down_list":
                    return _FakeResult({"stocks": []})
                if method == "get_stock_daily_range":
                    calls["n"] += 1
                    return _FakeResult(bars_ok) if a[0] == "600002" else _FakeResult(error="单票缺失")
                return _FakeResult(error="unknown")

        result = scanner.run_daily(conn, _R(), "2026-07-04")
        assert result["status"] == "ok"
        assert len(result["candidates"]) == 1
        assert result["rejects"]["bar_missing"] == 1


class TestClassifyEmptyKind:
    """_classify_empty_kind 纯函数直测：三分支各覆盖一次。"""

    def test_has_candidates_returns_none(self):
        assert scanner._classify_empty_kind(True, 5) is None

    def test_no_entrance_returns_source_ok_empty(self):
        assert scanner._classify_empty_kind(False, 0) == "source_ok_empty"

    def test_entrance_but_no_candidates_returns_rule_filtered_empty(self):
        assert scanner._classify_empty_kind(False, 3) == "rule_filtered_empty"


class TestMainSectors:
    """scanner._main_sectors 直测（与 trend_leader 口径一致的刻意内联副本，
    plan 角色边界约束 volume_concentration「不得改」，不下沉共用 vc_repo helper）。

    sqlite fixture 造 daily_volume_concentration 数据的方式参考
    scripts/tests/test_volume_concentration_repo.py 既有惯例（init_schema + save_concentration）。
    """

    @pytest.fixture
    def conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        init_schema(c)
        return c

    def _sample_record(self, date: str, sector_summary: list) -> dict:
        return {
            "date": date,
            "top_n": 20,
            "total_amount_billion": 350.5,
            "market_total_billion": 9800.0,
            "stocks": [],
            "sector_summary": sector_summary,
        }

    def test_hits_today(self, conn):
        """当日快照存在时,直接取其 sector_summary Top-K(剔未分类),degraded=False。"""
        record = self._sample_record("2026-07-04", [
            {"industry": "电池", "count": 1, "amount_billion": 58.3, "share_in_top_n": 0.5, "codes": []},
            {"industry": "未分类", "count": 1, "amount_billion": 10.0, "share_in_top_n": 0.1, "codes": []},
            {"industry": "半导体", "count": 1, "amount_billion": 20.0, "share_in_top_n": 0.2, "codes": []},
        ])
        vc_repo.save_concentration(conn, record)

        sectors, degraded = scanner._main_sectors(conn, "2026-07-04", top_k=1)

        assert sectors == {"电池"}  # top_k=1 只取第一条,未分类已被剔除
        assert degraded is False

    def test_falls_back_when_missing(self, conn):
        """当日无快照 → 回退最近一日(<=date),并标记 degraded=True。"""
        record = self._sample_record("2026-07-03", [
            {"industry": "电池", "count": 1, "amount_billion": 58.3, "share_in_top_n": 0.5, "codes": []},
        ])
        vc_repo.save_concentration(conn, record)

        sectors, degraded = scanner._main_sectors(conn, "2026-07-04", top_k=5)  # 07-04 当日无快照

        assert sectors == {"电池"}
        assert degraded is True
