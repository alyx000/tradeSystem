"""断板反包 PK 层（方式二：LLM 两两循环赛）。

与打分层（方式一，`scorer.score_all`）完全独立：PK 只喂 [事实] 卡（不含加权分），
板块规则优先，其余让LLM只依据事实判断；加权分不参与准入、排名或prompt。

- `parse_verdict`：严格校验 LLM 输出（`winner` 必须是 "A"/"B"、`reason` 必须是 str），
  容忍模型在 JSON 前后包一层自然语言（提取首个 `{...}` 结构再 `json.loads`）。
- `run_pk`：全部唯一候选参赛，`itertools.combinations`惰性枚举，
  跑循环赛（字典序小者固定为 A，spec 已知局限：不做双向去位置偏置）；单场失败区分
  「超时」（`llm_runner.last_diagnostics.reason == "timeout"`，不重试直接记无效场）与
  「其它失败」（重试 1 次后仍失败才记无效场）；预算超时 / 无效场占比超阈值 → 熔断
  （`status="melted"`，`ranks=None`）；正常收尾按胜场→同胜场组完整交手胜场，仍同则并列。
"""
from __future__ import annotations

import itertools
import logging
import json
import time

from services.board_break import constants as C
from services.board_break.sector_priority import decide as sector_decision
from services.recommend.formatter import REDLINE_KEYWORDS

logger = logging.getLogger(__name__)

# 事实卡中喂给 LLM 的 [事实] 字段（不含加权分/evidence，两法独立，见 task-s2-report.md 字段清单）
_FACT_FIELDS = (
    "code", "name", "limit_times", "pct_chg", "close", "industry",
    "branch", "pattern_evidence",
    "in_main_sector", "main_sector_status", "main_sector_degraded",
    "ann_status", "ann_events", "ann_titles", "holder_status", "holder_source",
    "earnings_status", "earnings_type", "earnings_direction",
    "gain10", "gain10_status", "dif", "dif_status",
    "position_value", "position_state", "position_bar_count",
    "board_path", "sector_context",
)

