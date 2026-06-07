"""渲染 5 段 MD + 红线落地。

红线（H1，约束生成不约束取数）：
- LLM 生成的 one_liner/theme 出口跑 _scan_redline，命中→丢该条 narration 降级模板（不输出违规文本）。
- 目标价（current_pt/prior_pt/target_price_*）一律不渲染（只在 raw 留存供溯源）。
- 评级词中性化：买入/Buy→偏多档 等，仅渲染层映射，raw 存原值。
红线只扫 LLM 生成层，不扫结构化事实（机构名/原始评级是事实）。
"""
from __future__ import annotations

import logging
from pathlib import Path

# 复用 recommend 的红线关键词表（唯一真源，避免漂移）
from services.recommend.formatter import REDLINE_KEYWORDS
# A股段展示上限复用 collector 的单一真源（与"补观点条数"同一常量，杜绝两处 15 漂移）
from services.research_digest.collector import CN_DISPLAY_CAP

logger = logging.getLogger(__name__)

_OUT_ROOT_DEFAULT = "data/reports/research-digest"
_ITEMS_CAP = 15  # 美股段展示上限（A股段用 CN_DISPLAY_CAP）

_NEUTRAL_MAP = [
    (("买入", "增持", "推荐", "strong buy", "buy", "overweight", "outperform", "accumulate"), "偏多档"),
    (("中性", "持有", "hold", "neutral", "equal-weight", "equalweight", "market perform", "in-line"), "中性档"),
    (("减持", "卖出", "回避", "sell", "underweight", "underperform", "reduce"), "偏空档"),
]


# 英文红线补充（M4：REDLINE_KEYWORDS 全中文，美股 narration 可能混入英文）。
# 只收**短语级/明确**违规词，lower-case 比较；**不收**裸 buy/sell（会误杀 sell-side/buyback 等中性术语，
# 同 recommend 的"约束生成不约束事实"carve-out 精神）。
_RD_REDLINE_EXTRA_EN = ("target price", "price target", "strong buy", "目标价位")


def _scan_redline(text) -> str | None:
    t = str(text or "")
    for kw in REDLINE_KEYWORDS:
        if kw in t:
            return kw
    low = t.lower()
    for kw in _RD_REDLINE_EXTRA_EN:
        if kw.lower() in low:
            return kw
    return None


# 鞠磊框架信号词的显著渲染：emoji + 关键信号加粗，钉钉手机端一眼可见（纯色 span 钉钉渲染不稳，故用 emoji+bold）。
# 首次覆盖=鞠磊 #1 信号，最突出；评级下调/多家覆盖给 emoji 但不加粗（前者是风险提示、后者是覆盖广度，不抢「首次覆盖」的视觉焦点）。
_SIGNAL_BADGE = {
    "首次覆盖": "🆕 **首次覆盖**",
    "评级上调": "📈 **评级上调**",
    "重启覆盖": "🔄 **重启覆盖**",
    "评级下调": "📉 评级下调",
    "多家覆盖": "👥 多家覆盖",
}


# 鞠磊框架 ② 研报「强提示词」：研报标题里的重点推荐/跟踪措辞，是高权重关注信号。
# 出现在研报观点（标题）自由文本里，故在标题就地加粗突出（非结构化信号，不走徽章）。
_STRONG_HINT_PHRASES = ("重点推荐", "重点关注", "重点跟踪", "强烈推荐", "核心推荐", "重点配置", "首推")


def _emphasize_hints(text: str) -> str:
    """把研报观点里的强提示词（重点推荐/重点关注/重点跟踪 等，鞠磊框架②）就地加粗突出。无命中原样返回。"""
    if not text:
        return text
    for p in _STRONG_HINT_PHRASES:
        text = text.replace(p, f"**{p}**")
    return text


def _badges(signals) -> str:
    """把 signals 渲染成显著徽章串（emoji + 关键词加粗）。

    signals 是**封闭内部词表**（仅由 collector._cn_signals / _US_ACTION_SIGNAL / 标题补的字面量"首次覆盖"
    产生，全在 _SIGNAL_BADGE 中），从不含外部/用户文本；故未知信号原样透出（无害兜底，且钉钉 markdown 不执行
    HTML/JS，无注入面）。空 / None 安全返空串。
    """
    return " ".join(_SIGNAL_BADGE.get(s, s) for s in (signals or []))


