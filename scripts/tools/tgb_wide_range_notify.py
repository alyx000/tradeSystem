"""Send the complete archived wide-range shortlist when membership changes."""

import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import signal
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "data/reports/tgb-wide-range"
SCRIPTS = ROOT / "scripts"
SHANGHAI = ZoneInfo("Asia/Shanghai")
SCREEN_RULES = (
    "**量化初筛条件（研究口径，须同时满足）**\n\n"
    "- 沪深北 A 股，剔除 ST、退市股和 B 股，至少有 100 根有效日线。价格用前复权，成交量用原始值。\n"
    "- 宽振幅：近 5 根日线平均振幅至少 10%。单日振幅为当日最高价与最低价之差除以前一根收盘价。\n"
    "- 持续堆量：近 10 根均量至少为紧邻其前 60 根均量的 1.5 倍，且近 10 根中至少 5 根成交量严格高于该基准的 1.5 倍。\n"
    "- 跌后修复：近 20 根至少出现 2 次不重叠的修复。单日收盘跌幅至少 3%，随后 1～3 根内收盘回到或超过下跌前收盘。\n"
    "- 震荡范围：近 20 根累计涨跌幅绝对值不超过 20%，收盘净位移绝对值除以逐日收盘变动绝对值之和不超过 0.35。最新收盘须位于此前 15 根日线的最低价至最高价之间，含边界。\n"
)


def checked_bytes(run, name, *, optional=False):
    manifest = dict(line.split("  ", 1)[::-1] for line in (run / "SHA256SUMS").read_text().splitlines())
    if optional and name not in manifest:
        return None
    raw = (run / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.get(name):
        raise ValueError("归档校验失败")
    return raw


def checked_artifact(run, name):
    return checked_bytes(run, name).decode("utf-8")


def read_receipt(run):
    return dict(line.split("=", 1) for line in checked_artifact(run, "verification.txt").splitlines() if line)


def comparison_baseline(receipt):
    """Follow the sealed comparison pointer, including successful retries of the same run."""
    base_date = date.fromisoformat(receipt["comparison_baseline_run"]).isoformat()
    market = date.fromisoformat(receipt["market_date"]).isoformat()
    base_market = date.fromisoformat(receipt["comparison_baseline_market"]).isoformat()
    if base_date >= receipt["run_date"] or market > receipt["run_date"] or base_market > min(base_date, market):
        raise ValueError("候选对比基线日期无效")
    runs = (REPORTS / "runs").resolve()
    relative = Path(receipt.get("comparison_baseline_archive", base_date))
    archive = (runs / relative).resolve()
    if (not relative.parts or relative.parts[0] != base_date or ".." in relative.parts
            or not archive.is_relative_to(runs / base_date)):
        raise ValueError("候选对比基线目录无效")
    baseline = read_receipt(archive)
    if (baseline.get("status") not in {"complete", "partial"} or baseline.get("run_date") != base_date
            or baseline.get("market_date") != base_market):
        raise ValueError("候选对比基线回执无效")
    return archive, baseline


def read_candidates(run, *, require_ma30=False):
    reader = csv.DictReader(io.StringIO(checked_artifact(run, "candidates.csv")))
    required = {"code", "name"}
    if require_ma30:
        required.update({"close_qfq", "ma30_qfq", "ma30_pass"})
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("候选表缺字段")
    candidates = {}
    for row in reader:
        code, name = row["code"], row["name"]
        if not code or not name or code in candidates:
            raise ValueError("候选身份缺失或重复")
        if require_ma30:
            close, ma30 = float(row["close_qfq"]), float(row["ma30_qfq"])
            if (row["ma30_pass"] != "True" or not all(math.isfinite(p) and p > 0 for p in (close, ma30))
                    or (close < ma30 and not math.isclose(close, ma30, rel_tol=1e-12))):
                raise ValueError("候选未通过MA30核验")
        candidates[code] = row
    return candidates


def apply_ma30_to_legacy_candidates(run, candidates, market_date):
    """Replay the added MA30 requirement against the sealed, earlier market date."""
    if not candidates:
        return {}
    import pandas as pd

    daily = pd.read_pickle(io.BytesIO(checked_bytes(run, "daily.pkl")))
    if daily.trade_date.max() != market_date:
        raise ValueError("基线日线与行情日期不一致")
    bars = daily[daily.ts_code.isin(candidates)].sort_values(["ts_code", "trade_date"])
    if bars.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("基线日线存在重复记录")
    eligible = {}
    for code, group in bars.groupby("ts_code"):
        window = group.tail(30)
        if len(window) != 30 or window.trade_date.iloc[-1] != market_date:
            raise ValueError("基线候选缺少MA30行情证据")
        factor = window.adj_factor.iloc[-1]
        if not all(math.isfinite(value) and value > 0 for value in window.adj_factor):
            raise ValueError("基线候选的复权因子无效")
        closes = window.close * window.adj_factor / factor
        if not all(math.isfinite(value) and value > 0 for value in closes):
            raise ValueError("基线候选的前复权收盘价无效")
        close, ma30 = closes.iloc[-1], math.fsum(closes) / 30
        if close >= ma30 or math.isclose(close, ma30, rel_tol=1e-12):
            eligible[code] = candidates[code]
    if set(bars.ts_code) != set(candidates):
        raise ValueError("基线候选缺少日线，无法同口径比较")
    return eligible


def current_industry_mapping():
    from main import load_config, setup_providers
    from utils.network_env import without_standard_http_proxy

    def on_timeout(*_):
        raise TimeoutError("申万行业接口超时")

    attempts = []
    previous = signal.signal(signal.SIGALRM, on_timeout)
    try:
        with without_standard_http_proxy():
            for _ in range(3):
                signal.alarm(20)
                try:
                    registry = setup_providers(load_config())
                    provider = registry.get_provider("tushare")
                    if provider is None or not provider.initialize():
                        attempts.append("not_initialized")
                        continue
                    response = registry.call_specific("tushare", "get_stock_sw_industry_map")
                    if (response.success and response.source == "tushare:index_member_all"
                            and isinstance(response.data, dict) and len(response.data) >= 4000):
                        return response.data, response.source, ",".join(attempts + ["success"])
                    attempts.append("unavailable")
                except Exception as exc:
                    attempts.append(type(exc).__name__)
                finally:
                    signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, previous)
    return {}, "tushare:index_member_all", ",".join(attempts)