_PROMPT = (
    "你是断板反包评审员。下面给出两只个股（A/B）的[事实]卡（不含任何加权分数），"
    "请仅依据事实判断反包强度更优者。"
    f"统一评价口径版本：{C.PK_POLICY_VERSION}。{C.PK_POLICY_SUMMARY}"
    "每一场都遵守以下相同规则，不得根据预想胜者切换评价方向："
    "1.近期涨幅gain10只衡量辨识度：在两侧数据有效时，涨幅更高表示该维度辨识度更高；"
    "不得因为涨幅较小、涨得少、补涨空间、位置低而奖励较低涨幅的一方，"
    "也不得仅因涨幅较大就判透支、过热或扣分。辨识度较高不等于整场必胜。"
    "2.分支标签branch本身不加分、不扣分。反包后再断板不天然判弱，连续板断板也不天然判强。"
    "branch=rebreak表示连板后断板、反包首板后再断板；limit_times是昨日实际连续板数，"
    "pattern_evidence.prior_limit_times是断板前一段的高度，两者不得相加或视为当前连板。"
    "跨分支比较须同时考虑前段高度与近期涨幅辨识度，不能仅以昨日1板小于2板、"
    "再次断板或分支名称就判连续性弱。缺少前段信息不能编造。"
    "3.再比较断板当日pct_chg及已核验的主线、公告、业绩等独立事实；"
    "当日收涨或跌幅较小可作为当日表现证据，但不得冒称已核验分时承接、成交量或次日走势。"
    "区间位置仅作背景，不能把低位置当作低涨幅的替代奖励；独立风险须引用具体事件证据。"
    "MACD优先比较零轴状态，不直接用不同股价股票的DIF绝对大小判强弱。缺失数据不视作利空或零。"
    "4.先评所属板块强弱，再评个股：sector_context为事实日申万二级、剔除自身的同业样本。"
    "板块评价的重要性高于个股评价；结合中位涨幅、上涨占比、强弱尾部交叉判断，"
    "中位涨幅与上涨占比在缺口最不利补全后仍同向严格分离时，程序直接执行板块优先，"
    "不再调用模型；你收到的是未满足该硬条件的对局。强弱尾部为补充，不能暗中否定已成立的硬条件。"
    "板块接近、指标冲突或不可比时，说明原因后用个股证据决定；不编造数值评分或固定权重。"
    "只比较同一事实日同一口径的有效证据；coverage_audit按行业定位缺口、"
    "用归属未知对象全归入本行业的最不利情形计算范围。partial不等于禁止比较；"
    "范围重叠或核心指标冲突时再结合个股事实判断。板块缺失不计零、不判弱，不能因另一方有数据就奖励它。"
    "全市场缺行情数量不得写成每个行业各缺相同数量。"
    "不得把成交额Top5归属等同完整板块评分，回退主线不能冒充当日板块强度；行业归属本身不代表带动。"
    "5.带动板块为独立正向加分项：必须有可核验的个股先启动、板块后跟随及广度扩散证据，"
    "理由列明来源、日期和先后。只有领涨、自己涨停或同日板块上涨，均不能认定带动；"
    "leadership为not_computed/missing时不加分也不扣分，不能编造因果。"
    "6.比较涨停路径board_path：一路非一价涨停（换手代理）优于已确定一字板占比超过一半。"
    "分母须denominator_status=verified；one_price_pct_lower/upper把未知日分别视为全非一价/全一价，"
    "下限>50%（majority_one_price=true）即可确认一价居多，即使status=partial也必须参与比较；"
    "例如已确认3次一价/总4板，即使另1日未知，一价占比至少75%。"
    "原连续段与反包分支前段+首板分别列日期，"
    "合计仅比较涨停路径，不拼接为连续板数。优先引用占比与样本数，不因板数不同直接比较数量。"
    "OHLC一价不是盘中封单记录，非一价只是日内有价格变化的换手代理，不等于充分换手。"
    "分母未核验或范围跨过50%时不得断言一价居多；只有全段非一价才能称一路换手代理。"
    "不把未知日补为非一字，不因另一方覆盖完整就额外奖励；已确定的占比界限参与比较，不能因partial整体弃用。"
    "7.reason按板块→涨停路径→个股取舍组织，须指出双方具体数值或事件；若辨识度与当日表现方向冲突，"
    "说明两侧各自优势以及哪项独立证据决定胜负，不能只重复分支标签。"
    f"只输出 JSON：{{\"winner\": \"A\"|\"B\", \"reason\": \"<={C.PK_REASON_MAX_CHARS}字\"}}，不要输出其它文字，"
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


def _scan_redline(text: str) -> str | None:
    """红线扫描（复用 cognition_digest.narrator._scan_redline 范式）：命中返回命中词，否则 None。"""
    for kw in REDLINE_KEYWORDS:
        if kw in (text or ""):
            return kw
    return None


def _filter_reason(reason: str) -> str:
    """红线扫描（AI 生成内容） + 截断 PK_REASON_MAX_CHARS；过滤/截断后为空则兜底占位符（防渲染层空白行）。"""
    hit = _scan_redline(reason)
    if hit:
        logger.warning("[board-break pk] reason 命中红线 '%s'，已替换", hit)
        reason = "(理由已按红线过滤)"
    reason = reason[: C.PK_REASON_MAX_CHARS]
    return reason if reason else "(无理由)"


def _safe_call(llm_runner, payload):
    """runner 守卫（门2 S3 R1/R2）：调用前清空诊断（防上一场 timeout 残留误归属本场），
    异常收敛为 (None, True)——显式标记为「非超时可重试失败」，不得打崩整场循环赛。
    返回 (text, raised)。

    注：真实 runner（narrator/huibo 模式）每次调用也会自清 last_diagnostics，
    此处为防不合规 runner 的防御纵深，非唯一清空点。"""
    if hasattr(llm_runner, "last_diagnostics"):
        try:
            llm_runner.last_diagnostics = None
        except Exception:
            pass  # 只读属性等极端情形：放弃清空，退化为旧行为
    try:
        return llm_runner(_PROMPT, payload), False
    except Exception:
        logger.warning("[board-break pk] runner 调用异常，按可重试失败处理", exc_info=True)
        return None, True


def _play_match(card_a: dict, card_b: dict, llm_runner) -> tuple[str | None, str | None]:
    """单场：可确定的板块优先由程序裁决，其余LLM（非超时失败重试1次）。"""
    priority = sector_decision(card_a, card_b)
    if priority:
        winner = card_a if priority["winner"] == "A" else card_b
        return winner.get("code"), priority["reason"]
    payload = _build_payload(card_a, card_b)
    text, raised = _safe_call(llm_runner, payload)
    verdict = parse_verdict(text)
    if verdict is None:
        diag = getattr(llm_runner, "last_diagnostics", None)
        if not raised and diag and diag.get("reason") == "timeout":
            return None, None  # 本场超时（诊断已在调用前清空,归属可信）直接计无效场，不重试
        text, _ = _safe_call(llm_runner, payload)  # 非超时失败（含异常）：重试 1 次
        verdict = parse_verdict(text)
        if verdict is None:
            return None, None
    winner_code = card_a.get("code") if verdict["winner"] == "A" else card_b.get("code")
    return winner_code, _filter_reason(verdict["reason"])


def _pool_and_excluded(fact_cards: list[dict], score_map: dict) -> tuple[list[str], list[str]]:
    """全部唯一非空候选参赛；兼容旧签名，score_map不再影响准入。"""
    # 去重去空（门2 S3 R2）：重复码会造成自我对局/胜场膨胀,空码会污染配对
    seen = set()
    codes_all = []
    for c in fact_cards:
        code = c.get("code")
        if code and code not in seen:
            seen.add(code)
            codes_all.append(code)
    return codes_all, []


def rank_matches(codes, wins, matches):
    """总胜场→同胜场完整小循环胜场；仍相同共享竞赛名次（1,2,2,4）。"""
    direct = {code: 0 for code in codes}
    for count in set(wins.values()):
        group = {c for c in codes if wins[c] == count}
        mutual = [m for m in matches if m.get("state") == "valid" and m["a"] in group and m["b"] in group]
        if len(mutual) == len(group)*(len(group)-1)//2:
            for match in mutual:
                direct[match["winner"]] += 1
        # 组内交手不全时不奖励缺口，直接共享总胜场名次。
    keys = {c: (wins[c], direct[c]) for c in codes}
    return {c: 1 + sum(keys[other] > keys[c] for other in codes) for c in codes}


def _result(
    status: str,
    *,
    wins: dict,
    ranks: dict | None,
    matches: list,
    invalid: int,
    total: int,
    attempted: int,
    valid_ratio: float,
    excluded: list,
) -> dict:
    """三处 return 唯一构造入口（渲染层落地前收敛 shape 单源）。"""
    return {
        "status": status, "wins": wins, "ranks": ranks, "matches": matches,
        "invalid": invalid, "total": total, "attempted": attempted,
        "valid_ratio": valid_ratio, "excluded": excluded,
        "policy_version": C.PK_POLICY_VERSION, "policy_summary": C.PK_POLICY_SUMMARY,
        "ranking_version": C.PK_RANKING_VERSION,
    }


def build_llm_runner():
    """构造断板反包 PK 用 LLM runner（同 `research_digest.narrator.build_antigravity_runner`
    范式：独立 prompt/payload/timeout，不 import `llm_commentary.comment`）。

    返回 `runner(prompt, payload) -> str | None`：subprocess 调本地 LLM CLI，成功返回
    **原始 stdout**（由 `parse_verdict` 自行提取/解析 JSON，不在此层预解析）；
    超时 / OSError（CLI 缺失或不可执行）/ 非零返回码 / **returncode==0 但 stdout 为空**
    → 返回 None 并设 `runner.last_diagnostics`（dict，含 `"reason"`，超时固定为
    `"timeout"`，启动失败固定为 `"startup_failed"`，空输出固定为 `"empty_stdout"`，
    其余交由 `build_diagnostics` 分类）；每次调用开头先自清 `last_diagnostics=None`
    （防上一场诊断残留误归属本场，`_safe_call` 亦有防御性清空，此处是双重保险的主
    清空点）；每次调用同 narrator 范式接 `_next_board_break_log_file` 生成独立日志
    文件路径传给 `build_prompt_command(..., log_file=...)`，供失败诊断读取日志内容。
    """
    import json
    import subprocess

    from utils.antigravity_diagnostics import build_diagnostics
    from utils.llm_cli import build_prompt_command, resolve_config

    config = resolve_config(default_timeout=180)
    timeout = config.timeout_seconds
    # 门1 效率复查：日志根目录只在 runner 构造时 mkdir 一次，而非每场 PK 都重复
    # mkdir——满池 12 只候选循环赛 C(12,2)=66 场，每场都 mkdir 同一个已存在目录是
    # 66 次冗余 syscall；每场仍各自生成独立文件名（不同 pid+timestamp），不影响
    # 诊断可追溯性。
    log_root = _board_break_log_root()

    def runner(prompt, payload):
        runner.last_diagnostics = None
        full_prompt = prompt + "\n\n输入数据（JSON）：\n" + json.dumps(payload, ensure_ascii=False)
        log_file = _next_board_break_log_file(log_root)
        cmd = build_prompt_command(config, full_prompt, log_file=str(log_file) if log_file else None)
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr=f"timeout after {timeout}s", log_file=log_file, reason="timeout")
            logger.warning("[board-break pk] LLM 调用超时 %ds", timeout)
            return None
        except OSError as e:
            # FileNotFoundError(CLI 缺失) / PermissionError(不可执行) 等均为 OSError 子类，
            # 统一归为 startup_failed；不扩到裸 Exception（那会吞编程错误）。
            runner.last_diagnostics = build_diagnostics(
                stdout="", stderr=str(e), log_file=log_file, reason="startup_failed")
            logger.warning("[board-break pk] LLM 启动失败(%s)", e)
            return None
        stdout = getattr(r, "stdout", "") or ""
        stderr = getattr(r, "stderr", "") or ""
        returncode = getattr(r, "returncode", 0)
        if returncode != 0:
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=stderr, log_file=log_file, returncode=returncode)
            logger.warning("[board-break pk] LLM returncode=%s", returncode)
            return None
        if not stdout.strip():
            # returncode==0 但空输出：同 narrator 三级 fallback 语义，视为无效场而非"胜负已判"
            runner.last_diagnostics = build_diagnostics(
                stdout=stdout, stderr=stderr, log_file=log_file, reason="empty_stdout")
            logger.warning("[board-break pk] LLM stdout 为空，判无效场")
            return None
        return stdout

    runner.last_diagnostics = None
    return runner


