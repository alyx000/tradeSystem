"""港股现货指数与 ETF 代理的身份，用于新行情和旧归档的只读展示。"""
from __future__ import annotations

HK_QUOTE_NAMES = {
    "HSI": "恒生指数",
    "HSTECH": "恒生科技指数",
    "3033.HK": "恒生科技 ETF 代理",
}


def hk_identity(code: str) -> dict:
    proxy = code == "3033.HK"
    return {"code": code, "name": HK_QUOTE_NAMES[code],
            "instrument_type": "etf" if proxy else "index", "unit": "HKD" if proxy else "points"}


def hk_indices_view(block: dict) -> dict:
    """旧信封能凭来源识别 ETF，但不能凭信封日期推定实际行情日。"""
    out = dict(block) if isinstance(block, dict) else {}
    spot = out.get("hstech")
    if isinstance(spot, dict) and (spot.get("_source") == "yfinance:3033.HK" or spot.get("code") == "3033.HK"):
        if spot.get("code") in (None, "HSTECH", "3033.HK"):
            # 旧版以 HSTECH 作为 ETF 的代码；该历史形状有确切的数据源证据。
            out.setdefault("hstech_etf_proxy", {**spot, "code": "3033.HK"})
            del out["hstech"]
    if "hstech_etf_proxy" in out and "hstech" not in out:
        out["hstech"] = {"status": "missing", "close": None}
    for key, code, ticker in (("hsi", "HSI", "^HSI"), ("hstech", "HSTECH", "HSTECH.HK"),
                              ("hstech_etf_proxy", "3033.HK", "3033.HK")):
        row = out.get(key)
        if not isinstance(row, dict):
            continue
        source = str(row.get("_source") or "")
        if (row.get("code") not in (None, code) or
                (source.startswith("yfinance:") and source != f"yfinance:{ticker}")):
            row = {"status": "missing", "reason": "行情证券身份不符", "close": None}
        out[key] = {**row, **hk_identity(code), "trade_date": row.get("trade_date")}
    return out
