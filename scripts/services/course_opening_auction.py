"""股票开盘集合撮合结果。严格事前选样，只用专用成交来源，不读09:30分钟。"""
from __future__ import annotations

from contextlib import closing, nullcontext
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
import socket

from analyzers import tracked_theme_members
from db.connection import get_readonly_connection
from providers.base import DataResult
from services import course_intraday_universe as universe
from services.board_break_intraday import data, store
from services.course_core_minutes import _daily_rows
from services.handwritten_review import DEFAULT_ARCHIVE, load_entry_before
from services.volume_record.service import atomic_write

ROOT = Path(__file__).resolve().parents[2] / "data/reports/course-opening-auction"
SCHEMA = "course-opening-auction-v1"
SOURCE = "tushare:stk_auction"
DOC = "https://tushare.pro/document/2?doc_id=369"
FIELDS = "ts_code,trade_date,price,vol,amount,pre_close"
ERRORS = (ValueError, KeyError, TypeError, OSError, sqlite3.Error)


def select(conn, day, cal):
    prior = cal["previous"]
    cutoff = datetime.combine(date.fromisoformat(day), time(9, 15), data.TZ)
    base = universe.build_universe(conn, day, cutoff_time=time(9, 15))
    gaps, sources, members = [], {"core_selection": base["source"], "core_selection_sha": base["sha256"]}, {}
    # No peer is implied by a lone stock in this list. Match groups against the complete prior limit-up list below.
    def add(code, name, core=False, group=None):
        item = members.setdefault(code, dict(code=code, name=name, core=False, previous_limit_up=None, groups=[]))
        item["core"] = item["core"] or core
        if group and group not in item["groups"]:
            item["groups"].append(group)
    for item in base["members"]:
        if item["role"] == "core":
            add(item["code"], item["name"], core=True)
    cores = set(members)
    if not cores:
        gaps.append("无可核验的事前核心")
    def seal():
        rows = sorted(members.values(), key=lambda r:r["code"])
        obj = dict(trade_date=day, as_of_date=prior, cutoff_at=cutoff.isoformat(), members=rows,
                   sources=sources, source_limitations=base["gaps"], gaps=gaps)
        return dict(**obj, sha256=data.digest(obj))
    try:
        raw, names, by_name, sources["identity"] = universe._previous_market(conn, prior, cutoff)
        limit_rows = data.limit_rows(raw, prior)
        limits = {r["code"]:r["name"] for r in limit_rows if names.get(r["code"]) == r["name"]}
        sources["limits"] = dict(trade_date=prior, source=raw["limit_up"]["_source"],
                                 rows=len(limit_rows), sha256=data.digest(limit_rows))
        conflicting = {r["code"] for r in limit_rows if r["code"] in names and names[r["code"]] != r["name"]}
        if conflicting:
            gaps.append(f"昨日涨停样本有{len(conflicting)}只证券名称与同日身份冲突，保留未知")
        for code in cores - conflicting:
            members[code]["previous_limit_up"] = code in limits
    except universe.SOURCE_ERRORS as exc:
        gaps.append("昨日涨停/身份样本未核验：" + str(exc))
        return seal()

    def add_group(label, group, origin, *, explicit=False):
        matching = sorted(c for c in cores if group.get(c) == names.get(c))
        if not matching and not explicit:
            return
        descriptor = dict(name=label, source=origin, core_codes=matching, explicitly_tracked=explicit)
        for code in sorted(limits.keys() & group.keys()):
            if group[code] == limits[code]:
                add(code, limits[code], group=descriptor)
                members[code]["previous_limit_up"] = True
    try:
        groups, sources["ranked_theme_members"], misses = universe._theme_groups(raw, prior, cutoff)
        gaps.extend(misses)
        for theme, group in groups:
            add_group(theme["name"], group, "prior_theme_members")
    except universe.SOURCE_ERRORS as exc:
        gaps.append("昨日题材成员未核验：" + str(exc))
    if tracked_theme_members.KEY in raw:
        try:
            groups, sources["tracked_theme_members"], misses = universe._tracked_groups(
                raw, prior, cutoff, cal["history"][-2])
            gaps.extend(misses)
            for theme, group in groups:
                add_group(theme["name"], group, "prior_tracked_theme_members", explicit=True)
        except universe.SOURCE_ERRORS as exc:
            gaps.append("指定题材成员未核验：" + str(exc))
    # Prior written small groups can supplement missing structured constituents.
    try:
        entry, _, _ = load_entry_before(Path(DEFAULT_ARCHIVE) / prior, cutoff)
        _, _, manual = universe._handwritten(entry, by_name)
        meta = entry["meta"]
        if datetime.fromisoformat(meta["recorded_at"]).astimezone(data.TZ).date().isoformat() < prior:
            raise data.EvidenceError("手写分组记录早于事实日")
        sources["manual_groups"] = {k:meta[k] for k in ("recorded_at", "source_sha256", "supplement_sha256")}
        for label, group_names in manual.items():
            group = {by_name[n][0]:n for n in group_names if len(by_name.get(n, [])) == 1}
            add_group(label, group, "prior_handwritten_named_group")
    except universe.SOURCE_ERRORS as exc:
        gaps.append("前夜手写同组名单未核验：" + str(exc))
    for core in base["members"]:
        for peer in core.get("comparisons", []):
            if core["code"] in cores:
                code = peer["code"]
                add_group(peer["theme"], {c:names[c] for c in (core["code"],code) if c in names},
                          "prior_named_pair:" + peer["source"])
    return seal()


