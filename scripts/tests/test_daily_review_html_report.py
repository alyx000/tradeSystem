"""每日多 Agent 复盘 HTML 组装器的结构、预算与 CLI 契约测试。"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
ASSEMBLER = (
    ROOT
    / ".agents"
    / "skills"
    / "daily-review"
    / "references"
    / "html-report-template"
    / "assemble_report.py"
)
CAPACITY_MANIFEST_BUILDER = ASSEMBLER.with_name("build_capacity_manifest.py")
NEW_HIGH_MANIFEST_BUILDER = ASSEMBLER.with_name(
    "build_new_high_structure_manifest.py"
)
DATE = "2026-07-16"
CAPACITY_TRADE_DATES = (
    "2026-07-10",
    "2026-07-13",
    "2026-07-14",
    "2026-07-15",
    DATE,
)
CHUNK_ORDER = ("head", "s0", "s1", "s2", "s456", "s7t", "proj", "s8ops")
ANCHORS = (
    "tldr",
    "factor",
    "s0",
    "s1",
    "s2",
    "s3",
    "s4",
    "s5",
    "s6",
    "s7",
    "teachers",
    "industry",
    "cognition",
    "proj",
    "s8",
    "ops",
)


def _capacity_no_data(
    *,
    date: str = DATE,
    source_status: str = "complete",
    text: str = "[事实] 本日无可确认容量中军",
) -> str:
    return (
        f'<p data-capacity-health="none" data-as-of="{date}" '
        f'data-source-status="{source_status}">{text}</p>'
    )


def _capacity_missing_data(
    *,
    date: str = DATE,
    source_status: str = "partial",
    text: str = "[事实] 容量排名数据不完整，本日无法判定",
) -> str:
    return (
        f'<p data-capacity-health="missing-data" data-as-of="{date}" '
        f'data-source-status="{source_status}">{text}</p>'
    )


def _new_high_structure_verdict(
    *,
    date: str = DATE,
    text: str = "[判断] 60/120/250 日滚动新高为 0/0/0，本日无符合项。",
) -> str:
    return (
        f'<p data-new-high-structure="verdict" data-as-of="{date}">'
        f"{text}</p>"
    )


def _new_high_structure_state(
    *,
    state: str = "none",
    date: str = DATE,
    source_status: str = "complete",
    text: str | None = None,
) -> str:
    texts = {
        "none": "[事实] 本日无符合 60/120/250 日滚动新高口径的个股",
        "missing-data": "[事实] 滚动新高结构数据不完整，本日无法判定",
    }
    value = text if text is not None else texts[state]
    return (
        f'<p data-new-high-structure="{state}" data-as-of="{date}" '
        f'data-source-status="{source_status}">{value}</p>'
    )


def _sector_state(
    contract: str,
    *,
    state: str = "none",
    date: str = DATE,
    source_status: str = "complete",
    text: str | None = None,
) -> str:
    texts = {
        ("sector-concentration", "none"): "[事实] 本日无可用板块集中度数据",
        (
            "sector-concentration",
            "missing-data",
        ): "[事实] 板块集中度数据不完整，本日无法判定",
        (
            "rising-recognition",
            "none",
        ): "[事实] 本日无符合规则的主升辨识度个股",
        (
            "rising-recognition",
            "missing-data",
        ): "[事实] 主升辨识度矩阵数据不完整，本日无法判定",
        (
            "falling-recognition",
            "none",
        ): "[事实] 本日无符合规则的主跌辨识度个股",
        (
            "falling-recognition",
            "missing-data",
        ): "[事实] 主跌辨识度矩阵数据不完整，本日无法判定",
    }
    value = text if text is not None else texts[(contract, state)]
    return (
        f'<p data-{contract}="{state}" data-as-of="{date}" '
        f'data-source-status="{source_status}">{value}</p>'
    )


def _sector_concentration_verdict(
    *,
    date: str = DATE,
    text: str = "[判断] 板块成交仍集中于少数方向。",
) -> str:
    return (
        f'<p data-sector-concentration="verdict" data-as-of="{date}">'
        f"{text}</p>"
    )


def _event_window_state(
    *,
    state: str = "none",
    date: str = DATE,
    source_status: str = "complete",
    text: str | None = None,
) -> str:
    texts = {
        "none": "[事实] 未来7个自然日无影响次日验证的新增事件",
        "missing-data": "[事实] 未来7个自然日事件窗数据不完整，本日无法判定",
    }
    value = text if text is not None else texts[state]
    report_day = date_type.fromisoformat(date)
    window_start = (report_day + timedelta(days=1)).isoformat()
    window_end = (report_day + timedelta(days=7)).isoformat()
    return (
        f'<p data-event-window="{state}" data-as-of="{date}" '
        f'data-source-status="{source_status}" data-window-start="{window_start}" '
        f'data-window-end="{window_end}">{value}</p>'
    )


def _event_window_verdict(
    *,
    date: str = DATE,
    text: str = "[判断] 当前节点未出现会改变次日验证的未来一周事件窗口。",
) -> str:
    return f'<p data-event-window="verdict" data-as-of="{date}">{text}</p>'


def _capacity_manifest_row(
    *,
    code: str = "000001.SZ",
    name: str = "容量标的",
    direction: str = "电子",
    tier: str = "core",
    market_rank: int = 20,
    direction_rank: int = 1,
    top50_days: int = 0,
) -> dict:
    return {
        "ts_code": code,
        "name": name,
        "direction": direction,
        "tier": tier,
        "market_rank": market_rank,
        "direction_rank": direction_rank,
        "top50_days": top50_days,
        "amount_yi": 100.0,
    }


def _capacity_manifest(
    *,
    report_date: str = DATE,
    as_of: str | None = None,
    universe_count: int = 5_000,
    rows: list[dict] | None = None,
    rank_trade_dates: list[str] | None = None,
) -> dict:
    as_of = as_of or report_date
    as_of_day = date_type.fromisoformat(as_of)
    if rank_trade_dates is None:
        weekdays: list[str] = []
        cursor = as_of_day
        while len(weekdays) < 5:
            if cursor.weekday() < 5:
                weekdays.append(cursor.isoformat())
            cursor -= timedelta(days=1)
        rank_trade_dates = list(reversed(weekdays))
    manifest_rows = list(rows or [])
    direction_ids = sorted(
        {str(row["direction"]) for row in manifest_rows}
    ) or ["电子"]
    return {
        "schema": "capacity-health-v1",
        "report_date": report_date,
        "as_of": as_of,
        "status": "complete",
        "complete": True,
        "rank_metric": "daily.amount",
        "market_source": "fixture.daily",
        "market_reference_source": "fixture.stock_basic",
        "market_reference_count": universe_count,
        "market_coverage": 1.0,
        "direction_source": "fixture.sw_l2",
        "industry_coverage": 1.0,
        "calendar_source": "fixture.calendar",
        "generator": "build_capacity_manifest.py",
        "market_universe_count": universe_count,
        "directions": [
            {"id": direction, "member_count": 100}
            for direction in direction_ids
        ],
        "rank_trade_dates": rank_trade_dates,
        "rows": manifest_rows,
        "errors": [],
    }


def _new_high_manifest(
    *,
    report_date: str = DATE,
    as_of: str | None = None,
    with_data: bool = False,
    complete: bool = True,
) -> dict:
    as_of = as_of or report_date
    trade_days: list[str] = []
    cursor = date_type.fromisoformat(as_of)
    while len(trade_days) < 251:
        if cursor.weekday() < 5:
            trade_days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    trade_days.reverse()
    prev_as_of = trade_days[-2]
    if not complete:
        return {
            "schema": "rolling-new-high-structure-v1",
            "generator": "build_new_high_structure_manifest.py",
            "report_date": report_date,
            "as_of": as_of,
            "prev_as_of": None,
            "status": "failed",
            "complete": False,
            "basis": "rolling-adjusted-high",
            "windows": [60, 120, 250],
            "trade_dates": [],
            "daily_market_counts": {},
            "daily_market_coverage_min": 0.0,
            "market_count": 0,
            "market_reference_count": 0,
            "market_coverage": 0.0,
            "industry_coverage": 0.0,
            "counts": {},
            "sectors": [],
            "representatives": [],
            "current_codes": {},
            "previous_codes": {},
            "sources": {},
            "errors": ["行情或复权因子不完整"],
        }

    if not with_data:
        current_codes = {str(window): [] for window in (60, 120, 250)}
        previous_codes = {str(window): [] for window in (60, 120, 250)}
        counts = {
            str(window): {"current": 0, "previous": 0, "delta": 0}
            for window in (60, 120, 250)
        }
        sectors: list[dict] = []
        representatives: list[dict] = []
        overlap = 0
        retention = None
        turnover = None
        cr3 = 0.0
    else:
        current_codes = {
            "60": [f"00000{index}.SZ" for index in range(1, 6)],
            "120": [f"00000{index}.SZ" for index in range(1, 4)],
            "250": [f"00000{index}.SZ" for index in range(1, 3)],
        }
        previous_codes = {
            "60": ["000001.SZ", "000002.SZ", "000006.SZ", "000007.SZ"],
            "120": ["000001.SZ", "000006.SZ"],
            "250": ["000001.SZ"],
        }
        counts = {
            "60": {"current": 5, "previous": 4, "delta": 1},
            "120": {"current": 3, "previous": 2, "delta": 1},
            "250": {"current": 2, "previous": 1, "delta": 1},
        }
        sectors = [
            {"industry": "通信设备", "count": 2, "share_pct": 40.0},
            {"industry": "医疗服务", "count": 1, "share_pct": 20.0},
            {"industry": "半导体", "count": 1, "share_pct": 20.0},
            {"industry": "电力", "count": 1, "share_pct": 20.0},
        ]
        representative_meta = (
            ("000001.SZ", "代表一", "通信设备", [60, 120, 250]),
            ("000002.SZ", "代表二", "通信设备", [60, 120, 250]),
            ("000003.SZ", "代表三", "半导体", [60, 120]),
            ("000004.SZ", "代表四", "医疗服务", [60]),
            ("000005.SZ", "代表五", "电力", [60]),
        )
        representatives = [
            {
                "ts_code": code,
                "name": name,
                "industry": industry,
                "amount_yi": float(100 - index),
                "pct_chg": float(index),
                "windows": windows,
            }
            for index, (code, name, industry, windows) in enumerate(
                representative_meta,
                start=1,
            )
        ]
        overlap = 2
        retention = 50.0
        turnover = 60.0
        cr3 = 80.0

    return {
        "schema": "rolling-new-high-structure-v1",
        "generator": "build_new_high_structure_manifest.py",
        "report_date": report_date,
        "as_of": as_of,
        "prev_as_of": prev_as_of,
        "status": "complete",
        "complete": True,
        "basis": "rolling-adjusted-high",
        "windows": [60, 120, 250],
        "trade_dates": trade_days,
        "daily_market_counts": {day: 5_000 for day in trade_days},
        "daily_market_coverage_min": 1.0,
        "market_count": 5_000,
        "market_reference_count": 5_000,
        "market_coverage": 1.0,
        "industry_coverage": 1.0,
        "counts": counts,
        "sixty_day_overlap": overlap,
        "sixty_day_retention_pct": retention,
        "sixty_day_turnover_pct": turnover,
        "sector_cr3_pct": cr3,
        "sectors": sectors,
        "representatives": representatives,
        "current_codes": current_codes,
        "previous_codes": previous_codes,
        "sources": {
            "quote": "fixture.daily",
            "adj_factor": "fixture.adj_factor",
            "calendar": "fixture.calendar",
            "industry": "fixture.sw_l2+fixture.stock_basic",
        },
        "errors": [],
    }


def _fake_market_rows(count: int = 4_000) -> list[dict]:
    rows = [
        {
            "ts_code": f"{index:06d}.SZ",
            "amount": float(100_000 - index),
        }
        for index in range(1, count + 1)
    ]
    if count >= 2:
        rows[0]["amount"] = 100_000.0
        rows[1]["amount"] = 100_000.0
    return rows


def _fake_industry_map(count: int = 4_000) -> dict[str, dict]:
    mapping = {
        f"{index:06d}.SZ": {
            "name": f"股票{index}",
            "sw_l2": "其他",
        }
        for index in range(1, count + 1)
    }
    for index, direction in (
        (1, "通信设备"),
        (2, "通信设备"),
        (31, "半导体"),
        (32, "半导体"),
        (40, "电力"),
        (41, "电力"),
    ):
        code = f"{index:06d}.SZ"
        if code in mapping:
            mapping[code]["sw_l2"] = direction
    return mapping


class _FakeCapacityProvider:
    def __init__(
        self,
        *,
        quotes_by_date: dict[str, list[dict]],
        industry_map: dict[str, dict],
        trade_dates: tuple[str, ...] = CAPACITY_TRADE_DATES,
    ) -> None:
        self.quotes_by_date = quotes_by_date
        self.industry_map = industry_map
        self.trade_dates = trade_dates
        self.calls: list[tuple[str, str | None]] = []

    @staticmethod
    def _result(data, source: str):
        return SimpleNamespace(success=True, data=data, source=source, error="")

    def get_market_daily_quotes(self, trade_date: str):
        self.calls.append(("market", trade_date))
        return self._result(
            self.quotes_by_date.get(trade_date, []),
            f"fake.daily:{trade_date}",
        )

    def get_stock_sw_industry_map(self):
        self.calls.append(("industry", None))
        return self._result(self.industry_map, "fake.sw_l2")

    def get_trade_calendar(self, as_of: str):
        self.calls.append(("calendar", as_of))
        return self._result(
            [
                {
                    "cal_date": trade_date.replace("-", ""),
                    "is_open": "1",
                }
                for trade_date in self.trade_dates
            ],
            "fake.calendar",
        )


class _YearScopedCapacityProvider(_FakeCapacityProvider):
    """模拟生产 provider：每次只返回查询日期所在自然年的交易日历。"""

    def get_trade_calendar(self, as_of: str):
        self.calls.append(("calendar", as_of))
        year = as_of[:4]
        return self._result(
            [
                {
                    "cal_date": trade_date.replace("-", ""),
                    "is_open": "1",
                }
                for trade_date in self.trade_dates
                if trade_date.startswith(year)
            ],
            "fake.calendar",
        )


def _fake_capacity_provider(
    *,
    market_count: int = 4_000,
    industry_count: int = 4_000,
    incomplete_history_date: str | None = None,
) -> _FakeCapacityProvider:
    quotes_by_date: dict[str, list[dict]] = {}
    for trade_date in CAPACITY_TRADE_DATES:
        count = 3_999 if trade_date == incomplete_history_date else market_count
        rows = _fake_market_rows(count)
        if trade_date in CAPACITY_TRADE_DATES[:2] and count >= 31:
            rows[30]["amount"] = 1.0
        quotes_by_date[trade_date] = rows
    return _FakeCapacityProvider(
        quotes_by_date=quotes_by_date,
        industry_map=_fake_industry_map(industry_count),
    )


def _new_high_trade_dates(count: int = 251) -> tuple[str, ...]:
    end = date_type.fromisoformat(DATE)
    return tuple(
        (end - timedelta(days=offset)).isoformat()
        for offset in range(count - 1, -1, -1)
    )


def _synthetic_new_high_inputs(builder):
    trade_dates = _new_high_trade_dates()
    codes = tuple(f"00000{index}.SZ" for index in range(1, 8))
    amounts = {
        "000001.SZ": 700_000,
        "000002.SZ": 600_000,
        "000003.SZ": 800_000,
        "000004.SZ": 400_000,
        "000005.SZ": 900_000,
        "000006.SZ": 300_000,
        "000007.SZ": 500_000,
    }
    normalized_by_date: dict[str, dict[str, dict]] = {}
    for trade_date in trade_dates:
        quotes: list[dict] = []
        factors: list[dict] = []
        for code in codes:
            high = 10.0
            factor = 1.0
            if code in {"000001.SZ", "000007.SZ"} and trade_date == trade_dates[-80]:
                high = 12.0
            if code == "000002.SZ" and trade_date == trade_dates[-180]:
                high = 12.0
            if code == "000004.SZ" and trade_date in trade_dates[-2:]:
                high = 11.0
            if trade_date == DATE:
                if code in {
                    "000001.SZ",
                    "000002.SZ",
                    "000003.SZ",
                    "000007.SZ",
                }:
                    high = 11.0
                elif code == "000005.SZ":
                    # 未复权价格低于历史值，乘复权因子后才构成严格突破。
                    high = 6.0
                    factor = 2.0
                elif code == "000006.SZ":
                    high = 20.0
            quotes.append(
                {
                    "ts_code": code,
                    "high": high,
                    "amount": amounts[code] if trade_date == DATE else 10_000,
                    "pct_chg": float(int(code[5]) - 3),
                }
            )
            factors.append({"ts_code": code, "adj_factor": factor})
        normalized_by_date[trade_date] = builder._normalize_day(quotes, factors)

    industry_map = {
        "000001.SZ": {"name": "通信甲", "sw_l2": "通信设备"},
        "000002.SZ": {"name": "通信乙", "sw_l2": "通信设备"},
        "000003.SZ": {"name": "半导体甲", "sw_l2": "半导体"},
        "000004.SZ": {"name": "等高未突破", "sw_l2": "电子"},
        "000005.SZ": {"name": "复权突破", "sw_l2": "医疗服务"},
        "000006.SZ": {"name": "上市不足", "sw_l2": "次新股"},
        "000007.SZ": {"name": "电力甲", "sw_l2": "电力"},
    }
    listing_dates = {code: trade_dates[0] for code in codes}
    listing_dates["000006.SZ"] = trade_dates[-30]
    return trade_dates, normalized_by_date, industry_map, listing_dates


def _calculate_synthetic_new_high_manifest(builder) -> dict:
    trade_dates, normalized_by_date, industry_map, listing_dates = (
        _synthetic_new_high_inputs(builder)
    )
    return builder.calculate_structure(
        DATE,
        DATE,
        trade_dates,
        normalized_by_date,
        industry_map,
        listing_dates,
        quote_source="fake.daily",
        factor_source="fake.adj_factor",
        calendar_source="fake.calendar",
        industry_source="fake.sw_l2+fake.stock_basic",
    )


class _FakeNewHighBoundaryProvider:
    def __init__(
        self,
        *,
        calendar_count: int = 251,
        industry_count: int = 4_000,
        basic_count: int = 4_000,
    ) -> None:
        self.trade_dates = _new_high_trade_dates(calendar_count)
        self.industry_count = industry_count
        self.basic_count = basic_count
        self.calls: list[str] = []

    @staticmethod
    def _result(data, source: str):
        return SimpleNamespace(success=True, data=data, source=source, error="")

    def get_trade_calendar(self, as_of: str):
        self.calls.append("calendar")
        return self._result(
            [
                {"cal_date": day.replace("-", ""), "is_open": "1"}
                for day in self.trade_dates
                if day <= as_of
            ],
            "fake.calendar",
        )

    def get_stock_sw_industry_map(self):
        self.calls.append("industry")
        return self._result(
            _fake_industry_map(self.industry_count),
            "fake.sw_l2",
        )

    def get_stock_basic_list(self, as_of: str):
        self.calls.append("basic")
        return self._result(
            [
                {
                    "ts_code": f"{index:06d}.SZ",
                    "list_date": "20000101",
                }
                for index in range(1, self.basic_count + 1)
            ],
            "fake.stock_basic",
        )


def _load_assembler():
    spec = importlib.util.spec_from_file_location("daily_review_assembler_under_test", ASSEMBLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses and postponed annotations resolve the defining module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_capacity_manifest_builder():
    spec = importlib.util.spec_from_file_location(
        "capacity_manifest_builder_under_test",
        CAPACITY_MANIFEST_BUILDER,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_new_high_manifest_builder():
    spec = importlib.util.spec_from_file_location(
        "new_high_manifest_builder_under_test",
        NEW_HIGH_MANIFEST_BUILDER,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def assembler():
    return _load_assembler()


@pytest.fixture(scope="module")
def capacity_manifest_builder():
    return _load_capacity_manifest_builder()


@pytest.fixture(scope="module")
def new_high_manifest_builder():
    return _load_new_high_manifest_builder()


def _valid_chunks(date: str = DATE) -> dict[str, str]:
    return {
        "head": f"""
