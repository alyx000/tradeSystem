"""断板反包 MD 渲染（盘后只读）。

五段结构（spec「报告结构」）：
① 头部（日期+prev_trade_date+用户规则转述+红线声明）
② 候选表（双排名：加权分排名[判断] + PK 胜场排名[判断]，PK 无效显 `—`，
   分歧标注仅在 PK `status=="ok"` 时按 `abs(rank_score-rank_pk)>=3` 计算）
③ 每票打分依据明细（八维度 `维度|得分|依据` 全列，含 0 分维度，`evidence.detail` 原样使用）
④ PK 理由段（红线过滤后的 reason；熔断/未跑/未运行分别标注原因）
⑤ 脚注（剔除统计逐项 + 数据完整性：三源状态/prev_trade_date/维度缺失统计/主线 degraded/
   PK invalid+attempted/total+熔断状态/excluded 未进池）

全部打分/PK 结论标 [判断]；筛选/取数标 [事实]；6% 参考位显式声明为机械换算，非价格预测。
`render_source_failed` 对应 `scanner.run_daily` 的 `source_failed` 状态，不产出正常候选清单。
"""
from __future__ import annotations

import logging
import pathlib

logger = logging.getLogger(__name__)

_REDLINE = (
    "> 红线声明：本报告筛选结果为 [事实]，打分与 PK 排名为 [判断]，"
    "非买卖建议、不预测价格目标；6% 参考位为用户既定规则的机械换算（收盘 × 1.06），"
    "非价格预测。"
)

_RULE_NOTE = (
    "> 用户既定打法转述：断板日涨幅 ≤6% 视为候选断板；隔日突破 6%、回踩后重新突破 6% "
    "为用户隔日盘中交易动作，本清单**不跟踪、不建池、不保留状态**，仅供次日观察参考。"
)

_DIMENSION_LABELS = {
    "main_sector": "主线板块",
    "increase": "增持/回购",
    "placement": "定增",
    "reduce": "减持",
    "announce": "其他重大公告",
    "earnings": "业绩",
    "gain10": "近期涨幅过高",
    "macd": "MACD零轴",
}

_REJECT_LABELS = {
    "lt_below_min": "连板数<2",
    "dirty_limit_times": "limit_times脏值",
    "non_main_board": "非主板",
    "st": "ST",
    "still_limit_up": "仍涨停（未断板）",
    "limit_down": "跌停",
    "bar_missing": "停牌或行情缺失",
    "pct_too_high": "断板日涨幅>6%",
}

_MISSING_STATUSES = ("missing", "source_failed")

# ② 候选表空态文案：按 scanner 的 empty_kind 三分语义区分「源本身无候选」与
# 「入口候选存在但全被规则剔除」，避免两种截然不同的空态共用一句模糊文案。
_EMPTY_KIND_MESSAGES = {
    "source_ok_empty": "今日无断板反包候选（昨日无连板≥2 的入口票）。",
    "rule_filtered_empty": "今日无断板反包候选（有入口票但全部被规则剔除，明细见脚注）。",
}
_DEFAULT_EMPTY_MSG = "今日无断板反包候选。"


def _fmt_num(value, spec: str = ".2f") -> str:
    if value is None:
        return "—"
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        return str(value)


def _render_source_status_lines(sources: dict) -> list[str]:
    """数据完整性脚注用：`sources` 恒为 scanner 产出的 `{"ok": bool, "source": str}` 形状
    （scanner._run_daily 状态机中，走到这里的 status 必为 "ok"，不存在裸字符串兼容需求）。"""
    lines = []
    for name, v in (sources or {}).items():
        status = "成功" if v.get("ok") else "失败"
        src = v.get("source") or ""
        lines.append(f"  - {name}：{status}" + (f"（源={src}）" if src else ""))
    return lines


def _cell(x) -> str:
    """自由文本进 markdown 表格单元格前的转义：竖线会拆表格列，换行会打断行结构。

    门1 correctness 复查：`x is None` 须先兜底成空串——`c.get("name", "")` 这类
    default 只在**键缺失**时生效，若上游给出显式 `None`（如某些行业分类为空的
    个股），`str(None)` 会把字面量 "None" 渲染进表格单元格，而非期望的空白。

    门1 reuse 复查：`scripts/automations/four_trading_day_review.py` 的 `_md_cell`
    做同样的转义（也含 None 兜底，写法是 `.strip() or "-"`），两处小工具重复。
    未合并抽取的理由：对方模块与本服务无引用关系、抽取需新开
    `scripts/utils/markdown.py` 并改两处调用点，超出本轮门1 批次范围；
    defer——下次任一侧改动时顺手提取共享。
    """
    if x is None:
        return ""
    return str(x).replace("|", "｜").replace("\n", " ")