def neutralize_rating(rating) -> str:
    r = str(rating or "").lower()
    for kws, label in _NEUTRAL_MAP:
        if any(k in r for k in kws):
            return label
    return "—"


def _safe_text(text, source: str = "文本") -> str:
    """输出层红线扫描：命中即丢（返空），否则返原文（H1）。

    既用于 LLM 生成的 theme/one_liner，也用于 viewpoint 真实研报标题在出口的兜底——
    标题虽属事实层（采集照取，约束生成不约束取数），但系统输出绝不 surface 目标价/操作词（系统最硬红线），
    故出口仍过一道：命中即整条丢（不中性化、不残留半句）。实测真实标题基本不含红线词，命中为极少数。

    日志只打 source + 命中的通用关键词，**绝不打原文片段**——否则被丢的"目标价/买入"会写进
    /tmp/*.log（本机任何用户可读）形成旁路泄漏（同凭据不进日志的纪律）。
    """
    t = _repair_common_text(str(text or "").strip())
    if not t:
        return ""
    hit = _scan_redline(t)
    if hit:
        logger.warning("[research-digest] %s 命中红线 '%s'，已丢弃（不打原文，避免 /tmp 日志旁路）", source, hit)
        return ""
    return t


def _repair_common_text(text: str) -> str:
    replacements = {
        "\ufffd": "",
        "AI数据中用电": "AI数据中心用电",
        "数据中用电": "数据中心用电",
        "数据中 心": "数据中心",
        "半体": "半导体",
        "半 导体": "半导体",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _narration(item: dict) -> str:
    """只渲染 LLM 的 theme（板块归类，过红线）。

    one_liner **不再渲染**：收紧 prompt 后它只能复述"板块·方向"，与结构化 theme 标签 + action 前缀冗余
    （见记忆 feedback_llm_narration_fabricates_rationale）。narrate 仍生成它并留在 raw（+ prompt 禁编造作
    安全带）；如日后要恢复展示，这里追加 one_liner 即可。
    """
    theme = _safe_text(item.get("theme"), "LLM theme")
    return f"〔{theme}〕" if theme else ""


def _cn_line(it: dict) -> str:
    name = it.get("stock_name") or it.get("stock_code")
    direction = "/".join(sorted({c for c in it.get("rating_changes", []) if c})) or "—"
    tmpl = f"{it.get('org_count', 0)} 家机构覆盖，评级方向：{direction}"
    parts = []
    # 简要观点 = 真实研报标题（事实层，出处=机构报告名）。采集层照取，渲染出口过一道红线兜底：
    # 命中（目标价/买入等）即整条丢，不 surface、不中性化（保留报告原名）；实测真实标题基本不命中。
    vp = it.get("viewpoint") or {}
    vp_title = _safe_text(vp.get("title"), "A股观点标题")
    if vp_title:
        vp_title = _emphasize_hints(vp_title)  # 强提示词就地加粗（重点推荐/重点关注…，鞠磊②）
        src = vp.get("institution")
        parts.append(f"观点：「{vp_title}」" + (f"（{src}）" if src else ""))
    extra = _narration(it)  # Antigravity 叙事（A股默认关，通常空；若开则已扫红线）
    if extra:
        parts.append(extra)
    tail = " — " + " ｜ ".join(parts) if parts else ""
    badge = _badges(it.get("signals"))
    badge_seg = f" {badge}" if badge else ""  # 信号徽章紧跟代码，显著突出（鞠磊 #1 首次覆盖）
    return f"- **{name}**（{it.get('stock_code')}）{badge_seg}：{tmpl}{tail}"


def _us_line(it: dict) -> str:
    org = it.get("firm") or "?"
    chg = neutralize_rating(it.get("to_grade"))
    act = {"up": "上调", "down": "下调", "init": "首次覆盖", "reinit": "重启覆盖"}.get(
        str(it.get("action", "")).lower(), "调整")
    tmpl = f"{org} {act}评级至「{chg}」（{it.get('grade_date', '')}）"
    extra = _narration(it)  # 美股板块归类 theme（〔...〕）
    tail = f" — {extra}" if extra else ""
    badge = _badges(it.get("signals"))
    badge_seg = f" {badge}" if badge else ""  # 美股信号徽章（init→🆕首次覆盖 最突出）
    return f"- **{it.get('ticker')}**{badge_seg}：{tmpl}{tail}"


def _top_line(rank: int, it: dict) -> str:
    if it.get("market") == "US":
        who = f"[美股] {it.get('ticker')} · {it.get('firm', '')}"
        base = f"{ {'up':'上调','down':'下调','init':'首次覆盖','reinit':'重启覆盖'}.get(str(it.get('action','')).lower(),'调整') }评级"
    else:
        who = f"[A股] {it.get('stock_name') or it.get('stock_code')}"
        base = f"{it.get('org_count', 0)} 家机构覆盖"
    # 鞠磊框架信号标签（首次覆盖/评级上调/多家覆盖）—— 点出"为什么值得读"
    sig_tag = _badges(it.get("signals"))  # 显著徽章（emoji+加粗），首次覆盖最突出
    # 美股追加 LLM theme（板块归类，one_liner 冗余已砍）；A股追加真实研报观点标题——出口都过红线兜底
    if it.get("market") == "US":
        extra = _safe_text(it.get("theme"), "LLM theme")
    else:
        extra = _emphasize_hints(_safe_text((it.get("viewpoint") or {}).get("title"), "A股观点标题"))
    tail = f"：{extra}" if extra else ""
    return f"{rank}. **{who}** {sig_tag} — {base}{tail}"


def _industry_line(cn_industry: list[dict]) -> str:
    """行业覆盖热度单行：「银行 2只/6篇 · 机械设备 3只/3篇 …还有 N 个」。"""
    from services.research_digest.collector import INDUSTRY_DISPLAY_CAP
    shown = cn_industry[:INDUSTRY_DISPLAY_CAP]
    parts = [f"{b['industry']} {b['stock_count']}只/{b['report_count']}篇" for b in shown]
    line = " · ".join(parts)
    extra = len(cn_industry) - len(shown)
    if extra > 0:
        line += f" …还有 {extra} 个"
    return line


def _huibo_lines(huibo_digest: dict | None) -> list[str]:
    if not huibo_digest:
        return []
    L: list[str] = []
    has_rendered_content = False

    recs_raw = huibo_digest.get("recommendations") or []
    recs = [rec for rec in recs_raw if isinstance(rec, dict)] if isinstance(recs_raw, list) else []
    if recs:
        has_rendered_content = True
        rendered_recs = recs
        L.append(f"\n## 📚 慧博深读 Top{len(rendered_recs)}")
        for i, rec in enumerate(rendered_recs, 1):
            title = _safe_text(rec.get("title"), "慧博推荐标题") or "未命名研报"
            reason = _safe_text(rec.get("reason"), "慧博推荐理由")
            source = _safe_text(rec.get("source"), "慧博推荐来源")
            tail = f" — {reason}" if reason else ""
            src = f"（{source}）" if source else ""
            L.append(f"{i}. **{title}**{src}{tail}")

    industry_raw = (huibo_digest.get("industry_summary") or {}).get("industries") or []
    industry = industry_raw if isinstance(industry_raw, list) else []
    if industry:
        has_rendered_content = True
        L.append("\n## 🧭 慧博行业观点聚合")
        for item in industry[:8]:
            if not isinstance(item, dict):
                continue
            name = _safe_text(item.get("industry"), "慧博行业") or "未分类"
            viewpoint = _safe_text(item.get("viewpoint") or item.get("consensus"), "慧博行业观点")
            sources_raw = item.get("sources") or []
            sources = sources_raw if isinstance(sources_raw, list) else []
            safe_sources = [
                safe
                for s in sources[:3]
                if (safe := _safe_text(s, "慧博行业来源"))
            ]
            src = f"｜来源：{'；'.join(safe_sources)}" if safe_sources else ""
            L.append(f"- **{name}**：{viewpoint or '—'}{src}")

    stock_lines = []
    for report in huibo_digest.get("reader_results") or []:
        if not isinstance(report, dict):
            continue
        reader = report.get("reader") or {}
        if not isinstance(reader, dict):
            continue
        title = _safe_text(report.get("title"), "慧博报告标题")
        inst = _safe_text(report.get("institution"), "慧博报告机构")
        date = _safe_text(report.get("date"), "慧博报告日期")
        stocks_raw = reader.get("mentioned_stocks") or []
        stocks = stocks_raw if isinstance(stocks_raw, list) else []
        for stock in stocks:
            if not isinstance(stock, dict):
                continue
            name = _safe_text(stock.get("name"), "慧博个股名称")
            if not name:
                continue
            viewpoint = _safe_text(stock.get("viewpoint"), "慧博个股观点")
            source = _safe_text(stock.get("source"), "慧博个股来源")
            meta = " ".join(part for part in (inst, date) if part)
            suffix = f"（{meta}）" if meta else ""
            stock_lines.append(
                f"- **{name}**：{viewpoint or '—'}｜来源：{source or title or '—'}{suffix}"
            )
    if stock_lines:
        has_rendered_content = True
        L.append("\n## 🧾 慧博提及个股")
        L.extend(stock_lines[:12])

    changes_raw = (huibo_digest.get("trend_summary") or {}).get("changes") or []
    changes = changes_raw if isinstance(changes_raw, list) else []
    if changes:
        has_rendered_content = True
        L.append("\n## 🔄 慧博近5交易日热点变化")
        for change in changes[:6]:
            text = _safe_text(change, "慧博热点变化")
            if text:
                L.append(f"- {text}")

    if not has_rendered_content:
        reader_results_raw = huibo_digest.get("reader_results") or []
        prescreened_raw = huibo_digest.get("prescreened") or []
        reader_results = reader_results_raw if isinstance(reader_results_raw, list) else []
        prescreened = prescreened_raw if isinstance(prescreened_raw, list) else []
        if reader_results or prescreened:
            success = sum(
                1
                for r in reader_results
                if isinstance(r, dict)
                and isinstance(r.get("reader"), dict)
                and not r["reader"].get("error")
            )
            failed = max(0, len(reader_results) - success)
            L.append("\n## 📚 慧博深读")
            L.append(f"- 已采集候选 {len(prescreened)} 篇；Antigravity 阅读成功 {success} 篇，失败 {failed} 篇；未生成推荐。")
            failed_titles = [
                _safe_text(r.get("title"), "慧博失败报告标题")
                for r in reader_results
                if isinstance(r, dict)
                if isinstance(r.get("reader"), dict) and r["reader"].get("error")
            ]
            for title in [t for t in failed_titles if t][:3]:
                L.append(f"- Reader 未完成：{title}")

    return L


def render_md(date: str, cn_items: list[dict], us_items: list[dict], top3: list[dict],
              *, cn_industry: list[dict] | None = None,
              huibo_digest: dict | None = None) -> tuple[str, str]:
    """返回 (title, markdown)。纯函数，不落盘（落盘见 write_md）。"""
    title = f"研报速读 · {date}"
    L = [f"# {title}", ""]
    L.append(f"> 窗口 {date}（最近交易日）｜A股 {len(cn_items)} 标的覆盖 ｜ 美股 {len(us_items)} 条评级变动")

    L.append("\n## 🏆 Top3 最值得读")
    if top3:
        for i, it in enumerate(top3, 1):
            L.append(_top_line(i, it))
    else:
        L.append("- 今日两市均无符合条件的评级变动")

    if cn_industry:
        L.append("\n## 📊 行业覆盖热度")
        L.append(_industry_line(cn_industry))

    L.extend(_huibo_lines(huibo_digest))

    L.append("\n## 🇨🇳 A股机构评级")
    if cn_items:
        for it in cn_items[:CN_DISPLAY_CAP]:
            L.append(_cn_line(it))
    else:
        L.append("- 今日 A股无研报评级数据")

    L.append("\n## 🇺🇸 美股评级变动")
    if us_items:
        for it in us_items[:_ITEMS_CAP]:
            L.append(_us_line(it))
    else:
        L.append("- 今日美股无符合条件的评级变动")

    L.append("\n---")
    L.append("> 本报告基于公开机构评级数据整理，不构成任何买卖建议，不预测价格目标。")
    return title, "\n".join(L)


def write_md(markdown: str, date: str, *, out_root: str = _OUT_ROOT_DEFAULT) -> str:
    """落盘 data/reports/research-digest/YYYY-MM-DD.md。

    H4：目录被 .gitignore 且全仓无写盘先例，必须显式 mkdir(parents=True) 否则 launchd 首跑炸。
    out_root 可注入（测试用 tmp_path，不硬编码 data/reports）。
    """
    path = Path(out_root) / f"{date}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)  # L2：mkdir 纳入 try，失败带路径日志
        path.write_text(markdown, encoding="utf-8")
    except OSError as e:
        logger.error("[research-digest] 落盘失败 %s: %s", path, e)
        raise
    return str(path)