<header class="top">
  <h1>每日多 Agent 复盘</h1>
  <p>只读产物 · 北向禁用 · 指数口径 000001.SH + 399106.SZ</p>
</header>
<div class="tldr" id="tldr">
  <h2>速览</h2>
  <div class="kpis">
    <span>上涨 1</span><span>下跌 2</span><span>涨停 3</span>
    <span>跌停 4</span><span>成交 5</span><span>连板 6</span>
  </div>
  <p>今天变了什么：<a data-claim-ref="claim-market" href="#claim-market">市场裁决</a>。</p>
  <p>核心矛盾：<a data-claim-ref="claim-sector" href="#claim-sector">板块事实</a>。</p>
  <p>明天验证什么：观察确认与证伪条件。</p>
  <span data-test-slot="tldr"></span>
</div>
<section class="blk" id="factor" data-factor-mode="shadow">
  <h2>三位一体重点因子</h2>
  <p data-as-of="{date}" data-claim-kind="judgment" data-source="shadow_factor_review" id="claim-primary-factor">[判断] 主因子维持 market_node。</p>
  <ul>
    <li>[事实] 指数结构仍是第一变量。</li>
    <li>[判断] sector_rhythm 仅作辅助。</li>
    <li>[判断] style_regime 与 leader_signal 未升格。</li>
  </ul>
  <p class="note" data-factor-role="status">[事实] 状态：正式 factor-score 停在 0713；本日仅影子口径，不写库。</p>
  <details class="evidence" data-as-of="{date}" data-items="4" data-evidence-kind="factor-detail">
    <summary>四因子完整对账（4 项）</summary>
    <div class="evidence-body"><p>market_node、sector_rhythm、style_regime、leader_signal 完整证据卡。</p></div>
  </details>
</section>
""",
        "s0": """
<section class="blk" id="s0">
  <h2>⓪ 前日判分</h2>
  <p>[事实] 本日无新增判分。</p>
</section>
""",
        "s1": f"""
<section class="blk" id="s1">
  <h2>① 大盘</h2>
  <p id="claim-market" data-claim-kind="judgment" data-source="market_snapshot" data-as-of="{date}">[判断] 市场仍在等待确认。</p>
  <ul><li>[事实] 成交较前日缩量。</li><li>[事实] 指数分化。</li></ul>
  <p>[判断] confirm_if：放量修复；invalidate_if：继续缩量下跌。</p>
  <details class="evidence" data-as-of="{date}" data-items="1">
    <summary>大盘原始证据（1 项）</summary>
    <div class="evidence-body">
      <p>六指数完整原始序列。</p>
      <span data-test-slot="appendix"></span>
    </div>
  </details>
</section>
""",
        "s2": f"""
<section class="blk" id="s2">
  <h2>② 板块</h2>
  <p id="claim-sector" data-claim-kind="fact" data-source="sector_flow" data-as-of="{date}">[事实] 主线资金与价格出现分歧。</p>
  <p>[判断] 本节只保留一条归属结论。</p>
  {_sector_concentration_verdict(date=date)}
  {_sector_state("sector-concentration", date=date)}
  {_sector_state("rising-recognition", date=date)}
  {_sector_state("falling-recognition", date=date)}
</section>
""",
        "s456": f"""
<section class="blk" id="s3">
  <h2>③ 情绪</h2>
  <p>[事实] 梯队变化有限。</p>
</section>
<section class="blk" id="s4">
  <h2>④ 风格</h2>
  <p>[判断] 风格暂未切换。</p>
</section>
<section class="blk" id="s5">
  <h2>⑤ 龙头</h2>
  <p>[事实] 本日无新增龙头。</p>
  {_capacity_no_data(date=date)}
  {_new_high_structure_verdict(date=date)}
  {_new_high_structure_state(date=date)}
</section>
<section class="blk" id="s6">
  <h2>⑥ 节点</h2>
  {_event_window_verdict(date=date)}
  {_event_window_state(date=date)}
</section>
""",
        "s7t": """
<section class="blk" id="s7">
  <h2>⑦ 持仓</h2>
  <p>[事实] active 持仓无变化；失效条件待补。</p>
</section>
<section class="blk" id="teachers">
  <h2>老师观点</h2>
  <p>[事实] 本日无新增。</p>
</section>
<section class="blk" id="industry">
  <h2>行业信息</h2>
  <p>[事实] 本日无改变判断的事项。</p>
</section>
<section class="blk" id="cognition">
  <h2>认知对照</h2>
  <p>[事实] 本日无新增或证伪认知。</p>
</section>
""",
        "proj": """
<section class="blk" id="proj">
  <h2>次日推演</h2>
  <p>[判断] 场景一：确认、证伪、优先级。</p>
</section>
""",
        "s8ops": """
<section class="blk" id="s8">
  <h2>⑧ 次日计划</h2>
  <ol><li>[判断] 验证点一</li><li>[判断] 验证点二</li><li>[判断] 验证点三</li></ol>
</section>
<section class="blk" id="ops">
  <h2>数据缺口</h2>
  <p>[事实] 仅列会改变结论可信度的缺口。</p>
  <span data-test-slot="visible"></span>