def archived_industries(market_date):
    directory = REPORTS.parent / "review-evidence" / market_date
    for path in sorted(directory.rglob(f"visual_identity_sources_{market_date}.json")):
        try:
            original = path.read_bytes()
            source = json.loads(original)[market_date.replace("-", "") + "_identity"]
            fetched_at = source["fetched_at"]
            if (source["api"] != "index_member_all" or source["status"] != "complete"
                    or source["as_of"] != market_date or not fetched_at.startswith(market_date)):
                continue
            rows, pages = source["rows"], source["pages"]
            if (not isinstance(rows, list) or not isinstance(pages, list) or len(pages) < 2
                    or pages[-1]["count"] != 0 or sum(page["count"] for page in pages) != len(rows)
                    or any(page["offset"] != index * 2000 or type(page["count"]) is not int
                           or not 0 < page["count"] <= 2000 for index, page in enumerate(pages[:-1]))
                    or pages[-1]["offset"] != (len(pages) - 1) * 2000):
                continue
            mapping = {}
            for row in rows:
                code = row["ts_code"]
                if code.startswith("T"):
                    continue
                label = row["l2_name"]
                if (row["is_new"] != "Y" or not code or not label.strip()
                        or (code in mapping and mapping[code]["sw_l2"] != label)):
                    raise ValueError("行业成分缺失或冲突")
                mapping[code] = {"sw_l2": label}
            if mapping:
                return mapping, str(path), fetched_at, hashlib.sha256(original).hexdigest(), "archived"
        except (OSError, KeyError, TypeError, AttributeError, ValueError):
            continue
    return None


def read_industries(market_date, *, allow_live):
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    archived = archived_industries(market_date)
    if archived is not None:
        return archived
    if allow_live and market_date == datetime.now(SHANGHAI).date().isoformat():
        mapping, source_name, attempts = current_industry_mapping()
        return mapping, source_name, datetime.now(SHANGHAI).isoformat(), "", attempts
    return {}, "同行情日申万二级快照缺失", "", "", "not_available"


