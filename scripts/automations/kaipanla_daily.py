"""Finalize or replay a day's Kaipanla evidence. Only publish can call DingTalk."""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import math
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from kaipanla_checkpoint import checkpoint_data, commodity_reconcile, commodity_views, write_projection
from kaipanla_etf import analyze_etf
from kaipanla_evidence import (
    ALERT_FIELDS, SHANGHAI, alert_facts, calendar, dated, digest, etf_gate, pct, read,
    require, shanghai, stock_facts, verify_manifest,
)

REPO = Path(__file__).resolve().parents[2]
UI_FILES = {
    "header.csv", "group.csv", "stock.csv", "tail.csv", "category-list.csv", "commodity.csv",
    "alert-headers.csv", "alert-tail.csv", "alert-raw.csv", "etf-market.csv", "etf-flows.csv",
    "etf-trends.csv", "etf-meta.csv",
}
FINAL_FILES = {
    "report.md", "report.html", "coverage-receipt.md", "pending-verification.csv",
    "commodity-snapshots.csv", "sector-snapshots.csv", "stock-snapshots.csv", "market-alerts.csv",
    "etf-market.csv", "etf-flows.csv", "etf-trends.csv",
}
GATES = {
    "stock_collection=complete", "alert_collection=complete", "etf_collection=complete_with_verified_source_gaps",
    "commodity_verification=complete", "pending_verification=0", "publication_eligible=true",
}
PENDING_FIELDS = ["date", "name", "category", "reason"]
REPAIR_FIELDS = ["date", "name", "category", "raw_pct", "view", "read_at", "evidence"]
RUNTIME_FILES = (
    "kaipanla_capture_bridge.py", "kaipanla_checkpoint.py", "kaipanla_daily.py",
    "kaipanla_etf.py", "kaipanla_evidence.py",
)


def runtime_sha256():
    scripts = Path(__file__).resolve().parent
    manifest = "".join(f"{name} {digest(scripts / name)}\n" for name in RUNTIME_FILES)
    return hashlib.sha256(manifest.encode()).hexdigest()


def now():
    return datetime.now(SHANGHAI)


def atomic(path, write):
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            write(handle)
            handle.flush()
            os.fsync(handle.fileno())
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def save(path, content):
    atomic(path, lambda handle: handle.write(content))


def write_csv(path, rows, fields=None):
    fields = fields or (list(rows[0]) if rows else [])

    def write(handle):
        if fields:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    atomic(path, write)


def seal(folder):
    paths = sorted(p for p in folder.iterdir() if p.is_file() and p.name != "artifact-sha256.txt")
    save(folder / "artifact-sha256.txt", "".join(f"{digest(path)}  {path.name}\n" for path in paths))


