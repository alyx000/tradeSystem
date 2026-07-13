"""tail-scan 只读观察清单渲染（排序标 [判断]，证据按行保留边界标签）。"""
from __future__ import annotations

import pathlib
import re

from services.tail_scan import constants as C

_PRODUCT_TEXT_MAX_CHARS = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS // 3
_SOURCE_TEXT_MAX_CHARS = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS // 2
_MARKDOWN_PLAIN_CHARS = frozenset(r"\[]()*_<>`#")
_TRUSTED_EVIDENCE_LABELS = {
    "老师观点·个股",
    "研报观点·个股催化",
    "来源陈述·个股关联",
    "事实·行业催化",
    "来源陈述·行业催化",
}
_SAFE_EVIDENCE_LABEL = "来源陈述·近期催化"
PUSH_BODY_MAX_BYTES = 18_000
_CANDIDATE_HEADING = "\n## 候选（按 PK 名次 / 粗分排序；排序为 [判断]，主营/催化按行内标签）\n"

_DISCLAIMER = (
    "> [判断] 盘中单次快照观察清单（尾盘前触发），仅供复盘参考，不构成买卖建议、不含价位。\n"
    "> 数据时效：涨幅/成交额/尾盘强度为 **T 实时快照**；逻辑/板块（主线、概念）为 **T-1** 口径；"
    "主营是扫描时当前公开静态资料（不是历史 as-of 快照）；催化为截至扫描日近30自然日的本地证据；"
    "产业链位置为程序 [判断]；盘中形态为单次快照代理（无分时数据）。\n"
)


def _fmt(value, nd: int = 2, suffix: str = "") -> str:
    """数值展示：None → 「—」；否则定点四舍五入（避免 8.1596/30.3661162… 这类长尾小数）。"""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{nd}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _rank_note(pk_result, code):
    if not pk_result or pk_result.get("status") != "ok":
        return ""
    r = pk_result.get("ranks", {}).get(code)
    return f" · PK名次 **{r}**" if r else ""


def _display_order(scored, pk_result):
    """展示顺序：PK status=ok 时按 PK 名次排（履行"按 PK 名次排"契约）；
    无名次的票（不在强池/被 excluded）用大 key 排后、`sorted` 稳定保留粗分序兜底。
    melted/skipped/no-llm/None → 保持传入的粗分序。
    （codex 门2 高危：此前只标注 PK名次却不重排，导致 #2 显示在 #3 之上，自相矛盾。）"""
    if pk_result and pk_result.get("status") == "ok":
        ranks = pk_result.get("ranks") or {}
        return sorted(scored, key=lambda c: ranks.get(c.get("code"), 10 ** 9))
    return scored


