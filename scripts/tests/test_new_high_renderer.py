from services.new_high import renderer


def test_renderer_limits_each_sector_to_top_n_but_marks_full_count():
    record = {
        "date": "2026-07-08",
        "market_count": 100,
        "new_high_count": 3,
        "sector_summary": [{
            "industry": "半导体",
            "count": 3,
            "stocks": [
                {"code": "1", "name": "A", "raw_high": 10, "pct_chg": 5},
                {"code": "2", "name": "B", "raw_high": 9, "pct_chg": 4},
                {"code": "3", "name": "C", "raw_high": 8, "pct_chg": 3},
            ],
        }],
        "source": {"adj_factor_missing": 0},
    }

    md = renderer.render_daily(record, top_n=2)

    assert "# 前复权历史新高统计 · 2026-07-08  [事实]" in md
    assert "半导体 · 3只" in md
    assert "A" in md and "B" in md
    assert "C" not in md
    assert "其余 1 只见 JSON/数据库" in md


def test_renderer_shows_empty_state_without_recommendations():
    record = {
        "date": "2026-07-08",
        "market_count": 100,
        "new_high_count": 0,
        "sector_summary": [],
        "stocks": [],
        "source": {},
    }

    md = renderer.render_daily(record, top_n=10)

    assert "当日无创前复权历史新高个股" in md
    assert "买入" not in md
    assert "目标价" not in md


def test_renderer_source_failed_does_not_claim_zero_new_highs():
    record = {
        "status": "source_failed",
        "date": "2026-07-08",
        "market_count": 0,
        "new_high_count": 0,
        "sector_summary": [],
        "stocks": [],
        "source": {
            "failed_source": "get_market_daily_quotes",
            "quote_source": "tushare:daily",
            "error": "",
        },
    }

    md = renderer.render_daily(record, top_n=10)

    assert "数据源未返回有效行情" in md
    assert "当日无创前复权历史新高个股" not in md
