"""10点环境证据：只复用本次已取K线，不产生告警或交易信号。"""
from decimal import Decimal, localcontext

from .core import compare_window, money
from .rules import INDEX_NAMES

RULE_VERSION = "opening-environment-v1"
FLAT_TOLERANCE_PCT = 5  # 工程观察范围，课程未定义数值；不能称为验证过的交易阈值。


def evaluate_opening(code, end, previous, rows, *, rule):
    if end.strftime("%H:%M:%S.%f") != "10:00:00.000000":
        raise ValueError("环境观察只接受10:00完成窗口")
    # 复用身份、单位、时间网格、重复行、严格日期及完整30分钟金额契约。
    amount = compare_window(code, 30, end, previous, rows, rule=rule)
    day = end.date().isoformat()
    keys = [f"{previous} 09:45", f"{previous} 10:00", f"{previous} 15:00",
            f"{day} 09:45", f"{day} 10:00"]
    indexed = {r["trade_time"]: r for r in rows}
    evidence = []
    for key in keys:
        if key not in indexed:
            raise ValueError(f"环境观察缺少K线：{key}")
        row = indexed[key]
        prices = {k: money(row.get(k)) for k in ("open", "high", "low", "close")}
        if (min(prices.values()) <= 0
                or not prices["low"] <= min(prices["open"], prices["close"])
                <= max(prices["open"], prices["close"]) <= prices["high"]):
            raise ValueError(f"环境观察OHLC非法：{key}")
        volume = money(row.get("volume"))
        evidence.append({"trade_time": key, "ts_code": code, "period_minutes": 15,
                         **{k: format(v, "f") for k, v in prices.items()}, "volume": format(volume, "f"),
                         "amount": format(money(row.get("amount")), "f"), "amount_unit": "CNY"})
    with localcontext() as ctx:
        ctx.prec = 64
        old_volume = sum((Decimal(r["volume"]) for r in evidence[:2]), Decimal(0))
        new_volume = sum((Decimal(r["volume"]) for r in evidence[3:]), Decimal(0))
        if old_volume <= 0:
            raise ValueError("上一交易日前半小时成交量为0，不能计算同比")
        growth = (new_volume / old_volume - 1) * 100
        near_flat = old_volume * (100-FLAT_TOLERANCE_PCT) <= new_volume * 100 <= old_volume * (100+FLAT_TOLERANCE_PCT)
        volume_state = "near_flat" if near_flat else ("contracted" if new_volume < old_volume else "expanded")
        prev_close = Decimal(evidence[2]["close"])
        opening, at_ten = Decimal(evidence[3]["open"]), Decimal(evidence[4]["close"])
        high_open, below_open = opening > prev_close, at_ten < opening
        candidate = high_open and below_open and new_volume * 100 <= old_volume * (100+FLAT_TOLERANCE_PCT)
    return {"status": "complete", "code": code, "name": INDEX_NAMES[code],
            "previous_close": str(prev_close), "opening_price": str(opening), "price_at_1000": str(at_ten),
            "high_open": high_open, "below_open_at_1000": below_open,
            "current_volume": str(new_volume), "previous_volume": str(old_volume),
            "volume_unit": "eastmoney_native_same_index", "volume_growth_pct": str(growth),
            "volume_state": volume_state, "flat_tolerance_pct": FLAT_TOLERANCE_PCT,
            "current_amount": amount["current_amount"], "previous_amount": amount["previous_amount"],
            "amount_growth_pct": amount["growth_pct"], "amount_unit": "CNY",
            "weak_environment_candidate": candidate, "source_bars": evidence,
            "summary": ("[判断] 高开后10点低于开盘，成交量缩减或近似持平，列为偏弱环境观察。"
                        if candidate else "[判断] 本指数未同时满足三项环境观察条件；不据此判定强势。")}


def opening_observation(results, codes, end, previous, *, rule, input_by, captured_at):
    """每次尝试独立证据。成功金额槽位不额外取数，不修改原检查账本。"""
    items = []
    for code in codes:
        base = {"code": code, "name": INDEX_NAMES[code]}
        if code not in results:
            items.append({**base, "status": "not_recomputed", "reason": "既有成交额槽位已检查；本次未重新取数，请查首次尝试记录"})
            continue
        rows, error = results[code]
        try:
            if error:
                raise ValueError(error)
            items.append(evaluate_opening(code, end, previous, rows, rule=rule))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            items.append({**base, "status": "source_failed", "reason": str(exc)})
    complete = sum(item["status"] == "complete" for item in items)
    failed = sum(item["status"] == "source_failed" for item in items)
    status = ("complete" if complete == len(items) else "partial" if complete
              else "source_failed" if failed else "not_recomputed")
    return {"version": RULE_VERSION, "status": status, "teacher_note_id": 1178,
            "trade_date": end.date().isoformat(), "previous_trade_date": previous,
            "window": "09:30–10:00", "captured_at": captured_at, "input_by": input_by,
            "source": "eastmoney:index_15min", "complete_count": complete, "expected_count": len(items),
            "items": items,
            "method": "课程为大盘案例；四指数分别观察，不合并成全市场结论。±5%是工程定义的近似平量范围，未验证交易效果。",
            "limitations": "成交量保留东方财富原生单位，仅比较同一指数同源同窗，不跨指数相加。缺源不补0。只随原成交额取数尝试计算；金额检查成功后环境缺口不单独重试。无记录不代表未命中。"}