def _board_break_log_root():
    """日志根目录 + mkdir（只应在 `build_llm_runner` 构造时调用一次，不放进逐场
    循环；`research_digest.narrator._next_antigravity_log_file` 是逐调用一起 mkdir
    的旧范式——那里典型只调 1 次 LLM 无感，board-break 一场 PK 循环赛最多 66 场，
    照抄会变成 66 次冗余 mkdir，故本服务拆开 root 与文件名两步，root 独立提出。
    `_cell`/本函数与 narrator 的同类小工具存在跨服务重复，属已知 tech-debt，
    与「invoke_llm_cli 共享抽取」defer 项同批次，见 task-s4-report.md「defer」节。
    """
    import os
    from pathlib import Path

    root = Path(os.getenv("ANTIGRAVITY_LOG_DIR", "/private/tmp/tradesystem-antigravity-logs"))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[board-break pk] antigravity log dir 创建失败(%s): %s", root, exc)
        return None
    return root


def _next_board_break_log_file(log_root):
    """按 `log_root`（`_board_break_log_root()` 产出，可能为 None）生成本场独立日志
    文件路径；不在此处 mkdir（root 已在构造 runner 时确保过一次）。"""
    import os
    import time

    if log_root is None:
        return None
    return log_root / f"board-break-{os.getpid()}-{int(time.time() * 1000)}.log"