def _clean(value, limit: int = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _plain(value, limit: int = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS) -> str:
    """把外部文本渲染为 Markdown 纯文本，系统生成的边界标签不经过此函数。"""
    text = _clean(value, limit)
    return "".join(f"\\{char}" if char in _MARKDOWN_PLAIN_CHARS else char for char in text)


def _business_source_name(source) -> str:
    source = _clean(source, _SOURCE_TEXT_MAX_CHARS)
    lower = source.lower()
    if lower.startswith("tushare"):
        return "Tushare公司资料"
    if lower.startswith("akshare"):
        return "AkShare主营介绍"
    return _plain(source, _SOURCE_TEXT_MAX_CHARS) if source else "公开公司资料"


def _tag(label) -> str:
    label = _clean(label, _SOURCE_TEXT_MAX_CHARS)
    if label.startswith("[") and label.endswith("]"):
        label = label[1:-1].strip()
    safe = label if label in _TRUSTED_EVIDENCE_LABELS else _SAFE_EVIDENCE_LABEL
    return f"[{safe}]"


def _render_industry_logic(card: dict) -> list[str]:
    """渲染固定的主营、产业位置、近期催化三块，不提升任何证据标签。"""
    lines = []
    business_status = card.get("business_status")
    if business_status == "source_failed":
        business_line = "[事实·主营] 主营资料源失败，本次未取得。"
    elif business_status == "ok":
        business = _plain(card.get("business_summary"))
        products = []
        for item in (card.get("product_names") or [])[: C.INDUSTRY_LOGIC_MAX_PRODUCTS]:
            product = _plain(item, _PRODUCT_TEXT_MAX_CHARS)
            if product:
                products.append(product)
        details = []
        if business:
            details.append(business)
        if products:
            details.append(f"核心产品：{'、'.join(products)}")
        detail_text = "；".join(details) or "暂无可展示主营摘要"
        source = _business_source_name(card.get("business_source"))
        business_line = f"[事实·主营] {detail_text}（来源：{source}）"
    else:
        business_line = "[事实·主营] 暂无可核验主营资料。"
    lines.append(f"  - {business_line}\n")

    position = _plain(card.get("industry_position")) or "暂无可核验归纳。"
    lines.append(f"  - [判断·产业链位置] {position}\n")

    evidence = [
        item for item in (card.get("catalyst_evidence") or []) if isinstance(item, dict)
    ][: C.INDUSTRY_LOGIC_MAX_CATALYSTS]
    if evidence:
        for item in evidence:
            text = _plain(item.get("text")) or "未提供可展示摘要"
            date = _plain(item.get("date"), _SOURCE_TEXT_MAX_CHARS) or "日期未标注"
            source = _plain(item.get("source"), _SOURCE_TEXT_MAX_CHARS) or "来源未标注"
            lines.append(f"  - {_tag(item.get('label'))} {text}（{date} · {source}）\n")
    elif card.get("catalyst_status") == "source_failed":
        lines.append("  - [来源状态·近期催化] 催化证据源失败，本次未取得。\n")
    else:
        lines.append("  - [来源状态·近期催化] 最近30日暂无可核验产业催化。\n")
    return lines


def _report_prefix(scan_result: dict, pk_result: dict | None) -> list[str]:
    date = scan_result.get("quote_date", "")
    qt = scan_result.get("quote_time", "")
    lines = [f"# 尾盘强势股观察清单 · {date} {qt}\n", _DISCLAIMER,
             f"\n筛选：涨幅>7% ∩ 非ST ∩ 成交额>20亿 · 全市场扫 {scan_result.get('scanned', 0)} 只 → "
             f"命中 **{scan_result.get('matched', 0)}** 只\n"]
    if pk_result and pk_result.get("status") == "melted":
        lines.append("\n> [判断] PK 循环赛熔断（预算/无效场率超限），仅按粗分排序展示。\n")
    return lines


def _candidate_block(card: dict, pk_result: dict | None) -> str:
    tags = []
    if card.get("in_main_sector"):
        tags.append("主线")
    if card.get("concept_names"):
        tags.append("概念:" + "/".join(card["concept_names"][:2]))
    if card.get("is_limit_up"):
        tags.append("已涨停")
    tag_s = ("｜" + " ".join(tags)) if tags else ""
    lines = [
        f"- **{card.get('name','')}**（{card.get('code','')}）涨{_fmt(card.get('pct_chg'), 2, '%')} "
        f"成交{_fmt(card.get('amount_yi'), 2, '亿')} 近5日{_fmt(card.get('gain5'), 1, '%')} "
        f"连涨{_fmt(card.get('up_days'), 0)}天"
        f"{tag_s}{_rank_note(pk_result, card.get('code'))}\n"
    ]
    lines.extend(_render_industry_logic(card))
    return "".join(lines)


def render_daily(scan_result: dict, scored: list, pk_result: dict | None) -> str:
    lines = _report_prefix(scan_result, pk_result)
    if not scored:
        lines.append("\n本次无满足条件个股。\n")
        return "".join(lines)
    lines.append(_CANDIDATE_HEADING)
    for c in _display_order(scored, pk_result):
        lines.append(_candidate_block(c, pk_result))

    lines.append(_degradation_note(scored))
    lines.append(_render_pk_detail(pk_result, scored))
    return "".join(lines)


def render_push_summary(
    scan_result: dict,
    scored: list,
    pk_result: dict | None,
    *,
    full_md: str,
    report_path: str,
) -> str:
    """生成钉钉预算内摘要；候选只按完整块追加，绝不按字符截断候选内容。"""
    if len(full_md.encode("utf-8")) <= PUSH_BODY_MAX_BYTES:
        return full_md

    ordered = _display_order(scored, pk_result)[: C.PK_POOL_MAX]
    prefix = "".join(_report_prefix(scan_result, pk_result)) + _CANDIDATE_HEADING
    path = _plain(report_path, C.INDUSTRY_LOGIC_TEXT_MAX_CHARS * 2)
    total = len(scored)
    shown_cards = []
    blocks = []
    for card in ordered:
        block = _candidate_block(card, pk_result)
        prospective = shown_cards + [card]
        note = (
            f"\n> [来源状态·推送摘要] 推送仅展示 {len(prospective)}/{total} 只，"
            f"完整报告：{path}\n"
        )
        candidate = prefix + "".join(blocks) + block + _degradation_note(prospective) + note
        if len(candidate.encode("utf-8")) > PUSH_BODY_MAX_BYTES:
            break
        shown_cards.append(card)
        blocks.append(block)

    note = (
        f"\n> [来源状态·推送摘要] 推送仅展示 {len(shown_cards)}/{total} 只，"
        f"完整报告：{path}\n"
    )
    summary = prefix + "".join(blocks) + _degradation_note(shown_cards) + note
    if len(summary.encode("utf-8")) <= PUSH_BODY_MAX_BYTES:
        return summary
    # 极端超长元数据时只保留固定说明；仍不截断任何候选块。
    return note


def _degradation_note(scored) -> str:
    """维度降级脚注（codex 门2 round3）：任一维度取数失败/缺失时显式提示，
    让用户区分"确定不强"与"数据没取到"。"""
    dims = {
        "主线": lambda c: c.get("main_sector_status") not in (None, "ok"),
        "概念": lambda c: c.get("concept_status") not in (None, "ok"),
        "大势": lambda c: c.get("index_status") not in (None, "ok"),
        "历史行情": lambda c: c.get("history_status") not in (None, "ok"),
        "主营资料": lambda c: c.get("business_status") == "source_failed",
        "近期催化": lambda c: c.get("catalyst_status") == "source_failed",
    }
    hit = [name for name, fn in dims.items() if any(fn(c) for c in scored)]
    if not hit:
        return ""
    return (f"\n> [判断] 数据降级：**{'/'.join(hit)}** 维度本次取数失败或缺失，"
            "相关判断已弱化（非「确定不强」），请知悉。\n")


def _render_pk_detail(pk_result, scored) -> str:
    """PK 对局明细：LLM 逐场依据事实判相对强弱（理由已过红线，全 [判断]）。
    仅在 status==ok 且有有效场时渲染。"""
    if not pk_result or pk_result.get("status") != "ok":
        return ""
    valid = [m for m in pk_result.get("matches", []) if m.get("state") == "valid"]
    if not valid:
        return ""
    names = {c.get("code"): c.get("name", "") for c in scored}
    out = ["\n## PK 对局明细（LLM 依据事实判相对强弱，全为 [判断]）\n"]
    for m in valid:
        a, b, w = m.get("a"), m.get("b"), m.get("winner")
        reason = _plain(m.get("reason", ""), C.PK_REASON_MAX_CHARS)
        out.append(
            f"- {names.get(a, a)} vs {names.get(b, b)} → 胜：**{names.get(w, w)}**"
            f" ｜ {reason}\n")
    return "".join(out)


def render_source_failed(scan_result: dict) -> str:
    return (f"# 尾盘强势股扫描 · 数据失败\n\n> [判断] 数据源失败，未产出候选清单。\n\n"
            f"原因：{scan_result.get('error', '未知')}\n")


def save_report(md: str, date: str, out_root: str = "data/reports/tail-scan") -> pathlib.Path:
    root = pathlib.Path(out_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{date}.md"
    path.write_text(md, encoding="utf-8")
    return path