@contextmanager
def archive_lock(root):
    with (root / ".kaipanla-run.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def check_archive(folder, fact, *, replay=False):
    verify_manifest(folder, "artifact-sha256.txt", FINAL_FILES)
    lines = set((folder / "coverage-receipt.md").read_text(encoding="utf-8").splitlines())
    needed = GATES | {"fact_date=" + fact}
    if replay:
        needed = (needed - {"publication_eligible=true"}) | {"publication_eligible=false", "run_mode=replay"}
    require(needed <= lines and not read(folder / "pending-verification.csv"),
            f"{fact}的四模块验收状态不完整")


def earlier_etf_archives(root, days):
    for day in reversed(days[:-1]):
        folder = root / day
        if not (folder / "etf-flows.csv").is_file():
            continue
        try:
            check_archive(folder, day)
        except (OSError, ValueError, KeyError) as exc:
            print(f"ETF较早归档未通过核验，跳过 {day}：{exc}", file=sys.stderr)
            continue
        yield folder


def read_history_revision(revision, input_hash):
    manifest = verify_manifest(revision, "artifact-sha256.txt", {"source.csv", "corrections.csv", "receipt.md"})
    receipt = set((revision / "receipt.md").read_text(encoding="utf-8").splitlines())
    require(manifest["source.csv"] == input_hash and f"input_sha256={input_hash}" in receipt,
            f"历史修订输入哈希与修订编号不符：{revision.name}")
    corrected = read(revision / "corrections.csv")
    original = read(revision / "source.csv")
    require(f"repaired_count={len(corrected)}" in receipt and
            [{key: row[key] for key in REPAIR_FIELDS} for row in corrected] == original,
            f"历史修订的原值及行数与输入不符：{revision.name}")
    return corrected


def history_repairs(folder):
    """Read sealed revisions. Each repair retains the original day's snapshot and its hash."""
    revisions = folder / "repairs"
    repairs = {}
    if revisions.is_dir():
        for revision in sorted(revisions.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{64}", revision.name):
                continue
            for row in read_history_revision(revision, revision.name):
                repairs.setdefault(row["date"], []).append((row, revision.name))
    return repairs


def repair_identity(row):
    return row["name"], row["category"]


def repaired_row(base, repair):
    require(base.get("status") == "source_pct_unavailable" and not base.get("pct"),
            f"{repair['date']} {repair['name']} 原始行不是已核实缺值")
    detail = repair["view"] == "详情"
    return dict(base, pct=str(pct(repair["raw_pct"])), raw_pct=repair["raw_pct"],
                status="detail_observed" if detail else "observed",
                source=f"开盘啦历史复核 · {repair['view']}", views=repair["view"], read_at=repair["read_at"])


def history_inputs(root, days):
    folder = root / "history"
    receipt = (folder / "coverage-receipt.txt").read_text(encoding="utf-8")
    baseline_path = folder / "commodity-baseline-20d.csv"
    baseline = None
    snapshots, hashes = [], []
    repairs = history_repairs(folder)
    for day in days:
        path = folder / f"{day}.csv"
        if not path.is_file():
            require(not repairs.get(day), f"{day}修订所绑定的原始快照缺失")
            snapshots.append({})
            hashes.append({"date": day, "sha256": "", "repair_revisions": "", "repair_count": 0,
                           "source": "历史快照缺失，所涉窗口留空"})
            continue
        sha = digest(path)
        rows = read(path)
        if not any(f"date={day} " in ln and f"sha256={sha}" in ln and "pending=0" in ln
                   for ln in receipt.splitlines()):
            manifest = root / day / "artifact-sha256.txt"
            if manifest.is_file() and (root / day / "commodity-snapshots.csv").is_file():
                verify_manifest(root / day, manifest.name, {"commodity-snapshots.csv"})
                require(digest(root / day / "commodity-snapshots.csv") == sha, f"{day}快照与历史不一致")
            else:
                if baseline is None:
                    require(f"sha256={digest(baseline_path)}" in receipt, "历史基线哈希未核实")
                    baseline = read(baseline_path)
                require({r["name"]: r for r in rows} == {r["name"]: r for r in baseline if r["date"] == day},
                        f"{day}历史与已核实基线不一致")
        require(all(r["date"] == day for r in rows) and len({r["name"] for r in rows}) == len(rows),
                f"{day}历史日期或品种身份无效")
        values = {repair_identity(row): row for row in rows}
        revisions = set()
        for repair, revision in repairs.get(day, ()):
            require(repair["base_sha256"] == sha, f"{day}历史修订所绑定的基线哈希不符")
            key = repair_identity(repair)
            values[key] = repaired_row(values.get(key, {}), repair)
            revisions.add(revision)
        snapshots.append(values)
        hashes.append({"date": day, "sha256": sha, "repair_revisions": "、".join(sorted(revisions)),
                       "repair_count": len(repairs.get(day, ())), "source": "开盘啦已验收历史快照"})
    return snapshots, hashes


def repair_history(root, input_path, fact, days, actor):
    require(input_path and input_path.is_file(), "历史修订须提供 --repair-input CSV")
    require(fact < now().date().isoformat(), "历史修订须使用已结束的参考事实日")
    input_hash = digest(input_path)
    folder = root / "history"
    revisions = folder / "repairs"
    output = revisions / input_hash
    if output.exists():
        read_history_revision(output, input_hash)
        print(f"同样修订证据已保存 {output}")
        return
    rows = read(input_path)
    require(bool(rows) and all(set(row) == set(REPAIR_FIELDS) for row in rows), "历史修订 CSV 列或记录有误")
    identities = {(row["date"], *repair_identity(row)) for row in rows}
    require(len(identities) == len(rows), "历史修订批次出现同日同品种重复")
    used_days = sorted({row["date"] for row in rows})
    require(set(used_days) <= set(days), "修订日期须为参考事实日前 19 个已核实开放日")
    snapshots, hashes = history_inputs(root, used_days)
    priors = dict(zip(used_days, snapshots))
    known_hashes = {row["date"]: row["sha256"] for row in hashes}
    updates = []
    for row in rows:
        day = row["date"]
        require(all((row[field] or "").strip() for field in REPAIR_FIELDS),
                f"{day} {row['name']} 修订缺少证据")
        require(row["view"] in {row["category"], "精选", "详情"},
                f"{day} {row['name']} 分类身份未核实")
        require(re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?%", row["raw_pct"]),
                f"{day} {row['name']} 涨幅须为页面明确百分比")
        require(shanghai(row["read_at"]).date().isoformat() > day and
                shanghai(row["read_at"]) <= now(), "历史回读时刻未核实")
        excerpt = unicodedata.normalize("NFKC", row["evidence"]).replace("−", "-")
        percentages = re.findall(r"(?<![\d.+-])[+-]?[0-9]+(?:\.[0-9]+)?%", excerpt)
        require(day in row["evidence"] and row["name"] in row["evidence"] and row["raw_pct"] in percentages,
                f"{day} {row['name']} 证据须包含事实日、品种及完整涨幅原值")
        repaired_row(priors[day].get(repair_identity(row), {}), row)
        updates.append({**row, "base_sha256": known_hashes[day]})
    require(digest(input_path) == input_hash, "修订输入在读取期间变化")
    revisions.mkdir(exist_ok=True)
    with TemporaryDirectory(dir=folder) as temp:
        staged = Path(temp) / "revision"
        staged.mkdir()
        shutil.copy2(input_path, staged / "source.csv")
        write_csv(staged / "corrections.csv", updates, REPAIR_FIELDS + ["base_sha256"])
        save(staged / "receipt.md", (
            "# 开盘啦商品历史修订\n\n"
            f"reference_date={fact}\ninput_by={actor}\ninput_sha256={input_hash}\n"
            f"verified_at={now().isoformat(timespec='seconds')}\nrepaired_count={len(updates)}\n"
            f"runtime_sha256={runtime_sha256()}\n"
            "\n来源为开盘啦历史分类或详情。只使用页面明确涨幅，援引原始快照哈希。旧日报及发送回执保留。\n"
        ))
        seal(staged)
        read_history_revision(staged, input_hash)
        staged.rename(output)
    print(f"已保存 {len(updates)} 项历史修订 {output}")


def compound(values):
    return (math.prod(Decimal(1) + value / 100 for value in values) - 1) * 100


def compute_commodity(current, history):
    screening = []
    for row in current:
        key = row["name"], row["category"]
        series = [previous.get(key, {}) for previous in history] + [row]
        values = [
            Decimal(r["pct"]) if r.get("status") in {"observed", "detail_observed"} and r.get("pct") else None
            for r in series
        ]
        require(all(v is None or v.is_finite() for v in values), f"{key}包含无效涨幅")
        latest = values[-1]
        result = {key: row[key] for key in ("name", "category", "pct", "status")}
        rising = []
        for val in reversed(values):
            if val is None or val <= 0:
                break
            rising.append(val)
        rise = compound(rising)
        flags = ["连涨"] if len(rising) >= 3 and rise >= 5 else []
        shorter = len(rising) < len(values) and values[-len(rising) - 1] is not None
        result.update(streak=len(rising) if latest is not None else "",
                      streak_gain=f"{rise:.4f}" if latest is not None else "",
                      streak_boundary="至少" if rising and not shorter else "",
                      day_surge=latest is not None and latest >= 3)
        for n in (5, 10, 20):
            window = values[-n:]
            complete = None not in window
            gain = compound(window) if complete else None
            up = sum(v > 0 for v in window) if complete else None
            result.update({f"complete_{n}": complete, f"gain_{n}": f"{gain:.4f}" if complete else "",
                           f"up_{n}": up if complete else ""})
            if complete and gain >= 5 and up * 5 >= n * 3:
                flags.append(f"{n}日累涨")
        result["hits"] = "、".join(flags)
        screening.append(result)
    return screening


def group_comparison(root, days, groups, fact):
    histories = {}
    for day in days[-4:]:
        folder = root / day
        if not (folder / "sector-snapshots.csv").is_file():
            histories[day] = None
            continue
        verify_manifest(folder, "artifact-sha256.txt", {"sector-snapshots.csv"})
        rows = read(folder / "sector-snapshots.csv")
        require(all(r["事实日期"] == day and r["覆盖"] == "完整分组" for r in rows)
                and len({r["页面分组"] for r in rows}) == len(rows), f"{day}新高分组不可比")
        histories[day] = {r["页面分组"]: int(r["新高股数量"]) for r in rows}
    histories[fact] = {r["name"]: int(r["count"]) for r in groups}
    yesterday, today = histories[days[-1]], histories[fact]
    result = []
    for label in sorted(today.keys() | (yesterday or {}).keys()):
        values = {day: snapshot.get(label, "") if snapshot is not None else "" for day, snapshot in histories.items()}
        a, b, c = list(values.values())[-3:]
        trend = "前日不可比" if yesterday is None else (
            "新增单列" if label not in yesterday else "未独立展示" if label not in today else f"{c-b:+d}"
        )
        continuous = "" not in (a, b, c) and a < b < c and label != "其他"
        result.append({"分组": label, **values, "变化": trend, "持续增加": continuous})
    return result, sum(yesterday.values()) if yesterday is not None else None


def stock_summary(stocks, group_compare, total, new, old_total, fact):
    def names(state):
        return "、".join(r["分组"] for r in group_compare if r["变化"] == state and r["分组"] != "其他")

    priorities = [r for r in group_compare if r["持续增加"]]
    priorities.sort(key=lambda r: r[fact], reverse=True)
    last_three = [field for field in group_compare[0] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", field)][-3:] if group_compare else []
    expansion = "、".join(r["分组"] + "→".join(str(r[d]) for d in last_three) for r in priorities[:2])
    comparison = f"前日{old_total}" if old_total is not None else "前日无可比快照"
    notes = [f"{total}只（{comparison}），新增{new}只。"]
    if expansion:
        notes.append(expansion + "。")
    if names("新增单列"):
        notes.append(names("新增单列") + "新增单列。")
    if names("未独立展示"):
        notes.append(names("未独立展示") + "未独立展示。")
    direction_order = {
        r["分组"]: (r["持续增加"], r["变化"] == "新增单列",
                  int(r["变化"]) if r["变化"].lstrip("+-").isdigit() else 0)
        for r in group_compare if r[fact] != "" and r["分组"] != "其他"
    }
    qualifying = sorted(
        (r for r in stocks if r["category"] in direction_order and (
            direction_order[r["category"]] > (False, False, 0) or r["new"] == "true"
        )),
        key=lambda r: (direction_order[r["category"]], r["new"] == "true", pct(r["raw_pct"])),
        reverse=True,
    )
    by_direction = list({r["category"]: r for r in reversed(qualifying)}.values())[::-1]
    selected = (by_direction + [r for r in qualifying if r not in by_direction])[:3]
    picks = []
    for r in selected:
        growing, added, _ = direction_order[r["category"]]
        reason = "持续扩散" if growing else "新列分组" if added else (
            "分组扩张" if direction_order[r["category"]][2] > 0 else "新增新高"
        )
        new_label = "，新高新增" if r["new"] == "true" and (growing or direction_order[r["category"]][2] > 0) else ""
        picks.append(f"{r['name']}{r['code']}（{r['category']}{reason}{new_label}）")
    if picks:
        notes.append("观察〔判断〕" + "、".join(picks) + "。")
    return "".join(notes)


def mapping_index(root, days, fact, source):
    result = {}
    paths = [root / "reference" / "commodity-a-share-mapping.csv"]
    paths.extend(root / day / "commodity-a-share-mapping.csv" for day in days)
    paths.append(source / "commodity-a-share-mapping.csv")
    for path in paths:
        if path.is_file():
            for row in read(path):
                if row.get("sources", "").startswith("https://") and row.get("checked_at", "9999")[:10] <= fact:
                    result[row["commodity"]] = row
    return result


def commodity_summary(hits, mapping):
    def show(row, info):
        m = mapping.get(row["name"], {})
        business = f"（{m['company']}{m['code'].split('.')[0]}）" if m else ""
        return row["name"].split("（")[0].split("(")[0] + info + business

    single = sorted((r for r in hits if r["day_surge"]), key=lambda r: Decimal(r["pct"]), reverse=True)[:2]
    streak = sorted((r for r in hits if "连涨" in r["hits"] and r not in single),
                    key=lambda r: Decimal(r["streak_gain"]), reverse=True)[:2]
    slow = [r for r in hits if "日累涨" in r["hits"] and r not in single + streak]
    segments = []
    if single:
        segments.append("单日 " + "、".join(show(r, f"{Decimal(r['pct']):+.2f}%") for r in single))
    if streak:
        def streak_line(r):
            return f"{r['streak_boundary']}{r['streak']}日{Decimal(r['streak_gain']):+.2f}%"

        companies = {mapping.get(r["name"], {}).get("code") for r in streak}
        if len(streak) > 1 and len(companies) == 1 and None not in companies:
            segments.append("连涨 " + "、".join(
                r["name"] + streak_line(r) if r is not streak[-1] else show(r, streak_line(r)) for r in streak
            ))
        else:
            segments.append("连涨 " + "、".join(show(r, streak_line(r)) for r in streak))
    if slow:
        def strongest(row):
            qualified = [n for n in (5, 10, 20) if f"{n}日累涨" in row["hits"].split("、")]
            n = max(qualified, key=lambda n: Decimal(row[f"gain_{n}"]))
            return Decimal(row[f"gain_{n}"]), n

        representative = max(slow, key=lambda r: (Decimal(r["pct"]) < 0, strongest(r)))
        gain, n = strongest(representative)
        segments.append("累涨 " + show(
            representative, f"当日{Decimal(representative['pct']):+.2f}%，{n}日{gain:+.2f}%（{representative[f'up_{n}']}日涨）"
        ))
    return "。".join(segments) + "。" if segments else "已核验样本中未命中观察阈值。"


def alert_summary(rows, next_open):
    fired = [r for r in rows if r["section"] == "当日异动" and r["status"] == "触发严重异动"]
    forecast = sorted((r for r in rows if r["section"] == "次日测算"), key=lambda r: pct(r["trigger_pct"]))
    date_label = f"{int(next_open[5:7])}/{int(next_open[8:10])}"
    text = "已触发 " + "、".join(f"{r['name']}{r['code']}" for r in fired[:3]) + "。" if fired else "当日已触发 0 条。"
    if forecast:
        text += date_label + "测算 " + "、".join(f"{r['name']}{r['code']} {r['trigger_pct']}" for r in forecast[:2]) + "。"
    monitoring = [r for r in rows if r["monitor_start"][:10] in {next_open, r["fact_date"]}]
    if monitoring:
        text += "新进监控 " + "、".join(
            f"{r['name']}（{int(r['monitor_start'][5:7])}/{int(r['monitor_start'][8:10])}）" for r in monitoring[:3]
        ) + "。"
    return text


def html_table(rows, columns):
    header = "".join(f"<th scope='col'>{escape(label)}</th>" for _, label in columns)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(r.get(key, '')))}</td>" for key, _ in columns) + "</tr>" for r in rows)
    return f"<div class='table-wrap'><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>"


def html_report(title, sections, detail_sections):
    body = "".join(f"<p><strong>{escape(label)}</strong> {escape(value)}</p>" for label, value in sections)
    detail = "".join(f"<details><summary>{escape(label)}</summary>{value}</details>" for label, value in detail_sections)
    return (
        '<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title><style>body{{max-width:820px;margin:22px auto;padding:0 14px;color:#24344b;"
        "font:16px/1.65 system-ui,sans-serif}h1{font-size:1.6rem}details{border:1px solid #dbe4f0;border-radius:8px;"
        "padding:10px;margin:8px 0}summary{cursor:pointer}.table-wrap{overflow:auto}table{border-collapse:collapse;"
        "width:100%}th,td{padding:7px;border-bottom:1px solid #dbe4f0;text-align:left;min-width:6em}</style>"
        f"<body><h1>{escape(title)}</h1>{body}{detail}</body></html>"
    )


def check_commodity_views(ui, fact):
    evidence = {key: dated(read(ui / f"{key}.csv"), fact, key) for key in ("commodity", "category-list", "tail")}
    if (ui / "one-view-recheck.csv").is_file():
        dated(read(ui / "one-view-recheck.csv"), fact, "商品复核")
    expected, complete = commodity_views(evidence)
    require(len(expected) > 1 and expected == complete
            and {r["view"] for r in evidence["commodity"]} <= expected,
            f"商品分类或精选尾页待核：{'、'.join(sorted(expected - complete))}")


def build_output(folder, root, source, fact, days, next_open, actor, *, mode):
    ui = folder / "ui-resume-evidence"
    shutil.copytree(source / ui.name, ui)
    verify_manifest(ui, "SHA256SUMS", UI_FILES)
    prior = root / days[-1]
    groups, stocks, stock_total, stock_new = stock_facts(ui, fact)
    prior_alerts = None
    if prior.exists():
        check_archive(prior, days[-1])
        prior_codes = {r["code"] for r in read(prior / "stock-snapshots.csv")}
        require({r["code"] for r in stocks} - prior_codes == {r["code"] for r in stocks if r["new"] == "true"},
                "百日新高 NEW 与上一开放日完整名单不一致")
        prior_alerts = read(prior / "market-alerts.csv")
    alerts = alert_facts(ui, fact, next_open, prior_alerts)
    etf_count = etf_gate(ui, fact, days[-1], days[-2])
    etf = analyze_etf(ui, prior if prior_alerts is not None else None, earlier_etf_archives(root, days))
    check_commodity_views(ui, fact)
    current, pending, single_view = commodity_reconcile(ui, fact)
    require(bool(current) and not pending, f"商品待核{len(pending)}项，见采集状态")
    history, history_sha = history_inputs(root, days)
    screen = compute_commodity(current, history)
    hits = [r for r in screen if r["day_surge"] or r["hits"]]
    gaps = [r for r in current if r["status"] == "source_pct_unavailable"]
    coverage = {n: sum(r[f"complete_{n}"] for r in screen) for n in (5, 10, 20)}
    historical_gaps = sum(not r["sha256"] for r in history_sha)
    repaired_count = sum(r["repair_count"] for r in history_sha)
    compare, old_total = group_comparison(root, days, groups, fact)
    mappings = mapping_index(root, days, fact, source)
    hit_maps = [mappings[r["name"]] for r in hits if r["name"] in mappings]
    observed = len(current) - len(gaps)
    title = f"开盘啦日报｜{int(fact[5:7])}月{int(fact[8:10])}日"
    sections = [
        ("百日新高", stock_summary(stocks, compare, stock_total, stock_new, old_total, fact)),
        ("异动", alert_summary(alerts, next_open)),
        ("ETF趋势〔判断〕", etf["summary"]),
        ("商品", commodity_summary(hits, mappings)),
        ("覆盖", f"商品{len(current)}项已核验，{observed}项有涨幅、{len(gaps)}项未显示，"
                 f"20日完整窗{coverage[20]}项，累计近似。"
                 + (f"历史快照缺{historical_gaps}日。" if historical_gaps else "")),
    ]
    stock_cols = [(k, k) for k in (list(compare[0]) if compare else ["分组"])]
    alert_cols = [("section", "分区"), ("name", "名称"), ("code", "代码"), ("status", "状态"),
                  ("actual_pct", "涨幅"), ("trigger_pct", "触发测算"), ("deviation_pct", "偏离值"),
                  ("event_date", "事件日"), ("monitor_start", "监控开始"), ("monitor_end", "监控结束")]
    commodity_cols = [("name", "品种"), ("pct", "当日%"), ("hits", "持续性"),
                      ("streak_label", "连涨天数"), ("streak_gain", "连涨幅度%"),
                      ("gain_5", "5日%"), ("gain_10", "10日%"), ("gain_20", "20日%"),
                      ("company", "A股产业链对应")]
    hit_display = [
        dict(r, streak_label=f"{r['streak_boundary']}{r['streak']}",
             company=mappings[r["name"]]["company"] + " " + mappings[r["name"]]["code"] if r["name"] in mappings else "待核")
        for r in hits
    ]
    details = [
        ("百日新高分组对比", html_table(compare, stock_cols) + '<p><a href="stock-snapshots.csv">个股明细</a></p>'),
        (f"异动明细（{len(alerts)}条）", html_table(alerts, alert_cols)),
        ("ETF榜单与趋势变化", etf["details"]),
        (f"商品命中与映射（{len(hits)}项）", html_table(hit_display, commodity_cols) +
         '<p><a href="commodity-screening.csv">全量筛选</a> · <a href="commodity-a-share-mapping.csv">映射披露依据</a> · '
         '<a href="verified-source-gaps.csv">来源缺值</a></p>'),
    ]
    md = f"# {title}\n\n" + "\n\n".join(f"**{label}** {value}" for label, value in sections) + "\n"
    save(folder / "report.md", md)
    save(folder / "report.html", html_report(title, sections, details))
    write_csv(folder / "sector-comparison.csv", compare)
    write_csv(folder / "stock-snapshots.csv", stocks, list(read_header(ui / "stock.csv")))
    write_csv(folder / "sector-snapshots.csv", [
        {"事实日期": fact, "页面分组": r["name"], "新高股数量": r["count"], "覆盖": "完整分组",
         "来源": "开盘啦按板块完整滚动至尾页", "读取时间": r["read_at"]} for r in groups
    ], ["事实日期", "页面分组", "新高股数量", "覆盖", "来源", "读取时间"])
    write_csv(folder / "market-alerts.csv", alerts, ALERT_FIELDS)
    write_csv(folder / "commodity-snapshots.csv", current)
    write_csv(folder / "commodity-screening.csv", screen)
    write_csv(folder / "confirmed-hits.csv", hits, list(screen[0]))
    write_csv(folder / "commodity-a-share-mapping.csv", hit_maps,
              ["commodity", "company", "code", "relationship", "limitation", "disclosure", "sources", "checked_at", "input_by"])
    write_csv(folder / "history-inputs.csv", history_sha)
    write_csv(folder / "verified-source-gaps.csv", gaps, list(current[0]))
    write_csv(folder / "pending-verification.csv", [], PENDING_FIELDS)
    for file in ("etf-market.csv", "etf-flows.csv", "etf-trends.csv"):
        shutil.copy2(ui / file, folder / file)
    write_csv(folder / "etf-trend-comparison.csv", etf["changes"])
    write_csv(folder / "etf-flow-comparison.csv", etf["flow_comparisons"])
    alert_boundary = "全月边界" if prior_alerts is None else "历史重叠边界"
    save(folder / "market-alert-coverage.md",
         f"# 异动提醒核验\n\nfact_date={fact}\nalert_rows={len(alerts)}\nnext_open={next_open}\n\n六分区已对账，监控边界、证券身份及{alert_boundary}已核实。\n")
    save(folder / "etf-coverage.md",
         f"# ETF核验\n\nfact_date={fact}\netf_rows={etf_count}\n\n日期、范围、尾页和动向证据见 ui-resume-evidence/etf-meta.csv。\n")
    flags = GATES if mode == "live" else (GATES - {"publication_eligible=true"}) | {"publication_eligible=false"}
    summary = "\n".join(sorted(flags))
    delivery_field = "delivery_state=see_receipt" if mode == "live" else "dingtalk_sent=false"
    save(folder / "coverage-receipt.md", f"""# {title}核验回执

input_by={actor}
fact_date={fact}
run_mode={mode}
runtime_sha256={runtime_sha256()}
verified_at={now().isoformat(timespec="seconds")}
{summary}
stock_unique={stock_total}
stock_new={stock_new}
alert_rows={len(alerts)}
etf_rows={etf_count}
commodity_list_count={len(current)}
commodity_pct_available={observed}
verified_source_pct_unavailable={len(gaps)}
complete_5d={coverage[5]}
complete_10d={coverage[10]}
complete_20d={coverage[20]}
confirmed_hits={len(hits)}
commodity_history_missing_days={historical_gaps}
commodity_history_repair_rows={repaired_count}
{delivery_field}

商品复用{len(days)-historical_gaps}个前开放日的已验收归档。{single_view}项单入口空值有同日批量复核记录。来源未显示涨幅保留原值，跨缺口的窗口留空。
""")
    seal(folder)
    check_archive(folder, fact, replay=mode == "replay")


def read_header(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def save_history(root, archive, fact, actor):
    path = root / "history" / f"{fact}.csv"
    incoming = archive / "commodity-snapshots.csv"
    sha = digest(incoming)
    if not path.exists():
        with TemporaryDirectory(dir=path.parent) as temp:
            copy = Path(temp) / path.name
            shutil.copy2(incoming, copy)
            copy.replace(path)
    require(digest(path) == sha, f"{fact}历史已存在不同内容，需要版本化核验")
    receipt = path.parent / "coverage-receipt.txt"
    old = receipt.read_text(encoding="utf-8")
    fields = f"date={fact} input_by={actor} collection_status=complete_with_verified_source_gaps pending=0 sha256={sha}"
    if not any(f"date={fact} " in line and f"sha256={sha}" in line for line in old.splitlines()):
        save(receipt, old.rstrip() + f"\n\n{now().isoformat(timespec='seconds')} {fields}\n")


def publish(root, folder, fact, actor, db_path):
    require(now().date().isoformat() == fact and now().hour >= 21 and calendar(db_path, fact),
            "只发布当日21:00后、日历已核实的正式日报")
    check_archive(folder, fact)
    verify_manifest(folder / "ui-resume-evidence", "SHA256SUMS", UI_FILES)
    report = folder / "report.md"
    sha = digest(report)
    receipt = folder / "delivery-receipt.md"
    if receipt.exists():
        require(f"success=true sha256={sha}" in receipt.read_text(encoding="utf-8"), "已有回执，需人工核对版本")
        print("同日同内容已有钉钉成功回执")
        return
    require("run_mode=live" in (folder / "coverage-receipt.md").read_text(encoding="utf-8").splitlines(),
            "仅发布新版正式归档")
    attempt = folder / "delivery-attempt.md"
    require(not attempt.exists(), "已有发送尝试，须先核对钉钉回执")
    require(digest(root / "history" / f"{fact}.csv") == digest(folder / "commodity-snapshots.csv"), "正式历史尚未归档")
    from dotenv import load_dotenv

    sys.path.insert(0, str(REPO / "scripts"))
    from pushers.dingtalk_pusher import DingTalkPusher

    load_dotenv(REPO / "scripts" / ".env")
    sender = DingTalkPusher()
    require(sender.initialize(), "钉钉配置尚未就绪")
    runtime = runtime_sha256()
    save(attempt, (f"{now().isoformat()} input_by={actor} fact_date={fact} sha256={sha} "
                   f"status=send_started runtime_sha256={runtime}\n"))
    body = report.read_text(encoding="utf-8")
    accepted = sender.send_markdown(body.splitlines()[0].removeprefix("# "), body)
    if not accepted:
        raise ValueError("钉钉结果未确认，尝试已记录")
    save(receipt, (f"{now().isoformat()} input_by={actor} fact_date={fact} success=true sha256={sha} "
                   f"runtime_sha256={runtime}\n"))
    check_archive(folder, fact)
    print("钉钉接口确认成功")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["calendar", "replay", "finalize", "publish", "repair-history"])
    parser.add_argument("--date", required=True)
    parser.add_argument("--input-by")
    parser.add_argument("--root", type=Path, default=REPO / "data" / "reports" / "kaipanla-daily")
    parser.add_argument("--calendar-db", type=Path, default=REPO / "data" / "trade.db")
    parser.add_argument("--output", type=Path, help="新建回放预览路径")
    parser.add_argument("--repair-input", type=Path, help="已核验历史原值 CSV")
    args = parser.parse_args()
    args.root = args.root.resolve()
    try:
        fact = date.fromisoformat(args.date).isoformat()
        archive = args.root / fact
        calendar_days = calendar(args.calendar_db, fact)
        if calendar_days is None:
            print(f"closed {fact}")
            return 0
        days, next_open = calendar_days
        if args.action == "calendar":
            print(f"open {fact} previous={days[-1]} cross_reference={days[-2]} next={next_open}")
            return 0
        require(args.input_by and args.input_by.strip(), "写入需提供 --input-by")
        if args.action == "replay":
            require(args.output, "预览需指定 --output")
            output = args.output.resolve()
            require(not output.exists(), f"预览路径已存在：{output}")
            check_archive(archive, fact)
            with TemporaryDirectory(dir=output.parent) as temp:
                candidate = Path(temp) / "preview"
                candidate.mkdir()
                build_output(candidate, args.root, archive, fact, days, next_open, args.input_by, mode="replay")
                candidate.rename(output)
            print(f"本地预览已核验 {output / 'report.html'}")
        else:
            with archive_lock(args.root):
                if args.action == "repair-history":
                    repair_history(args.root, args.repair_input, fact, days, args.input_by)
                elif args.action == "publish":
                    require(args.root == (REPO / "data" / "reports" / "kaipanla-daily").resolve(),
                            "发布仅接受正式报告目录")
                    publish(args.root, archive, fact, args.input_by, args.calendar_db)
                else:
                    if not archive.exists():
                        stage = args.root / ".staging" / fact
                        require(stage.is_dir(), f"还没有{fact}采集批次")
                        with (stage / ".checkpoint.lock").open("a") as lock, TemporaryDirectory(dir=args.root) as temp:
                            fcntl.flock(lock, fcntl.LOCK_EX)
                            data, _, _ = checkpoint_data(stage, fact)
                            write_projection(stage, data, fact)
                            candidate = Path(temp) / "archive"
                            candidate.mkdir()
                            build_output(candidate, args.root, stage, fact, days, next_open, args.input_by, mode="live")
                            candidate.rename(archive)
                    check_archive(archive, fact)
                    save_history(args.root, archive, fact, args.input_by)
                    print(f"四模块已核验并归档 {archive}")
        return 0
    except (OSError, ValueError, KeyError, IndexError, ArithmeticError, sqlite3.Error) as exc:
        print(f"未通过：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