def _blank_wrapped(*msgs: str) -> list[str]:
    """空态提示块：空行 + 1~N 行消息 + 空行（候选表/打分依据明细两处空候选提示复用
    同一 shape）。门1 correctness 复查：候选表分支有两句话须相邻展示——若外部拼接
    额外一行再包一层空行，会在两句话之间插入多余空行；改为变参一次性传入两句
    话，保持它们相邻，同时仍与打分依据明细分支共用同一 helper。"""
    return ["", *msgs, ""]


def _pk_stats_phrase(attempted: int, total: int, invalid: int, valid_ratio: float) -> str:
    """PK 场次统计短语（熔断态与正常态收尾复用同一口径，防两处措辞漂移）。"""
    return f"已打 {attempted}/{total} 场，无效 {invalid} 场，有效率 {valid_ratio:.0%}"


def _pk_summary_lines(pk_result: dict | None) -> list[str]:
    """④ PK 理由段：区分未运行/熔断/未打/正常四态，正常态列每场有效理由。"""
    lines = ["## PK 理由（LLM 两两裁判，[判断]）", ""]
    if pk_result is None:
        lines.append("PK 未运行（`--no-llm` 或 LLM runner 不可用），仅出加权分排序。")
        return lines
    status = pk_result.get("status")
    attempted = pk_result.get("attempted", 0)
    total = pk_result.get("total", 0)
    invalid = pk_result.get("invalid", 0)
    valid_ratio = pk_result.get("valid_ratio", 0.0)
    if status == "skipped":
        lines.append("候选不足 2 只，PK 未运行。")
        return lines
    if status == "melted":
        lines.append(
            f"PK 因预算超时或无效场占比过高熔断，未完赛（"
            f"{_pk_stats_phrase(attempted, total, invalid, valid_ratio)}）；"
            "本报告不渲染 PK 排名列，只出加权分排序。"
        )
        return lines
    matches = pk_result.get("matches") or []
    valid_matches = [m for m in matches if m.get("state") == "valid"]
    if not valid_matches:
        lines.append("PK 已完赛，但暂无可展示的有效理由。")
        return lines
    for m in valid_matches:
        loser = m["b"] if m["winner"] == m["a"] else m["a"]
        reason = _cell(m.get("reason") or "（无理由）")
        lines.append(f"- {m['a']} vs {m['b']} → 胜者 {m['winner']}（负 {loser}）：{reason}")
    lines.append("")
    lines.append(f"（{_pk_stats_phrase(attempted, total, invalid, valid_ratio)}）")
    return lines


def _dimension_missing_counts(scored: list[dict]) -> dict:
    """跨候选聚合各维度 missing/source_failed 计数（数据完整性脚注用）。"""
    counts: dict[str, int] = {}
    for c in scored:
        for ev in c.get("evidences") or []:
            if ev.get("status") in _MISSING_STATUSES:
                dim = ev.get("dimension", "?")
                counts[dim] = counts.get(dim, 0) + 1
    return counts