def fetch(day, code=None):
    """沿用项目固定镜像传输，不将异常响应、账号或密钥写入回执。"""
    from providers.tushare_transport import TushareMirrorClient
    from requests import RequestException
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise data.EvidenceError("TUSHARE_TOKEN 未配置")
    try:
        params = {"trade_date": day.replace("-", "")}
        if code is not None:
            params["ts_code"] = code
        df = TushareMirrorClient(token, timeout=15).query("stk_auction", fields=FIELDS, **params)
        return df.to_dict("records")
    except (RequestException, RuntimeError, ValueError, KeyError) as exc:
        reason = "当日竞价接口权限未开通" if any(t in str(exc).lower() for t in ("权限","积分","permission")) else type(exc).__name__
        causes = [exc]
        for cause in causes:
            for nested in (cause.__cause__, cause.__context__, getattr(cause, "reason", None)):
                if isinstance(nested, BaseException) and nested not in causes:
                    causes.append(nested)
        if any(isinstance(cause, socket.gaierror) for cause in causes):
            reason = "镜像域名解析失败"
        raise data.EvidenceError(f"stk_auction 请求失败（{reason}）") from None


def _query_scope(day, pending, query, query_code, attempt, problems):
    requests = attempt["requests"] = []
    trading_day = day.replace("-", "")

    def request(code=None):
        params = {"trade_date": trading_day}
        if code:
            params["ts_code"] = code
        receipt = {"params": params, "rows": None, "status": "source_failed"}
        requests.append(receipt)
        try:
            rows = query(day) if code is None else query_code(day, code)
            if not isinstance(rows, list):
                raise data.EvidenceError("集合竞价回复结构异常")
            receipt["rows"] = len(rows)
            for row in rows:
                if not isinstance(row, dict) or row.get("trade_date") != trading_day or not row.get("ts_code"):
                    raise data.EvidenceError("集合竞价响应日期或结构不符")
                if code and row.get("ts_code") != code:
                    raise data.EvidenceError("定向竞价响应代码不符")
            receipt["status"] = "received" if rows else "empty"
            return rows
        except (ValueError, OSError, RuntimeError) as exc:
            receipt["reason"] = str(exc)
            raise

    rows = request()
    attempt["source_rows"] = len(rows)
    selected = {}
    for row in rows:
        code = row.get("ts_code")
        if code in pending:
            if code in selected:
                problems[code] = "同一证券记录重复"
            selected[code] = row
    # 达到接口上限也不丢已到且唯一的观察。整源失败/空回复不会放大成逐股调用。
    attempt["day_response_at_limit"] = len(rows) >= 8000
    if (selected or attempt["day_response_at_limit"]) and query_code is not None:
        for code in sorted(pending - selected.keys()):
            try:
                reply = request(code)
            except (ValueError, OSError, RuntimeError) as exc:
                problems[code] = str(exc)
                attempt["targeted_stop_reason"] = str(exc)
                break
            if reply:
                selected[code] = reply[0]
                if len(reply) != 1:
                    problems[code] = "同一证券记录重复"
    attempt["matched_scope_rows"] = len(selected)
    attempt["status"] = "received" if rows else "empty"
    return selected


