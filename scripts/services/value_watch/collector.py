"""value-watch 采集层：申万指数直连 + 个股区间日线 + 持仓装载。

数据源契约（spec v8）：
- `801780.SI` 走 **provider.pro.sw_daily 直连**（同 sector_crowding/collector 模式），
  无 akshare 降级；失败/空返回 fail-closed（None，调用方按标的隔离降级）；
  单次返回恰 2000 行视为镜像静默截断直接 raise（memory: index_member_all 先例）。
- 个股走 registry `get_stock_daily_range` capability（带降级链）。
- 取数起点必须为 config.HISTORY_ANCHOR_DATE（键确定性锚定契约，见 engine docstring）。
- 持仓身份键 {canonical_ts_code}:{entry_date}；entry_price/entry_date 任一缺失 →
  insufficient_identity（报告呈现，不推送不猜测）。
"""
from __future__ import annotations

import logging
import math
import sqlite3

from .config import LADDER_CODES

logger = logging.getLogger(__name__)

# 镜像单页静默截断判据（tushare 批量接口默认 2000 行上限，恰满页=疑似被截）
_MIRROR_PAGE_LIMIT = 2000


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _norm_date(raw) -> "str | None":
    s = str(raw or "").replace("-", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def fetch_sw_index_series(provider, code: str, start: str, end: str) -> "list[dict] | None":
    """申万行业指数区间日线 → 升序 [{"date","close"}]；失败/空 → None。"""
    try:
        df = provider.pro.sw_daily(ts_code=code,
                                   start_date=start.replace("-", ""),
                                   end_date=end.replace("-", ""))
    except Exception as e:
        logger.warning("[value-watch] sw_daily %s 失败: %s", code, e)
        return None
    if df is None or df.empty:
        return None
    if len(df) == _MIRROR_PAGE_LIMIT:
        raise RuntimeError(
            f"sw_daily {code} 返回恰 {_MIRROR_PAGE_LIMIT} 行,疑似镜像静默截断;"
            "残缺历史会让回撤基准与事件键漂移,fail-closed")
    out = []
    for row in df.to_dict("records"):
        d = _norm_date(row.get("trade_date"))
        close = row.get("close")
        if d is None or not _finite(close):
            continue   # NaN close 落库会毒化 dd 计算,单行丢弃
        out.append({"date": d, "close": float(close)})
    out.sort(key=lambda r: r["date"])
    return out or None


def fetch_stock_series(registry, code: str, start: str, end: str) -> "list[dict] | None":
    """个股区间日线（registry 降级链）→ 升序 [{"date","close","volume"}]；失败/空 → None。"""
    r = registry.call("get_stock_daily_range", code, start, end)
    if not (r.success and r.data):
        logger.warning("[value-watch] get_stock_daily_range %s 失败: %s",
                       code, getattr(r, "error", ""))
        return None
    out = []
    for row in r.data:
        d = _norm_date(row.get("trade_date"))
        close = row.get("close")
        if d is None or not _finite(close):
            continue
        vol = row.get("vol")
        out.append({"date": d, "close": float(close),
                    "volume": float(vol) if _finite(vol) else None})
    out.sort(key=lambda r: r["date"])
    return out or None


def _canonical_ts_code(raw: str) -> "str | None":
    """裸 6 位/带后缀 → canonical ts_code（按 LADDER_CODES 键去后缀匹配）。"""
    from db.dual_write import _normalize_stock_code_for_match

    norm = _normalize_stock_code_for_match(raw)
    for canonical in LADDER_CODES:
        if _normalize_stock_code_for_match(canonical) == norm:
            return canonical
    return None


def load_ladder_positions(conn: sqlite3.Connection) -> list[dict]:
    """active 持仓 ∩ LADDER_CODES。身份键 {canonical}:{entry_date}:{holding_id}——
    holding_id 在同一持仓周期内稳定（upsert 补字段复用同 active 行）、跨周期唯一
    （soft-close 后重开走 INSERT 新行）：同日平仓再开仓若无 id 成分会生成相同键,
    旧账本静默压制新持仓提醒（门2 G3 round2 high）。thesis_id/entry_price 不进键
    （补录 thesis、修正成本不换键不重推）；entry_price/entry_date 任一缺失 →
    insufficient_identity=True 且 position_key=None（只报告不推送）。"""
    rows = conn.execute(
        "SELECT id, stock_code, stock_name, entry_price, entry_date, thesis_id "
        "FROM holdings WHERE status = 'active'"
    ).fetchall()
    out = []
    for r in rows:
        canonical = _canonical_ts_code(r["stock_code"])
        if canonical is None:
            continue   # 非监控标的
        entry_price = r["entry_price"]
        entry_date = r["entry_date"]
        insufficient = not (_finite(entry_price) and entry_price > 0 and entry_date)
        out.append({
            "code": canonical,
            "name": LADDER_CODES[canonical],
            "holding_id": r["id"],
            "entry_price": entry_price,
            "entry_date": entry_date,
            "thesis_id": r["thesis_id"],
            "insufficient_identity": insufficient,
            "position_key": None if insufficient else f"{canonical}:{entry_date}:{r['id']}",
        })
    return out
