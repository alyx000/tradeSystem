"""受控 LLM 叙事：仅对**已拉到的真实条目**补 theme/one_liner，不发现、不改事实（H6/M3/M8）。

- 依赖注入 `llm_runner` callable（由 service 构造，独立 prompt/timeout，**不 import llm_commentary.comment**）。
- 无 llm_runner → 原样返回（纯结构化，renderer 出基础行）；A股默认走此路（决策：A股 narrator 默认关）。
- 三级 fallback（M3）：
    L1 调用异常 / 解析失败            → 全量降级（返回原 items，无 narration）
    L2 返回条数≠输入 或 出现输入外主键 → 丢全部 narration 防幻觉新增 → 全量降级
    L3 单条字段缺失                   → 该条无 narration，其余保留
- 事实为主键回填：只取 LLM 的 theme/one_liner 两软字段，绝不接受 LLM 自带 ticker/code/date/firm。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def narrate(items: list[dict], *, llm_runner=None, market: str = "us") -> list[dict]:
    """对 items 补 theme/one_liner。llm_runner=None → 原样返回（不调 LLM）。"""
    if not items or llm_runner is None:
        return items

    payload = [{"id": i, **_facts_for_llm(it, market)} for i, it in enumerate(items)]
    try:
        result = llm_runner(_build_prompt(market), payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("[research-digest] narrator LLM 异常，全量降级: %s", e)
        return items

    narr = _extract(result)
    if narr is None:
        logger.info("[research-digest] narrator 解析失败(L1)，全量降级")
        return items

    valid_ids = set(range(len(items)))
    if set(narr.keys()) != valid_ids:  # L2：条数/主键不符 → 防幻觉新增，丢全部
        logger.warning("[research-digest] narrator 主键/条数不符(in=%d out=%d)，丢全部 narration 防幻觉",
                        len(items), len(narr))
        return items

    out = []
    for i, it in enumerate(items):
        n = narr.get(i) or {}
        merged = dict(it)  # 事实为主键，LLM 只追加两软字段
        theme = str(n.get("theme") or "").strip()
        one = str(n.get("one_liner") or "").strip()
        # M2：该条须 theme + one_liner 都在才采纳；任一缺 → 整条退模板（不写半截 narration，防幻觉）
        if theme and one:
            merged["theme"] = theme
            merged["one_liner"] = one
        out.append(merged)
    return out


def _facts_for_llm(it: dict, market: str) -> dict:
    """只把事实喂给 LLM 作素材（不含目标价，红线）；要求它仅归类不改事实。"""
    if market == "cn":
        return {
            "name": it.get("stock_name") or it.get("stock_code"),
            "org_count": it.get("org_count"),
            "rating_changes": it.get("rating_changes"),
        }
    return {
        "ticker": it.get("ticker"),
        "firm": it.get("firm"),
        "action": it.get("action"),
        "to_grade": it.get("to_grade"),
    }


def _build_prompt(market: str) -> str:
    scope = "A股" if market == "cn" else "美股"
    return (
        f"你是 {scope} 研报速读助理。下面 JSON 数组每条是一项**真实**机构评级事实（id 为主键）。"
        "请仅为每条补两个字段：theme（板块/主题归类，≤8 字）、"
        "one_liner（一句话中文，≤40 字，**只能复述板块归类与评级方向本身**，"
        "如『跨境电商·评级下调』『AI算力·首次覆盖』；"
        "**严禁编造或推测机构的降级/上调理由、经营基本面、业绩或竞争影响等任何未在事实字段中给出的因果信息**，"
        "也不得出现目标价、价格预测、买入/卖出/加仓/清仓等操作词）。"
        "严禁新增、删除、合并条目，严禁修改任何事实字段，严禁臆造未提供的标的。"
        "仅输出 JSON：{\"items\":[{\"id\":<原id>,\"theme\":\"...\",\"one_liner\":\"...\"}]}，不要 markdown 围栏。"
    )


def build_gemini_runner():
    """构造受控 gemini 叙事 callable（独立 env/prompt/timeout，**不 import llm_commentary.comment**，M8）。

    返回 runner(prompt, payload)->dict|None：subprocess 调 gemini，解析 JSON；任何失败返 None
    （→ narrate 全量降级走纯结构化）。launchd 下 LLM 启动慢，默认 timeout 180s。
    """
    import json
    import os
    import subprocess

    bin_path = os.getenv("GEMINI_BIN", "/opt/homebrew/bin/gemini")
    # 构造期解析,在 narrate() 的 try/except 之外:env 手填成非整数若直接 int() 会崩整个 CLI
    # 任务(launchd 下排障无门),故兜底回退 180s,与 docstring「任何失败降级」契约一致。
    raw_timeout = os.getenv("LLM_TIMEOUT_SECONDS", "180")
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError):
        logger.warning("[research-digest] LLM_TIMEOUT_SECONDS=%r 非整数，回退 180s", raw_timeout)
        timeout = 180
    model = os.getenv("GEMINI_MODEL", "")

    def runner(prompt, payload):
        full = prompt + "\n\n输入数据（JSON 数组）：\n" + json.dumps(payload, ensure_ascii=False)
        cmd = [bin_path, "--prompt", full]
        if model:
            cmd += ["-m", model]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.warning("[research-digest] gemini 超时 %ds，叙事降级", timeout)
            return None
        except OSError as e:
            # FileNotFoundError(gemini 缺) / PermissionError(不可执行) 等都是 OSError 子类:
            # 统一降级返 None,而非仅捕 FileNotFoundError 漏掉权限/其它 OS 错。
            # 不扩到裸 Exception:那会吞掉编程错误,且 _parse_json 已自带异常安全。
            logger.warning("[research-digest] gemini 启动失败(%s)，叙事降级", e)
            return None
        if r.returncode != 0:
            logger.warning("[research-digest] gemini returncode=%s，叙事降级", r.returncode)
            return None
        return _parse_json(r.stdout)

    return runner


def _parse_json(text):
    """解析 gemini 输出 JSON：直接 loads → 去 markdown 围栏 → 平衡括号提取首个 {...}。失败返 None。"""
    import json
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        nl = t.find("\n")
        if nl != -1 and t[:nl].strip().lower() in ("json", ""):
            t = t[nl + 1:]
    try:
        return json.loads(t)
    except Exception:
        pass
    start = t.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except Exception:
                    return None
    return None


def _extract(result):
    """把 llm_runner 返回（已解析 dict）抽成 {id: {theme, one_liner}}；不可用返 None。"""
    if not isinstance(result, dict):
        return None
    arr = result.get("items")
    if not isinstance(arr, list):
        return None
    out: dict[int, dict] = {}
    for e in arr:
        if not isinstance(e, dict) or "id" not in e:
            continue
        raw_id = e["id"]
        # 只认纯 int 主键：拒 float（int(0.5)→0 蒙混）/ bool / 字符串 id（防幻觉，M1）
        if type(raw_id) is not int or isinstance(raw_id, bool):
            continue
        out[raw_id] = {"theme": e.get("theme"), "one_liner": e.get("one_liner")}
    # 任一畸形条目（非 dict / 缺 id / id 非整 / 重复 id）都会使 len(out) != len(arr)：
    # 整批不可信 → 返 None 触发全量降级（防幻觉，M3 严格化）。
    if len(out) != len(arr):
        return None
    return out
