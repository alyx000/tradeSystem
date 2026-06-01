"""Top3 排序：合并 A/US 候选。

"A/US 各≥1" 是**软约束**：两侧都有候选时尽量各保 1 条；任一侧空则全取另一侧，
**绝不为凑数冒充**（M1/M2）。两侧全空 → 返回空（service 渲染显式空报告）。
"""
from __future__ import annotations

# 美股方向优先级（小=优先）。鞠磊框架：首次覆盖(init)是 #1 信号，排最前；其次升级，再降级/重启。
_US_ACTION_PRIORITY = {"init": 0, "up": 1, "down": 2, "reinit": 3}


def _us_priority(item: dict):
    act = str(item.get("action", "")).lower()
    return (_US_ACTION_PRIORITY.get(act, 3), str(item.get("ticker", "")))


def pick_top3(cn_items: list[dict], us_items: list[dict], *, k: int = 3) -> list[dict]:
    cn = sorted(cn_items or [], key=lambda x: (-float(x.get("score", 0)), x.get("stock_code", "")))
    us = sorted(us_items or [], key=_us_priority)

    if cn and us:
        out = [cn[0], us[0]]
        i, j = 1, 1
        # A 先交替补足，直到 k 或两侧耗尽
        while len(out) < k and (i < len(cn) or j < len(us)):
            if i < len(cn):
                out.append(cn[i]); i += 1
            if len(out) >= k:
                break
            if j < len(us):
                out.append(us[j]); j += 1
        return out[:k]

    # 单侧或全空：全取非空侧（不冒充）
    pool = cn if cn else us
    return pool[:k]