def archive_industries(run_date, market_date, current, previous, labels, sources, input_by):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["market_date", "change", "code", "name", "sw_l2", "status", "source",
                     "fetched_at", "source_sha256", "source_attempts", "input_by"])
    for code in sorted(current.keys() | previous.keys()):
        if code in current:
            change, name = ("retained" if code in previous else "added"), current[code]["name"]
        else:
            change, name = "removed", previous[code]["name"]
        label = labels.get(code)
        writer.writerow([market_date, change, code, name, label or "待核验",
                         "verified" if label else "unverified", *sources[code], input_by])
    body = output.getvalue().encode("utf-8")
    directory = REPORTS / "industry"
    directory.mkdir(exist_ok=True)
    path = directory / f"{run_date}-{hashlib.sha256(body).hexdigest()[:16]}.csv"
    if not path.exists():
        with NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write(body)
        Path(temporary.name).replace(path)
    return path


def saved_industries(run_date, market_date):
    """Recover verified labels with their original source, rejecting conflicting snapshots per stock."""
    observed = defaultdict(dict)
    for path in sorted((REPORTS / "industry").glob(f"{run_date}-*.csv")):
        raw = path.read_bytes()
        if path.stem != f"{run_date}-{hashlib.sha256(raw).hexdigest()[:16]}":
            continue
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
            label = (row.get("sw_l2") or "").strip()
            if (row.get("market_date") == market_date and row.get("status") == "verified"
                    and (row.get("fetched_at") or "").startswith(market_date + "T")
                    and row.get("source") and row.get("code") and label not in {"", "待核验", "板块待核验"}):
                source = (row["source"], row["fetched_at"], row.get("source_sha256", ""), row.get("source_attempts", ""))
                observed[row["code"]][label] = source
    return {code: next(iter(labels.items())) for code, labels in observed.items() if len(labels) == 1}


def previous_appearances(baseline, added, rule):
    """Find the latest strictly earlier market date proving a return under the same rules."""
    missing = set(added)
    seen = {}
    receipt = baseline
    while missing and receipt.get("comparison_baseline_run"):
        try:
            archive, receipt = comparison_baseline(receipt)
            earlier_rule = receipt.get("screen_rules", "legacy")
            ma30_transition = (earlier_rule, rule) == ("legacy", "ma30_v1")
            if earlier_rule != rule and not ma30_transition:
                return seen, "更早归档的筛选规则不同，重新入选仅标同口径记录。"
            if receipt["market_date"] < baseline["market_date"]:
                earlier = read_candidates(archive, require_ma30=earlier_rule == "ma30_v1")
                if len(earlier) != int(receipt["candidates"]):
                    raise ValueError("历史候选计数不符")
                known = missing.intersection(earlier)
                if ma30_transition and known:
                    eligible = apply_ma30_to_legacy_candidates(archive, {code: earlier[code] for code in known},
                                                               receipt["market_date"])
                    known = set(eligible)
                seen.update(dict.fromkeys(known, receipt["market_date"]))
                missing -= known
            if ma30_transition:
                return seen, f"重新入选按同一套条件回溯至 {receipt['market_date']}。"
        except (KeyError, OSError, ValueError):
            # Only positive, verified appearances are labelled. The immediate delta is already verified.
            return seen, "部分历史名单缺少可核验归档，重新入选仅标已核实个股。"
    return seen, ""


def stock_evidence(run, filename, codes):
    if not codes:
        return {}
    raw = checked_bytes(run, filename, optional=True)
    result = {}
    if raw is not None:
        for row in csv.DictReader(io.StringIO(raw.decode("utf-8"))):
            code = row.get("code")
            if code in codes:
                if code in result:
                    raise ValueError(f"{filename} 存在重复代码")
                result[code] = row
    return result


MA30_REASON = "收盘低于MA30"
REASON_LABELS = {
    "收盘低于30日均线": MA30_REASON,
    "suspend_d_reported_no_daily_quote": "停牌缺当日行情",
    "missing_target_quote_suspension_unverified": "缺当日行情，停牌待核",
    "sample_insufficient": "有效历史样本不足",
    "invalid_or_incomplete_stock_history": "历史行情缺失或无效",
    "identity_missing": "证券身份待核",
    "identity_unavailable": "证券身份待核",
    "ST_delisting_B": "ST、退市或B股范围排除",
}