def _decimal(value):
    try:
        num = Decimal(str(value))
        if not num.is_finite() or num < 0:
            raise ValueError
        return num
    except (InvalidOperation, ValueError):
        raise data.EvidenceError("成交字段含非法数值") from None


def _trade(row):
    vol = _decimal(row.get("vol"))
    amount = _decimal(row.get("amount"))
    if vol != int(vol):
        raise data.EvidenceError("成交股数非整数")
    if not vol and not amount:
        raw_price = row.get("price")
        if str(raw_price).lower() not in ("none", "nan", "<na>") and _decimal(raw_price) != 0:
            raise data.EvidenceError("零量额却带非零竞价成交均价，来源语义待核")
        return dict(status="no_match", price=None, volume_shares=0, amount_cny=0)
    price = _decimal(row.get("price"))
    if not price or not vol or not amount:
        raise data.EvidenceError("成交价格、量额不一致")
    # 部分镜像金额做了末位舍入；以每股半分或总额1元的较大者作一致性检查。
    if abs(price * vol - amount) > max(Decimal(1), vol * Decimal("0.005")):
        raise data.EvidenceError("价乘股数与成交额不符")
    return dict(status="matched", price=float(price), volume_shares=int(vol), amount_cny=float(amount))


def _references(conn, day, codes):
    row = conn.execute("SELECT json_extract(raw_data,'$.raw_data.zhong_prefix_effect.evidence'),"
                       "json_extract(raw_data,'$.date'),json_extract(raw_data,'$.raw_data.date') "
                       "FROM daily_market WHERE date=?", (day,)).fetchone()
    if not row or not row[0] or tuple(row[1:]) != (day, day):
        return {}
    ev = json.loads(row[0])
    return _daily_rows(DataResult(ev.get("quotes"), ev.get("quote_source")), "tushare:daily",
                       day, codes, ("open", "pre_close"))


def observation(row, day, reference):
    result = _trade(row)
    value = row.get("pre_close")
    try:
        previous = _decimal(value)
    except data.EvidenceError:
        previous = None
    reference_source = SOURCE if previous else None
    if previous and reference and abs(previous - Decimal(str(reference["pre_close"]))) > Decimal("0.005"):
        raise data.EvidenceError("竞价昨收与同日日线除权参考冲突")
    if result["price"] is not None and reference and abs(result["price"] - reference["open"]) > 0.005:
        raise data.EvidenceError("专用竞价来源与同日开盘价冲突")
    if not previous and reference:
        previous = Decimal(str(reference["pre_close"]))
        reference_source = "tushare:daily"
    result.update(pre_close=float(previous) if previous else None,
                  pre_close_source=reference_source,
                  open_return_pct=round((result["price"]/float(previous)-1)*100, 4)
                  if result["price"] is not None and previous else None,
                  trade_date=day, source=SOURCE, raw={k:v for k,v in row.items() if k in FIELDS.split(",")})
    # Missing source values must serialize as null, including pandas' NaN.
    result["raw"] = {k:(None if str(v).lower() in ("nan", "none", "<na>") else v) for k,v in result["raw"].items()}
    return result


def _attach_reference(previous, current, checked_at):
    """已有成交的原始响应、首次采集时间和实际价量额均保留；只追加已核验的昨收证据。"""
    keys = ("status", "price", "volume_shares", "amount_cny")
    if any(previous.get(k) != current[k] for k in keys):
        raise data.EvidenceError("重取竞价成交与已有回执不一致，保留旧事实")
    if current["pre_close"] is None:
        return previous
    return {**previous, "pre_close": current["pre_close"], "pre_close_source": current["pre_close_source"],
            "open_return_pct": current["open_return_pct"], "reference_verified_at": checked_at,
            "reference_supplement": dict(trade_date=current["trade_date"], code=current["raw"]["ts_code"],
                field="pre_close", value=current["pre_close"], source=current["pre_close_source"],
                acquired_at=checked_at)}


