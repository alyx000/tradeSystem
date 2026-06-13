"""A 股涨跌停价计算工具。

权威基准：以「前一交易日收盘价」× 板块涨跌幅比例计算当日涨跌停价，
舍入采用交易所规则 ROUND_HALF_UP（非 Python 默认银行家舍入）到 0.01 元。
ETF/LOF 等无固定涨跌停的品种返回 None。

由 watchlist 到价提醒与盘前持仓信号共用，避免比例口径漂移。
"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

# ETF / LOF / 分级基金等无个股涨跌停限制的代码前缀。
# 沪：50x（含 508 REITs）、51x（含 518）、56x、58x（科创板 ETF）；深：15x（含 159）、16x（LOF）、18x。
# 用 startswith 多字符前缀，"51" 已覆盖 518xxx，无需单列。
_FUND_PREFIXES = ("15", "16", "18", "50", "51", "56", "58")

# 创业板（300/301）/ 科创板（688/689）= 20cm 板块。单一真源，_board_limit_pct 与 is_dual_board 共用。
_DUAL_BOARD_PREFIXES = ("300", "301", "688", "689")


def is_dual_board(code: str) -> bool:
    """创业板/科创板（20cm）个股；ETF/LOF 等基金前缀不算。板块前缀口径单一真源。"""
    code_num = code.split(".")[0]
    if code_num.startswith(_FUND_PREFIXES):
        return False
    return code_num.startswith(_DUAL_BOARD_PREFIXES)


def _board_limit_pct(code: str, is_st: bool) -> float:
    """按板块 × ST 状态返回涨跌幅比例（个股口径，不含基金）。

    板块 × ST 矩阵：
    - 北交所（.BJ 后缀或 8x/43x 代码）：30%，ST 不改变。
    - 创业板（300/301）/ 科创板（688/689）：20%，ST 仍 20%（ST 不收窄到 5%）。
    - 沪深主板：ST → 5%，否则 10%。
    """
    suffix = code.split(".")[-1].upper() if "." in code else ""
    code_num = code.split(".")[0]
    # 北交所统一 30%：优先按 .BJ 后缀（系统内代码均带后缀）；无后缀时按数字段兜底
    # —— 8x（83/87/88）、43x、920x 新代码段（920 以 9 开头，不被 "8" 覆盖，需显式列出）。
    if suffix == "BJ" or code_num.startswith(("8", "43", "920")):
        return 30.0
    if code_num.startswith(_DUAL_BOARD_PREFIXES):
        return 20.0   # 创业板 / 科创板，ST 仍 20%
    return 5.0 if is_st else 10.0   # 沪深主板：ST 5% / 普通 10%


def _get_limit_pct(code: str, name: str = "") -> float:
    """个股涨跌停比例（名称判 ST，向后兼容 watchlist 既有调用）。"""
    return _board_limit_pct(code, "ST" in name.upper())


def limit_pct_for(code: str, name: str = "", is_st: bool | None = None) -> float | None:
    """涨跌幅比例(%)；ETF/LOF 等无固定涨跌停 → None。

    :param is_st: 权威 ST 标志（来自 stock_st 名单）。非 None 时优先于名称判定；
        为 None 时回退「名称含 ST」启发式（watchlist 既有口径）。
    """
    code_num = code.split(".")[0]
    if code_num.startswith(_FUND_PREFIXES):
        return None
    st = is_st if is_st is not None else ("ST" in name.upper())
    return _board_limit_pct(code, st)


def _round_half_up(value: Decimal, ndigits: int = 2) -> float:
    quant = Decimal(10) ** -ndigits
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def compute_limit_prices(prev_close: float | None, code: str, name: str = "",
                         is_st: bool | None = None) -> dict:
    """基于前一交易日收盘价计算当日涨跌停价。

    :param prev_close: 前一交易日收盘价（当日涨跌停的计算基准）
    :param is_st: 权威 ST 标志（来自 stock_st 名单），透传给 `limit_pct_for`。
    :return: {"up_limit", "down_limit", "pre_close"}；
             ETF/LOF 或 prev_close 非法时 up/down 为 None。
    """
    pct = limit_pct_for(code, name, is_st=is_st)
    # 基准价非法（缺失 / 非有限 NaN·inf / 非正数脏数据）→ 不给限价，避免算出 0/负/NaN 假限价。
    price_ok = prev_close is not None and math.isfinite(prev_close) and prev_close > 0
    pre_close_out = prev_close if (prev_close is not None and math.isfinite(prev_close)) else None
    if not price_ok or pct is None:
        return {"up_limit": None, "down_limit": None, "pre_close": pre_close_out}
    # Decimal(str(x)) 是规范安全转换：str() 给 float 的最短往返 repr（如 146.28 → "146.28"），
    # 不会带入二进制浮点污染；直接 Decimal(float) 才会。
    base = Decimal(str(prev_close))
    ratio = Decimal(str(pct)) / Decimal(100)
    up = _round_half_up(base * (Decimal(1) + ratio))
    down = _round_half_up(base * (Decimal(1) - ratio))
    return {"up_limit": up, "down_limit": down, "pre_close": prev_close}
