"""板块相关性 markdown 渲染（钉钉手机端友好：列表为主，不堆大表）。

daily：板块×大盘关系（重点高亮逆向）+ 联动榜 + 反向榜(双窗对照) + 方法学/红线脚注。
matrix：打印更全的逐窗矩阵（命令行细看）。trend：跨日漂移摘要。
"""
from __future__ import annotations

# 超额强逆向阈值（与 analyzer 一致）：判定双窗"稳定跷跷板"
_EXCESS_STRONG_INV = -0.4


def _sign(v) -> str:
    if v is None:
        return "—"
    return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"


def _primary_window(record: dict) -> int:
    return max(record.get("windows") or [60])


def _pair_key(p: dict) -> tuple:
    return tuple(sorted((p["a"], p["b"])))


def _pair_lookup(pairs: list[dict]) -> dict:
    return {_pair_key(p): p["corr"] for p in pairs}


def format_daily_report(record: dict, top_k: int = 8) -> str:
    date = record["date"]
    windows = record.get("windows") or [60]
    pw = _primary_window(record)
    base = record.get("base_index", "")
    sample = record.get("sample_days", {})
    L: list[str] = []

    L.append(f"## 板块相关性 · {date}")
    L.append(
        f"- 窗口 {windows} | 样本 {sample} 日 | 板块 {record.get('top_n')} 个 "
        f"| 对标 {len(record.get('indices', []))} 指数（基准 {base}）"
    )
    L.append("")

    # 板块 × 大盘：重点列逆向（A股稀少、最有信息量）+ 高弹性同向
    si = (record.get("sector_index") or {}).get(str(pw), {})
    inverse, high_beta = [], []
    for name, by_idx in si.items():
        cell = by_idx.get(base) or {}
        label, b = cell.get("label"), cell.get("beta")
        if label in ("逆向", "弱逆向", "强逆向"):
            inverse.append(f"{name}（{_sign(cell.get('raw_corr'))}, β{_sign(b)}）")
        elif b is not None and b >= 1.2:
            high_beta.append(f"{name}（β{_sign(b)}）")
    L.append(f"### 📊 与大盘（{base}）关系 · {pw}日窗")
    L.append(f"- 逆向板块：{('、'.join(inverse)) if inverse else '本期无明显逆向'}")
    if high_beta:
        L.append(f"- 高弹性同向（β≥1.2）：{'、'.join(high_beta[:top_k])}")
    L.append("")

    # 联动榜（原始相关降序）
    raw_pairs = (record.get("pair_raw") or {}).get(str(pw), [])
    L.append(f"### 🤝 联动榜 · {pw}日（原始相关）")
    if raw_pairs:
        for p in raw_pairs[:top_k]:
            L.append(f"- {p['a']} ⟷ {p['b']}  {_sign(p['corr'])}")
    else:
        L.append("- （样本不足，无可用对）")
    L.append("")

    # 反向榜（超额相关）⭐；双窗时给 短/长 对照 + 稳定/近期 标签，单窗退单值（review L1）
    short_w = min(windows)
    dual = len(windows) >= 2 and short_w != pw
    L.append("### ⚖️ 反向榜（剔除大盘超额" + ("· 双窗对照）" if dual else "）"))
    excess_pw = (record.get("pair_excess") or {}).get(str(pw), [])
    excess_short = _pair_lookup((record.get("pair_excess") or {}).get(str(short_w), [])) if dual else {}
    listed = 0
    for p in excess_pw[:top_k] if excess_pw else []:
        if p["corr"] is None or p["corr"] >= 0:
            continue  # 反向榜只列负相关
        if dual:
            sv = excess_short.get(_pair_key(p))
            both = sv is not None and sv <= _EXCESS_STRONG_INV and p["corr"] <= _EXCESS_STRONG_INV
            tag = "稳定" if both else "近期"
            L.append(f"- {p['a']} ⟷ {p['b']}  {short_w}日 {_sign(sv)} / {pw}日 {_sign(p['corr'])}  [{tag}]")
        else:
            L.append(f"- {p['a']} ⟷ {p['b']}  {pw}日 {_sign(p['corr'])}")
        listed += 1
    if listed == 0:
        L.append("- （无显著反向对）")
    L.append("")

    L.append(f"> 窗口 {windows} 日、样本 {sample} 日；超额=剔除 {base} 后的残差相关。")
    L.append("> 相关为同期统计共现，**非因果、非买卖建议**（仅供复盘参考）。")
    return "\n".join(L)


def format_matrix(record: dict) -> str:
    """逐窗打印 板块×指数 与 板块×板块（命令行细看，不推送）。"""
    L = [f"# 板块相关性矩阵 · {record['date']}"]
    for w in record.get("windows", []):
        sw = str(w)
        L.append(f"\n## {w}日窗（样本 {record.get('sample_days', {}).get(sw)} 日）")
        L.append("### 板块 × 指数（raw_corr / β / 判定）")
        for name, by_idx in (record.get("sector_index") or {}).get(sw, {}).items():
            cells = "  ".join(
                f"{idx}:{_sign(c.get('raw_corr'))}/β{_sign(c.get('beta'))}/{c.get('label')}"
                for idx, c in by_idx.items()
            )
            L.append(f"- {name}: {cells}")
        L.append("### 联动榜（原始相关 Top10）")
        for p in (record.get("pair_raw") or {}).get(sw, [])[:10]:
            L.append(f"- {p['a']} ⟷ {p['b']}  {_sign(p['corr'])}")
        L.append("### 反向榜（超额相关 Top10）")
        for p in (record.get("pair_excess") or {}).get(sw, [])[:10]:
            L.append(f"- {p['a']} ⟷ {p['b']}  {_sign(p['corr'])}")
    return "\n".join(L)


def format_trend(records: list[dict]) -> str:
    """跨日漂移：每日板块数 / 样本数 / 60日窗强逆向对数。"""
    if not records:
        return "（无历史快照）"
    L = ["# 板块相关性趋势"]
    for r in records:
        pw = str(_primary_window(r))
        strong_inv = sum(
            1 for p in (r.get("pair_excess") or {}).get(pw, [])
            if p.get("corr") is not None and p["corr"] <= _EXCESS_STRONG_INV
        )
        L.append(
            f"- {r['date']}: 板块{r.get('top_n')} 样本{r.get('sample_days', {}).get(pw)}日 "
            f"强逆向对{strong_inv}"
        )
    return "\n".join(L)
