"""七天横盘不创新低全量 Markdown/HTML 与 JSON 报告渲染及原子落盘。"""
from __future__ import annotations

import datetime
import html
import json
import os
import pathlib
import tempfile

from services.no_new_low import constants as C


def _cell(value) -> str:
    return str(value or "").replace("|", "｜").replace("\n", " ")


def _num(value, digits: int = 2, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _ordered_pool(result: dict, pk_result: dict | None) -> list[dict]:
    pool = result.get("pk_pool") or []
    by_code = {item.get("code"): item for item in pool}
    order = pk_result.get("order") if pk_result and pk_result.get("status") == "ok" else None
    if order:
        return [by_code[code] for code in order if code in by_code]
    return pool


def _ranking_lines(result: dict, pk_result: dict | None) -> list[str]:
    pool = _ordered_pool(result, pk_result)
    if not pool:
        return ["本次为真实空候选：全市场硬源与覆盖门均通过，但没有股票满足机械条件。"]
    ranks = pk_result.get("ranks") if pk_result and pk_result.get("status") == "ok" else {}
    wins = pk_result.get("wins") if pk_result else {}
    lines = [
        "| PK名次[判断] | 横盘事实名次[事实] | 股票 | 申万二级[事实] | 基准日 | 8日总振幅 | 最大收盘偏离 | 期末净漂移 | 后7根最低垫高 | 量比 | T日成交额 | PK胜场 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in pool:
        code = item.get("code", "")
        rank = ranks.get(code, "—")
        lines.append(
            f"| {rank} | {item.get('horizontal_rank', '—')} | {_cell(item.get('name'))}（{code}） "
            f"| {_cell(item.get('industry') or '未分类')} | {item.get('anchor_date', '—')} "
            f"| {_num(item.get('base_range_pct'), suffix='%')} "
            f"| {_num(item.get('max_close_deviation_pct'), suffix='%')} "
            f"| {_num(item.get('window_return_pct'), suffix='%')} "
            f"| {_num(item.get('low_floor_margin_pct'), suffix='%')} "
            f"| {_num(item.get('volume_ratio'))} | {_num(item.get('today_amount_yi'), suffix='亿')} "
            f"| {wins.get(code, '—')} |"
        )
    return lines


def _sector_groups(result: dict) -> list[tuple[str, list[dict]]]:
    """全量候选按行业、代码排列供查阅，顺序不代表质量或推荐。"""
    groups: dict[str, list[dict]] = {}
    for item in result.get("candidates") or []:
        groups.setdefault(item.get("industry") or "未分类", []).append(item)
    return [(sector, sorted(groups[sector], key=lambda item: str(item.get("code", ""))))
            for sector in sorted(groups, key=lambda sector: (sector == "未分类", sector))]


def _candidate_values(item: dict) -> list[str]:
    return [str(item.get("name") or "—"), str(item.get("code") or "—"),
            str(item.get("anchor_date") or "—"),
            _num(item.get("base_range_pct"), suffix="%"),
            _num(item.get("max_close_deviation_pct"), suffix="%"),
            _num(item.get("window_return_pct"), suffix="%"),
            _num(item.get("volume_ratio")), _num(item.get("today_amount_yi"), suffix="亿")]


_CANDIDATE_HEADERS = ["股票", "代码", "基准日", "8日总振幅", "最大收盘偏离", "期末净漂移", "量比", "T日成交额"]


def _candidate_lines(result: dict) -> list[str]:
    lines = ["## 全量合格名单 · 按板块查阅", "",
             "[事实] 板块内按代码排列，仅便于查找，不代表综合质量排名。搜索版见同名 HTML。", ""]
    groups = _sector_groups(result)
    if not groups:
        lines.append("全市场硬源与覆盖门已通过，本次没有股票满足机械条件。")
    for sector, items in groups:
        lines.extend([f"### {_cell(sector)} · {len(items)} 只", "",
                      "| " + " | ".join(_CANDIDATE_HEADERS) + " |",
                      "| " + " | ".join(["---"] * len(_CANDIDATE_HEADERS)) + " |"])
        lines.extend("| " + " | ".join(_cell(v) for v in _candidate_values(item)) + " |" for item in items)
        lines.append("")
    return lines


def render_daily(result: dict, pk_result: dict | None, *, include_match_reasons: bool = True,
                 include_candidates: bool = True) -> str:
    date = result.get("date", "")
    industry = result.get("industry") or {}
    industry_status = industry.get("status", "unknown")
    industry_rows = int(industry.get("candidate_rows") or 0)
    industry_matched = int(industry.get("matched_rows") or 0)
    if industry_status in {"ok", "partial"}:
        industry_line = (
            f"- 板块标签：申万二级当前成分快照（仅展示，不参与筛选/PK）；"
            f"状态={industry_status}，候选覆盖 {industry_matched}/{industry_rows} "
            f"({float(industry.get('coverage') or 0):.2%})"
        )
    elif industry_status == "skipped":
        industry_line = "- 板块标签：无候选，申万二级映射跳过"
    else:
        industry_line = (
            f"- 板块标签：source_failed，候选统一显示“未分类”；"
            f"错误={_cell(industry.get('error') or '未提供板块来源状态')}"
        )
    lines = [
        f"# 七天横盘不创新低观察清单 · {date}",
        "",
        f"> [事实·机械筛选] 每只股票最近 8 根有效 K 线中，第 1 根为基准；后续 7 根前复权最低价均未严格跌破基准，并同时满足总振幅≤{C.MAX_BASE_RANGE_PCT:g}%、收盘最大偏离≤{C.MAX_CLOSE_DEVIATION_PCT:g}%、期末净漂移≤±{C.MAX_ABS_WINDOW_RETURN_PCT:g}%。停牌日不占 K 线。",
        "> [判断] 两两 PK 只比较横盘收敛、低点守稳与量能平稳，不奖励上涨幅度；不是买卖建议，不预测价格，不写关注池或计划层。",
        "",
        "## 运行摘要",
        "",
        f"- 全市场身份：{result.get('universe_count', 0)} 只",
        f"- 机械命中：{result.get('candidate_count', 0)} 只（全量按板块见 Markdown / 可搜索 HTML，原始明细见同名 JSON）",
        f"- 极窄横盘附表：按横盘收敛事实取前 {result.get('pk_pool_count', 0)} 只，T 日成交额仅作最后破平；其余 {result.get('pk_excluded_count', 0)} 只仍属合格候选",
        industry_line,
        f"- PK 状态：{pk_result.get('status') if pk_result else '未运行（--no-llm）'}",
        "",
        *(_candidate_lines(result) if include_candidates else [
            "本文为摘要，未列出全量候选。完整名单请查看本地 data/reports/no-new-low/ 同日 .html / .md；可搜索名称、代码和板块。", ""]),
        "## 附表：极窄横盘前 12 只及两两 PK",
        "",
        "附表名次仅衡量横盘收敛，不代表股票综合质量；未进附表不等于未通过筛选。",
        "",
        *_ranking_lines(result, pk_result),
        "",
    ]
    if include_match_reasons:
        lines.extend(["## PK 对局理由（[判断]）", ""])
        if pk_result is None:
            lines.append("PK 未运行；附表仅展示横盘事实排序，全量候选不受影响。")
        elif pk_result.get("status") == "skipped":
            lines.append("候选不足 2 只，PK 跳过。")
        elif pk_result.get("status") == "melted":
            lines.append(
                f"PK 熔断：已打 {pk_result.get('attempted', 0)}/{pk_result.get('total', 0)} 场，"
                f"无效 {pk_result.get('invalid', 0)} 场；不输出伪完整名次。"
            )
        else:
            for match in pk_result.get("matches") or []:
                if match.get("state") == "valid":
                    lines.append(
                        f"- {match['a']} vs {match['b']} → {match['winner']}：{_cell(match.get('reason'))}"
                    )
        lines.append("")

    lines.extend(["## 数据完整性", ""])
    target_coverage = (result.get("coverage_by_date") or {}).get(date, {})
    if target_coverage:
        lines.append(
            f"- T 日行情/复权 join：{target_coverage.get('joint_rows', 0)} 行；"
            f"目标宇宙覆盖 {target_coverage.get('target_universe_coverage', 0):.2%}"
        )
    lines.append(f"- 取数窗口：{len(result.get('snapshot_dates') or [])} 个市场开放日")
    rejects = result.get("rejects") or {}
    if rejects:
        lines.append("- 未纳入统计：" + "；".join(f"{key}={value}" for key, value in rejects.items()))
    if pk_result is not None:
        lines.append(
            f"- PK：状态={pk_result.get('status')}，已打={pk_result.get('attempted', 0)}/"
            f"{pk_result.get('total', 0)}，有效率={pk_result.get('valid_ratio', 0):.0%}"
        )
    return "\n".join(lines)


def render_source_failed(result: dict) -> str:
    lines = [
        f"# 七天横盘不创新低·数据失败 · {result.get('date', '')}", "",
        "> 全市场硬源或覆盖门未通过，本次不代表没有候选；请修复数据源后按目标日补跑。",
        "", "## 失败明细", "",
    ]
    for source, error in (result.get("failed_sources") or {}).items():
        lines.append(f"- {_cell(source)}：{_cell(error)}")
    return "\n".join(lines)


def render_push(result: dict, pk_result: dict | None) -> str:
    compact = render_daily(result, pk_result, include_match_reasons=False, include_candidates=False)
    if len(compact.encode("utf-8")) <= C.PUSH_BODY_MAX_BYTES:
        return compact
    # 理论上 12 行强池远低于预算；防未来列扩张时整块截断并明确指向本地全量产物。
    suffix = "\n\n> 钉钉正文超过预算已截断；完整报告见 data/reports/no-new-low/。"
    budget = C.PUSH_BODY_MAX_BYTES - len(suffix.encode("utf-8"))
    encoded = compact.encode("utf-8")[:budget]
    while True:
        try:
            prefix = encoded.decode("utf-8")
            break
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return prefix + suffix


def _atomic_write(path: pathlib.Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    tmp_path = pathlib.Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def render_html(result: dict, pk_result: dict | None) -> str:
    """离线可搜索的全量名单；来源失败时不生成合格名单或空池断言。"""
    escape = html.escape
    date = escape(str(result.get("date") or ""))
    failed = result.get("status") == "source_failed"
    summary = render_source_failed(result) if failed else render_daily(
        result, pk_result, include_candidates=False)
    # 摘要留存完整口径、覆盖与 PK 状态；全量表是页面主入口。
    summary = summary.replace(
        "本文为摘要，未列出全量候选。完整名单请查看本地 data/reports/no-new-low/ 同日 .html / .md；可搜索名称、代码和板块。",
        "全量候选见本页上方；下方仅为极窄横盘附表及运行证据。")
    sections = []
    groups = [] if failed else _sector_groups(result)
    for sector, items in groups:
        rows = []
        for item in items:
            search = " ".join([str(item.get("name") or ""), str(item.get("code") or ""), sector])
            cells = "".join(f"<td>{escape(v)}</td>" for v in _candidate_values(item))
            rows.append(f'<tr data-search="{escape(search, quote=True)}">{cells}</tr>')
        headers = "".join(f"<th scope=col>{escape(v)}</th>" for v in _CANDIDATE_HEADERS)
        sections.append(f'<section class="sector"><h2>{escape(sector)} <small>{len(items)} 只</small></h2>'
                        f'<div class="table-scroll"><table><thead><tr>{headers}</tr></thead>'
                        f'<tbody>{"".join(rows)}</tbody></table></div></section>')
    count = sum(len(items) for _, items in groups)
    if failed:
        controls = '<p class="notice">数据源失败，本次不代表没有候选。</p>'
    else:
        controls = (f'<p class="notice">[事实] 全量合格 {count} 只 · {len(groups)} 个板块。'
                    '申万二级为当前成分快照，板块内按代码排列，顺序不代表综合质量。</p>'
                    '<label for="search">查找股票或板块</label>'
                    '<input id="search" type="search" placeholder="输入名称、代码或板块，例如：药明康德" autocomplete="off">'
                    f'<p id="count" role="status" aria-live="polite">显示 {count} / {count} 只</p>'
                    '<p id="empty" hidden>没有匹配结果，请更换搜索词。</p>')
        if not count:
            controls += '<p>全市场硬源与覆盖门已通过，本次没有股票满足机械条件。</p>'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>七天横盘不创新低 · {date}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f3f5f8;color:#203047;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1240px;margin:auto;padding:32px 20px}}h1{{margin-bottom:8px;font-size:30px}}h2{{font-size:21px;margin:0 0 12px}}
.subtitle,small{{color:#5b687b}}small{{font-size:14px;font-weight:normal;margin-left:8px}}
.notice{{background:#e7eef9;padding:16px;border-radius:10px}}label{{display:block;font-weight:600}}
input{{width:100%;padding:13px;font:inherit;border:1px solid #a7b4c6;border-radius:8px;margin-top:6px}}
.sector,details{{background:white;border:1px solid #dce2ea;border-radius:12px;padding:20px;margin:18px 0}}
.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap;font-variant-numeric:tabular-nums}}
th,td{{padding:10px 13px;text-align:right;border-bottom:1px solid #e9edf2}}th{{color:#5b687b;background:#f8fafc;font-size:14px}}
th:first-child,td:first-child{{text-align:left}}tbody tr:hover{{background:#eef4fc}}summary{{cursor:pointer;font-weight:600}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.7 system-ui,sans-serif}}[hidden]{{display:none!important}}
@media(max-width:600px){{main{{padding:18px 12px}}h1{{font-size:24px}}.sector{{padding:12px}}}}
</style></head><body><main><h1>七天横盘不创新低</h1>
<p class="subtitle">{date} · 全量名单按板块查阅</p>
<p>最近 8 根有效 K 线中，后 7 根前复权最低价不低于首根基准；总振幅≤{C.MAX_BASE_RANGE_PCT:g}%、最大收盘偏离≤{C.MAX_CLOSE_DEVIATION_PCT:g}%、期末净漂移≤±{C.MAX_ABS_WINDOW_RETURN_PCT:g}%。停牌日不占根数。</p>
<p>仅为条件筛选观察，不构成买卖建议。极窄横盘前 12 只单列附表。</p>
{controls}{''.join(sections)}
<details{' open' if failed else ''}><summary>极窄横盘附表、筛选口径与数据完整性（状态：{escape(str(result.get('status') or 'unknown'))}；板块：{escape(str((result.get('industry') or {}).get('status') or 'unknown'))}；PK：{escape(str((pk_result or {}).get('status') or '未运行'))}）</summary><pre>{escape(summary)}</pre></details>
</main><script>
const input=document.getElementById('search');
if(input){{
 const rows=[...document.querySelectorAll('tr[data-search]')];
 input.addEventListener('input',()=>{{
  const query=input.value.trim().toLocaleLowerCase();let shown=0;
  rows.forEach(row=>{{row.hidden=!row.dataset.search.toLocaleLowerCase().includes(query);if(!row.hidden)shown++;}});
  document.querySelectorAll('.sector').forEach(section=>{{
   const sectorRows=[...section.querySelectorAll('tbody tr')];
   const visible=sectorRows.filter(row=>!row.hidden).length;section.hidden=visible===0;
   section.querySelector('small').textContent=query ? `${{visible}} / ${{sectorRows.length}} 只` : `${{sectorRows.length}} 只`;
  }});
  document.getElementById('count').textContent=`显示 ${{shown}} / ${{rows.length}} 只`;
  document.getElementById('empty').hidden=shown>0 || query==='';
 }});
}}
</script></body></html>'''


def save_artifacts(
    markdown: str, result: dict, pk_result: dict | None, date: str,
    *, out_root: str = "data/reports/no-new-low",
) -> tuple[pathlib.Path, pathlib.Path]:
    try:
        parsed_date = datetime.date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("report date must be YYYY-MM-DD") from exc
    if parsed_date.isoformat() != date:
        raise ValueError("report date must be YYYY-MM-DD")
    root = pathlib.Path(out_root)
    md_path = root / f"{date}.md"
    json_path = root / f"{date}.json"
    receipt = {"date": date, "status": result.get("status"), "scan": result, "pk": pk_result}
    _atomic_write(root / f"{date}.html", render_html(result, pk_result))
    _atomic_write(md_path, markdown)
    _atomic_write(json_path, json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return md_path, json_path
