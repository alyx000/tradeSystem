"""断板反包 PK 层（方式二：LLM 两两循环赛）。

与打分层（方式一，`scorer.score_all`）完全独立：PK 只喂 [事实] 卡（不含加权分），
让 LLM 只依据事实判断两票反包强度谁更优；加权分只用来做池截断与并列破平，不进 prompt。

- `parse_verdict`：严格校验 LLM 输出（`winner` 必须是 "A"/"B"、`reason` 必须是 str），
  容忍模型在 JSON 前后包一层自然语言（提取首个 `{...}` 结构再 `json.loads`）。
- `run_pk`：候选按加权分 `total` 截断到 `PK_POOL_MAX` 强池，`itertools.combinations`
  跑循环赛（字典序小者固定为 A，spec 已知局限：不做双向去位置偏置）；单场失败区分
  「超时」（`llm_runner.last_diagnostics.reason == "timeout"`，不重试直接记无效场）与
  「其它失败」（重试 1 次后仍失败才记无效场）；预算超时 / 无效场占比超阈值 → 熔断
  （`status="melted"`，`ranks=None`）；正常收尾按 胜场→加权分→裸码字典序 破平出名次。
"""
from __future__ import annotations

import itertools
import logging
import json
import time

from services.board_break import constants as C
from services.recommend.formatter import REDLINE_KEYWORDS

logger = logging.getLogger(__name__)

# 事实卡中喂给 LLM 的 [事实] 字段（不含加权分/evidence，两法独立，见 task-s2-report.md 字段清单）
_FACT_FIELDS = (
    "code", "name", "limit_times", "pct_chg", "close", "industry",
    "in_main_sector", "main_sector_status", "main_sector_degraded",
    "ann_status", "ann_events", "ann_titles", "holder_status", "holder_source",
    "earnings_status", "earnings_type", "earnings_direction",
    "gain10", "gain10_status", "dif", "dif_status",
    "position_value", "position_state", "position_bar_count",
)

_PROMPT = (
    "你是断板反包评审员。下面给出两只个股（A/B）的[事实]卡（不含任何加权分数），"
    "请仅依据事实判断反包强度更优者。"
    "只输出 JSON：{\"winner\": \"A\"|\"B\", \"reason\": \"<=60字\"}，不要输出其它文字，"
    "不给出具体买卖建议，不给出价位预测。"
)


def parse_verdict(text: str) -> dict | None:
    """严格解析 LLM 裁决文本 → `{"winner": "A"|"B", "reason": str}`；不合法返回 None。

    容忍 JSON 外层包裹自然语言：找首个 `{`，用 `json.JSONDecoder.raw_decode` 只解析
    第一段合法 JSON（忽略其后残留文本），比贪婪正则更抗嵌套花括号干扰。
    """
    if not isinstance(text, str):
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    winner = obj.get("winner")
    reason = obj.get("reason")
    if winner not in ("A", "B") or not isinstance(reason, str):
        return None
    return {"winner": winner, "reason": reason}


def _fact_payload(card: dict) -> dict:
    """只取 [事实] 字段（ann_titles 已由 scorer.build_fact_card 截断，此处不重复截断）。"""
    return {k: card.get(k) for k in _FACT_FIELDS}


def _build_payload(card_a: dict, card_b: dict) -> dict:
    return {"A": _fact_payload(card_a), "B": _fact_payload(card_b)}


def _filter_reason(reason: str) -> str:
    """红线扫描（AI 生成内容） + 截断 PK_REASON_MAX_CHARS。"""
    if any(kw in reason for kw in REDLINE_KEYWORDS):
        reason = "(理由已按红线过滤)"
    return reason[: C.PK_REASON_MAX_CHARS]


def _safe_call(llm_runner, payload):
    """runner 守卫（门2 S3 R1）：异常一律收敛为 None（等同失败），不得打崩整场循环赛。"""
    try:
        return llm_runner(_PROMPT, payload)
    except Exception:
        logger.warning("[board-break pk] runner 调用异常，按失败场处理", exc_info=True)
        return None


