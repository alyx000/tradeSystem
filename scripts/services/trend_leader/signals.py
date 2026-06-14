"""把池里已存的「嵌套明细」重建成「布尔命中」，供 web 渲染信号 chip（只读路径，不落库）。

scanner Pass2 维护时 `pool.touch` 落进 last_signal_json 的是各检测器的 **detail dict**
（如 `near_ma5={"deviation": .., "insufficient_history": ..}`），而钉钉观察清单展示的「命中布尔」
（`shrink_pullback_buy`/`near_ma5`/`overheat`）是扫描当场算的、**不落库**。web 读池只能拿到 detail，
故在此用同一套 constants 阈值把 detail 重建回布尔。

红线：纯客观状态标记，不暗示买卖。重建表达式必须与 detectors 的 matched 语义一致——
对账单测（test_trend_leader_signals.py）逐场景锁死，防漂移。

可扩展约定：每个信号一个 case；advisory 信号（entry_filter/串阳/双底/etf）落地后，
在此加对应 case + 前端 label 表加一行即可，页面骨架不动。
"""
from __future__ import annotations

import math

from services.trend_leader import constants as C


def _detail(value) -> dict:
    """嵌套明细归一为 dict：非 dict（脏值/形态漂移，如 {"near_ma5": true}）→ 空 dict。
    防 `value or {}` 对 truthy 非 dict（True/列表/字符串）放行后 .get 击穿 → 整个只读 API 500
    （门2 codex 前端 round-1：一条坏行不能拖垮整页）。非 dict 一律按「未维护/未命中」处理。"""
    return value if isinstance(value, dict) else {}


def _number(value):
    """数值字段（deviation）归一：仅接受有限 int/float（排除 bool），其余 → None。
    防 last_signal_json 数值漂移成字符串/对象后 abs()/比较抛 TypeError → 整端 500
    （门2 codex 前端 round-2）。此为 signal_hits 内最后一处算术面，归一后该函数对任意 JSON 全总。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _ok(detail: dict) -> bool:
    """detail 有效（存在且历史充足）才参与判定，否则视为未命中。"""
    return bool(detail) and not detail.get("insufficient_history")


def signal_hits(last_signal: dict | None) -> dict | None:
    """从 last_signal 嵌套明细重建在池信号布尔。

    返回 None 表示该行还停在 Pass1（入池未经 Pass2 维护，无在池信号）→ 前端显示「待维护」。
    """
    # Pass2 维护必写 shrink_pullback；缺该键即 Pass1（只有 first_limit/gentle）或空。
    # isinstance 守卫：last_signal 恒为 dict|None（scanner 只写 dict），但 DB 脏值反序列化成
    # str/list/int 时 `in` 会误判或对非可迭代体抛 TypeError——显式挡掉，统一回退「待维护」。
    #
    # 反驳门2 codex round-2「legacy flat 行被降级为 null」：scanner.run_daily 的 touch() 自始
    # 只写 **嵌套** detail 形态（shrink_pullback/near_ma5/overheat 各为 detail dict，见 scanner
    # Pass2），扁平布尔只进 summary["in_pool_signals"] 返给 renderer、从不落库。codex 看到的
    # {"overheat": True, "deviation": 0.09} 等扁平体是 test_trend_leader_pool.py 的「不透明 JSON
    # 持久化」夹具（验池状态机对任意 signal_json 的 round-trip），非生产 last_signal 形态——故无
    # legacy flat 生产行，不补兜底分支（不为不存在的场景兜底）。
    if not isinstance(last_signal, dict) or "shrink_pullback" not in last_signal:
        return None

    sp = _detail(last_signal.get("shrink_pullback"))
    nm = _detail(last_signal.get("near_ma5"))
    ov = _detail(last_signal.get("overheat"))

    near_dev = _number(nm.get("deviation"))
    over_dev = _number(ov.get("deviation"))

    # 布尔字段严格归一 `is True`：脏值（字符串 "false"/非空对象/列表）truthy 会误报命中、把错误
    # [判断] 信号展示给用户（门2 codex 前端 round-3）。只认真正的 bool true。
    return {
        "shrink_pullback_buy": _ok(sp) and sp.get("is_yin") is True and sp.get("shrink") is True,
        "near_ma5": _ok(nm) and near_dev is not None
                    and abs(near_dev) <= C.NEAR_MA5_MAX_DEVIATION,
        "overheat": _ok(ov) and over_dev is not None
                    and over_dev >= C.FAR_FROM_MA5_MIN_DEVIATION,
    }