</section>
<footer><p>只读边界；[事实]/[判断] 分层；北向禁用；000001.SH + 399106.SZ。</p></footer>
""",
    }


def _write_chunks(directory: Path, date: str = DATE) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for chunk, body in _valid_chunks(date).items():
        path = directory / f"b{date}_{chunk}.html"
        path.write_text(body, encoding="utf-8")
        paths[chunk] = path
    _write_capacity_manifest(directory, date=date)
    _write_new_high_manifest(directory, date=date)
    return paths


def _write_capacity_manifest(
    directory: Path,
    *,
    date: str = DATE,
    payload: dict | None = None,
    name: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"capacity_{date}.json")
    path.write_text(
        json.dumps(
            payload if payload is not None else _capacity_manifest(report_date=date),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _write_new_high_manifest(
    directory: Path,
    *,
    date: str = DATE,
    payload: dict | None = None,
    name: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"new_high_{date}.json")
    path.write_text(
        json.dumps(
            payload if payload is not None else _new_high_manifest(report_date=date),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _render_valid(assembler, tmp_path: Path, date: str = DATE) -> tuple[str, dict[str, Path]]:
    paths = _write_chunks(tmp_path, date)
    return assembler.render_report(tmp_path, date), paths


def _replace_once(html: str, old: str, new: str) -> str:
    assert html.count(old) == 1, old
    return html.replace(old, new, 1)


def _extract_section(html: str, anchor: str) -> tuple[str, str]:
    anchor_id = html.index(f'id="{anchor}"')
    start = html.rfind("<section", 0, anchor_id)
    end = html.index("</section>", anchor_id) + len("</section>")
    assert start >= 0 and end > start
    return html[start:end], html[:start] + html[end:]


def _visible(html: str, fragment: str) -> str:
    marker = '<span data-test-slot="visible"></span>'
    return _replace_once(html, marker, f"{fragment}{marker}")


def _tldr(html: str, fragment: str) -> str:
    marker = '<span data-test-slot="tldr"></span>'
    return _replace_once(html, marker, f"{fragment}{marker}")


def _appendix(html: str, fragment: str) -> str:
    marker = '<span data-test-slot="appendix"></span>'
    return _replace_once(html, marker, f"{fragment}{marker}")


def _unscoped(html: str, fragment: str) -> str:
    return _replace_once(html, "    </article>", f"{fragment}    </article>")


def _table(rows: int, prefix: str = "行") -> str:
    assert rows >= 1
    body = [f"<tr><th>{prefix}0</th></tr>"]
    body.extend(f"<tr><td>{prefix}{i}</td></tr>" for i in range(1, rows))
    return f"<table>{''.join(body)}</table>"


def _capacity_health(html: str, fragment: str) -> str:
    marker = _capacity_no_data()
    return _replace_once(html, marker, fragment)


def _new_high_structure(html: str, fragment: str) -> str:
    return _replace_once(html, _new_high_structure_state(), fragment)


def _sector_contract(html: str, contract: str, fragment: str) -> str:
    marker = _sector_state(contract)
    return _replace_once(html, marker, fragment)


def _event_window(html: str, fragment: str) -> str:
    return _replace_once(html, _event_window_state(), fragment)


def _capacity_ops_notice(html: str) -> str:
    return _visible(html, "<p>[事实] 容量排名数据不完整。</p>")


def _new_high_ops_notice(
    html: str,
    *,
    hidden: bool = False,
    css_hidden: bool = False,
) -> str:
    hidden_attr = " hidden" if hidden else ""
    class_attr = ' class="toc"' if css_hidden else ""
    return _visible(
        html,
        f"<p{hidden_attr}{class_attr}>[事实] 滚动新高结构数据不完整。</p>",
    )


def _sector_ops_notice(
    html: str,
    contract: str,
    *,
    hidden: bool = False,
    css_hidden: bool = False,
) -> str:
    markers = {
        "sector-concentration": "板块集中度数据不完整",
        "rising-recognition": "主升辨识度矩阵数据不完整",
        "falling-recognition": "主跌辨识度矩阵数据不完整",
    }
    hidden_attr = " hidden" if hidden else ""
    class_attr = ' class="toc"' if css_hidden else ""
    return _visible(
        html,
        f"<p{hidden_attr}{class_attr}>[事实] {markers[contract]}。</p>",
    )


def _event_ops_notice(
    html: str,
    *,
    hidden: bool = False,
    css_hidden: bool = False,
) -> str:
    hidden_attr = " hidden" if hidden else ""
    class_attr = ' class="toc"' if css_hidden else ""
    return _visible(
        html,
        f"<p{hidden_attr}{class_attr}>[事实] 未来7个自然日事件窗数据不完整。</p>",
    )


def _capacity_row(
    *,
    name: str = "容量标的",
    code: str = "000001.SZ",
    direction: str = "电子",
    tier: str = "core",
    market_rank: int = 20,
    direction_rank: int = 1,
    top50_days: int = 0,
) -> str:
    return (
        f'<tr data-code="{code}" data-direction="{direction}" '
        f'data-tier="{tier}" '
        f'data-market-rank="{market_rank}" '
        f'data-direction-rank="{direction_rank}" '
        f'data-top50-days="{top50_days}">'
        f"<td>{code}</td><td>{name}</td><td>{direction}</td><td>{tier}</td>"
        f"<td>{market_rank}</td><td>{direction_rank}</td><td>{top50_days}</td></tr>"
    )


def _capacity_table(
    *rows: str,
    date: str = DATE,
    source_status: str = "complete",
    universe_count: int | str = 5_000,
    rank_source: str = "daily.amount",
) -> str:
    return (
        '<h3>中军健康度</h3><table data-capacity-health="v1" '
        f'data-as-of="{date}" data-source-status="{source_status}" '
        f'data-universe-count="{universe_count}" data-rank-source="{rank_source}">'
        "<thead><tr><th>代码</th><th>股票</th><th>方向</th><th>容量层级</th>"
        "<th>市场排名</th><th>方向排名</th><th>近5日Top50次数</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _new_high_structure_row(
    *,
    label: str = "全市场滚动新高结构",
    current_counts: str = "44 / 25 / 19",
    prev_counts: str = "71 / 38 / 27",
) -> str:
    return (
        f"<tr><td>{label}</td><td>{current_counts}</td>"
        f"<td>{prev_counts}</td></tr>"
    )


def _new_high_structure_table(
    *rows: str,
    date: str = DATE,
    prev_date: str = "2026-07-15",
    source_status: str = "complete",
    market_count: int | str = 5_000,
    current_60: int | str = 44,
    current_120: int | str = 25,
    current_250: int | str = 19,
    prev_60: int | str = 71,
    prev_120: int | str = 38,
    prev_250: int | str = 27,
    basis: str = "rolling-adjusted-high",
    include_header: bool = True,
) -> str:
    header = (
        "<thead><tr><th>口径</th><th>当日 60 / 120 / 250 日</th>"
        "<th>前一交易日 60 / 120 / 250 日</th></tr></thead>"
        if include_header
        else ""
    )
    return (
        f'<table data-new-high-structure="v1" data-as-of="{date}" '
        f'data-prev-as-of="{prev_date}" '
        f'data-source-status="{source_status}" '
        f'data-market-count="{market_count}" '
        f'data-current-60-count="{current_60}" '
        f'data-current-120-count="{current_120}" '
        f'data-current-250-count="{current_250}" '
        f'data-prev-60-count="{prev_60}" '
        f'data-prev-120-count="{prev_120}" '
        f'data-prev-250-count="{prev_250}" '
        f'data-basis="{basis}">'
        f"{header}<tbody>{''.join(rows)}</tbody></table>"
    )


def _new_high_manifest_table(**overrides) -> str:
    options = {
        "market_count": 5_000,
        "current_60": 5,
        "current_120": 3,
        "current_250": 2,
        "prev_60": 4,
        "prev_120": 2,
        "prev_250": 1,
    }
    options.update(overrides)
    return _new_high_structure_table(
        _new_high_structure_row(
            label=(
                "行业 Top3：通信设备 / 医疗服务 / 半导体；CR3 80.0%；"
                "重合 2；延续率 50.00%；换手率 60.00%；"
                "代表票：代表一、代表二、代表三、代表四、代表五"
            ),
            current_counts="5 / 3 / 2",
            prev_counts="4 / 2 / 1",
        ),
        **options,
    )


def _with_complete_new_high_manifest_contract(html: str, table: str) -> str:
    html = _replace_once(
        html,
        _new_high_structure_verdict(),
        _new_high_structure_verdict(
            text="[判断] 60/120/250 日滚动新高为 5/3/2，广度待确认。"
        ),
    )
    return _new_high_structure(html, table)


def _sector_concentration_table(
    *rows: str,
    date: str = DATE,
    source_status: str = "complete",
) -> str:
    return (
        f'<table data-sector-concentration="v1" data-as-of="{date}" '
        f'data-source-status="{source_status}">'
        "<thead><tr><th>方向</th><th>成交占比</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _concentration_row(direction: str = "电子", share: str = "12.30") -> str:
    return (
        f'<tr data-direction="{direction}" data-market-share="{share}">'
        f"<td>{direction}</td><td>{share}%</td></tr>"
    )


def _rising_recognition_table(
    *rows: str,
    date: str = DATE,
    source_status: str = "complete",
) -> str:
    return (
        f'<table data-rising-recognition="v1" data-as-of="{date}" '
        f'data-source-status="{source_status}">'
        "<thead><tr><th>主升方向</th><th>辨识度个股</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _recognition_row(
    *,
    direction: str = "电子",
    code: str = "000001.SZ",
    name: str = "辨识度标的",
) -> str:
    return (
        f'<tr data-direction="{direction}" data-code="{code}">'
        f"<td>{direction}</td><td>{code} {name}</td></tr>"
    )


def _falling_recognition_table(*rows: str, date: str = DATE) -> str:
    return (
        f'<table data-falling-recognition="v1" data-as-of="{date}" '
        'data-source-status="complete">'
        "<thead><tr><th>主跌方向</th><th>辨识度个股</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _event_window_table(
    *rows: str,
    date: str = DATE,
    source_status: str = "complete",
) -> str:
    report_day = date_type.fromisoformat(date)
    window_start = (report_day + timedelta(days=1)).isoformat()
    window_end = (report_day + timedelta(days=7)).isoformat()
    return (
        f'<table data-event-window="v1" data-as-of="{date}" '
        f'data-source-status="{source_status}" data-window-start="{window_start}" '
        f'data-window-end="{window_end}">'
        "<thead><tr><th>日期</th><th>事件</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _event_row(
    event_date: str,
    event: str = "窗口事件",
    market_status: str = "open",
) -> str:
    return (
        f'<tr data-event-date="{event_date}" data-market-status="{market_status}">'
        f"<td>{event_date}</td>"
        f"<td>{event}</td></tr>"
    )


def _event_rows(*, date: str = DATE) -> tuple[str, ...]:
    report_day = date_type.fromisoformat(date)
    return tuple(
        _event_row(
            (report_day + timedelta(days=offset)).isoformat(),
            event="交易日观察" if offset <= 1 or offset >= 4 else "周末事件观察",
            market_status="open" if offset <= 1 or offset >= 4 else "closed",
        )
        for offset in range(1, 8)
    )


def _assert_report_error(
    assembler,
    html: str,
    expected_code: str | None = None,
    *,
    capacity_manifest: dict | None = None,
    new_high_manifest: dict | None = None,
):
    with pytest.raises(assembler.ReportValidationError) as caught:
        assembler.validate_report(
            html,
            capacity_manifest=capacity_manifest,
            new_high_manifest=new_high_manifest,
        )
    error = caught.value
    assert getattr(error, "code", None)
    if expected_code:
        assert error.code == expected_code
    assert hasattr(error, "section")
    assert hasattr(error, "metrics")
    return error


def test_render_validate_and_write_valid_compact_report(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    assert assembler.REQUIRED_ANCHORS == ANCHORS
    assert tuple(anchor for anchor, _ in assembler.NAV) == ANCHORS
    assert 'data-report-schema="compact-v1"' in html
    assert f'data-report-date="{DATE}"' in html
    assert "只读" in html
    assert "北向禁用" in html
    assert "000001.SH + 399106.SZ" in html
    assert "[事实]" in html and "[判断]" in html
    for anchor in ANCHORS:
        assert html.count(f'id="{anchor}"') == 1

    positions = [html.index(f'id="{anchor}"') for anchor in ANCHORS]
    assert positions == sorted(positions)
    assert html.index('id="tldr"') < html.index('id="factor"') < html.index('id="s0"')
    assert html.index('id="cognition"') < html.index('id="proj"') < html.index('id="s8"')
    for aria_label in ("章节导航", "移动章节导航"):
        match = re.search(
            rf'<nav aria-label="{aria_label}"[^>]*>(.*?)</nav>',
            html,
            re.DOTALL,
        )
        assert match
        assert tuple(re.findall(r'href="#([^"]+)"', match.group(1))) == ANCHORS

    metrics = assembler.validate_report(html)
    assert type(metrics).__name__ == "ReportMetrics"
    assert metrics.tldr_chars <= 500
    assert metrics.visible_chars <= 10_000
    assert metrics.visible_tables <= 12
    assert metrics.visible_rows <= 80
    assert metrics.appendix_chars <= 40_000
    assert metrics.appendix_tables <= 60
    assert metrics.appendix_rows <= 400

    output = tmp_path / "nested" / "compact.html"
    written = assembler.write_report(
        html,
        output,
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(),
    )
    assert isinstance(written, Path)
    assert written == output
    assert output.read_text(encoding="utf-8") == html
    assert output.stat().st_mode & 0o777 == 0o644


def test_write_report_requires_capacity_manifest_before_writing(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    output = tmp_path / "must-not-exist.html"

    with pytest.raises(assembler.ReportValidationError) as caught:
        assembler.write_report(html, output)

    assert caught.value.code
    assert caught.value.section == "s5"
    assert not output.exists()


def test_write_report_requires_new_high_manifest_before_writing(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    output = tmp_path / "missing-new-high-manifest.html"

    with pytest.raises(assembler.ReportValidationError) as caught:
        assembler.write_report(
            html,
            output,
            capacity_manifest=_capacity_manifest(),
        )

    assert caught.value.code == "missing_new_high_manifest"
    assert caught.value.section == "s5"
    assert not output.exists()


def test_collect_metrics_decodes_entities_and_ignores_unicode_whitespace(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    before = assembler.collect_metrics(html)

    changed = _visible(html, " \n\t\u3000&amp;")
    after = assembler.collect_metrics(changed)

    assert after.visible_chars == before.visible_chars + 1
    assert after.tldr_chars == before.tldr_chars


def test_search_opens_nested_details_and_only_global_toggles_evidence(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    assert "document.querySelectorAll('details')" in html
    assert "d.classList.contains('evidence')" in html
    assert "node.closest('details')" in html
    assert "d.parentElement.closest('details')" in html
    assert '<html class="no-js" lang="zh-CN">' in html
    assert "document.documentElement.classList.remove('no-js')" in html
    assert "if('IntersectionObserver' in window)" in html


def test_missing_chunk_is_rejected(assembler, tmp_path):
    chunk_dir = tmp_path / "chunks"
    paths = _write_chunks(chunk_dir)
    paths["s2"].unlink()

    with pytest.raises(assembler.ReportValidationError) as caught:
        assembler.render_report(chunk_dir, DATE)
    assert getattr(caught.value, "code", None)
    assert hasattr(caught.value, "section")
    assert hasattr(caught.value, "metrics")


def test_chunk_repeating_another_chunks_anchor_is_rejected(assembler, tmp_path):
    html, paths = _render_valid(assembler, tmp_path / "chunks")
    assert html.count('id="s1"') == 1
    paths["s2"].write_text(
        paths["s2"].read_text(encoding="utf-8")
        + '<section class="blk" id="s1"><h2>重复大盘</h2></section>',
        encoding="utf-8",
    )

    invalid = assembler.render_report(paths["s2"].parent, DATE)
    _assert_report_error(assembler, invalid, "invalid_anchor")


def test_missing_anchor_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(html, 'id="industry"', 'id="industry-missing"')
    _assert_report_error(assembler, invalid)


def test_missing_factor_anchor_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(html, 'id="factor"', 'id="factor-missing"')
    error = _assert_report_error(assembler, invalid, "invalid_anchor")
    assert error.section == "factor"


def test_legacy_factor_location_in_proj_is_rejected(assembler, tmp_path):
    paths = _write_chunks(tmp_path / "chunks")
    head = paths["head"].read_text(encoding="utf-8")
    factor, head_without_factor = _extract_section(head, "factor")
    paths["head"].write_text(head_without_factor, encoding="utf-8")
    paths["proj"].write_text(
        factor + paths["proj"].read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    invalid = assembler.render_report(paths["head"].parent, DATE)
    error = _assert_report_error(
        assembler,
        invalid,
        "anchor_chunk_mismatch",
    )
    assert error.section == "factor"


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (' data-factor-mode="shadow"', ""),
        ('data-factor-mode="shadow"', 'data-factor-mode="unknown"'),
        (' data-factor-role="status"', ""),
        (' data-evidence-kind="factor-detail"', ""),
    ],
)
def test_factor_analysis_requires_mode_status_and_detail_evidence(
    assembler, tmp_path, old, new
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(html, old, new)
    error = _assert_report_error(assembler, invalid, "invalid_factor_contract")
    assert error.section == "factor"


def test_factor_must_be_a_blk_section(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    factor, _ = _extract_section(html, "factor")

    wrong_tag = factor.replace("<section", "<div", 1)
    wrong_tag = wrong_tag[: -len("</section>")] + "</div>"
    _assert_report_error(
        assembler,
        _replace_once(html, factor, wrong_tag),
        "invalid_factor_contract",
    )

    missing_class = factor.replace('class="blk" ', "", 1)
    _assert_report_error(
        assembler,
        _replace_once(html, factor, missing_class),
        "invalid_factor_contract",
    )


def test_factor_analysis_rejects_empty_shell_and_more_than_three_evidence_items(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    factor, _ = _extract_section(html, "factor")
    shell = """<section class="blk" id="factor" data-factor-mode="shadow">
      <h2>三位一体重点因子</h2><p>[事实] 本日无可判数据。</p>
    </section>\n"""
    _assert_report_error(
        assembler,
        _replace_once(html, factor, shell),
        "invalid_factor_contract",
    )

    too_many = factor.replace(
        "</ul>",
        "<li>[事实] 第四条可见证据。</li></ul>",
        1,
    )
    _assert_report_error(
        assembler,
        _replace_once(html, factor, too_many),
        "invalid_factor_contract",
    )


def test_factor_no_data_mode_requires_explicit_no_data_statement(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    factor, _ = _extract_section(html, "factor")
    no_data = """<section class="blk" id="factor" data-factor-mode="no_data">
      <h2>三位一体重点因子</h2><p data-factor-role="no-data">[事实] 本日无可判数据。</p>
    </section>\n"""
    assembler.validate_report(_replace_once(html, factor, no_data))

    invalid = no_data.replace("本日无可判数据", "暂无说明")
    _assert_report_error(
        assembler,
        _replace_once(html, factor, invalid),
        "invalid_factor_contract",
    )

    missing_role = no_data.replace(' data-factor-role="no-data"', "")
    _assert_report_error(
        assembler,
        _replace_once(html, factor, missing_role),
        "invalid_factor_contract",
    )


def test_factor_no_data_mode_rejects_additional_analysis(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    factor, _ = _extract_section(html, "factor")
    no_data_with_analysis = """<section class="blk" id="factor" data-factor-mode="no_data">
      <h2>三位一体重点因子</h2>
      <p data-factor-role="no-data">[事实] 本日无可判数据。</p>
      <table><tr><td>[判断] 主因子已切换，完整分析在此。</td></tr></table>
    </section>\n"""
    _assert_report_error(
        assembler,
        _replace_once(html, factor, no_data_with_analysis),
        "invalid_factor_contract",
    )


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("formal", "[事实] 状态：正式 factor-score 已完成。"),
        ("rule_only", "[事实] 状态：rule_only 结果，仅作只读引用。"),
    ],
)
def test_factor_analysis_accepts_formal_and_rule_only_modes(
    assembler, tmp_path, mode, status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    changed = _replace_once(
        html,
        'data-factor-mode="shadow"',
        f'data-factor-mode="{mode}"',
    )
    changed = _replace_once(
        changed,
        "[事实] 状态：正式 factor-score 停在 0713；本日仅影子口径，不写库。",
        status,
    )
    assembler.validate_report(changed)


def test_factor_mode_cannot_relabel_shadow_analysis_as_formal(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        'data-factor-mode="shadow"',
        'data-factor-mode="formal"',
    )
    _assert_report_error(assembler, invalid, "invalid_factor_contract")


@pytest.mark.parametrize(
    "completed_status",
    [
        "正式 factor-score 已完成",
        "正式 factor-score：已完成",
        "正式 factor-score（已完成）",
        "正式 factor-score 评分已完成",
    ],
)
def test_shadow_factor_rejects_completed_formal_score_status(
    assembler, tmp_path, completed_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        "[事实] 状态：正式 factor-score 停在 0713；本日仅影子口径，不写库。",
        f"[事实] 状态：{completed_status}；本日仅影子口径，不写库。",
    )
    _assert_report_error(assembler, invalid, "invalid_factor_contract")


def test_shadow_factor_allows_unmet_formal_completion_condition(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    valid = _replace_once(
        html,
        "[事实] 状态：正式 factor-score 停在 0713；本日仅影子口径，不写库。",
        "[事实] 状态：正式 factor-score 完成条件未满足；本日仅影子口径，不写库。",
    )
    assembler.validate_report(valid)


@pytest.mark.parametrize("wrapper", ["details", "hidden"])
def test_factor_analysis_must_be_visible_by_default(assembler, tmp_path, wrapper):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    factor, _ = _extract_section(html, "factor")
    visible_start = factor.index('<p data-as-of="')
    evidence_start = factor.index('<details class="evidence"')
    visible_analysis = factor[visible_start:evidence_start]
    if wrapper == "details":
        hidden_analysis = (
            "<details><summary>重点因子</summary>"
            + visible_analysis
            + "</details>"
        )
        invalid_factor = factor.replace(visible_analysis, hidden_analysis, 1)
    else:
        invalid_factor = factor.replace(
            '<section class="blk" id="factor" data-factor-mode="shadow">',
            '<section class="blk" id="factor" data-factor-mode="shadow" hidden>',
            1,
        )
    _assert_report_error(
        assembler,
        _replace_once(html, factor, invalid_factor),
        "invalid_factor_contract",
    )


def test_factor_claim_and_items_reject_text_hidden_in_descendants(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    hidden = html
    for text in (
        "[判断] 主因子维持 market_node。",
        "[事实] 指数结构仍是第一变量。",
        "[判断] sector_rhythm 仅作辅助。",
        "[判断] style_regime 与 leader_signal 未升格。",
    ):
        hidden = _replace_once(hidden, text, f"<span hidden>{text}</span>")
    _assert_report_error(assembler, hidden, "invalid_factor_contract")


def test_factor_detail_evidence_requires_all_four_factor_keys(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        "market_node、sector_rhythm、style_regime、leader_signal 完整证据卡。",
        "占位证据。",
    )
    _assert_report_error(assembler, invalid, "invalid_factor_contract")


def test_duplicate_anchor_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _visible(html, '<section id="s4"><h2>重复风格</h2></section>')
    _assert_report_error(assembler, invalid)


def test_anchors_must_follow_the_fixed_document_order(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    s4_start = html.index('<section class="blk" id="s4">')
    s5_start = html.index('<section class="blk" id="s5">')
    s6_start = html.index('<section class="blk" id="s6">')
    invalid = (
        html[:s4_start]
        + html[s5_start:s6_start]
        + html[s4_start:s5_start]
        + html[s6_start:]
    )
    _assert_report_error(assembler, invalid, "invalid_anchor_order")


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "<summary>大盘原始证据（1 项）</summary>",
            "<summary> \n\t </summary>",
        ),
        (
            f' data-as-of="{DATE}" data-items="1"',
            ' data-items="1"',
        ),
        (
            f' data-as-of="{DATE}" data-items="1"',
            ' data-as-of="not-a-date" data-items="1"',
        ),
        (
            f' data-as-of="{DATE}" data-items="1"',
            ' data-as-of="2026-07-17" data-items="1"',
        ),
        (
            f' data-as-of="{DATE}" data-items="1"',
            f' data-as-of="{DATE}"',
        ),
        (
            f' data-as-of="{DATE}" data-items="1"',
            f' data-as-of="{DATE}" data-items="many"',
        ),
        (
            "<summary>大盘原始证据（1 项）</summary>",
            "<summary>大盘原始证据（10 项）</summary>",
        ),
    ],
)
def test_evidence_requires_nonempty_summary_and_valid_metadata(
    assembler, tmp_path, old, new
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    _assert_report_error(assembler, _replace_once(html, old, new))


def test_evidence_must_be_closed_by_default(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        f'<details class="evidence" data-as-of="{DATE}" data-items="1">',
        f'<details class="evidence" open data-as-of="{DATE}" data-items="1">',
    )
    _assert_report_error(assembler, invalid)


def test_unclosed_evidence_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    closing = """      <span data-test-slot="appendix"></span>
    </div>
  </details>"""
    invalid = _replace_once(html, closing, closing.replace("</details>", ""))
    _assert_report_error(assembler, invalid)


def test_evidence_requires_a_nonempty_body(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    body = """    <div class="evidence-body">
      <p>六指数完整原始序列。</p>
      <span data-test-slot="appendix"></span>
    </div>