def change_evidence(run, codes, *, additions, ma30, legacy_filtered=()):
    """Describe archived exclusions on the side of the comparison where each stock was absent."""
    rows = stock_evidence(run, "full-screen.csv", set(codes))
    missing = set(codes).difference(rows, legacy_filtered)
    fallback = stock_evidence(run, "universe-status.csv" if additions else "exits.csv", missing)
    notes = {}
    counts = Counter()
    for code in codes:
        row = rows.get(code, {})
        if code in legacy_filtered:
            reason = MA30_REASON
        elif code in fallback:
            reason = fallback[code].get("status" if additions else "reason")
            if additions and reason not in REASON_LABELS:
                reason = None
        else:
            reason = row.get("exclusion_reason") if row.get("match") == "False" else None
        reasons = (reason or "原因待核").split("；")
        if "20日涨跌超出±20%" in reasons:
            try:
                drift = float(row.get("drift") or "nan")
            except ValueError:
                drift = math.nan
            if math.isfinite(drift) and abs(drift) > 20:
                reasons[reasons.index("20日涨跌超出±20%")] = "20日涨幅超过20%" if drift > 0 else "20日跌幅超过20%"
        labels = list(dict.fromkeys(REASON_LABELS.get(item, item) for item in reasons))
        notes[code] = "、".join(labels)
        counts.update(labels)
    if codes and ma30 and all(rows.get(code, {}).get("ma30_pass") == "True" for code in codes):
        counts.setdefault(MA30_REASON, 0)
    summary = "、".join(f"{reason} {count}只" for reason, count in counts.most_common())
    return notes, summary


def repair_count(row, market):
    try:
        onset = date.fromisoformat(row.get("onset") or "")
    except ValueError:
        onset = None
    if row.get("left_censored") != "False" or onset is None or onset.isoformat() > market:
        return "起点待核"
    try:
        count = float(row.get("post_rebounds") or "nan")
    except ValueError:
        count = math.nan
    if count.is_integer() and count >= 0:
        return int(count)
    return "修复次数待核"


def candidate_list(current, labels, reasons, reentries, market):
    lines = []
    counts = Counter()
    for code, row in current.items():
        repairs = repair_count(row, market)
        if isinstance(repairs, int):
            counts["修复至少2次" if repairs >= 2 else f"修复{repairs}次"] += 1
            evidence = f"堆量后修复 {repairs} 次"
        else:
            counts[repairs] += 1
            evidence = "堆量" + repairs if repairs == "起点待核" else repairs
        stock = f"- {row['name']}（{code}｜{labels.get(code) or '板块待核验'}）"
        if code in reasons:
            stock += " **新增**"
        if code in reentries:
            stock += f" · 重新入选（上次 {reentries[code]}）"
        stock += f" · {evidence}"
        if code in reasons:
            stock += f" · 上期未入选（{reasons[code]}）"
        lines.append(stock)
    order = ("修复至少2次", "修复1次", "修复0次", "起点待核", "修复次数待核")
    summary = "、".join(f"{key} {counts[key]}只" for key in order if counts[key])
    return "\n".join(lines) or "无", summary


