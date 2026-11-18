"""从同一事实日的盘后归档展示跨资产报价。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

import yaml

from providers.sina_provider import POST_ASSET_MAX_AGE, POST_ASSET_SOURCE, POST_ASSET_SYMBOLS

_INSTRUMENTS = {
    "brent": ("布伦特原油（CFD）", "美元/桶"),
    "usd_index": ("美元指数（DXY）", "点"),
    "xau_usd": ("现货黄金（XAU/USD）", "美元/盎司"),
}


def _valid_quote(quote: dict, fact_date: str, fetched_at: str) -> bool:
    if quote.get("status") != "ok" or quote.get("as_of") != fact_date:
        return False
    price, change = quote.get("price"), quote.get("change_pct")
    try:
        observed = datetime.fromisoformat(quote["quote_time"])
        fetched = datetime.fromisoformat(fetched_at)
        return (
            type(price) in (int, float) and math.isfinite(price) and price > 0
            and type(change) in (int, float) and math.isfinite(change)
            and observed.utcoffset() == fetched.utcoffset() == timedelta(hours=8)
            and observed.date().isoformat() == fetched.date().isoformat() == fact_date
            and fetched.hour >= 20
            and timedelta(0) <= fetched - observed <= POST_ASSET_MAX_AGE
        )
    except (TypeError, ValueError, KeyError, OverflowError):
        return False


def render(daily_dir: str | Path, fact_date: str) -> tuple[str, list[str]]:
    path = Path(daily_dir) / fact_date / "post-market.yaml"
    try:
        envelope = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        return "", []
    if not isinstance(envelope, dict):
        return "", []
    raw_data = envelope.get("raw_data")
    if not isinstance(raw_data, dict) or "cross_asset_quotes" not in raw_data:
        return "", []  # 兼容接入前的盘后归档。
    block = raw_data["cross_asset_quotes"]
    if not isinstance(block, dict):
        block = {}
    date_matches = str(envelope.get("date")) == fact_date
    has_source = date_matches and block.get("_source") == POST_ASSET_SOURCE
    fetched_at = block.get("_fetched_at")
    rows, gaps = [], []
    for key, (name, unit) in _INSTRUMENTS.items():
        quote = block.get(key)
        if not isinstance(quote, dict):
            quote = {}
        if (
            has_source and quote.get("symbol") == POST_ASSET_SYMBOLS[key]
            and _valid_quote(quote, fact_date, fetched_at)
        ):
            quote_time = datetime.fromisoformat(quote["quote_time"])
            price = f'{quote["price"]:,.4f}'.rstrip("0").rstrip(".")
            cells = (f"{price} {unit}", f'{quote["change_pct"]:+.2f}%', f'{quote_time:%Y-%m-%d %H:%M:%S}')
        else:
            reason = "归档日期与事实日不符"
            if date_matches:
                reason = quote.get("error") or block.get("error") or "报价、品种或来源时间未核验"
            gaps.append(f"{name}（{reason}）")
            cells = ("缺失", "—", "—")
        rows.append(f'<tr><th scope="row">{escape(name)}</th>'
                    + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
    return (
        '<table data-post-asset-quotes="v1">'
        '<caption>大类资产 · 盘后参考报价</caption>'
        '<thead><tr><th scope="col">品种</th><th scope="col">报价</th>'
        '<th scope="col">较源前值</th><th scope="col">报价时间（北京时间）</th></tr></thead>'
        '<tbody>' + "".join(rows) + '</tbody></table>'
        '<p>来源为新浪财经。布伦特采用 CFD 报价，'
        'XAU/USD 采用新浪计算的现货参考价。</p>',
        gaps,
    )