"""
    invalid = _replace_once(html, body, "")
    _assert_report_error(assembler, invalid, "empty_evidence_body")


def test_evidence_body_may_be_a_table_artifact(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    body = """    <div class="evidence-body">
      <p>六指数完整原始序列。</p>
      <span data-test-slot="appendix"></span>
    </div>
"""
    changed = _replace_once(html, body, "    <table><tr></tr></table>\n")
    assembler.validate_report(changed)


def test_capacity_health_heading_requires_v1_table_metadata(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    valid_table = _capacity_table(_capacity_row())
    invalid_table = valid_table.replace(' data-capacity-health="v1"', "", 1)

    error = _assert_report_error(
        assembler,
        _capacity_health(html, invalid_table),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


def test_capacity_health_contract_cannot_be_omitted(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    marker = _capacity_no_data()

    error = _assert_report_error(
        assembler,
        _replace_once(html, marker, ""),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


@pytest.mark.parametrize(
    "attribute",
    [
        ' data-code="000001.SZ"',
        ' data-direction="电子"',
        ' data-tier="core"',
        ' data-market-rank="20"',
        ' data-direction-rank="1"',
        ' data-top50-days="0"',
    ],
)
def test_capacity_health_rows_require_complete_capacity_metadata(
    assembler, tmp_path, attribute
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _capacity_table(_capacity_row())
    invalid = table.replace(attribute, "", 1)

    error = _assert_report_error(
        assembler,
        _capacity_health(html, invalid),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


@pytest.mark.parametrize(
    "attribute",
    [
        f' data-as-of="{DATE}"',
        ' data-source-status="complete"',
        ' data-universe-count="5000"',
        ' data-rank-source="daily.amount"',
    ],
)
def test_capacity_health_table_requires_complete_ranking_provenance(
    assembler, tmp_path, attribute
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _capacity_table(_capacity_row()).replace(attribute, "", 1)

    _assert_report_error(
        assembler,
        _capacity_health(html, table),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    "table",
    [
        _capacity_table(_capacity_row(), date="not-a-date"),
        _capacity_table(_capacity_row(), date="2026-07-17"),
        _capacity_table(_capacity_row(), source_status="partial"),
        _capacity_table(_capacity_row(), universe_count=0),
        _capacity_table(_capacity_row(), universe_count="many"),
        _capacity_table(_capacity_row(), rank_source="trend_leader_pool"),
    ],
)
def test_capacity_health_table_rejects_invalid_ranking_provenance(
    assembler, tmp_path, table
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _capacity_health(html, table),
        "invalid_capacity_health",
    )


def test_capacity_health_core_over_capacity_threshold_is_rejected(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(tier="core", market_rank=31, direction_rank=1, top50_days=5)

    error = _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


def test_capacity_health_candidate_over_capacity_threshold_is_rejected(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        tier="candidate", market_rank=51, direction_rank=1, top50_days=3
    )

    error = _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


@pytest.mark.parametrize("market_rank", [20, 30])
def test_capacity_health_candidate_cannot_overlap_core_market_rank(
    assembler, tmp_path, market_rank
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        tier="candidate",
        market_rank=market_rank,
        direction_rank=1,
        top50_days=0,
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    ("tier", "market_rank"),
    [("core", 20), ("candidate", 40)],
)
def test_capacity_health_direction_rank_over_two_is_rejected(
    assembler, tmp_path, tier, market_rank
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        tier=tier,
        market_rank=market_rank,
        direction_rank=3,
        top50_days=3,
    )

    error = _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


def test_capacity_health_rejects_unknown_tier(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(tier="trend", market_rank=10, direction_rank=1)

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize("top50_days", [-1, 6])
def test_capacity_health_top50_days_must_be_between_zero_and_five(
    assembler, tmp_path, top50_days
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        tier="core",
        market_rank=20,
        direction_rank=1,
        top50_days=top50_days,
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    ("market_rank", "direction_rank"),
    [(0, 1), (-1, 1), (20, 0), (20, -1)],
)
def test_capacity_health_ranks_must_be_positive_and_within_universe(
    assembler, tmp_path, market_rank, direction_rank
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        market_rank=market_rank,
        direction_rank=direction_rank,
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(row)),
        "invalid_capacity_health",
    )


def test_capacity_health_market_rank_cannot_exceed_declared_universe(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _capacity_table(_capacity_row(market_rank=20), universe_count=19)

    _assert_report_error(
        assembler,
        _capacity_health(html, table),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize("code", ["000001", "000001.XSHE", "ABC001.SZ"])
def test_capacity_health_requires_normalized_ts_code(assembler, tmp_path, code):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_table(_capacity_row(code=code))),
        "invalid_capacity_health",
    )


def test_capacity_health_rejects_duplicate_stock_code(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    duplicate = _capacity_table(
        _capacity_row(code="000001.SZ", name="标的一"),
        _capacity_row(code="000001.SZ", name="标的二", market_rank=21),
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, duplicate),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    "marker",
    [
        _capacity_no_data(),
        "<p>[判断] 本节只保留一条归属结论。</p>",
    ],
)
def test_old_pool_cannot_be_labeled_as_capacity_health(
    assembler, tmp_path, marker
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        marker,
        f"{marker}<p>[判断] 旧池中军仍保持健康。</p>",
    )

    error = _assert_report_error(
        assembler,
        invalid,
        "invalid_capacity_health",
    )
    assert error.section in {"s2", "s5"}


def test_trend_pool_identity_cannot_replace_capacity_metadata(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = (
        '<h3>中军健康度</h3><table data-capacity-health="v1">'
        "<tbody><tr data-tier=\"core\" data-source=\"trend_leader_pool\">"
        "<td>趋势池成员</td></tr></tbody></table>"
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, table),
        "invalid_capacity_health",
    )


def test_capacity_health_requires_table_immediately_after_heading(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _capacity_table(_capacity_row()).replace(
        "</h3><table", "</h3><p>趋势池说明。</p><table", 1
    )

    _assert_report_error(
        assembler,
        _capacity_health(html, table),
        "invalid_capacity_health",
    )


def test_capacity_health_empty_table_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    empty = _capacity_table()

    _assert_report_error(
        assembler,
        _capacity_health(html, empty),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f' data-as-of="{DATE}"', ""),
        (' data-source-status="complete"', ""),
        (f'data-as-of="{DATE}"', 'data-as-of="not-a-date"'),
        (f'data-as-of="{DATE}"', 'data-as-of="2026-07-17"'),
        ('data-source-status="complete"', 'data-source-status="unknown"'),
    ],
)
def test_capacity_health_no_data_requires_valid_provenance(
    assembler, tmp_path, old, new
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid_state = _capacity_no_data().replace(old, new, 1)

    error = _assert_report_error(
        assembler,
        _capacity_health(html, invalid_state),
        "invalid_capacity_health",
    )
    assert error.section == "s5"


def test_capacity_health_no_data_accepts_complete_source_status(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    assembler.validate_report(_capacity_health(html, _capacity_no_data()))


@pytest.mark.parametrize("source_status", ["partial", "failed"])
def test_capacity_health_no_data_rejects_incomplete_source_status(
    assembler, tmp_path, source_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _capacity_health(html, _capacity_no_data(source_status=source_status)),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize("source_status", ["partial", "failed"])
def test_capacity_health_missing_data_requires_visible_ops_notice(
    assembler, tmp_path, source_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    missing = _capacity_health(
        html,
        _capacity_missing_data(source_status=source_status),
    )

    _assert_report_error(assembler, missing, "invalid_capacity_health")
    _assert_report_error(
        assembler,
        _visible(missing, "<p hidden>[事实] 容量排名数据不完整。</p>"),
        "invalid_capacity_health",
    )
    _assert_report_error(
        assembler,
        _visible(missing, '<p class="toc">[事实] 容量排名数据不完整。</p>'),
        "invalid_capacity_health",
    )
    assembler.validate_report(_capacity_ops_notice(missing))


@pytest.mark.parametrize(
    "state",
    [
        _capacity_missing_data(source_status="complete"),
        _capacity_missing_data(text="[事实] 本日无可确认容量中军"),
        _capacity_missing_data(date="not-a-date"),
        _capacity_missing_data(date="2026-07-17"),
    ],
)
def test_capacity_health_missing_data_requires_exact_state(
    assembler, tmp_path, state
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _capacity_ops_notice(_capacity_health(html, state))

    _assert_report_error(assembler, report, "invalid_capacity_health")


def test_capacity_health_requires_exact_unique_no_data_state(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    wrong_text = _capacity_no_data(text="[事实] 本日无新增中军")
    _assert_report_error(
        assembler,
        _capacity_health(html, wrong_text),
        "invalid_capacity_health",
    )

    duplicate = _capacity_no_data() * 2
    _assert_report_error(
        assembler,
        _capacity_health(html, duplicate),
        "invalid_capacity_health",
    )


def test_capacity_health_requires_exactly_one_structured_table(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    duplicate = _capacity_table(_capacity_row()) * 2

    _assert_report_error(
        assembler,
        _capacity_health(html, duplicate),
        "invalid_capacity_health",
    )


@pytest.mark.parametrize(
    ("tier", "market_rank", "direction_rank", "top50_days"),
    [
        ("core", 30, 2, 0),
        ("candidate", 50, 2, 0),
    ],
)
def test_capacity_health_accepts_qualified_core_and_candidate_rows(
    assembler,
    tmp_path,
    tier,
    market_rank,
    direction_rank,
    top50_days,
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    row = _capacity_row(
        tier=tier,
        market_rank=market_rank,
        direction_rank=direction_rank,
        top50_days=top50_days,
    )

    assembler.validate_report(_capacity_health(html, _capacity_table(row)))


def test_capacity_health_no_qualified_items_uses_explicit_text_without_table(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = html

    assembler.validate_report(report)
    s5, _ = _extract_section(report, "s5")
    assert "本日无可确认容量中军" in s5
    assert f'data-as-of="{DATE}"' in s5
    assert 'data-source-status="complete"' in s5
    assert 'data-capacity-health="v1"' not in s5


def test_structural_validation_may_omit_capacity_manifest(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    assembler.validate_report(html)
    assembler.validate_report(html, capacity_manifest=_capacity_manifest())


def test_capacity_manifest_accepts_exact_html_row_match(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _capacity_health(
        html,
        _capacity_table(_capacity_row()),
    )
    manifest = _capacity_manifest(rows=[_capacity_manifest_row()])

    assembler.validate_report(report, capacity_manifest=manifest)


@pytest.mark.parametrize(
    ("visible_old", "visible_new"),
    [
        ("<td>容量标的</td>", "<td>伪造名称</td>"),
        ("<td>电子</td>", "<td>传媒</td>"),
        ("<td>core</td>", "<td>candidate</td>"),
        ("<td>20</td>", "<td>999</td>"),
        ("<td>1</td>", "<td>9</td>"),
        ("<td>0</td>", "<td>5</td>"),
    ],
)
def test_capacity_manifest_rejects_visible_cells_that_disagree_with_metadata(
    assembler,
    tmp_path,
    visible_old,
    visible_new,
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _capacity_health(html, _capacity_table(_capacity_row()))
    report = _replace_once(report, visible_old, visible_new)
    manifest = _capacity_manifest(rows=[_capacity_manifest_row()])

    error = _assert_report_error(
        assembler,
        report,
        capacity_manifest=manifest,
    )
    assert error.section == "s5"


@pytest.mark.parametrize(
    "mismatch",
    ["row_set", "rank", "tier", "as_of", "universe"],
)
def test_capacity_manifest_rejects_html_sidecar_mismatch(
    assembler, tmp_path, mismatch
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _capacity_health(html, _capacity_table(_capacity_row()))
    row = _capacity_manifest_row()
    manifest_kwargs: dict = {"rows": [row]}
    if mismatch == "row_set":
        manifest_kwargs["rows"] = [
            _capacity_manifest_row(code="000002.SZ", name="另一标的")
        ]
    elif mismatch == "rank":
        row["market_rank"] = 21
    elif mismatch == "tier":
        row["tier"] = "candidate"
        row["market_rank"] = 40
    elif mismatch == "as_of":
        manifest_kwargs["as_of"] = "2026-07-15"
    elif mismatch == "universe":
        manifest_kwargs["universe_count"] = 4_999
    manifest = _capacity_manifest(**manifest_kwargs)

    _assert_report_error(
        assembler,
        report,
        "capacity_manifest_mismatch",
        capacity_manifest=manifest,
    )


@pytest.mark.parametrize(
    "invalid_case",
    ["schema", "future_as_of", "status", "universe", "duplicate_code"],
)
def test_capacity_manifest_rejects_invalid_payload(
    assembler, tmp_path, invalid_case
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    manifest = _capacity_manifest()
    if invalid_case == "schema":
        manifest["schema"] = "capacity-health-v0"
    elif invalid_case == "future_as_of":
        manifest = _capacity_manifest(as_of="2026-07-17")
    elif invalid_case == "status":
        manifest["status"] = "partial"
    elif invalid_case == "universe":
        manifest["market_universe_count"] = 3_999
    elif invalid_case == "duplicate_code":
        manifest = _capacity_manifest(
            rows=[
                _capacity_manifest_row(),
                _capacity_manifest_row(market_rank=21),
            ]
        )

    _assert_report_error(
        assembler,
        html,
        "invalid_capacity_manifest",
        capacity_manifest=manifest,
    )


def test_capacity_manifest_rejects_cross_month_stale_as_of(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    stale_as_of = "2026-06-30"
    report = _capacity_health(html, _capacity_no_data(date=stale_as_of))
    manifest = _capacity_manifest(
        as_of=stale_as_of,
        rank_trade_dates=[
            "2026-06-24",
            "2026-06-25",
            "2026-06-26",
            "2026-06-29",
            stale_as_of,
        ],
    )

    error = _assert_report_error(
        assembler,
        report,
        capacity_manifest=manifest,
    )
    assert error.section == "s5"


def test_capacity_manifest_rejects_weekend_rank_dates(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    manifest = _capacity_manifest(
        rank_trade_dates=[
            "2026-07-12",  # Sunday
            "2026-07-13",
            "2026-07-14",
            "2026-07-15",
            DATE,
        ]
    )

    error = _assert_report_error(
        assembler,
        html,
        capacity_manifest=manifest,
    )
    assert error.section == "s5"


def test_build_capacity_manifest_uses_market_rank_tiers_and_five_day_counts(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider()

    manifest = capacity_manifest_builder.build_manifest(
        DATE,
        DATE,
        ["通信设备", "半导体", "电力"],
        provider=provider,
    )

    assert manifest["complete"] is True
    assert manifest["market_universe_count"] == 4_000
    assert [item["id"] for item in manifest["directions"]] == [
        "通信设备",
        "半导体",
        "电力",
    ]
    assert manifest["rank_trade_dates"] == list(CAPACITY_TRADE_DATES)
    rows_by_code = {row["ts_code"]: row for row in manifest["rows"]}
    assert set(rows_by_code) == {
        "000001.SZ",
        "000002.SZ",
        "000031.SZ",
        "000032.SZ",
        "000040.SZ",
        "000041.SZ",
    }

    # 全市场同成交额时按 ts_code 稳定破同分，不能由 provider 返回顺序决定。
    assert rows_by_code["000001.SZ"]["market_rank"] == 1
    assert rows_by_code["000002.SZ"]["market_rank"] == 2
    assert rows_by_code["000001.SZ"]["tier"] == "core"
    assert rows_by_code["000031.SZ"]["market_rank"] == 31
    assert rows_by_code["000031.SZ"]["direction_rank"] == 1
    assert rows_by_code["000031.SZ"]["tier"] == "candidate"
    assert rows_by_code["000031.SZ"]["top50_days"] == 3
    assert rows_by_code["000032.SZ"]["top50_days"] == 5
    assert provider.calls.count(("market", DATE)) == 2
    assert len([call for call in provider.calls if call[0] == "market"]) == 6
    assert ("industry", None) in provider.calls
    assert ("calendar", DATE) in provider.calls


def test_build_capacity_manifest_rejects_more_than_three_directions(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider()

    with pytest.raises(ValueError, match="1-3"):
        capacity_manifest_builder.build_manifest(
            DATE,
            DATE,
            ["通信设备", "半导体", "电力", "银行"],
            provider=provider,
        )

    assert provider.calls == []


@pytest.mark.parametrize(
    ("provider_kwargs", "message"),
    [
        ({"market_count": 3_999}, "全市场行情仅 3999 行"),
        ({"industry_count": 3_999}, "申万二级成员映射不完整"),
        (
            {"incomplete_history_date": CAPACITY_TRADE_DATES[0]},
            rf"{CAPACITY_TRADE_DATES[0]} 全市场行情不完整",
        ),
    ],
)
def test_build_capacity_manifest_fails_closed_on_incomplete_sources(
    capacity_manifest_builder, monkeypatch, provider_kwargs, message
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider(**provider_kwargs)

    with pytest.raises(capacity_manifest_builder.ManifestBuildError, match=message):
        capacity_manifest_builder.build_manifest(
            DATE,
            DATE,
            ["通信设备"],
            provider=provider,
        )


def test_build_capacity_manifest_rejects_market_truncation_against_reliable_baseline(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider(
        market_count=4_000,
        industry_count=5_522,
    )

    with pytest.raises(
        capacity_manifest_builder.ManifestBuildError,
        match="全市场|覆盖|基线|不完整",
    ):
        capacity_manifest_builder.build_manifest(
            DATE,
            DATE,
            ["通信设备"],
            provider=provider,
        )


def test_build_capacity_manifest_rejects_insufficient_industry_mapping_coverage(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider(
        market_count=5_522,
        industry_count=4_000,
    )

    with pytest.raises(
        capacity_manifest_builder.ManifestBuildError,
        match="申万|映射|覆盖|不完整",
    ):
        capacity_manifest_builder.build_manifest(
            DATE,
            DATE,
            ["通信设备"],
            provider=provider,
        )


def test_build_capacity_manifest_requires_five_open_trade_dates(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _fake_capacity_provider()
    provider.trade_dates = CAPACITY_TRADE_DATES[1:]

    with pytest.raises(
        capacity_manifest_builder.ManifestBuildError,
        match="最近 5 个开放日",
    ):
        capacity_manifest_builder.build_manifest(
            DATE,
            DATE,
            ["通信设备"],
            provider=provider,
        )


def test_build_capacity_manifest_loads_previous_year_for_last_five_trade_dates(
    capacity_manifest_builder, monkeypatch
):
    monkeypatch.setattr(
        capacity_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    cross_year_dates = (
        "2026-12-29",
        "2026-12-30",
        "2026-12-31",
        "2027-01-04",
        "2027-01-05",
    )
    provider = _YearScopedCapacityProvider(
        quotes_by_date={
            trade_date: _fake_market_rows()
            for trade_date in cross_year_dates
        },
        industry_map=_fake_industry_map(),
        trade_dates=cross_year_dates,
    )

    manifest = capacity_manifest_builder.build_manifest(
        "2027-01-05",
        "2027-01-05",
        ["通信设备"],
        provider=provider,
    )

    assert manifest["rank_trade_dates"] == list(cross_year_dates)
    assert any(
        call[0] == "calendar" and str(call[1]).startswith("2026-")
        for call in provider.calls
    )


def test_calculate_new_high_structure_uses_adjusted_strict_breakouts_and_listing_age(
    new_high_manifest_builder,
):
    trade_dates, normalized_by_date, _, _ = _synthetic_new_high_inputs(
        new_high_manifest_builder
    )
    manifest = _calculate_synthetic_new_high_manifest(new_high_manifest_builder)

    assert normalized_by_date[DATE]["000005.SZ"]["adj_high"] == 12.0
    assert manifest["basis"] == "rolling-adjusted-high"
    assert manifest["prev_as_of"] == trade_dates[-2]
    assert "000005.SZ" in manifest["current_codes"]["250"]
    assert "000004.SZ" not in manifest["current_codes"]["60"]
    assert all(
        "000006.SZ" not in manifest["current_codes"][str(window)]
        for window in (60, 120, 250)
    )


def test_calculate_new_high_structure_preserves_window_nesting(
    new_high_manifest_builder,
):
    manifest = _calculate_synthetic_new_high_manifest(new_high_manifest_builder)
    current = manifest["current_codes"]

    assert current["60"] == [
        "000001.SZ",
        "000002.SZ",
        "000003.SZ",
        "000005.SZ",
        "000007.SZ",
    ]
    assert current["120"] == ["000002.SZ", "000003.SZ", "000005.SZ"]
    assert current["250"] == ["000003.SZ", "000005.SZ"]
    assert manifest["counts"]["60"]["current"] == 5
    assert manifest["counts"]["120"]["current"] == 3
    assert manifest["counts"]["250"]["current"] == 2
    assert (
        manifest["counts"]["60"]["current"]
        >= manifest["counts"]["120"]["current"]
        >= manifest["counts"]["250"]["current"]
    )


def test_calculate_new_high_structure_builds_sector_cr3_and_representatives(
    new_high_manifest_builder,
):
    manifest = _calculate_synthetic_new_high_manifest(new_high_manifest_builder)

    assert manifest["sectors"][0] == {
        "industry": "通信设备",
        "count": 2,
        "share_pct": 40.0,
    }
    assert manifest["sector_cr3_pct"] == 80.0
    assert len(manifest["sectors"]) == 4
    assert [item["ts_code"] for item in manifest["representatives"]] == [
        "000005.SZ",
        "000003.SZ",
        "000001.SZ",
        "000002.SZ",
        "000007.SZ",
    ]
    assert manifest["representatives"][0] == {
        "ts_code": "000005.SZ",
        "name": "复权突破",
        "industry": "医疗服务",
        "amount_yi": 9.0,
        "pct_chg": 2.0,
        "windows": [60, 120, 250],
    }


def test_normalize_new_high_day_fails_closed_on_missing_adjustment_factors(
    new_high_manifest_builder,
):
    quotes = [
        {"ts_code": "000001.SZ", "high": 10.0},
        {"ts_code": "000002.SZ", "high": 10.0},
    ]
    factors = [{"ts_code": "000001.SZ", "adj_factor": 1.0}]

    with pytest.raises(
        new_high_manifest_builder.ManifestBuildError,
        match="复权因子覆盖率",
    ):
        new_high_manifest_builder._normalize_day(quotes, factors)


@pytest.mark.parametrize(
    ("provider_kwargs", "message", "last_call"),
    [
        ({"calendar_count": 250}, "251 个开放日", "calendar"),
        ({"industry_count": 3_999}, "申万二级成员映射不完整", "industry"),
        ({"basic_count": 3_999}, "上市日期基线不完整", "basic"),
    ],
)
def test_build_new_high_manifest_fails_closed_before_market_fetch(
    new_high_manifest_builder,
    monkeypatch,
    provider_kwargs,
    message,
    last_call,
):
    monkeypatch.setattr(
        new_high_manifest_builder,
        "_provider",
        lambda: pytest.fail("不得调用真实行情 provider"),
    )
    provider = _FakeNewHighBoundaryProvider(**provider_kwargs)

    with pytest.raises(
        new_high_manifest_builder.ManifestBuildError,
        match=message,
    ):
        new_high_manifest_builder.build_manifest(
            DATE,
            DATE,
            provider=provider,
            workers=1,
        )

    assert provider.calls[-1] == last_call


def test_new_high_failure_manifest_is_explicit_and_non_complete(
    new_high_manifest_builder,
):
    manifest = new_high_manifest_builder._failure_manifest(
        DATE,
        DATE,
        "行情缺失",
    )

    assert manifest["status"] == "failed"
    assert manifest["complete"] is False
    assert manifest["basis"] == "rolling-adjusted-high"
    assert manifest["market_count"] == 0
    assert manifest["counts"] == {}
    assert manifest["sectors"] == []
    assert manifest["representatives"] == []
    assert manifest["errors"] == ["行情缺失"]


def test_load_new_high_manifest_accepts_complete_sidecar(assembler, tmp_path):
    payload = _new_high_manifest(with_data=True)
    path = _write_new_high_manifest(tmp_path, payload=payload)

    assert assembler.load_new_high_structure_manifest(path, DATE) == payload


@pytest.mark.parametrize(
    "invalid_case",
    [
        "schema",
        "report_date",
        "as_of",
        "prev_as_of",
        "status",
        "basis",
        "count",
        "market",
        "daily_coverage",
        "sector",
        "representative",
    ],
)
def test_load_new_high_manifest_rejects_invalid_payload(
    assembler, tmp_path, invalid_case
):
    payload = _new_high_manifest(with_data=True)
    if invalid_case == "schema":
        payload["schema"] = "rolling-new-high-structure-v0"
    elif invalid_case == "report_date":
        payload["report_date"] = "2026-07-15"
    elif invalid_case == "as_of":
        payload["as_of"] = "2026-07-17"
    elif invalid_case == "prev_as_of":
        payload["prev_as_of"] = payload["as_of"]
    elif invalid_case == "status":
        payload["status"] = "partial"
    elif invalid_case == "basis":
        payload["basis"] = "rolling-high"
    elif invalid_case == "count":
        payload["counts"]["120"]["current"] = 6
    elif invalid_case == "market":
        payload["market_count"] = 3_999
    elif invalid_case == "daily_coverage":
        first_day = payload["trade_dates"][0]
        payload["daily_market_counts"][first_day] = 4_000
        payload["daily_market_coverage_min"] = 0.8
    elif invalid_case == "sector":
        payload["sectors"][0]["count"] = 3
    elif invalid_case == "representative":
        payload["representatives"][0]["ts_code"] = "999999.SZ"
    path = _write_new_high_manifest(
        tmp_path,
        payload=payload,
        name=f"invalid-{invalid_case}.json",
    )

    with pytest.raises(assembler.ReportValidationError) as caught:
        assembler.load_new_high_structure_manifest(path, DATE)

    assert caught.value.code == "invalid_new_high_manifest"
    assert caught.value.section == "s5"


def test_new_high_manifest_matches_complete_html_table(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_manifest_table()

    assembler.validate_report(
        _with_complete_new_high_manifest_contract(html, table),
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(with_data=True),
    )


def test_new_high_manifest_rejects_verdict_that_contradicts_sidecar(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _with_complete_new_high_manifest_contract(
        html, _new_high_manifest_table()
    )
    report = report.replace(
        "60/120/250 日滚动新高为 5/3/2",
        "60/120/250 日滚动新高为 999/998/997",
        1,
    )

    _assert_report_error(
        assembler,
        report,
        "new_high_manifest_mismatch",
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(with_data=True),
    )


@pytest.mark.parametrize(
    "hidden_prefix",
    (
        "hidden ",
        'aria-hidden="true" ',
        'style="display:none" ',
        'class="back-to-top" ',
    ),
)
def test_new_high_manifest_rejects_explicitly_hidden_table(
    assembler, tmp_path, hidden_prefix
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    hidden_table = _new_high_manifest_table().replace(
        '<table data-new-high-structure="v1"',
        f'<table {hidden_prefix}data-new-high-structure="v1"',
        1,
    )

    _assert_report_error(
        assembler,
        _with_complete_new_high_manifest_contract(html, hidden_table),
        "invalid_new_high_structure",
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(with_data=True),
    )


def test_new_high_manifest_ignores_hidden_correct_values(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_manifest_table()
    table = table.replace("5 / 3 / 2", "999 / 998 / 997", 1)
    table = table.replace("4 / 2 / 1", "996 / 995 / 994", 1)
    hidden_truth = (
        '<span hidden>5/3/2 4/2/1 CR3 80.0% '
        '重合 2 延续率 50.00% 换手率 60.00% '
        '通信设备 医疗服务 半导体 代表一 代表二 代表三 代表四 代表五</span>'
    )
    table = table.replace("</tbody>", f"{hidden_truth}</tbody>", 1)

    _assert_report_error(
        assembler,
        _with_complete_new_high_manifest_contract(html, table),
        "new_high_manifest_mismatch",
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(with_data=True),
    )


@pytest.mark.parametrize(
    "table_kwargs",
    [
        {"date": "2026-07-15", "prev_date": "2026-07-14"},
        {"market_count": 5_001},
        {"current_60": 4},
        {"current_120": 2},
        {"current_250": 1},
        {"prev_60": 3},
        {"prev_120": 1},
        {"prev_250": 0},
    ],
)
def test_new_high_manifest_rejects_html_metadata_mismatch(
    assembler, tmp_path, table_kwargs
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_manifest_table(**table_kwargs)

    _assert_report_error(
        assembler,
        _new_high_structure(html, table),
        "new_high_manifest_mismatch",
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=_new_high_manifest(with_data=True),
    )


def test_incomplete_new_high_manifest_requires_missing_data_html(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    failed_manifest = _new_high_manifest(complete=False)

    _assert_report_error(
        assembler,
        html,
        "new_high_manifest_mismatch",
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=failed_manifest,
    )

    missing = _new_high_structure(
        html,
        _new_high_structure_state(
            state="missing-data",
            source_status="failed",
        ),
    )
    missing = _replace_once(
        missing,
        _new_high_structure_verdict(),
        _new_high_structure_verdict(
            text="[判断] 滚动新高结构数据不完整，本日无法判定。"
        ),
    )
    assembler.validate_report(
        _new_high_ops_notice(missing),
        capacity_manifest=_capacity_manifest(),
        new_high_manifest=failed_manifest,
    )


def test_new_high_structure_accepts_complete_rolling_high_table(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_structure_table(_new_high_structure_row())

    assembler.validate_report(_new_high_structure(html, table))


def test_new_high_structure_accepts_exact_complete_none_state(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    assembler.validate_report(html)


@pytest.mark.parametrize("source_status", ["partial", "failed"])
def test_new_high_structure_missing_data_requires_visible_ops_notice(
    assembler, tmp_path, source_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    missing = _new_high_structure(
        html,
        _new_high_structure_state(
            state="missing-data",
            source_status=source_status,
        ),
    )

    _assert_report_error(assembler, missing, "invalid_new_high_structure")
    _assert_report_error(
        assembler,
        _new_high_ops_notice(missing, hidden=True),
        "invalid_new_high_structure",
    )
    _assert_report_error(
        assembler,
        _new_high_ops_notice(missing, css_hidden=True),
        "invalid_new_high_structure",
    )
    assembler.validate_report(_new_high_ops_notice(missing))


@pytest.mark.parametrize(
    "invalid_verdict",
    [
        "",
        _new_high_structure_verdict() * 2,
        _new_high_structure_verdict().replace("<p ", "<p hidden ", 1),
        _new_high_structure_verdict().replace(
            "<p ", '<p class="toc" ', 1
        ),
    ],
)
def test_new_high_structure_verdict_must_be_unique_and_default_visible(
    assembler, tmp_path, invalid_verdict
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        _new_high_structure_verdict(),
        invalid_verdict,
    )

    _assert_report_error(
        assembler,
        invalid,
        "invalid_new_high_structure",
    )


def test_new_high_structure_contract_must_stay_in_s5(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    verdict = _new_high_structure_verdict()
    state = _new_high_structure_state()
    without_contract = _replace_once(_replace_once(html, verdict, ""), state, "")
    moved = _replace_once(
        without_contract,
        "<h2>② 板块</h2>",
        f"<h2>② 板块</h2>{verdict}{state}",
    )

    _assert_report_error(
        assembler,
        moved,
        "invalid_new_high_structure",
    )


@pytest.mark.parametrize(
    "invalid_structure",
    [
        "",
        _new_high_structure_state() * 2,
        (
            _new_high_structure_state()
            + _new_high_structure_table(_new_high_structure_row())
        ),
    ],
)
def test_new_high_structure_requires_exactly_one_table_none_or_missing(
    assembler, tmp_path, invalid_structure
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _new_high_structure(html, invalid_structure),
        "invalid_new_high_structure",
    )


@pytest.mark.parametrize(
    "table_kwargs",
    [
        {"source_status": "partial"},
        {"date": "not-a-date"},
        {"date": "2026-07-17"},
        {"prev_date": "not-a-date"},
        {"prev_date": DATE},
        {"market_count": 3_999},
        {"market_count": "not-an-int"},
        {"current_60": -1},
        {"current_120": -1},
        {"current_250": -1},
        {"prev_60": -1},
        {"prev_120": -1},
        {"prev_250": -1},
        {"current_60": "not-an-int"},
        {"basis": "forward-adjusted-high"},
    ],
)
def test_new_high_structure_table_rejects_fake_complete_or_invalid_metadata(
    assembler, tmp_path, table_kwargs
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_structure_table(
        _new_high_structure_row(),
        **table_kwargs,
    )

    _assert_report_error(
        assembler,
        _new_high_structure(html, table),
        "invalid_new_high_structure",
    )


@pytest.mark.parametrize(
    "attribute",
    [
        f'data-as-of="{DATE}"',
        'data-prev-as-of="2026-07-15"',
        'data-source-status="complete"',
        'data-market-count="5000"',
        'data-current-60-count="44"',
        'data-current-120-count="25"',
        'data-current-250-count="19"',
        'data-prev-60-count="71"',
        'data-prev-120-count="38"',
        'data-prev-250-count="27"',
        'data-basis="rolling-adjusted-high"',
    ],
)
def test_new_high_structure_table_requires_all_rolling_high_metadata(
    assembler, tmp_path, attribute
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _new_high_structure_table(_new_high_structure_row())
    invalid_table = _replace_once(table, f" {attribute}", "")

    _assert_report_error(
        assembler,
        _new_high_structure(html, invalid_table),
        "invalid_new_high_structure",
    )


@pytest.mark.parametrize(
    "table",
    [
        _new_high_structure_table(),
        _new_high_structure_table(
            _new_high_structure_row(),
            include_header=False,
        ),
        _new_high_structure_table("<tr></tr>"),
    ],
)
def test_new_high_structure_table_requires_header_and_nonempty_data_row(
    assembler, tmp_path, table
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _new_high_structure(html, table),
        "invalid_new_high_structure",
    )


@pytest.mark.parametrize(
    "state",
    [
        _new_high_structure_state(source_status="partial"),
        _new_high_structure_state(source_status="failed"),
        _new_high_structure_state(date="2026-07-17"),
        _new_high_structure_state(text="[事实] 本日无新增"),
        _new_high_structure_state(
            state="missing-data",
            source_status="complete",
        ),
    ],
)
def test_new_high_structure_none_and_missing_states_cannot_fake_complete(
    assembler, tmp_path, state
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _new_high_structure(html, state)
    if state.find('="missing-data"') >= 0:
        report = _new_high_ops_notice(report)

    _assert_report_error(
        assembler,
        report,
        "invalid_new_high_structure",
    )


def test_sector_concentration_verdict_must_be_unique_visible_and_labeled(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    verdict = _sector_concentration_verdict()
    variants = (
        "",
        verdict * 2,
        verdict.replace("<p ", "<p hidden ", 1),
        verdict.replace("<p ", '<p class="toc" ', 1),
        _sector_concentration_verdict(text="板块成交集中于少数方向。"),
    )

    for invalid_verdict in variants:
        _assert_report_error(
            assembler,
            _replace_once(html, verdict, invalid_verdict),
            "invalid_sector_concentration",
        )


@pytest.mark.parametrize(
    ("contract", "error_code"),
    [
        ("sector-concentration", "invalid_sector_concentration"),
        ("rising-recognition", "invalid_recognition_matrix"),
        ("falling-recognition", "invalid_recognition_matrix"),
    ],
)
def test_sector_evidence_contract_cannot_be_silently_omitted(
    assembler, tmp_path, contract, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    error = _assert_report_error(
        assembler,
        _replace_once(html, _sector_state(contract), ""),
        error_code,
    )
    assert error.section == "s2"


@pytest.mark.parametrize(
    ("contract", "table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table(_concentration_row()),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table(_recognition_row()),
            "invalid_recognition_matrix",
        ),
        (
            "falling-recognition",
            _falling_recognition_table(
                _recognition_row(
                    direction="传媒",
                    code="000002.SZ",
                    name="领跌标的",
                )
            ),
            "invalid_recognition_matrix",
        ),
    ],
)
def test_sector_evidence_accepts_one_nonempty_structured_table(
    assembler, tmp_path, contract, table, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _sector_contract(html, contract, table)

    assembler.validate_report(report)

    duplicate = _sector_contract(html, contract, table * 2)
    _assert_report_error(assembler, duplicate, error_code)

    both = _sector_contract(html, contract, f"{_sector_state(contract)}{table}")
    _assert_report_error(assembler, both, error_code)


@pytest.mark.parametrize(
    ("contract", "empty_table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table(),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table(),
            "invalid_recognition_matrix",
        ),
        (
            "falling-recognition",
            _falling_recognition_table(),
            "invalid_recognition_matrix",
        ),
    ],
)
def test_sector_evidence_rejects_empty_structured_table(
    assembler, tmp_path, contract, empty_table, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _sector_contract(html, contract, empty_table),
        error_code,
    )


@pytest.mark.parametrize(
    ("contract", "table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table("<tr></tr>", "<tr></tr>"),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table("<tr></tr>", "<tr></tr>"),
            "invalid_recognition_matrix",
        ),
        (
            "falling-recognition",
            _falling_recognition_table("<tr></tr>", "<tr></tr>"),
            "invalid_recognition_matrix",
        ),
    ],
)
def test_sector_evidence_rejects_two_empty_data_rows(
    assembler, tmp_path, contract, table, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _sector_contract(html, contract, table),
        error_code,
    )


@pytest.mark.parametrize(
    ("contract", "table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table(
                "<tr><td>电子</td><td>12.30%</td></tr>"
            ),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table(
                "<tr><td>电子</td><td>000001.SZ 辨识度标的</td></tr>"
            ),
            "invalid_recognition_matrix",
        ),
        (
            "falling-recognition",
            _falling_recognition_table(
                "<tr><td>传媒</td><td>000002.SZ 领跌标的</td></tr>"
            ),
            "invalid_recognition_matrix",
        ),
    ],
)
def test_sector_evidence_rows_require_structured_metadata(
    assembler, tmp_path, contract, table, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _sector_contract(html, contract, table),
        error_code,
    )


@pytest.mark.parametrize(
    ("contract", "table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table(_concentration_row()),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table(_recognition_row()),
            "invalid_recognition_matrix",
        ),
    ],
)
@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f' data-as-of="{DATE}"', ""),
        (' data-source-status="complete"', ""),
        (f'data-as-of="{DATE}"', 'data-as-of="2026-07-17"'),
        ('data-source-status="complete"', 'data-source-status="partial"'),
    ],
)
def test_sector_evidence_table_requires_complete_current_provenance(
    assembler,
    tmp_path,
    contract,
    table,
    error_code,
    old,
    new,
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = table.replace(old, new, 1)

    _assert_report_error(
        assembler,
        _sector_contract(html, contract, invalid),
        error_code,
    )


@pytest.mark.parametrize(
    ("contract", "error_code"),
    [
        ("sector-concentration", "invalid_sector_concentration"),
        ("rising-recognition", "invalid_recognition_matrix"),
        ("falling-recognition", "invalid_recognition_matrix"),
    ],
)
def test_sector_evidence_supports_explicit_none_and_missing_data_states(
    assembler, tmp_path, contract, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    assembler.validate_report(html)

    for source_status in ("partial", "failed"):
        state = _sector_state(
            contract,
            state="missing-data",
            source_status=source_status,
        )
        missing = _sector_contract(html, contract, state)
        _assert_report_error(assembler, missing, error_code)
        _assert_report_error(
            assembler,
            _sector_ops_notice(missing, contract, hidden=True),
            error_code,
        )
        _assert_report_error(
            assembler,
            _sector_ops_notice(missing, contract, css_hidden=True),
            error_code,
        )
        assembler.validate_report(_sector_ops_notice(missing, contract))

    invalid_states = (
        _sector_state(contract, state="none", source_status="partial"),
        _sector_state(contract, state="missing-data", source_status="complete"),
        _sector_state(contract, date="2026-07-17"),
        _sector_state(contract, text="[事实] 本日无新增"),
    )
    for state in invalid_states:
        report = _sector_contract(html, contract, state)
        if state.find('="missing-data"') >= 0:
            report = _sector_ops_notice(report, contract)
        _assert_report_error(
            assembler,
            report,
            error_code,
        )


@pytest.mark.parametrize(
    ("contract", "table", "error_code"),
    [
        (
            "sector-concentration",
            _sector_concentration_table(_concentration_row()),
            "invalid_sector_concentration",
        ),
        (
            "rising-recognition",
            _rising_recognition_table(_recognition_row()),
            "invalid_recognition_matrix",
        ),
    ],
)
def test_sector_evidence_contract_cannot_be_moved_outside_s2(
    assembler, tmp_path, contract, table, error_code
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(assembler, _appendix(html, table), error_code)


def test_main_fall_matrix_cannot_satisfy_main_rise_contract(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    without_falling = _replace_once(
        html,
        _sector_state("falling-recognition"),
        "",
    )
    falling = _falling_recognition_table(
        _recognition_row(direction="传媒", code="000002.SZ", name="领跌标的")
    )

    _assert_report_error(
        assembler,
        _sector_contract(without_falling, "rising-recognition", falling),
        "invalid_recognition_matrix",
    )

    rising = _rising_recognition_table(_recognition_row())
    mixed = rising.replace(
        'data-rising-recognition="v1"',
        'data-rising-recognition="v1" data-falling-recognition="v1"',
        1,
    )
    _assert_report_error(
        assembler,
        _sector_contract(html, "rising-recognition", mixed),
        "invalid_structured_contract",
    )


def test_main_fall_matrix_may_coexist_as_a_separate_artifact(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    falling = _falling_recognition_table(
        _recognition_row(direction="传媒", code="000002.SZ", name="领跌标的")
    )
    report = _sector_contract(html, "falling-recognition", falling)

    assembler.validate_report(report)


def test_event_window_contract_and_visible_verdict_cannot_be_omitted(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    for marker in (_event_window_state(), _event_window_verdict()):
        error = _assert_report_error(
            assembler,
            _replace_once(html, marker, ""),
            "invalid_event_window",
        )
        assert error.section == "s6"


def test_event_window_verdict_must_be_unique_visible_and_labeled(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    verdict = _event_window_verdict()
    variants = (
        verdict.replace("<p ", "<p hidden ", 1),
        verdict.replace("<p ", '<p class="toc" ', 1),
        verdict * 2,
        _event_window_verdict(text="当前节点暂无事件窗口。"),
    )

    for invalid_verdict in variants:
        _assert_report_error(
            assembler,
            _replace_once(html, verdict, invalid_verdict),
            "invalid_event_window",
        )


def test_event_window_accepts_unique_table_for_next_seven_calendar_days(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _event_window_table(*_event_rows())

    assembler.validate_report(_event_window(html, table))


@pytest.mark.parametrize(
    "table",
    [
        _event_window_table(),
        _event_window_table(_event_row(DATE)),
        _event_window_table(_event_row("2026-07-24")),
        _event_window_table(_event_row("not-a-date")),
        _event_window_table("<tr><td>缺日期</td><td>事件</td></tr>"),
        _event_window_table(
            *_event_rows()[:-1],
            _event_row("2026-07-22", event="重复日期"),
        ),
    ],
)
def test_event_window_rejects_empty_or_out_of_range_rows(
    assembler, tmp_path, table
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")

    _assert_report_error(
        assembler,
        _event_window(html, table),
        "invalid_event_window",
    )


@pytest.mark.parametrize("market_status", ["", "unknown"])
def test_event_window_rows_require_open_or_closed_market_status(
    assembler, tmp_path, market_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    rows = list(_event_rows())
    rows[0] = _event_row(
        "2026-07-17",
        event="次日事件",
        market_status=market_status,
    )

    _assert_report_error(
        assembler,
        _event_window(html, _event_window_table(*rows)),
        "invalid_event_window",
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f' data-as-of="{DATE}"', ""),
        (' data-source-status="complete"', ""),
        (' data-window-start="2026-07-17"', ""),
        (' data-window-end="2026-07-23"', ""),
        (f'data-as-of="{DATE}"', 'data-as-of="2026-07-17"'),
        (
            'data-window-start="2026-07-17"',
            'data-window-start="2026-07-16"',
        ),
        ('data-window-end="2026-07-23"', 'data-window-end="2026-07-24"'),
        ('data-source-status="complete"', 'data-source-status="partial"'),
    ],
)
def test_event_window_table_requires_exact_window_and_complete_provenance(
    assembler, tmp_path, old, new
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _event_window_table(*_event_rows()).replace(old, new, 1)

    _assert_report_error(
        assembler,
        _event_window(html, table),
        "invalid_event_window",
    )


def test_event_window_table_or_state_must_be_unique(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _event_window_table(*_event_rows())

    _assert_report_error(
        assembler,
        _event_window(html, table * 2),
        "invalid_event_window",
    )
    _assert_report_error(
        assembler,
        _event_window(html, f"{_event_window_state()}{table}"),
        "invalid_event_window",
    )


@pytest.mark.parametrize("source_status", ["partial", "failed"])
def test_event_window_supports_structured_missing_data(
    assembler, tmp_path, source_status
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    state = _event_window_state(
        state="missing-data",
        source_status=source_status,
    )
    missing = _event_window(html, state)

    _assert_report_error(assembler, missing, "invalid_event_window")
    _assert_report_error(
        assembler,
        _event_ops_notice(missing, hidden=True),
        "invalid_event_window",
    )
    _assert_report_error(
        assembler,
        _event_ops_notice(missing, css_hidden=True),
        "invalid_event_window",
    )
    assembler.validate_report(_event_ops_notice(missing))


@pytest.mark.parametrize(
    "state",
    [
        _event_window_state(source_status="partial"),
        _event_window_state(state="missing-data", source_status="complete"),
        _event_window_state(date="2026-07-17"),
        _event_window_state(text="[事实] 本日无新增"),
    ],
)
def test_event_window_none_and_missing_data_states_cannot_be_confused(
    assembler, tmp_path, state
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    report = _event_window(html, state)
    if state.find('="missing-data"') >= 0:
        report = _event_ops_notice(report)

    _assert_report_error(
        assembler,
        report,
        "invalid_event_window",
    )


def test_event_window_contract_cannot_be_moved_outside_s6(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    table = _event_window_table(*_event_rows())

    _assert_report_error(
        assembler,
        _appendix(html, table),
        "invalid_event_window",
    )


@pytest.mark.parametrize(
    "old,new",
    [
        (
            'id="claim-market" data-claim-kind="judgment"',
            'id="claim-market"',
        ),
        (
            'id="claim-market" data-claim-kind="judgment"',
            'id="claim-market" data-claim-kind="opinion"',
        ),
        ('data-source="market_snapshot"', 'data-source=""'),
        (
            f'data-source="market_snapshot" data-as-of="{DATE}"',
            'data-source="market_snapshot" data-as-of="2026/07/16"',
        ),
        (
            f'data-source="market_snapshot" data-as-of="{DATE}"',
            'data-source="market_snapshot" data-as-of="2026-07-17"',
        ),
        ("[判断] 市场仍在等待确认。", "市场仍在等待确认。"),
        ("[事实] 主线资金与价格出现分歧。", "主线资金与价格出现分歧。"),
    ],
)
def test_claim_requires_kind_source_date_and_matching_fact_judgment_label(
    assembler, tmp_path, old, new
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    _assert_report_error(assembler, _replace_once(html, old, new))


def test_duplicate_claim_owner_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    duplicate = (
        f'<p id="claim-market" data-claim-kind="judgment" '
        f'data-source="duplicate" data-as-of="{DATE}">[判断] 重复裁决。</p>'
    )
    _assert_report_error(assembler, _visible(html, duplicate))


def test_dangling_claim_reference_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    dangling = '<a data-claim-ref="claim-missing" href="#claim-missing">悬空引用</a>'
    _assert_report_error(assembler, _visible(html, dangling))


def test_claim_allows_at_most_one_short_reference(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    duplicate_ref = (
        '<a data-claim-ref="claim-market" href="#claim-market">再次引用</a>'
    )
    _assert_report_error(
        assembler,
        _visible(html, duplicate_ref),
        "duplicate_claim_ref",
    )


def test_claim_link_requires_matching_reference_metadata(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _visible(html, '<a href="#claim-market">无引用元数据</a>')
    _assert_report_error(assembler, invalid, "invalid_claim_ref")


def test_tldr_character_budget_accepts_boundary_and_rejects_one_over(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)
    assert base.tldr_chars < 500

    at_limit = _tldr(html, "甲" * (500 - base.tldr_chars))
    assert assembler.validate_report(at_limit).tldr_chars == 500
    _assert_report_error(assembler, _tldr(at_limit, "乙"))


def test_visible_character_budget_accepts_boundary_and_rejects_one_over(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)
    assert base.visible_chars < 10_000

    at_limit = _visible(html, "甲" * (10_000 - base.visible_chars))
    assert assembler.validate_report(at_limit).visible_chars == 10_000
    _assert_report_error(assembler, _visible(at_limit, "乙"))


def test_unscoped_report_text_is_included_in_visible_budget(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)

    at_limit = _unscoped(html, "甲" * (10_000 - base.visible_chars))
    metrics = assembler.validate_report(at_limit)
    assert metrics.visible_chars == 10_000
    assert metrics.sections["document"].visible_chars > 0
    _assert_report_error(
        assembler,
        _unscoped(at_limit, "乙"),
        "visible_chars_exceeded",
    )


def test_unscoped_report_tables_and_rows_are_counted(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)
    metrics = assembler.validate_report(_unscoped(html, _table(2, "外")))

    assert metrics.visible_tables == base.visible_tables + 1
    assert metrics.visible_rows == base.visible_rows + 2
    assert metrics.sections["document"].visible_tables == 1
    assert metrics.sections["document"].visible_rows == 2


def test_evidence_outside_a_fixed_anchor_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    evidence = (
        f'<details class="evidence" data-as-of="{DATE}" data-items="1">'
        "<summary>游离证据（1 项）</summary><p>原始事实</p></details>"
    )
    _assert_report_error(
        assembler,
        _unscoped(html, evidence),
        "evidence_without_home",
    )


def test_hidden_evidence_does_not_consume_visible_character_budget(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)
    changed = _appendix(html, "证" * 5_000)
    metrics = assembler.validate_report(changed)

    assert metrics.visible_chars == base.visible_chars
    assert metrics.appendix_chars == base.appendix_chars + 5_000


def test_appendix_character_budget_accepts_boundary_and_rejects_one_over(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    base = assembler.collect_metrics(html)
    assert base.appendix_chars < 40_000

    at_limit = _appendix(html, "证" * (40_000 - base.appendix_chars))
    assert assembler.validate_report(at_limit).appendix_chars == 40_000
    _assert_report_error(assembler, _appendix(at_limit, "据"))


def test_visible_table_budget_accepts_boundary_and_rejects_one_over(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    at_limit = _visible(html, "".join(_table(1, str(i)) for i in range(12)))
    metrics = assembler.validate_report(at_limit)
    assert metrics.visible_tables == 12
    assert metrics.visible_rows == 12

    _assert_report_error(assembler, _visible(at_limit, _table(1, "extra")))


def test_visible_row_budget_accepts_boundary_and_rejects_one_over(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    at_limit = _visible(html, _table(80))
    metrics = assembler.validate_report(at_limit)
    assert metrics.visible_tables == 1
    assert metrics.visible_rows == 80

    _assert_report_error(assembler, _visible(html, _table(81)))


def test_appendix_table_budget_accepts_boundary_and_rejects_one_over(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    at_limit = _appendix(html, "".join(_table(1, str(i)) for i in range(60)))
    metrics = assembler.validate_report(at_limit)
    assert metrics.visible_tables == 0
    assert metrics.visible_rows == 0
    assert metrics.appendix_tables == 60
    assert metrics.appendix_rows == 60

    _assert_report_error(assembler, _appendix(at_limit, _table(1, "extra")))


def test_appendix_row_budget_accepts_boundary_and_rejects_one_over(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    at_limit = _appendix(html, _table(400))
    metrics = assembler.validate_report(at_limit)
    assert metrics.visible_tables == 0
    assert metrics.visible_rows == 0
    assert metrics.appendix_tables == 1
    assert metrics.appendix_rows == 400

    _assert_report_error(assembler, _appendix(html, _table(401)))


@pytest.mark.parametrize(
    "fragment",
    [
        '<script src="http://example.com/app.js"></script>',
        '<script src="app.js"></script>',
        '<script src="//cdn.example.com/app.js"></script>',
        '<script src="data:text/javascript,fetch(\'/api\')"></script>',
        '<script>window["fe"+"tch"]("/api/review")</script>',
        '<img src="https://example.com/chart.png" alt="图">',
        '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" onerror="this.src=\'https://example.com/x\'">',
        '<img srcset="data:image/gif;base64,R0lGODlhAQABAAAAACw= 1x">',
        '<link rel="stylesheet" href="style.css">',
        '<link rel="stylesheet" href="data:text/css,@import url(https://example.com/a.css)">',
        '<iframe src="report.html"></iframe>',
        '<iframe srcdoc="&lt;script>fetch(\'/api\')&lt;/script>"></iframe>',
        '<p style="background:url(https://example.com/a.png)">样式</p>',
        '<style>@import "https://example.com/a.css";</style>',
        '<style>.x{color:red}</style>',
        '<button onclick="fetch(\'/api/review\')">触发</button>',
        '<a href="javascript:fetch(\'/api/review\')">触发</a>',
        '<meta http-equiv="refresh" content="0;url=https://example.com">',
        '<script>fetch("/api/review")</script>',
        '<script>new XMLHttpRequest()</script>',
        '<script>new WebSocket("wss://example.com")</script>',
    ],
)
def test_external_or_runtime_dependencies_are_rejected(assembler, tmp_path, fragment):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    _assert_report_error(
        assembler,
        _visible(html, fragment),
        "external_dependency",
    )


def test_source_links_and_literal_runtime_examples_are_not_dependencies(
    assembler, tmp_path
):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    allowed = (
        '<a href="https://example.com/source">来源链接</a>'
        '<pre>fetch("/api/review")</pre>'
        '<p style="color:red">安全内联样式</p>'
        '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">'
    )
    assembler.validate_report(_visible(html, allowed))


def test_outer_shell_network_script_and_css_are_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    script = _replace_once(
        html,
        "</body>",
        '<script>fetch("/api/review")</script></body>',
    )
    _assert_report_error(assembler, script, "external_dependency")

    style = _replace_once(
        html,
        "</head>",
        '<style>@import "https://example.com/a.css";</style></head>',
    )
    _assert_report_error(assembler, style, "external_dependency")


def test_duplicate_html_attribute_is_rejected(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = _replace_once(
        html,
        'id="report-document"',
        'id="report-document" id="duplicate"',
    )
    _assert_report_error(assembler, invalid, "duplicate_attribute")


def test_guardrail_requires_exact_index_expression(assembler, tmp_path):
    html, _ = _render_valid(assembler, tmp_path / "chunks")
    invalid = html.replace("000001.SH + 399106.SZ", "000001.SH - 399106.SZ")
    _assert_report_error(assembler, invalid, "missing_guardrail")


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ASSEMBLER), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_default_output_path_remains_compatible(
    assembler, tmp_path, monkeypatch, capsys
):
    date = DATE
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir, date)
    fake_root = tmp_path / "repo"
    expected = fake_root / "data" / "reports" / f"复盘_{date}.html"
    monkeypatch.setattr(assembler, "_repo_root", lambda: fake_root)

    calls = 0
    original_validate = assembler.validate_report

    def counted_validate(
        html,
        *,
        capacity_manifest=None,
        new_high_manifest=None,
    ):
        nonlocal calls
        calls += 1
        return original_validate(
            html,
            capacity_manifest=capacity_manifest,
            new_high_manifest=new_high_manifest,
        )

    monkeypatch.setattr(assembler, "validate_report", counted_validate)
    assert assembler.main([str(chunk_dir), date]) == 0

    assert calls == 1
    assert expected.exists()
    assert 'data-report-schema="compact-v1"' in expected.read_text(encoding="utf-8")
    assert str(expected) in capsys.readouterr().out


def test_cli_accepts_explicit_noncanonical_output(tmp_path):
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir)
    output = tmp_path / "reports" / "复盘_2026-07-16_compact.html"

    result = _run_cli([str(chunk_dir), DATE, "--output", str(output)])

    assert result.returncode == 0, result.stderr or result.stdout
    assert output.exists()
    assert 'data-report-schema="compact-v1"' in output.read_text(encoding="utf-8")
    assert str(output) in result.stdout


def test_cli_requires_default_capacity_manifest(tmp_path):
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir)
    (chunk_dir / f"capacity_{DATE}.json").unlink()
    output = tmp_path / "missing-manifest.html"

    result = _run_cli([str(chunk_dir), DATE, "--output", str(output)])

    assert result.returncode == 1
    assert "[missing_capacity_manifest]" in result.stderr
    assert not output.exists()


def test_cli_requires_default_new_high_manifest(tmp_path):
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir)
    (chunk_dir / f"new_high_{DATE}.json").unlink()
    output = tmp_path / "missing-new-high-manifest.html"

    result = _run_cli([str(chunk_dir), DATE, "--output", str(output)])

    assert result.returncode == 1
    assert "[missing_new_high_manifest]" in result.stderr
    assert not output.exists()


def test_cli_rejects_invalid_capacity_manifest_file(tmp_path):
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir)
    manifest = chunk_dir / f"capacity_{DATE}.json"
    manifest.write_text("{invalid", encoding="utf-8")
    output = tmp_path / "invalid-manifest.html"

    result = _run_cli([str(chunk_dir), DATE, "--output", str(output)])

    assert result.returncode == 1
    assert "[invalid_capacity_manifest]" in result.stderr
    assert not output.exists()


def test_cli_accepts_explicit_capacity_manifest_path(tmp_path):
    chunk_dir = tmp_path / "chunks"
    _write_chunks(chunk_dir)
    (chunk_dir / f"capacity_{DATE}.json").unlink()
    manifest = _write_capacity_manifest(
        tmp_path,
        name="explicit-capacity.json",
    )
    output = tmp_path / "explicit-manifest.html"

    result = _run_cli(
        [
            str(chunk_dir),
            DATE,
            "--capacity-manifest",
            str(manifest),
            "--output",
            str(output),
        ]
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert output.exists()


def test_cli_returns_nonzero_without_all_eight_chunks(tmp_path):
    chunk_dir = tmp_path / "chunks"
    paths = _write_chunks(chunk_dir)
    paths[CHUNK_ORDER[-1]].unlink()

    result = _run_cli(
        [str(chunk_dir), DATE, "--output", str(tmp_path / "should-not-exist.html")]
    )

    assert result.returncode == 1
    assert not (tmp_path / "should-not-exist.html").exists()