def plan(run, run_date, *, archive_industry_by=None, require_ma30=False, previous_policy_events=None):
    receipt = read_receipt(run)
    if receipt["run_date"] != run_date:
        raise ValueError("运行日期不一致")
    status = receipt["status"]
    if status == "skipped":
        return None
    checked_artifact(run, "report.md")
    if status == "source_failed":
        target = receipt.get("market_date_target") or receipt.get("market_date")
        target_date = date.fromisoformat(target) if target else None
        if target_date is not None and target_date > date.fromisoformat(run_date):
            raise ValueError("目标行情日期晚于运行日期")
        target_line = f"目标行情日 {target_date}" if target_date else "目标行情日未锁定"
        stages = {"trade_calendar": "交易日历", "get_market_daily_quotes": "全市场日线",
                  "get_adj_factor": "复权因子", "get_stock_basic_list": "证券身份",
                  "get_stock_st": "ST名单", "get_stock_daily_range": "逐票区间行情"}
        stage = stages.get(receipt.get("failed_interface"), "详见采集收据")
        title = f"宽幅震荡采集失败｜{run_date}"
        body = f"### {title}\n\n{target_line}。失败环节：{stage}。本次候选变化没有形成可发布结果。"
        return [status, run_date], title, body
    if status not in {"complete", "partial"}:
        raise ValueError("无法识别采集状态")
    market_date = date.fromisoformat(receipt["market_date"]).isoformat()
    base_run, baseline = comparison_baseline(receipt)
    base_market = baseline["market_date"]
    rule, base_rule = receipt.get("screen_rules", "legacy"), baseline.get("screen_rules", "legacy")
    ma30_applied = rule == "ma30_v1"
    current = read_candidates(run, require_ma30=ma30_applied)
    previous = read_candidates(base_run, require_ma30=base_rule == "ma30_v1")
    archived_added = sorted(current.keys() - previous.keys())
    archived_removed = sorted(previous.keys() - current.keys())
    if (len(current), len(archived_added), len(archived_removed)) != tuple(
        int(receipt[field]) for field in ("candidates", "candidates_added", "candidates_removed")
    ) or len(previous) != int(baseline["candidates"]):
        raise ValueError("候选名单与归档计数不一致")
    legacy_filtered = set()
    if rule != base_rule:
        if (base_rule, rule) != ("legacy", "ma30_v1"):
            raise ValueError("两个日期的规则不同，缺少可验证的同口径回算")
        comparable = apply_ma30_to_legacy_candidates(base_run, previous, base_market)
        legacy_filtered = previous.keys() - comparable.keys()
        previous = comparable
        if previous_policy_events is not None:
            previous_policy_events.append(["delta", market_date, archived_added, archived_removed])
    added, removed = sorted(current.keys() - previous.keys()), sorted(previous.keys() - current.keys())
    if not (added or removed):
        return None
    if require_ma30 and not ma30_applied:
        raise ValueError("当前报告缺少MA30筛选回执")
    prior_reasons, entry_summary = change_evidence(base_run, added, additions=True, ma30=ma30_applied,
                                                   legacy_filtered=legacy_filtered)
    exit_reasons, exit_summary = change_evidence(run, removed, additions=False, ma30=ma30_applied)
    reentries, history_note = previous_appearances(baseline, added, rule) if rule == base_rule else ({}, "")
    mapping, *provenance = read_industries(market_date, allow_live=archive_industry_by is not None)
    labels = {code: (mapping.get(code) or mapping.get(code.split(".")[0]) or {}).get("sw_l2")
              for code in current.keys() | previous.keys()}
    sources = dict.fromkeys(labels, provenance)
    if not all(labels.values()):
        saved = saved_industries(run_date, market_date)
        for code in saved.keys() & labels.keys():
            if not labels[code]:
                labels[code], sources[code] = saved[code]
    if archive_industry_by is not None:
        archive_industries(run_date, market_date, current, previous, labels, sources, archive_industry_by)
    stocks, repairs = candidate_list(current, labels, prior_reasons, reentries, market_date)
    exits = "\n".join(
        f"- {previous[code]['name']}（{code}｜{labels.get(code) or '板块待核验'}） · {exit_reasons[code]}"
        for code in removed
    ) or "无"
    rules = SCREEN_RULES
    if ma30_applied:
        rules += ("- 30日均线：最新前复权收盘价须不低于含当日的近 30 根有效日线收盘均值。"
                  "这是额外筛选条件。\n")
    title = f"宽幅震荡候选变动｜{market_date}"
    total_label = "全部通过MA30的候选" if ma30_applied else "全部候选"
    retained = len(current) - len(added)
    sections = [
        f"### {title}",
        f"与 {base_market} 按同一套筛选条件比较，上次 {len(previous)} 只，本次 {len(current)} 只，"
        f"新增 {len(added)} 只，退出 {len(removed)} 只，保留 {retained} 只。",
    ]
    if entry_summary:
        sections.append(f"新增股上期未入选的原因包括{entry_summary}（同一只可有多项）。")
    if exit_summary:
        sections.append(f"本期退出原因包括{exit_summary}（同一只可有多项）。")
    if repairs:
        sections.append(f"近20根日线内，按堆量起点后的修复记录统计，{repairs}。")
    if history_note:
        sections.append(history_note)
    sections.extend([f"**{total_label} · 申万二级行业**", stocks, f"**退出 {len(removed)} 只**", exits,
                     f"板块口径为 {market_date} 申万二级行业。", rules.rstrip(), f"完整核对见本地 {run_date} 报告。"])
    body = "\n\n".join(sections)
    if status == "partial":
        body += " 当前为初筛名单，箱体、堆量日成本支撑和板块联动仍待逐票核实。"
    return ["delta", market_date, added, removed], title, body