def render_daily(result: dict, scored: list, pk_result: dict | None) -> str:
    date = result.get("date", "")
    prev_date = result.get("prev_trade_date", "")
    lines: list[str] = [
        f"# 断板反包观察清单 · {date}  [判断]",
        "",
        f"> 上一交易日（T-1）：{prev_date}",
        _RULE_NOTE,
        _REDLINE,
        "",
    ]

    pk_status = pk_result.get("status") if pk_result else None
    ranks = pk_result.get("ranks") if (pk_result and pk_status == "ok") else None
    wins = pk_result.get("wins") if pk_result else {}

    # —— ② 候选表 ——
    lines.append("## 候选清单（按加权分降序）")
    if not scored:
        # 走到 render_daily 说明 status=="ok"（source_failed 在 CLI 层已分流到
        # render_source_failed），核心数据源必然全部成功——原先包一层
        # `if _sources_all_ok(...)` 判断恒为真，是死代码，直接无条件输出该行。
        empty_msg = _EMPTY_KIND_MESSAGES.get(result.get("empty_kind"), _DEFAULT_EMPTY_MSG)
        lines += _blank_wrapped(
            empty_msg, "核心数据源全部成功，本次为真实空候选（非采集故障）。")
    else:
        lines += [
            "| 代码 | 名称 | 连板 | 断板日涨幅 | 收盘 | 6%参考位 | 行业 "
            "| 加权分及排名[判断] | PK胜场及排名[判断] | 分歧⚠ |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for c in scored:
            code = c.get("code", "")
            rank_score = c.get("rank_score")
            pct_chg = _fmt_num(c.get("pct_chg"), ".2f")
            close = _fmt_num(c.get("close"), ".2f")
            ref_price = c.get("ref_price")
            ref_price_cell = _fmt_num(ref_price, ".2f") if isinstance(ref_price, (int, float)) else (ref_price or "—")
            total_cell = f"{c.get('total', 0.0):.1f}（第{rank_score}名）"
            diverge = ""
            if ranks is not None and code in ranks:
                rank_pk = ranks[code]
                pk_cell = f"{wins.get(code, 0)}胜（第{rank_pk}名）"
                if rank_score is not None and abs(rank_score - rank_pk) >= 3:
                    diverge = "⚠"
            else:
                pk_cell = "—"
            lines.append(
                f"| {code} | {_cell(c.get('name', ''))} | {c.get('limit_times', '—')} | {pct_chg}% "
                f"| {close} | {ref_price_cell} | {_cell(c.get('industry', ''))} "
                f"| {total_cell} | {pk_cell} | {diverge} |"
            )
        lines.append("")

    # —— ③ 每票打分依据明细 ——
    lines.append("## 打分依据明细（[判断]，0 分维度亦列出处）")
    if not scored:
        lines += _blank_wrapped("（无候选，无需展示打分依据）")
    for c in scored:
        lines.append(f"### {c.get('code', '')} {_cell(c.get('name', ''))}")
        lines.append("| 维度 | 得分 | 依据 |")
        lines.append("| --- | --- | --- |")
        for ev in c.get("evidences") or []:
            dim_label = _DIMENSION_LABELS.get(ev.get("dimension"), ev.get("dimension", "?"))
            lines.append(f"| {dim_label} | {ev.get('score', 0.0):+.1f} | {_cell(ev.get('detail', ''))} |")
        lines.append("")

    # —— ④ PK 理由段 ——
    lines += _pk_summary_lines(pk_result)
    lines.append("")

    # —— ⑤ 脚注一：剔除统计 ——
    lines.append("## 脚注一：剔除统计")
    rejects = result.get("rejects") or {}
    if not rejects:
        lines.append("（无剔除记录）")
    else:
        for k, v in rejects.items():
            lines.append(f"- {_REJECT_LABELS.get(k, k)}：{v} 只")
    lines.append("")

    # —— ⑤ 脚注二：数据完整性 ——
    lines.append("## 脚注二：数据完整性")
    lines.append(f"- 上一交易日（prev_trade_date）：{prev_date}")
    lines += _render_source_status_lines(result.get("sources") or {})
    if result.get("main_sector_degraded"):
        lines.append("- 主线板块：当日成交额集中度快照缺失，已回退最近一日（degraded）")
    missing_counts = _dimension_missing_counts(scored)
    if missing_counts:
        for dim, n in missing_counts.items():
            lines.append(f"- 维度缺失/源失败：{_DIMENSION_LABELS.get(dim, dim)} × {n} 只")
    else:
        lines.append("- 维度缺失/源失败：无")
    if pk_result is not None:
        lines.append(
            f"- PK：状态={pk_result.get('status')}，无效场={pk_result.get('invalid', 0)}，"
            f"已打场次={pk_result.get('attempted', 0)}/{pk_result.get('total', 0)}"
        )
        excluded = pk_result.get("excluded") or []
        if excluded:
            lines.append(f"- PK 未进池（超 Top-K 截断，不参与分歧标注）：{'、'.join(excluded)}")
    else:
        lines.append("- PK：未运行")

    return "\n".join(lines)


def render_source_failed(result: dict) -> str:
    """核心数据源失败报告：不产出正常候选清单，只展示失败源与错误摘要。"""
    date = result.get("date", "")
    failed_sources = result.get("failed_sources") or {}
    lines = [
        f"# 断板反包 数据失败 · {date}  [事实]",
        "",
        "> 核心数据源失败，今日不产出候选清单，请人工核实数据源后用 `--date` 手动补跑。",
        "",
        "## 失败源明细",
    ]
    if isinstance(failed_sources, dict):
        for name, err in failed_sources.items():
            lines.append(f"- {name}：{err}")
    else:
        for name in failed_sources:
            lines.append(f"- {name}")
    return "\n".join(lines)


def save_report(md: str, date: str, out_root: str = "data/reports/board-break") -> pathlib.Path:
    """落盘 `<out_root>/<date>.md`；`out_root` 可注入供测试使用 `tmp_path`。

    对齐 `research_digest.renderer.write_md` 约定：mkdir + 写盘均纳入 try，失败带路径
    日志后原样 raise（不吞异常静默失败，落盘失败必须让调用方感知）。
    """
    path = pathlib.Path(out_root) / f"{date}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")
    except OSError as e:
        logger.error("[board-break] 落盘失败 %s: %s", path, e)
        raise
    return path