def _play_match(card_a: dict, card_b: dict, llm_runner) -> tuple[str | None, str | None]:
    """单场：失败区分超时（不重试）与其它失败（重试 1 次）。返回 (winner_code, reason)；无效场为 (None, None)。"""
    payload = _build_payload(card_a, card_b)
    verdict = parse_verdict(_safe_call(llm_runner, payload))
    if verdict is None:
        diag = getattr(llm_runner, "last_diagnostics", None)
        if diag and diag.get("reason") == "timeout":
            return None, None  # 超时直接计无效场，不重试
        verdict = parse_verdict(_safe_call(llm_runner, payload))  # 非超时失败：重试 1 次
        if verdict is None:
            return None, None
    winner_code = card_a.get("code") if verdict["winner"] == "A" else card_b.get("code")
    return winner_code, _filter_reason(verdict["reason"])


def _pool_and_excluded(fact_cards: list[dict], scored: list[dict]) -> tuple[list[str], list[str]]:
    """按加权分 total 截断到 PK_POOL_MAX 强池；不足则全入池、excluded 为空。"""
    # 去重去空（门2 S3 R2）：重复码会造成自我对局/胜场膨胀,空码会污染配对
    seen = set()
    codes_all = []
    for c in fact_cards:
        code = c.get("code")
        if code and code not in seen:
            seen.add(code)
            codes_all.append(code)
    if len(codes_all) <= C.PK_POOL_MAX:
        return codes_all, []
    score_map = {s.get("code"): s.get("total", 0.0) for s in scored}
    ranked = sorted(codes_all, key=lambda code: (-score_map.get(code, 0.0), code))
    return ranked[: C.PK_POOL_MAX], sorted(ranked[C.PK_POOL_MAX :])


def run_pk(
    fact_cards: list[dict],
    scored: list[dict],
    llm_runner,
    *,
    budget_seconds: float = C.PK_BUDGET_SECONDS,
    clock=time.monotonic,
) -> dict:
    """LLM 两两循环赛：预算熔断 + 无效场率熔断 + 胜场/加权分/裸码破平出名次。"""
    card_map = {c.get("code"): c for c in fact_cards}
    score_map = {s.get("code"): s.get("total", 0.0) for s in scored}

    pool, excluded = _pool_and_excluded(fact_cards, scored)
    if len(pool) < 2:
        return {
            "status": "skipped", "wins": {}, "ranks": None, "matches": [],
            "invalid": 0, "total": 0, "attempted": 0, "valid_ratio": 0.0, "excluded": excluded,
        }

    pairs = list(itertools.combinations(sorted(pool), 2))
    total = len(pairs)
    wins = {code: 0 for code in pool}
    matches: list[dict] = []
    invalid = 0
    start = clock()
    melted_by_budget = False

    for a, b in pairs:
        if clock() - start > budget_seconds:
            melted_by_budget = True
            break
        card_a = card_map.get(a, {"code": a})
        card_b = card_map.get(b, {"code": b})
        winner, reason = _play_match(card_a, card_b, llm_runner)
        if winner is None:
            invalid += 1
            matches.append({"a": a, "b": b, "winner": None, "reason": None, "state": "invalid"})
        else:
            wins[winner] += 1
            matches.append({"a": a, "b": b, "winner": winner, "reason": reason, "state": "valid"})

    # attempted=实际已打场次：预算熔断中途退出时,未打场次不得被隐性算作"有效"
    # （审查 Important1:total=理论场次会让 valid_ratio 虚高误导渲染层）
    attempted = len(matches)
    valid_ratio = (attempted - invalid) / attempted if attempted else 0.0

    # 熔断三判据（spec 锁定）:预算超时 / 无效场占比超上限 / 有效场率低于下限。
    # 后两者在默认常量下算术互补(0.30+0.70=1.0),仍显式各判一次——防未来只调
    # PK_VALID_RATIO_MIN 而与 spec"两条件"静默脱钩（审查 Important2）。
    if melted_by_budget or (attempted and invalid / attempted > C.PK_INVALID_RATIO_MAX) \
            or valid_ratio < C.PK_VALID_RATIO_MIN:
        return {
            "status": "melted", "wins": wins, "ranks": None, "matches": matches,
            "invalid": invalid, "total": total, "attempted": attempted,
            "valid_ratio": valid_ratio, "excluded": excluded,
        }

    ordered = sorted(pool, key=lambda code: (-wins[code], -score_map.get(code, 0.0), code))
    ranks = {code: i for i, code in enumerate(ordered, start=1)}

    return {
        "status": "ok", "wins": wins, "ranks": ranks, "matches": matches,
        "invalid": invalid, "total": total, "attempted": attempted,
        "valid_ratio": valid_ratio, "excluded": excluded,
    }
