"""放量启动后的回踩证据。纯观察，不参与原池准入/退出，不产生操作指令。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date
from statistics import mean

from analyzers.low_price_effect import _finite_float
from analyzers.sector_increment import SCHEMA as SECTOR_SCHEMA
from analyzers.sector_increment import validate as validate_sector
from services.trend_leader import detectors as D
from utils.qfq import apply_qfq, OHLC_PRICE_KEYS

PARAMETERS = {"launch_window": 20, "baseline_bars": 5, "launch_volume_ratio": 1.5}
LABELS = {"missing_data": "数据不足，未判断", "no_launch": "近20根未见放量启动",
          "launched": "当日放量启动", "tracking": "启动后观察中", "invalidated": "启动结构已破坏",
          "support_weakened": "板块成交额支持转弱", "pullback_observed": "放量后缩量回踩成立",
          "sector_unverified": "回踩形态成立／板块支持待核验"}


def missing(day, reason):
    return dict(trade_date=day, status="partial", state="missing_data", reason=str(reason),
                parameters=PARAMETERS, label=LABELS["missing_data"])


def prepare(bars, factors, day):
    """严格验证原始时间序列/复权因子，绝不把未来或重复行情交给复权工具。"""
    try:
        if not isinstance(bars, list) or not bars or not isinstance(factors, list):
            return []
        dates = [date.fromisoformat(b["trade_date"]).isoformat() for b in bars]
        fd = [f["trade_date"] for f in factors]
        if dates != sorted(set(dates)) or dates[-1] != day or len(fd) != len(set(fd)):
            return []
        adjusted = apply_qfq(bars, factors, keys=OHLC_PRICE_KEYS)
        return adjusted or []
    except (ValueError, TypeError, KeyError, AttributeError):
        return []


def load_sector(conn, day):
    """只认同日事实块，不以最近历史行情替代。"""
    try:
        row = conn.execute("SELECT raw_data FROM daily_market WHERE date=?", (day,)).fetchone()
        raw = json.loads(row[0]) if row and isinstance(row[0], str) else {}
        # 正式双写保存完整盘后信封；仅无 raw_data 键的旧记录使用扁平兼容。
        # 嵌套来源存在但损坏时不回退另一份旧块。
        source = raw["raw_data"] if "raw_data" in raw else raw
        block = source.get("sector_increment") if isinstance(source, dict) else None
        if isinstance(block, dict) and block.get("trade_date") == day and block.get("schema") == SECTOR_SCHEMA:
            return validate_sector(block, day)
    except (ValueError, TypeError, AttributeError, sqlite3.Error):
        pass
    return None


def sector_support(block, industry, day):
    """只在完整同日块上判支持；delta与份额同时为正，仅为工程观察代理。"""
    if (not isinstance(block, dict) or block.get("trade_date") != day
            or block.get("schema") != SECTOR_SCHEMA or block.get("status") != "complete"):
        return dict(status="missing_data", supported=None, industry=industry, trade_date=day)
    rows = [r for r in block.get("rows", []) if isinstance(r, dict) and r.get("industry") == industry]
    if len(rows) != 1 or industry == "未分类":
        return dict(status="missing_data", supported=None, industry=industry, trade_date=day)
    r = rows[0]
    delta, share = _finite_float(r.get("delta_yi")), _finite_float(r.get("share_delta_pp"))
    if delta is None or share is None:
        return dict(status="missing_data", supported=None, industry=industry, trade_date=day)
    return dict(status="complete", supported=delta > 0 and share > 0, industry=industry,
                trade_date=day, delta_yi=delta, share_delta_pp=share)


def analyze(bars, day, support):
    """最近20有效K线内：放量阳线突破前5高点 → 后续回踩；失效后不复活旧启动。"""
    try:
        if len(bars) < 30:
            return missing(day, "前复权OHLCV不足30根，无法完整验证近20根启动及MA10结构")
        dates = [date.fromisoformat(b["trade_date"]).isoformat() for b in bars]
        if dates != sorted(set(dates)) or dates[-1] != day:
            return missing(day, "行情重复、错序或目标日缺失")
        for b in bars:
            values = [_finite_float(b.get(k)) for k in (*OHLC_PRICE_KEYS, "vol")]
            if any(v is None or v <= 0 for v in values):
                return missing(day, "OHLCV缺失、非有限或非正")
            if not b["low"] <= min(b["open"], b["close"]) <= max(b["open"], b["close"]) <= b["high"]:
                return missing(day, "OHLC关系非法")
        result = dict(trade_date=day, status="complete", parameters=PARAMETERS,
                      sector_support=support, price_basis="前复权OHLC", volume_basis="成交量vol")
        launch = None
        # 启动前至少10根，使随后每根的MA10破坏检查都有完整历史。
        for i in range(max(10, len(bars)-PARAMETERS["launch_window"]), len(bars)):
            b, prior = bars[i], bars[i-5:i]
            ratio = b["vol"]/mean(x["vol"] for x in prior)
            if (b["close"] > b["open"] and b["close"] > max(x["high"] for x in prior)
                    and ratio >= PARAMETERS["launch_volume_ratio"]):
                launch = i
        state = "no_launch"
        if launch is not None:
            b = bars[launch]
            result.update(launch_date=b["trade_date"], bars_since_launch=len(bars)-1-launch,
                          launch_volume_ratio=b["vol"]/mean(x["vol"] for x in bars[launch-5:launch]))
            state = "launched" if launch == len(bars)-1 else "tracking"
            for i in range(launch+1, len(bars)):
                broken, _ = D.is_trend_broken(bars[:i+1])
                if broken or bars[i]["low"] < b["low"]:
                    state = "invalidated"
                    result["invalidated_date"] = bars[i]["trade_date"]
                    break
            shrink, sd = D.is_volume_shrink_pullback(bars)
            near, nd = D.is_near_ma5(bars)
            result.update(shrink_pullback=shrink, near_ma5=near, volume_evidence=sd, ma5_evidence=nd)
            if state == "tracking":
                if support.get("supported") is False:
                    state = "support_weakened"
                elif shrink and near:
                    state = "pullback_observed" if support.get("supported") is True else "sector_unverified"
        if support.get("supported") is None:
            result["status"] = "partial"
        return {**result, "state": state, "label": LABELS[state]}
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError) as exc:
        return missing(day, exc)
