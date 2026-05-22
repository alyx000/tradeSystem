from __future__ import annotations

TRADE_MODES: tuple[str, ...] = (
    "break", "dip", "trend", "scalp",
    "swing", "arbitrage", "gap_jump", "sentiment_relay", "other",
)

MARKET_REGIONS: tuple[str, ...] = ("a-share", "hk", "us")


def validate_trade_mode(mode: str) -> None:
    if mode not in TRADE_MODES:
        raise ValueError(
            f"trade_mode 必须是 {TRADE_MODES} 之一,得到 {mode!r}"
        )


def validate_market_region(region: str) -> None:
    if region not in MARKET_REGIONS:
        raise ValueError(
            f"market_region 必须是 {MARKET_REGIONS} 之一,得到 {region!r}"
        )
