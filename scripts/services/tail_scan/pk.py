"""tail-scan PK 层：LLM 两两循环赛（复用 board_break 引擎范式）。

parse_verdict / build_llm_runner 直接复用 board_break（generic）。run_pk 引擎与
board_break 同构（预算熔断 + 无效场率熔断 + 胜场/粗分/裸码破平），仅事实字段/prompt
不同。跨服务 PK 引擎共享抽取列为 tech-debt（与 board_break.pk 同批 defer）。
"""
from __future__ import annotations

import itertools
import logging
import time

from services.board_break.pk import build_llm_runner, parse_verdict  # 复用 generic 积木
from services.recommend.formatter import REDLINE_KEYWORDS
from services.tail_scan import constants as C

logger = logging.getLogger(__name__)

# 喂 LLM 的 [事实] 字段（不含粗权重分/价位）
_FACT_FIELDS = (
    "code", "name", "pct_chg", "amount_yi", "is_limit_up", "close_pos", "amplitude",
    "in_main_sector", "in_hot_concept", "concept_names", "teacher_hit",
    "rank_in_pool", "index_context",
    "gain5", "ma_above", "up_days", "first_surge", "vol_ratio",
    "dist_to_high", "broke_high", "calendar",
)

_PROMPT = (
    "你是尾盘强势股评审员。下面给出两只个股（A/B）的[事实]卡（不含任何加权分数），"
    "请仅依据事实，从逻辑、三位一体、节奏、节点四个维度判断谁更支持尾盘介入（相对强弱）。"
    "只输出 JSON：{\"winner\": \"A\"|\"B\", \"reason\": \"<=60字\"}，不要输出其它文字，"
    "不给出具体买卖建议，不给出价位预测，不出仓位。"
)


# tail-scan 补充红线词（codex 门2 中）：REDLINE_KEYWORDS 已含 买入/加仓/建仓 等，但漏了这些
# 更硬的仓位动作词。补拦之。
# 反驳(codex 门2)：不拦「介入/参与」——"尾盘介入"是用户原始需求("最支持尾盘买入的个股")的工具
# 框架本身，工具的产出语义就是"谁相对更支持尾盘介入"。守红线靠三层：① 全标 [判断] ② 不出价位
# ③ 不出显式买卖指令/仓位。把"介入"也拦掉等于把工具语义本身拦没，非红线本意。
_TAIL_ACTION_KEYWORDS = ("上车", "梭哈", "满仓", "重仓", "清仓", "加码", "抄底")


def _scan_redline(text: str) -> str | None:
    body = text or ""
    for kw in REDLINE_KEYWORDS:
        if kw in body:
            return kw
    for kw in _TAIL_ACTION_KEYWORDS:
        if kw in body:
            return kw
    return None


def _filter_reason(reason: str) -> str:
    hit = _scan_redline(reason)
    if hit:
        logger.warning("[tail-scan pk] reason 命中红线 '%s'，已替换", hit)
        reason = "(理由已按红线过滤)"
    reason = reason[: C.PK_REASON_MAX_CHARS]
    return reason if reason else "(无理由)"


def _payload(card_a, card_b):
    def one(c):
        return {k: c.get(k) for k in _FACT_FIELDS}
    return {"A": one(card_a), "B": one(card_b)}


def _safe_call(llm_runner, payload):
    """runner 守卫（镜像 board_break.pk._safe_call）：调用前清空诊断（防上一场 timeout
    残留误归属本场），异常收敛为 (None, True)。返回 (text, raised)。"""
    if hasattr(llm_runner, "last_diagnostics"):
        try:
            llm_runner.last_diagnostics = None
        except Exception:
            pass  # 只读属性等极端情形：放弃清空，退化为旧行为
    try:
        return llm_runner(_PROMPT, payload), False
    except Exception:
        logger.warning("[tail-scan pk] runner 调用异常，按可重试失败处理", exc_info=True)
        return None, True


def _play_match(card_a, card_b, runner):
    """单场：失败区分超时（不重试）与其它失败（重试 1 次）。返回 (winner_code, reason)；
    无效场为 (None, None)。verdict is None 也须重试——`build_llm_runner` 在超时/OSError/
    非零返回码/空 stdout 时都返回 None 而不是抛异常（镜像 board_break.pk._play_match）。"""
    payload = _payload(card_a, card_b)
    text, raised = _safe_call(runner, payload)
    verdict = parse_verdict(text)
    if verdict is None:
        diag = getattr(runner, "last_diagnostics", None)
        if not raised and diag and diag.get("reason") == "timeout":
            return None, None  # 本场超时（诊断已在调用前清空,归属可信）直接计无效场，不重试
        text, _ = _safe_call(runner, payload)  # 非超时失败（含异常）：重试 1 次
        verdict = parse_verdict(text)
        if verdict is None:
            return None, None
    winner = card_a["code"] if verdict["winner"] == "A" else card_b["code"]
    return winner, _filter_reason(verdict["reason"])


def _pool(cards, score_map):
    seen, codes = set(), []
    for c in cards:
        code = c.get("code")
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    if len(codes) <= C.PK_POOL_MAX:
        return codes, []
    ranked = sorted(codes, key=lambda x: (-score_map.get(x, 0.0), x))
    return ranked[: C.PK_POOL_MAX], sorted(ranked[C.PK_POOL_MAX:])


def run_pk(fact_cards, scored, llm_runner, *, budget_seconds=C.PK_BUDGET_SECONDS, clock=time.monotonic):
    card_map = {}
    for c in fact_cards:
        card_map.setdefault(c.get("code"), c)
    score_map = {s.get("code"): s.get("total", 0.0) for s in scored}
    pool, excluded = _pool(fact_cards, score_map)
    if len(pool) < 2:
        return {"status": "skipped", "wins": {}, "ranks": None, "matches": [],
                "invalid": 0, "attempted": 0, "valid_ratio": 0.0, "excluded": excluded}

    pairs = list(itertools.combinations(sorted(pool), 2))
    wins = {c: 0 for c in pool}
    matches, invalid = [], 0
    start, melted = clock(), False
    for a, b in pairs:
        if clock() - start > budget_seconds:
            melted = True
            break
        winner, reason = _play_match(card_map[a], card_map[b], llm_runner)
        if clock() - start > budget_seconds:
            # 场后复查（镜像 board_break.pk.run_pk）：末场/单场跨预算不得落入正常排名
            melted = True
        if winner is None:
            invalid += 1
            matches.append({"a": a, "b": b, "winner": None, "reason": None, "state": "invalid"})
        else:
            wins[winner] += 1
            matches.append({"a": a, "b": b, "winner": winner, "reason": reason, "state": "valid"})

    attempted = len(matches)
    valid_ratio = (attempted - invalid) / attempted if attempted else 0.0
    if melted or (attempted and invalid / attempted > C.PK_INVALID_RATIO_MAX):
        return {"status": "melted", "wins": wins, "ranks": None, "matches": matches,
                "invalid": invalid, "attempted": attempted, "valid_ratio": valid_ratio,
                "excluded": excluded}
    ordered = sorted(pool, key=lambda c: (-wins[c], -score_map.get(c, 0.0), c))
    ranks = {c: i for i, c in enumerate(ordered, start=1)}
    return {"status": "ok", "wins": wins, "ranks": ranks, "matches": matches,
            "invalid": invalid, "attempted": attempted, "valid_ratio": valid_ratio,
            "excluded": excluded}