def configured_pusher():
    sys.path.insert(0, str(SCRIPTS))
    from dotenv import load_dotenv
    from pushers.dingtalk_pusher import DingTalkPusher

    load_dotenv(SCRIPTS / ".env")
    return DingTalkPusher(config={})


def message_parts(body, limit=12000):
    if len(body.encode("utf-8")) <= limit:
        return [body]
    parts, lines, size = [], [], 0
    for line in body.splitlines(keepends=True):
        length = len(line.encode("utf-8"))
        if length > limit:
            raise ValueError("摘要单行超出发送长度")
        if size + length > limit:
            parts.append("".join(lines))
            lines, size = [], 0
        lines.append(line)
        size += length
    return [*parts, "".join(lines)]


def deliver(event, title, body, run_date, input_by, *, previous_policy_events=()):
    parts = message_parts(body)
    directory = REPORTS / "dingtalk"
    directory.mkdir(exist_ok=True)
    event_hash = hashlib.sha256(json.dumps(event).encode()).hexdigest()
    path = directory / f"{event_hash}.json"
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        candidates_for_dedup = [path]
        for earlier_event in previous_policy_events:
            digest = hashlib.sha256(json.dumps(earlier_event).encode()).hexdigest()
            candidates_for_dedup.append(directory / f"{digest}.json")
        for receipt_path in candidates_for_dedup:
            if not receipt_path.exists():
                continue
            saved = json.loads(receipt_path.read_text())
            if saved["status"] != "configuration_failed":
                return {"status": "already_sent" if saved["status"] == "sent" else "delivery_unconfirmed",
                        "receipt": str(receipt_path)}

        confirmed_parts = []

        def record(status):
            receipt = {"status": status, "run_date": run_date, "input_by": input_by, "event": event,
                       "title": title, "message": body, "recorded_at": datetime.now(SHANGHAI).isoformat(),
                       "total_parts": len(parts), "confirmed_parts": confirmed_parts}
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(path)
            return {"status": status, "receipt": str(path)}

        pusher = configured_pusher()
        if not pusher.initialize():
            return record("configuration_failed")
        record("sending")
        for index, part in enumerate(parts, 1):
            part_title = f"{title}（{index}/{len(parts)}）" if len(parts) > 1 else title
            if not pusher.send_markdown(part_title, part):
                return record("delivery_unconfirmed")
            confirmed_parts.append(index)
            if index < len(parts):
                record("sending")
        return record("sent")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-date", required=True, type=date.fromisoformat)
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--archive-industry", action="store_true", help="归档申万二级板块附件，默认仅预览")
    args = parser.parse_args()
    run_date = args.run_date.isoformat()
    try:
        if not args.input_by.strip() or (args.send and args.run_date != datetime.now(SHANGHAI).date()):
            raise ValueError("发送只接受当天运行及明确请求者")
        archive_by = args.input_by if args.send or args.archive_industry else None
        previous_policy_events = []
        payload = plan(REPORTS / "runs" / run_date, run_date, archive_industry_by=archive_by,
                       require_ma30=args.send, previous_policy_events=previous_policy_events)
        if payload is None:
            result = {"status": "not_eligible"}
        elif args.send:
            result = deliver(*payload, run_date, args.input_by, previous_policy_events=previous_policy_events)
        else:
            result = {"status": "preview", "title": payload[1], "message": payload[2]}
    except Exception as exc:
        result = {"status": "blocked", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return int(result["status"] not in {"not_eligible", "preview", "sent", "already_sent"})


if __name__ == "__main__":
    sys.exit(main())