def _read(path, day):
    if not path.exists():
        return None
    report = json.loads(path.read_text())
    sha = report.pop("sha256", None)
    if report.get("schema") != SCHEMA or report.get("trade_date") != day or sha != data.digest(report):
        raise data.EvidenceError("已有竞价归档损坏，保留原文件")
    return dict(**report, sha256=sha)


def summary(result):
    return {k:result[k] for k in ("trade_date","status","coverage","latest_attempt","path","gaps") if k in result}


def capture(day, input_by, *, dry_run=False, root=ROOT, db_path=None, now=None,
            build_selection=select, query=None, query_code=None, load_references=_references, scheduled_at=None):
    if date.fromisoformat(day).isoformat() != day or not input_by or not input_by.strip():
        raise ValueError("需有效日期和请求者")
    clock = (now or datetime.now(data.TZ)).astimezone(data.TZ)
    if query is None:
        query = fetch
        query_code = fetch if query_code is None else query_code
    if scheduled_at is not None:
        planned = datetime.combine(date.fromisoformat(day), time(9, 32), data.TZ)
        if scheduled_at != planned.isoformat() or not 0 <= (clock - planned).total_seconds() < 180:
            return dict(trade_date=day, status="skipped", gaps=["超出当日 09:32 采样时段"])
    if date.fromisoformat(day) > clock.date():
        return dict(trade_date=day, status="skipped", gaps=["未来日期"])
    root = Path(root)
    path = root / f"{day}.json"
    with closing(get_readonly_connection(db_path)) as conn:
        cal = data.calendar(conn, day)
        if not cal["is_open"] or (day == clock.date().isoformat() and clock.time() < time(9,29)):
            return dict(trade_date=day, status="skipped", gaps=["休市或竞价数据尚未到达可用时段"])
        with (nullcontext() if dry_run else store.lock(root, day)):
            old = _read(path, day) or {}
            same_slot = scheduled_at and old.get("latest_attempt", {}).get("scheduled_at") == scheduled_at
            if same_slot or (old.get("status") == "complete" and not old.get("post_reference_pending")):
                return old
            roster = old.get("selection") if old.get("selection", {}).get("members") else build_selection(conn, day, cal)
            members = roster["members"]
            codes = {r["code"] for r in members}
            cutoff = datetime.combine(date.fromisoformat(day), time(9,15), data.TZ)
            if (len(codes) != len(members) or roster["as_of_date"] != cal["previous"] or roster["trade_date"] != day
                    or roster["cutoff_at"] != cutoff.isoformat()
                    or roster.get("sha256") != data.digest({k:v for k,v in roster.items() if k!="sha256"})):
                raise data.EvidenceError("样本来源日期、摘要、截止或代码冲突")
            facts = dict(old.get("observations", {}))
            pending = {c for c in codes if c not in facts or facts[c].get("pre_close") is None}
            if old and codes and not pending and not old.get("reference_conflicts") and not old.get("post_reference_pending"):
                return old
            gaps = list(roster.get("gaps", []))
            refs = dict(old.get("independent_daily_reference", {}))
            reference_conflicts = dict(old.get("reference_conflicts", {}))
            try:
                fresh = load_references(conn, day, codes)
                for code, ref in fresh.items():
                    original = refs.get(code)
                    if original and any(original.get(k) != ref.get(k) for k in ("open", "pre_close")):
                        reference_conflicts[code] = dict(prior=original, received=ref, seen_at=clock.isoformat())
                    else:
                        refs.setdefault(code, ref)
                        reference_conflicts.pop(code, None)
            except ERRORS:
                gaps.append("同日独立日线参考未核验")
            attempt = dict(source=SOURCE, trade_date=day, input_by=input_by, requested_at=clock.isoformat(),
                           source_rows=None, matched_scope_rows=0, documentary_source=DOC,
                           scheduled_at=scheduled_at, auction_session_end_at=day+"T09:25:00+08:00",
                           source_timestamp_precision="trade_date",
                           mode="same_day" if day == clock.date().isoformat() else "historical_replay")
            problems = {c:"同日独立日线参考刷新值与旧依据冲突，保留原值" for c in reference_conflicts}
            # An observed auction can acquire its reference on a retry even if the auction source is temporarily down.
            for code in sorted((pending | set(old.get("post_reference_pending", []))) & facts.keys() & refs.keys()):
                try:
                    enriched = observation(facts[code]["raw"], day, refs[code])
                    if facts[code].get("pre_close") is None:
                        facts[code] = _attach_reference(facts[code], enriched, clock.isoformat())
                    else:
                        facts[code] = {**facts[code], "daily_reference_verified_at": clock.isoformat()}
                    pending.discard(code)
                except data.EvidenceError as exc:
                    problems[code] = str(exc)
            try:
                selected = _query_scope(day, pending, query, query_code, attempt, problems) if pending else {}
                received = clock if now is not None else datetime.now(data.TZ)
                attempt["finished_at"] = received.isoformat()
                if not pending:
                    attempt["status"] = "not_requested"
                for code, row in selected.items():
                    if code in problems:
                        continue
                    try:
                        item = observation(row, day, refs.get(code))
                        prior = facts.get(code)
                        if prior:
                            facts[code] = _attach_reference(prior, item, received.isoformat())
                        else:
                            item["acquired_at"] = received.isoformat()
                            item["auction_session_end_at"] = attempt["auction_session_end_at"]
                            item["source_timestamp_precision"] = "trade_date"
                            item["reference_verified_at"] = received.isoformat() if item["pre_close"] else None
                            facts[code] = item
                    except ERRORS as exc:
                        problems[code] = str(exc)
            except (ValueError, OSError, RuntimeError) as exc:
                finished = clock if now is not None else datetime.now(data.TZ)
                attempt.update(status="source_failed", reason=str(exc), finished_at=finished.isoformat())
                gaps.append(str(exc))
            for code in pending - facts.keys() - problems.keys():
                problems[code] = attempt.get("reason") or "没有日期代码匹配的有效竞价成交记录"
            for code, fact in facts.items():
                if fact["pre_close"] is None:
                    problems.setdefault(code,"成交事实已取得，除权参考昨收待核验")
            cores = {m["code"] for m in members if m["core"]}
            cohort = {m["code"] for m in members if m["groups"] and m["previous_limit_up"] is True}
            coverage = dict(expected=len(codes), core_expected=len(cores), previous_limit_up_in_theme_expected=len(cohort),
                            overlap=len(cores & cohort), matched=sum(v["status"]=="matched" for v in facts.values()),
                            explicit_no_match=sum(v["status"]=="no_match" for v in facts.values()),
                            core_observed=len(cores & facts.keys()), previous_limit_up_in_theme_observed=len(cohort & facts.keys()),
                            observed=len(facts), return_reference_available=sum(v["pre_close"] is not None for v in facts.values()),
                            independent_daily_reference=len(refs))
            status = ("complete" if codes and len(facts)==len(codes) and not problems and not gaps else
                      "partial" if facts else "source_failed")
            result = dict(schema=SCHEMA, trade_date=day, status=status, input_by=input_by, captured_at=clock.isoformat(),
                          path=str(path), selection=roster, observations=facts, independent_daily_reference=refs,
                          coverage=coverage, stock_gaps=problems, reference_conflicts=reference_conflicts,
                          gaps=gaps, latest_attempt=attempt)
            if scheduled_at or old.get("post_reference_pending"):
                # 早盘日线可能尚未生成。保留竞价原始回执，盘后只补参考核验。
                result["post_reference_pending"] = sorted(
                    code for code in facts if code not in refs or code in problems)
            if old:
                revision = root / "revisions" / day / (old["sha256"] + ".json")
                result["previous_sha256"] = old["sha256"]
                if not dry_run:
                    if revision.exists():
                        if _read(revision, day)["sha256"] != old["sha256"]:
                            raise data.EvidenceError("竞价旧版本备份摘要冲突")
                    else:
                        atomic_write(revision, path.read_text(encoding="utf-8"))
            result["sha256"] = data.digest(result)
            if not dry_run:
                atomic_write(path, json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))+"\n")
            return result
