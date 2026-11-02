"""六指数15/30/60分钟结构 sidecar 的确定性计算测试。"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = (
    ROOT
    / ".agents"
    / "skills"
    / "daily-review"
    / "references"
    / "html-report-template"
    / "build_index_intraday_manifest.py"
)
DATE = "2026-08-24"


def _load_builder():
    spec = importlib.util.spec_from_file_location(
        "index_intraday_manifest_builder_under_test", BUILDER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load_builder()


def _bars(count: int, *, last_at: str = f"{DATE} 15:00") -> list[dict]:
    end = datetime.fromisoformat(last_at)
    rows: list[dict] = []
    for index in range(count):
        stamp = end - timedelta(minutes=15 * (count - 1 - index))
        close = 1000.0 + index * 0.5
        rows.append(
            {
                "datetime": stamp.strftime("%Y-%m-%d %H:%M"),
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        )
    return rows


class _FakeApi:
    def __init__(self, *, empty_key: tuple[int, int, str] | None = None):
        self.empty_key = empty_key
        self.calls: list[tuple[int, int, str, int]] = []

    def connect(self, host, port, time_out):
        return True

    def get_index_bars(self, category, market, code, start, count):
        self.calls.append((category, market, code, count))
        if (category, market, code) == self.empty_key:
            return []
        return _bars(count)

    def disconnect(self):
        return None


def test_calculate_row_builds_compact_technical_structure(builder):
    row = builder.calculate_row(
        _bars(400),
        report_date=DATE,
        index_id="shanghai",
        index_name="上证",
        market=1,
        code="000001",
        proxy_note="",
        period="15m",
        category=1,
        requested_count=400,
        source_node="fixture:7709",
    )

    assert row["last_bar_at"] == f"{DATE} 15:00"
    assert row["sample_count"] == 400
    assert row["close_vs_ma5"] == "above"
    assert row["macd_axis"] == "above"
    assert row["ma5"] == pytest.approx(1198.5)
    assert row["ma10"] == pytest.approx(1197.25)
    assert row["ma20"] == pytest.approx(1194.75)
    assert row["hist"] == pytest.approx(row["dif"] - row["dea"], abs=0.000002)
    assert row["bars_since_dead_cross"] is None
    assert row["swing_high_at"] == f"{DATE} 15:00"
    assert row["source_node"] == "fixture:7709"


def test_calculate_row_rejects_stale_last_bar(builder):
    with pytest.raises(builder.ManifestBuildError, match="末根"):
        builder.calculate_row(
            _bars(400, last_at="2026-08-24 14:45"),
            report_date=DATE,
            index_id="shanghai",
            index_name="上证",
            market=1,
            code="000001",
            proxy_note="",
            period="15m",
            category=1,
            requested_count=400,
            source_node="fixture:7709",
        )


def test_fetch_bars_pages_back_for_historical_report(builder):
    all_bars = _bars(2_400, last_at="2026-09-20 15:00")
    target = next(
        row for row in all_bars[500:800] if row["datetime"].endswith("15:00")
    )
    report_date = target["datetime"][:10]

    class PagedApi:
        def __init__(self):
            self.starts: list[int] = []

        def get_index_bars(self, category, market, code, start, count):
            self.starts.append(start)
            end = len(all_bars) - start
            begin = max(0, end - count)
            return all_bars[begin:end]

    api = PagedApi()
    bars = builder._fetch_bars_through(
        api,
        category=1,
        market=1,
        code="000001",
        report_date=report_date,
        requested_count=400,
    )
    row = builder.calculate_row(
        bars,
        report_date=report_date,
        index_id="shanghai",
        index_name="上证",
        market=1,
        code="000001",
        proxy_note="",
        period="15m",
        category=1,
        requested_count=400,
        source_node="fixture:7709",
    )

    assert api.starts == [0, 800, 1600]
    assert row["last_bar_at"] == f"{report_date} 15:00"


def test_build_manifest_requires_all_six_indices_and_three_periods(
    builder, monkeypatch
):
    api = _FakeApi()
    import pytdx.hq

    monkeypatch.setattr(pytdx.hq, "TdxHq_API", lambda heartbeat=True: api)
    manifest = builder.build_manifest(DATE, servers=[("fixture", 7709)])

    assert manifest["status"] == "complete"
    assert manifest["row_count"] == 18
    assert len(api.calls) == 18
    assert [(row["index_id"], row["period"]) for row in manifest["rows"]] == [
        (index_id, period)
        for index_id, *_ in builder.INDEXES
        for period, *_ in builder.PERIODS
    ]


def test_build_manifest_keeps_valid_rows_when_one_period_is_missing(
    builder, monkeypatch
):
    api = _FakeApi(empty_key=(1, 1, "000688"))
    import pytdx.hq

    monkeypatch.setattr(pytdx.hq, "TdxHq_API", lambda heartbeat=True: api)
    manifest = builder.build_manifest(DATE, servers=[("fixture", 7709)])

    assert manifest["status"] == "partial"
    assert manifest["complete"] is False
    assert manifest["row_count"] == 17
    assert any("star50/15m" in error for error in manifest["errors"])


@pytest.mark.parametrize('field', ['open', 'high', 'low', 'close', 'datetime'])
def test_missing_raw_fields_are_not_replaced_with_close(builder, field):
    rows = _bars(400)
    del rows[-1][field]
    with pytest.raises(builder.ManifestBuildError, match='缺少'):
        builder._normalize_bars(rows)


@pytest.mark.parametrize('field,value', [('open', True), ('low', -1), ('high', 1), ('close', float('nan')), ('datetime', 'broken')])
def test_invalid_raw_bar_is_rejected(builder, field, value):
    rows = _bars(400)
    rows[-1][field] = value
    with pytest.raises(builder.ManifestBuildError):
        builder._normalize_bars(rows)


def test_exact_duplicate_is_folded_but_conflicting_bar_is_rejected(builder):
    rows = _bars(400)
    assert builder._normalize_bars(rows + [dict(rows[-1])]) == builder._normalize_bars(rows)
    duplicate = dict(rows[-1], close=rows[-1]['close'] + 0.1)
    with pytest.raises(builder.ManifestBuildError, match='冲突'):
        builder._normalize_bars(rows + [duplicate])


@pytest.mark.parametrize('conflict', [False, True])
def test_cross_page_overlap_must_agree(builder, monkeypatch, conflict):
    rows = _bars(4)
    overlap = dict(rows[2])
    if conflict:
        overlap['close'] += 0.1
    batches = [rows[2:], [rows[1], overlap], rows[:2]]
    class Api:
        def get_index_bars(self, category, market, code, start, count):
            return batches[start // 2]
    monkeypatch.setattr(builder, 'FETCH_COUNT', 2)
    def fetch():
        return builder._fetch_bars_through(Api(), category=1, market=1, code='000001', report_date=DATE, requested_count=4)
    if conflict:
        with pytest.raises(builder.ManifestBuildError, match='冲突'):
            fetch()
    else:
        assert builder._normalize_bars(fetch()) == builder._normalize_bars(rows)


def test_invalid_bar_is_a_source_gap_not_complete(builder, monkeypatch):
    import pytdx.hq
    class Api(_FakeApi):
        def get_index_bars(self, category, market, code, start, count):
            rows = super().get_index_bars(category, market, code, start, count)
            if (category, market, code) == (1, 1, '000001'):
                del rows[-1]['high']
            return rows
    monkeypatch.setattr(pytdx.hq, 'TdxHq_API', lambda **kw: Api())
    result = builder.build_manifest(DATE, servers=[('fixture', 7709)])
    assert result['status'] == 'partial' and result['row_count'] == 17
    assert not result['complete'] and result['errors']
