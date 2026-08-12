"""情绪核心生命周期 Markdown/JSON 报告。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from services.emotion_leader import constants as C


def _pct(value) -> str:
    return f"{float(value):+.1f}%" if isinstance(value, (int, float)) else "—"


def _short_date(value) -> str:
    text = str(value or "")
    return text[5:7] + text[8:10] if len(text) == 10 else "—"


def render_daily(result: dict, *, max_rows: int = C.DEFAULT_MAX_ROWS) -> str:
    date = result.get("date", "")
    status = result.get("status", "source_failed")
    lines = [
        f"# 情绪核心生命周期监控 · {date}",
        "",
        "> 盘后只读观察 · 数值为 [事实]，波段为 [判断] · 不构成买卖建议、不含价位目标、不写计划层或关注池。",
        "",
        "## 口径",
        f"- {result.get('definition', '')}",
        "- 启动日＝本轮连板第一板；涨幅基准＝启动日前一交易日收盘；最大涨幅使用前复权最高价，区间涨幅使用前复权目标日收盘。",
        f"- 自动归档只影响展示：最后一次涨停超过 {C.ARCHIVE_AFTER_TRADE_DAYS} 个交易日，且距峰值回撤不高于 {C.ARCHIVE_DRAWDOWN_PCT:.0f}%。",
        f"- 波段证据：[判断] 收盘回撤不少于 {C.WAVE_PULLBACK_PCT:.0f}% 后，收复前高确认下一波；仅自低点反弹不少于 {C.WAVE_RECOVERY_PCT:.0f}% 时标候选。",
        "",
        "## 数据状态",
        f"- 状态：**{status}**",
        f"- 生成时间：{result.get('generated_at', '—')}（Asia/Shanghai）",
        f"- 事实来源：{result.get('fact_source', '—')}",
    ]
    coverage = result.get("coverage") or {}
    lines.append(
        f"- 历史覆盖：{coverage.get('loaded_limit_days', 0)}/{coverage.get('expected_open_days', 0)} 个开放日"
    )
    refresh = result.get("refresh") or {}
    if refresh:
        mode_label = {
            "full_initial": "首次全量",
            "full_refresh": "强制全量",
            "incremental": "增量",
        }.get(refresh.get("mode"), str(refresh.get("mode") or "—"))
        previous = refresh.get("previous_report_date") or "无"
        lines.append(
            f"- 指标刷新：{mode_label}；本次 {refresh.get('metric_refresh_count', 0)}/"
            f"{refresh.get('discovered_count', 0)} 只；复用已归档 {refresh.get('cached_archived_count', 0)} 只；"
            f"上期日报 {previous}"
        )
    if status == "source_failed":
        lines += ["- 目标日涨跌停事实不完整，不能生成正常清单。", ""]
    else:
        summary = result.get("summary") or {}
        lines += [
            "",
            "## 汇总 [事实]",
            f"- 活跃 {summary.get('active_count', 0)} 只｜今日涨停 {summary.get('today_limit_up_count', 0)} 只｜"
            f"创新高 {summary.get('new_peak_count', 0)} 只｜跌停 {summary.get('today_limit_down_count', 0)} 只",
            f"- 区间涨幅中位数 {_pct(summary.get('interval_gain_median_pct'))}｜"
            f"距峰值中位数 {_pct(summary.get('distance_from_peak_median_pct'))}",
            "",
            "## 活跃情绪核心",
        ]
        active = result.get("active") or []
        if not active:
            lines += ["当前无可计算的活跃情绪核心。", ""]
        else:
            lines += [
                "| 名称 | 板型/波段[判断] | 题材/行业 | 最大涨幅 | 区间涨幅 | 距峰值 | 启动日 | 高度 | 今日状态 |",
                "| --- | --- | --- | ---: | ---: | ---: | --- | ---: | --- |",
            ]
            for row in active[:max_rows]:
                manual = "·人工" if row.get("manual_confirmed") else ""
                lines.append(
                    f"| {row.get('name')} `{row.get('code')}` | {row.get('board_type')} / "
                    f"{row.get('wave_label', '未计算')}{manual} | {row.get('industry', '未分类')} | "
                    f"{_pct(row.get('max_gain_pct'))} | {_pct(row.get('interval_gain_pct'))} | "
                    f"{_pct(row.get('distance_from_peak_pct'))} | {_short_date(row.get('launch_date'))} | "
                    f"{row.get('max_height') or '—'}板 | {row.get('current_state', '未计算')} |"
                )
            if len(active) > max_rows:
                lines.append(f"\n> 仅展示前 {max_rows} 只；完整 {len(active)} 只保留在 JSON。")
            lines.append("")

        lines += ["## 今日变化"]
        promoted = result.get("promoted_today") or []
        candidates = result.get("new_candidates") or []
        new_peaks = [row for row in active if row.get("new_peak_today")]
        height_breakthrough = result.get("height_breakthrough") or {}
        lines.append("- 晋级核心：" + ("、".join(row["name"] for row in promoted) or "无"))
        lines.append("- 新增二连板候选：" + ("、".join(row["name"] for row in candidates) or "无"))
        lines.append("- 创生命周期新高：" + ("、".join(row["name"] for row in new_peaks) or "无"))
        if height_breakthrough.get("status") == "triggered":
            leader_text = "、".join(
                f"{row.get('name')}（{row.get('current_height')}板，启动日{row.get('launch_date')}）"
                for row in height_breakthrough.get("leaders") or []
            )
            lines.append(
                f"- [事实] 打开高度：{leader_text}；此前"
                f"{height_breakthrough.get('lookback_open_days')}个开放日最高"
                f"{height_breakthrough.get('previous_max_height')}板。"
                "[判断] 上述启动日列为情绪节点日候选。"
            )
        elif height_breakthrough.get("status") == "none":
            lines.append(
                "- [事实] 今日非ST连板最高高度未超过此前"
                f"{height_breakthrough.get('lookback_open_days')}个开放日高度。"
            )
        else:
            lines.append(
                "- [事实] 打开高度证据不完整，本日无法判定启动日节点联动。"
            )
        lines.append("")

    errors = result.get("source_errors") or []
    if errors:
        lines += ["## 数据提示"]
        for error in errors[:20]:
            lines.append(f"- {error}")
        if len(errors) > 20:
            lines.append(f"- 另有 {len(errors) - 20} 条未展开，详见 JSON。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_reports(result: dict, markdown: str, *, root: Path | None = None) -> tuple[Path, Path]:
    repo_root = root or Path(__file__).resolve().parents[3]
    out_dir = repo_root / C.REPORT_DIR
    md_path = out_dir / f"{result['date']}.md"
    json_path = out_dir / f"{result['date']}.json"
    _atomic_write(md_path, markdown)
    _atomic_write(json_path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return md_path, json_path