def run_pk(
    fact_cards: list[dict],
    scored: list[dict],
    llm_runner,
    *,
    budget_seconds: float = C.PK_BUDGET_SECONDS,
    clock=time.monotonic,
) -> dict:
    """全部候选循环赛；仅按PK排名，预算与失败熔断保留。scored仅兼容调用签名。"""
    # first-wins：与 _pool_and_excluded 的去重顺序对齐（同 code 多卡时取第一条参赛）
    card_map: dict = {}
    for c in fact_cards:
        card_map.setdefault(c.get("code"), c)
    pool, excluded = _pool_and_excluded(fact_cards, {})
    if len(pool) < 2:
        return _result(
            "skipped", wins={}, ranks=None, matches=[],
            invalid=0, total=0, attempted=0, valid_ratio=0.0, excluded=excluded,
        )

    pairs = itertools.combinations(sorted(pool), 2)
    total = len(pool)*(len(pool)-1)//2
    wins = {code: 0 for code in pool}
    matches: list[dict] = []
    invalid = 0
    start = clock()
    melted_by_budget = False

    def _over_budget() -> bool:
        return clock() - start > budget_seconds

    for a, b in pairs:
        if _over_budget():
            melted_by_budget = True
            break
        card_a = card_map[a]
        card_b = card_map[b]
        winner, reason = _play_match(card_a, card_b, llm_runner)
        if _over_budget():
            # 场后复查（门2 S3 R3）：末场/单场跨预算不得落入正常排名——预算是硬熔断契约
            melted_by_budget = True
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
        return _result(
            "melted", wins=wins, ranks=None, matches=matches,
            invalid=invalid, total=total, attempted=attempted,
            valid_ratio=valid_ratio, excluded=excluded,
        )

    ranks = rank_matches(pool, wins, matches)

    return _result(
        "ok", wins=wins, ranks=ranks, matches=matches,
        invalid=invalid, total=total, attempted=attempted,
        valid_ratio=valid_ratio, excluded=excluded,
    )
