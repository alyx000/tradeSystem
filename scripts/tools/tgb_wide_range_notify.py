"""Send the complete archived wide-range shortlist when membership changes."""

import argparse
import csv
import fcntl
import hashlib
import io
import json
import math
import signal
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


def checked_artifact(run, name):
    manifest = dict(line.split("  ", 1)[::-1] for line in (run / "SHA256SUMS").read_text().splitlines())
    raw = (run / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest.get(name):
        raise ValueError("归档校验失败")
    return raw.decode("utf-8")


def read_receipt(run):
    return dict(line.split("=", 1) for line in checked_artifact(run, "verification.txt").splitlines() if line)


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
        candidates[code] = name
    return candidates


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


def archive_industries(run_date, market_date, current, previous, labels, provenance, input_by):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["market_date", "change", "code", "name", "sw_l2", "status", "source",
                     "fetched_at", "source_sha256", "source_attempts", "input_by"])
    for code in sorted(current.keys() | previous.keys()):
        if code in current:
            change, name = ("retained" if code in previous else "added"), current[code]
        else:
            change, name = "removed", previous[code]
        label = labels.get(code)
        writer.writerow([market_date, change, code, name, label or "待核验",
                         "verified" if label else "unverified", *provenance, input_by])
    body = output.getvalue().encode("utf-8")
    directory = REPORTS / "industry"
    directory.mkdir(exist_ok=True)
    path = directory / f"{run_date}-{hashlib.sha256(body).hexdigest()[:16]}.csv"
    if not path.exists():
        with NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temporary.write(body)
        Path(temporary.name).replace(path)
    return path


def stock_list(codes, names, labels, *, added=()):
    return "\n".join(
        f"- {names[code]}（{code}｜{labels.get(code) or '板块待核验'}）"
        f"{' **新增**' if code in added else ''}"
        for code in codes
    ) or "无"


def plan(run, run_date, *, archive_industry_by=None, require_ma30=False):
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
    base_date = date.fromisoformat(receipt["comparison_baseline_run"]).isoformat()
    base_run = REPORTS / "runs" / base_date
    baseline = read_receipt(base_run)
    baseline_market = date.fromisoformat(baseline["market_date"]).isoformat()
    if (base_date >= run_date or market_date > run_date or baseline_market > min(base_date, market_date)
            or baseline["run_date"] != base_date
            or baseline["status"] not in {"complete", "partial"}
            or baseline_market != receipt["comparison_baseline_market"]):
        raise ValueError("候选对比基线无效")
    ma30_applied = receipt.get("screen_rules") == "ma30_v1"
    current = read_candidates(run, require_ma30=ma30_applied)
    previous = read_candidates(base_run)
    added, removed = sorted(current.keys() - previous.keys()), sorted(previous.keys() - current.keys())
    if (len(current), len(added), len(removed)) != tuple(
        int(receipt[field]) for field in ("candidates", "candidates_added", "candidates_removed")
    ) or len(previous) != int(baseline["candidates"]):
        raise ValueError("候选名单与归档计数不一致")
    if not (added or removed):
        return None
    if require_ma30 and not ma30_applied:
        raise ValueError("当前报告缺少MA30筛选回执")
    mapping, *provenance = read_industries(market_date, allow_live=archive_industry_by is not None)
    labels = {code: (mapping.get(code) or mapping.get(code.split(".")[0]) or {}).get("sw_l2")
              for code in current.keys() | previous.keys()}
    if archive_industry_by is not None:
        archive_industries(run_date, market_date, current, previous, labels, provenance, archive_industry_by)
    rules = SCREEN_RULES
    if ma30_applied:
        rules += ("- 30日均线：最新前复权收盘价须不低于含当日的近 30 根有效日线收盘均值。"
                  "这是额外筛选条件。\n")
    title = f"宽幅震荡候选变动｜{market_date}"
    if ma30_applied and baseline.get("screen_rules") != "ma30_v1":
        comparison_note = "本次启用30日均线筛选，基线采用当时的筛选条件。\n\n"
    else:
        comparison_note = ""
    total_label = "全部通过MA30的候选" if ma30_applied else "全部候选"
    body = (f"### {title}\n\n与 {baseline['market_date']} 行情相比，"
            f"本次初筛 {len(current)} 只，新增 {len(added)} 只，退出 {len(removed)} 只。\n\n"
            f"{comparison_note}"
            f"**{total_label} · 申万二级行业**\n\n{stock_list(current, current, labels, added=set(added))}\n\n"
            f"**退出 {len(removed)} 只**\n\n{stock_list(removed, previous, labels)}\n\n"
            f"板块口径为 {market_date} 申万二级行业。\n\n"
            f"{rules}\n"
            f"完整核对见本地 {run_date} 报告。")
    if status == "partial":
        body += " 当前为初筛名单，完整模式仍待核验。"
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


def deliver(event, title, body, run_date, input_by):
    parts = message_parts(body)
    directory = REPORTS / "dingtalk"
    directory.mkdir(exist_ok=True)
    event_hash = hashlib.sha256(json.dumps(event).encode()).hexdigest()
    path = directory / f"{event_hash}.json"
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["status"] != "configuration_failed":
                return {"status": "already_sent" if saved["status"] == "sent" else "delivery_unconfirmed",
                        "receipt": str(path)}

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
        payload = plan(REPORTS / "runs" / run_date, run_date, archive_industry_by=archive_by,
                       require_ma30=args.send)
        if payload is None:
            result = {"status": "not_eligible"}
        elif args.send:
            result = deliver(*payload, run_date, args.input_by)
        else:
            result = {"status": "preview", "title": payload[1], "message": payload[2]}
    except Exception as exc:
        result = {"status": "blocked", "error_type": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return int(result["status"] not in {"not_eligible", "preview", "sent", "already_sent"})


if __name__ == "__main__":
    sys.exit(main())
