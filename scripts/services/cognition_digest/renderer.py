"""渲染钉钉 Markdown：概览 + Top-N + 体系/方向建议 + 页脚。"""
from __future__ import annotations

from .windows import WindowSpec


def render_md(spec: WindowSpec, start: str, end: str, scored, stats: dict,
              suggestions: dict, *, llm_used: bool) -> tuple[str, str]:
    title = f"📚 交易认知沉淀·{spec.label}（{start}~{end}）"
    lines = [f"## {title}", ""]
    # stats 用 .get 兜底：上游漏键不应让整条日报渲染崩（codex 中项：测试永远绿但生产静默炸）
    lines.append(
        f"**概览**：活跃认知 {stats.get('active', 0)} 条｜新增 {stats.get('new', 0)}｜"
        f"实例 {stats.get('instances', 0)}｜覆盖老师 {stats.get('teachers', 0)} 位"
    )
    lines.append("")

    if scored:
        lines.append(f"### 🏆 值得沉淀 Top{len(scored)}")
        for i, s in enumerate(scored, 1):
            badge = "🆕" if s.is_new else f"置信{s.confidence:.2f}"
            cat = s.category + (f"·{s.sub_category}" if s.sub_category else "")
            lines.append(f"**{i}. {s.title}**")
            lines.append(f"`{cat}` 🔥{s.heat} 🤝{s.consensus}位 {badge}")
            if s.pattern:
                lines.append(f"> {s.pattern}")
            lines.append("")
    else:
        lines.append("_本窗口无新增认知沉淀。_")
        lines.append("")

    sys_s = suggestions.get("system_suggestions") or []
    dir_s = suggestions.get("direction_suggestions") or []
    if sys_s or dir_s:
        tag = "LLM" if llm_used else "纯结构化"
        lines.append(f"### 🤖 体系与方向建议（{tag}）")
        if sys_s:
            lines.append("**交易体系建议**")
            lines += [f"- {b}" for b in sys_s]
        if dir_s:
            lines.append("**下一步方向建议**")
            lines += [f"- {b}" for b in dir_s]
        lines.append("")

    footer = f"———\n数据源 trade.db ｜ {end} 生成"
    if not llm_used:
        footer += " ｜ 纯结构化"
    lines.append(footer)
    return title, "\n".join(lines)
