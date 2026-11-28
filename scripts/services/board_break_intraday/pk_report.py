"""尾盘及次日复核的逐股观察、PK 名次和逐场理由。"""

from collections import Counter
from html import escape


def _cell(value):
    return escape(" ".join(str(value).splitlines()), quote=False).replace("|", "／")


def _observation(candidate, verdicts):
    verdict = verdicts.get(candidate["code"], {})
    assessment = candidate.get("status") or verdict.get("assessment", "数值初筛")
    reason = candidate.get("reason") or verdict.get("reason", "尚未完成模型判断")
    return _cell(assessment), _cell(reason)


def _observations_table(candidates, verdicts):
    lines = ["", "|代码|名称|状态|截止涨幅%|依据／排除原因|", "|---|---|---|---:|---|"]
    for row in candidates:
        assessment, reason = _observation(row, verdicts)
        lines.append(f'|{_cell(row["code"])}|{_cell(row["name"])}|{assessment}|'
                     f'{_cell(row.get("pct_chg", "—"))}|{reason}|')
    return lines


def _standings(rows, candidates, verdicts):
    rank_counts = Counter(row["rank"] for row in rows)
    lines = ["", "|PK 名次|名称（代码）|胜／负|为什么排在这里|截止涨幅%|观察判断及依据|",
             "|---:|---|---:|---|---:|---|"]
    for row in rows:
        candidate = candidates[row["code"]]
        assessment, reason = _observation(candidate, verdicts)
        rank = row["rank"]
        label = f"{'并列' if rank_counts[rank] > 1 else ''}第{rank}名"
        lines.append(f'|{label}|{_cell(candidate["name"])}（{_cell(row["code"])}）|'
                     f'{row["wins"]}／{row["losses"]}|{_cell(row["reason"])}|'
                     f'{_cell(candidate.get("pct_chg", "—"))}|{assessment}：{reason}|')
    return lines


def _match_details(matches, candidates):
    if not matches:
        return []
    lines = ["", f"<details><summary>逐场 PK 胜负与依据（{len(matches)} 场）</summary>", "",
             "|交手双方|胜出|比较依据|", "|---|---|---|"]
    for match in matches:
        a, b = match["a"], match["b"]
        left = f'{_cell(candidates[a]["name"])}（{_cell(a)}）'
        right = f'{_cell(candidates[b]["name"])}（{_cell(b)}）'
        winner = _cell(candidates[match["winner"]]["name"])
        lines.append(f'|{left} / {right}|{winner}|{_cell(match["reason"])}|')
    return lines + ["", "</details>"]


def render_rows(result):
    """返回 Markdown 行，沿用同批的观察判断和 PK 已核验名次。"""
    candidates = {row["code"]: row for row in result["candidates"]}
    remaining = result["excluded"] + result["unverified"]
    verdicts = {row["code"]: row for row in
                result["evaluation"].get("response", {}).get("candidates", [])}
    pk = result.get("pk")
    if pk is None:
        return _observations_table([*candidates.values(), *remaining], verdicts)

    stage = "14:50 尾盘" if result["stage"] == "tail" else "09:40 开盘复核"
    matches = [match for match in pk.get("matches", []) if match["state"] == "valid"]
    lines = ["", f"## {stage} PK 排名 [判断]", ""]
    if pk["status"] == "complete":
        rows = pk["rows"]
        lines.append(f'{len(rows)} 只数值候选，已完成 {len(matches)} 场交手。'
                     '按总胜场排名，同胜场比较组内交手胜场，仍相同则并列。')
        lines.extend(_standings(rows, candidates, verdicts))
    elif pk["status"] == "not_needed":
        lines.append("本次无数值候选。")
    else:
        reason = pk.get("reason") or "PK 未完成"
        total = pk.get("total")
        count = f' 已完成 {len(matches)}/{total} 场交手。' if total is not None else ""
        lines.append(f"排名未计算。{_cell(reason)}。{count}")
        if candidates:
            lines += ["", "### 数值候选"]
            lines.extend(_observations_table(candidates.values(), verdicts))
    lines.extend(_match_details(matches, candidates))
    if remaining:
        lines += ["", "### 排除及未核验对象"]
        lines.extend(_observations_table(remaining, verdicts))
    return lines
