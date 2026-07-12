from services.new_high import detector


def test_detects_strict_adjusted_new_high_and_updates_watermark():
    rows = [{
        "code": "600001.SH",
        "name": "样例A",
        "industry": "银行",
        "high": 11.0,
        "adj_factor": 1.1,
    }]
    watermarks = {"600001.SH": {"max_adj_high": 12.0, "max_high_date": "2026-07-07"}}

    result = detector.detect_new_highs(rows, watermarks, "2026-07-08")

    assert result["market_count"] == 1
    assert result["new_highs"][0]["code"] == "600001.SH"
    assert result["watermark_updates"][0]["max_adj_high"] == 12.1


def test_equal_high_is_not_new_high_but_last_seen_updates():
    rows = [{
        "code": "600001.SH",
        "name": "样例A",
        "industry": "银行",
        "high": 10.0,
        "adj_factor": 1.2,
    }]
    watermarks = {"600001.SH": {"max_adj_high": 12.0, "max_high_date": "2026-07-07"}}

    result = detector.detect_new_highs(rows, watermarks, "2026-07-08")

    assert result["new_highs"] == []
    assert result["watermark_updates"][0]["max_adj_high"] == 12.0
    assert result["watermark_updates"][0]["last_seen_date"] == "2026-07-08"


def test_first_seen_stock_initializes_without_counting_as_new_high():
    rows = [{
        "code": "301001.SZ",
        "name": "新股",
        "industry": "未分类",
        "high": 20.0,
        "adj_factor": 1.0,
    }]

    result = detector.detect_new_highs(rows, {}, "2026-07-08")

    assert result["new_highs"] == []
    assert result["initialized_count"] == 1
    assert result["watermark_updates"][0]["max_high_date"] == "2026-07-08"


def test_missing_adj_factor_is_excluded_from_market_count():
    rows = [{
        "code": "600001.SH",
        "name": "样例A",
        "industry": "银行",
        "high": 10.0,
        "adj_factor": None,
    }]

    result = detector.detect_new_highs(rows, {}, "2026-07-08")

    assert result["market_count"] == 0
    assert result["skipped_count"] == 1
