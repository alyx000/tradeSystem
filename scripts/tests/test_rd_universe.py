"""research_digest.universe：config/env 维护，去重保序，空回退默认池。"""
from __future__ import annotations

from services.research_digest.universe import us_universe


def test_raw_list_normalized():
    assert us_universe(["nvda", " amd "]) == ["NVDA", "AMD"]


def test_raw_str_comma_and_cjk_comma():
    assert us_universe("NVDA, amd，TSM") == ["NVDA", "AMD", "TSM"]


def test_dedup_preserve_order():
    assert us_universe("NVDA,NVDA,AMD") == ["NVDA", "AMD"]


def test_empty_falls_to_default_pool():
    out = us_universe("")
    assert "NVDA" in out and len(out) > 5


def test_env_source(monkeypatch):
    monkeypatch.setenv("RESEARCH_DIGEST_US_TICKERS", "AAPL,MSFT")
    assert us_universe() == ["AAPL", "MSFT"]
