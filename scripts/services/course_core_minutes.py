"""盘后固定前一开放日观察名单，保存单源全天一分钟行情及同日交易依据。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from datetime import date, datetime, time
import hashlib
import io
import json
import os
import pickle
from pathlib import Path
import re
import sqlite3

from db.connection import get_readonly_connection
from providers.base import DataResult
from providers.sina_provider import SinaProvider
from providers.tdx_provider import TdxProvider
from providers.tushare_provider import TushareProvider
from providers.tushare_transport import TushareMirrorClient
from services.board_break_intraday import data, eastmoney, store
from services.volume_record.service import atomic_write

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data/reports/course-core-minutes"
SCHEMA = "course-core-minutes-v1"
FINAL_STATES = {"complete", "suspended"}
SOURCES = {"sina:CN_MarketDataService.getKLineData:1min", "tushare:stk_mins",
           "tdx:get_security_bars:1min", eastmoney.SOURCE}


def _stamp():
    return datetime.now(data.TZ).isoformat()


def _daily_rows(response, source, day, codes, fields, *, include_unlimited=False):
    """部分回包逐票核验。其他票缺失不影响可核验行；目标票重复整票拒收。"""
    if not response.success or response.source != source or not isinstance(response.data, list):
        raise data.EvidenceError(f"{source}来源未取得")
    values, invalid = {}, set()
    for row in response.data:
        if not isinstance(row, dict):
            continue
        code = row.get("ts_code")
        if code not in codes:
            continue
        if (code in values or code in invalid or row.get("code", code) != code or
                str(row.get("trade_date")).replace("-", "") != day.replace("-", "")):
            invalid.add(code)
            continue
        try:
            if (include_unlimited and source == "tushare:stk_limit" and fields == ("up_limit", "down_limit")
                    and data.number(row.get("down_limit")) == 0 and data.number(row.get("up_limit")) == 99999.99):
                nums = {"down_limit": 0.0, "up_limit": 99999.99}
            else:
                nums = {key: data.number(row.get(key), positive=True) for key in fields}
            if "up_limit" in nums and nums["down_limit"] >= nums["up_limit"]:
                raise data.EvidenceError("涨跌停价无效")
            values[code] = {**nums, "source": source, "trade_date": day, "code": code}
        except data.EvidenceError:
            invalid.add(code)
    return {code: val for code, val in values.items() if code not in invalid}


class _PrimitiveUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        raise ValueError("归档只允许普通数据字段")


def _local_limits(day, codes, directory, *, include_unlimited=False):
    run = directory / "volume-buildup" / "runs" / day
    target = run / f"limits-{day}.pkl"
    if not target.exists():
        return {}
    report = json.loads((run / "report.json").read_text())
    receipts = json.loads((run / "source-receipts.json").read_text())
    if target.stat().st_size > 16_000_000:
        raise data.EvidenceError("同日限价快照大小异常")
    raw = target.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if report.get("date") != day or report.get("source_sha256", {}).get(str(target.resolve())) != sha:
        raise data.EvidenceError("同日限价归档日期或SHA不符")
    receipt = next((r for r in receipts if r.get("capability") == "get_stock_limit_prices"
        and r.get("args") == [day] and r.get("source") == "tushare:stk_limit" and r.get("success") is True
        and not r.get("error") and r.get("fetched_at")), None)
    if not receipt:
        raise data.EvidenceError("同日限价取数回执缺失")
    response = DataResult(_PrimitiveUnpickler(io.BytesIO(raw)).load(), "tushare:stk_limit")
    prices = _daily_rows(response, "tushare:stk_limit", day, codes, ("up_limit", "down_limit"),
                         include_unlimited=include_unlimited)
    return {code: {**row, "archive": str(target), "source_sha256": sha, "requested_at": receipt["fetched_at"]}
            for code, row in prices.items()}


def _local_contexts(conn, day, codes, report_root):
    """核验同日盘后日线及断板批次的哈希，保留单股已闭合窗口的实际长度。"""
    out = {c: {} for c in codes}
    gaps = []
    try:
        record = conn.execute(
            "SELECT json_extract(raw_data,'$.raw_data.zhong_prefix_effect.evidence'),"
            "json_extract(raw_data,'$.raw_data.date'),json_extract(raw_data,'$.date') "
            "FROM daily_market WHERE date=?", (day,)).fetchone()
        if record and record[0] and tuple(record[1:]) == (day, day):
            ev = json.loads(record[0])
            result = DataResult(ev.get("quotes"), ev.get("quote_source"))
            prices = _daily_rows(result, "tushare:daily", day, codes, ("pre_close", "close"))
            for c, price in prices.items():
                out[c]["reference"] = dict(price, archive="daily_market.zhong_prefix_effect",
                                           requested_at=ev.get("quote_fetched_at"))
    except (ValueError, TypeError, sqlite3.Error) as exc:
        gaps.append("本地日线未核验（" + type(exc).__name__ + "）")
    try:
        for code, val in _local_limits(day, codes, report_root.parent).items():
            out[code]["limits"] = val
    except (ValueError, TypeError, OSError, EOFError, pickle.UnpicklingError) as exc:
        gaps.append("本地日期限价快照未通过来源核验（" + type(exc).__name__ + "）")
    for manifest in sorted((report_root / "live" / day).glob("*/*/manifest.json")):
        try:
            snap, sha = store.load(manifest)
            if snap["date"] != day or snap["mode"] != "live":
                continue
            stage = snap["stage"]
            cutoff = {"tail": "14:50", "open-review": "09:40"}[stage]
            proof = dict(batch_manifest=str(manifest), batch_sha256=sha)
            susp = snap.get("suspensions") or {}
            if susp.get("date") == day and susp.get("raw_sha256") == data.digest(susp.get("rows")):
                checked = data.normalize_suspensions(DataResult(susp.get("rows"), susp.get("source")), day)
                matched = {r["ts_code"]: r for r in checked["rows"] if r["ts_code"] in codes}
                for c in codes:
                    out[c]["suspension"] = dict(full_day=c in checked["full_day"], record=matched.get(c),
                        source=checked["source"], trade_date=day, requested_at=susp["requested_at"],
                        source_sha256=checked["raw_sha256"], **proof)
            for obj in snap.get("candidates", []) + snap.get("excluded", []):
                code = obj["code"]
                if code not in codes:
                    continue
                limits = obj.get("limits") or {}
                if limits.get("date") == day and not out[code].get("limits"):
                    low = data.number(limits.get("down_limit"), positive=True)
                    high = data.number(limits.get("up_limit"), positive=True)
                    if low < high:
                        out[code]["limits"] = dict(down_limit=low, up_limit=high, code=code, trade_date=day,
                            source="tushare:stk_limit", requested_at=snap["started_at"], **proof)
                block = obj.get("minute")
                if not block or block.get("source") not in SOURCES or block.get("status") != "complete":
                    continue
                validated = data.normalize_minutes([dict(r, ts_code=code) for r in block["rows"]], code, day, cutoff)
                if (validated["rows"] == block["rows"] and block.get("volume_unit") == "share"
                        and block.get("amount_unit") == "CNY"
                        and re.fullmatch("[0-9a-f]{64}", str(block.get("raw_sha256")))):
                    old = out[code].get("partial_minutes", {})
                    if len(validated["rows"]) > len(old.get("rows", [])):
                        out[code]["partial_minutes"] = dict(block, cutoff=cutoff, **proof)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            gaps.append("本地分钟批次未通过核验：" + str(manifest) + "（" + type(exc).__name__ + "）")
    return out, gaps


class ClosedMarketSource(data.MarketSource):
    """日线辅助源独立失败时，新浪/通达信/东方财富的完整分钟仍可保留。"""
    def __init__(self):
        self.provider = TushareProvider({})
        # 开放日已从本地完整日历核验。分钟、日线和停牌接口各自验收，
        # 不以另一日期的远程 trade_cal 探针决定这些接口能否调用。
        token = os.environ.get("TUSHARE_TOKEN")
        self.has_daily = bool(token)
        if token:
            self.provider.pro = TushareMirrorClient(token, timeout=12)
        self.sina = SinaProvider({})
        self.sina.initialize()
        self.tdx = TdxProvider({"timeout": 3})
        self.tdx.initialize()
        self.eastmoney = eastmoney

    def _tushare_minutes(self, code, day, cutoff, mode):
        if not self.has_daily:
            raise data.EvidenceError("Tushare分钟凭据缺失")
        return super()._tushare_minutes(code, day, cutoff, mode)

    def contexts(self, day, codes):
        contexts = {c: {} for c in codes}
        gaps = []
        if not self.has_daily:
            return contexts, ["Tushare每日参考价、限价、停牌源未连接"]
        for key, name, fetch, fields in (
            ("reference", "tushare:daily", self.provider.get_market_daily_quotes, ("pre_close", "close")),
            ("limits", "tushare:stk_limit", self.provider.get_stock_limit_prices, ("up_limit", "down_limit")),
        ):
            try:
                requested = _stamp()
                records = _daily_rows(fetch(day), name, day, codes, fields)
                for c, row in records.items():
                    contexts[c][key] = {**row, "requested_at": requested}
            except (TypeError, ValueError, OSError) as exc:
                gaps.append(f"{name}来源失败（{type(exc).__name__}）")
        try:
            susp = self.suspensions(day)
            by_code = {r["ts_code"]: r for r in susp["rows"] if r["ts_code"] in codes}
            for c in codes:
                contexts[c]["suspension"] = {
                    "full_day": c in susp["full_day"], "record": by_code.get(c),
                    "source": susp["source"], "trade_date": day,
                    "source_sha256": susp["raw_sha256"], "requested_at": susp["requested_at"]}
        except (OSError, TypeError, ValueError) as exc:
            gaps.append(f"tushare:suspend_d当日记录未核验（{type(exc).__name__}）")
        return contexts, gaps


def _check_window(rows, reference=None):
    if any((r["vol"] == 0) != (r["amount"] == 0) for r in rows):
        raise data.EvidenceError("分钟成交额和成交量的零值冲突")
    if not any(r["vol"] > 0 for r in rows):
        raise data.EvidenceError("全天没有可验证的交易分钟")
    if (reference and rows[-1]["time"].endswith("15:00:00")
            and abs(rows[-1]["close"] - reference["close"]) > 0.005):
        raise data.EvidenceError("分钟尾盘收盘与同日日线收盘不符")


def _check_minutes(block, code, day):
    if (not isinstance(block, dict) or block.get("status") != "complete" or block.get("source") not in SOURCES
            or block.get("volume_unit") != "share" or block.get("amount_unit") != "CNY"
            or not re.fullmatch("[0-9a-f]{64}", str(block.get("raw_sha256")))
            or len(block.get("rows", ())) != 240 or not block.get("requested_at")):
        raise data.EvidenceError("全天分钟身份、单位、来源或根数未经核验")
    rows = block["rows"]
    checked = data.normalize_minutes([dict(row, ts_code=code) for row in rows], code, day, "15:00")
    if rows != checked["rows"]:
        raise data.EvidenceError("分钟顺序或数据规范不一致")
    _check_window(rows)


def _collect(code, day, prior, ctx, market):
    row = {**prior, "code": code, "trade_date": day, "gaps": [], "requested_at": _stamp()}
    for k, val in ctx.items():
        if k == "partial_minutes" and len(row.get(k, {}).get("rows", [])) > len(val["rows"]):
            continue
        if val:
            row[k] = val
    suspended = row.get("suspension", {}).get("full_day")
    partial_trades = any(bar["vol"] > 0 and bar["amount"] > 0
                         for bar in row.get("partial_minutes", {}).get("rows", []))
    if suspended and not row.get("minutes") and not row.get("reference") and not partial_trades:
        row["status"] = "suspended"
        return row
    if not row.get("minutes"):
        try:
            if market is None:
                raise data.EvidenceError("分钟来源未连接")
            bars = market.minutes(code, day, "15:00", "history", keep_partial=True,
                                  verify_rows=lambda rows: _check_window(rows, row.get("reference")))
            _check_minutes(bars, code, day)
            bars.setdefault("adjust", "none")
            bars.setdefault("identity", "response_ts_code" if bars["source"] == "tushare:stk_mins"
                            else "single_symbol_request")
            acquired = datetime.now(data.TZ)
            bars.update(acquired_at=acquired.isoformat(), acquisition=(
                "post_close" if acquired.date().isoformat() == day else "historical_replay"))
            row["minutes"] = bars
        except (ValueError, OSError, KeyError, TypeError, RuntimeError, AttributeError) as exc:
            partial = getattr(exc, "partial_minutes", None)
            if (partial and partial.get("source") in SOURCES and
                    len(partial["rows"]) > len(row.get("partial_minutes", {}).get("rows", []))):
                try:
                    _check_window(partial["rows"], row.get("reference"))
                except data.EvidenceError as problem:
                    row["gaps"].append("候选分钟片段未通过交易校验：" + str(problem))
                else:
                    row["partial_minutes"] = partial
            row["gaps"].append("分钟：" + (str(exc) if isinstance(exc, data.EvidenceError) else type(exc).__name__))
    if not row.get("reference"):
        row["gaps"].append("当日行情的除权除息参考昨收缺失")
    if not row.get("limits"):
        row["gaps"].append("同日实际涨跌停价格缺失")
    if suspended is None:
        row["gaps"].append("同日停牌名单未核验")
    elif suspended:
        row["gaps"].append("全天停牌记录与当日交易证据冲突")
    minutes = row.get("minutes")
    if minutes and row.get("reference"):
        # 同日官方未复权收盘与最后一根闭合分钟互验，防止错票或复权序列混入。
        if abs(minutes["rows"][-1]["close"] - row["reference"]["close"]) > 0.005:
            row["gaps"].append("分钟尾盘收盘与同日日线收盘不符")
            row.pop("minutes")
    if not row["gaps"]:
        row["status"] = "complete"
    else:
        row["status"] = "partial" if row.get("minutes") or row.get("partial_minutes") else "source_failed"
    return row


def _read(path, day):
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    checksum = obj.pop("sha256", None)
    if checksum != data.digest(obj) or obj.get("schema") != SCHEMA or obj.get("trade_date") != day:
        raise data.EvidenceError("已有分钟归档损坏或日期不符，保留原件")
    return {**obj, "sha256": checksum}


def summary(result):
    return {key: result[key] for key in ("trade_date", "status", "coverage", "path", "gaps") if key in result}


def capture_day(day, input_by, *, dry_run=False, root=DEFAULT_ROOT, db_path=None,
                now=None, universe_builder=None, source_factory=ClosedMarketSource,
                local_root=None, expected_prior_universe_sha=None):
    """完整已收盘开放日；同一名单重跑只补缺项，历史回采标实际取得时间。"""
    if not isinstance(input_by, str) or not input_by.strip():
        raise ValueError("请求者不能为空")
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("交易日格式应为YYYY-MM-DD")
    clock = (now or datetime.now(data.TZ)).astimezone(data.TZ)
    if clock < datetime.combine(date.fromisoformat(day), time(15, 0), tzinfo=data.TZ):
        return dict(status="skipped", trade_date=day, gaps=["目标日尚未收盘"])
    root = Path(root)
    path = root / f"{day}.json"
    with (nullcontext() if dry_run else store.lock(root, day)):
        old = _read(path, day)
        revise = expected_prior_universe_sha is not None
        if revise and (not old or old["universe"].get("sha256") != expected_prior_universe_sha):
            raise data.EvidenceError("待更正的名单摘要与现存归档不匹配")
        if old and old["status"] == "complete" and not revise:
            return old
        with closing(get_readonly_connection(db_path)) as conn:
            calendar = data.calendar(conn, day)
            if not calendar["is_open"]:
                return dict(status="skipped", trade_date=day, gaps=["SSE休市日"])
            if old and old.get("universe", {}).get("members") and not revise:
                universe = old["universe"]
            else:
                if universe_builder is None:
                    from services.course_intraday_universe import build_universe
                    universe_builder = build_universe
                universe = universe_builder(conn, day)
        members = universe.get("members", [])
        codes = [m["code"] for m in members]
        if codes and (universe["status"] not in {"complete", "partial"} or universe["trade_date"] != day
                or universe["as_of_date"] != calendar["previous"] or len(codes) != len(set(codes))
                or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", c) for c in codes)):
            raise data.EvidenceError("名单时点、代码或日期无效")
        if codes:
            expected_cutoff = datetime.combine(date.fromisoformat(day), time(9, 30), tzinfo=data.TZ)
            if (datetime.fromisoformat(universe["cutoff_at"]) != expected_cutoff
                    or universe.get("sha256") != data.digest({k: v for k, v in universe.items() if k != "sha256"})):
                raise data.EvidenceError("名单开盘截止时间或摘要未经核验")
        if revise:
            if not codes or not universe.get("rule_version") or (
                    universe["rule_version"] == old["universe"].get("rule_version")):
                raise data.EvidenceError("只允许事前证据和新名单规则均齐备的显式更正")
        rows = {c: r for c, r in (old or {}).get("stocks", {}).items() if c in codes}
        pending = [c for c in codes if rows.get(c, {}).get("status") not in FINAL_STATES]
        gaps = list(universe.get("gaps", []))
        if pending:
            with closing(get_readonly_connection(db_path)) as conn:
                local, local_gaps = _local_contexts(conn, day, set(pending),
                    Path(local_root) if local_root is not None else root.parent / "board-break-intraday")
            gaps.extend(local_gaps)
            market, contexts = None, {}
            try:
                market = source_factory()
                missing_context = {c for c in pending if any(
                    not local[c].get(k) and not rows.get(c, {}).get(k)
                    for k in ("reference", "limits", "suspension"))}
                if missing_context:
                    contexts, new_gaps = market.contexts(day, missing_context)
                    gaps.extend(new_gaps)
            except (OSError, RuntimeError, ValueError, ImportError, TypeError, AttributeError, KeyError) as exc:
                gaps.append("上游初始化或日期来源失败（" + type(exc).__name__ + "）")
            def fetch(code):
                context = {**contexts.get(code, {}), **local[code]}
                for key in ("reference", "limits", "suspension"):
                    if rows.get(code, {}).get(key):
                        context.pop(key, None)
                return _collect(code, day, rows.get(code, {}), context, market)
            with ThreadPoolExecutor(max_workers=4) as pool:
                for item in pool.map(fetch, pending):
                    rows[item["code"]] = item
        if old and not pending and old.get("universe", {}).get("members") and not revise:
            return old
        coverage = dict(expected=len(codes), minute_complete=sum(bool(r.get("minutes")) for r in rows.values()),
                        complete=sum(r["status"] == "complete" for r in rows.values()),
                        suspended=sum(r["status"] == "suspended" for r in rows.values()))
        coverage["unresolved"] = len(codes) - coverage["complete"] - coverage["suspended"]
        status = "partial"
        if universe["status"] == "complete" and not coverage["unresolved"]:
            status = "complete"
        elif not (coverage["minute_complete"] or coverage["suspended"]):
            status = "source_failed"
        coverage["reference_verified"] = sum(1 for row in rows.values() if row.get("reference"))
        coverage["limit_prices_verified"] = sum(1 for row in rows.values() if row.get("limits"))
        coverage["suspension_verified"] = sum(1 for row in rows.values() if row.get("suspension"))
        coverage["partial_window_only"] = sum(
            1 for r in rows.values() if r.get("partial_minutes") and not r.get("minutes"))
        if coverage["partial_window_only"] and status == "source_failed":
            status = "partial"
        result = dict(schema=SCHEMA, trade_date=day, created_at=(old or {}).get("created_at", clock.isoformat()),
                      captured_at=clock.isoformat(), input_by=input_by, status=status,
                      scope="前一开放日已知名单；收盘后采集09:31至15:00的240个交易分钟，单票不混源",
                      universe=universe, stocks=rows, coverage=coverage, gaps=gaps, path=str(path))
        if old and old.get("supersedes"):
            result["supersedes"] = old["supersedes"]
        if revise:
            backup = root / "revisions" / day / f"{old['sha256']}.json"
            result["supersedes"] = dict(previous_file=str(backup), previous_sha256=old["sha256"],
                previous_universe_sha256=expected_prior_universe_sha, new_universe_sha256=universe["sha256"],
                reason="事前名单规则版本更正", input_by=input_by, revised_at=clock.isoformat())
            if not dry_run:
                if backup.exists() and _read(backup, day)["sha256"] != old["sha256"]:
                    raise data.EvidenceError("已有原版本备份摘要不符")
                if not backup.exists():
                    atomic_write(backup, path.read_text(encoding="utf-8"))
        result["sha256"] = data.digest(result)
        if not dry_run:
            atomic_write(path, json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        return result


def capture_with_previous(day, input_by, *, dry_run=False, root=DEFAULT_ROOT, db_path=None, now=None):
    """先采本日，收盘后一次补采严格前一开放日现有、仍缺全天分钟的日档。"""
    clock = (now or datetime.now(data.TZ)).astimezone(data.TZ)
    main = capture_day(day, input_by, dry_run=dry_run, root=root, db_path=db_path, now=clock)
    prior = dict(status="skipped", reason="仅本日开放且收盘后才触发前日补采")
    if (main["status"] != "skipped" and day == clock.date().isoformat()
            and clock.time() >= time(15, 0)):
        try:
            with closing(get_readonly_connection(db_path)) as conn:
                previous = data.calendar(conn, day)["previous"]
            root = Path(root)
            receipt_path = root / "previous-retries" / f"{day}.json"
            with (nullcontext() if dry_run else store.lock(root, "previous-retry-" + day)):
                old = _read(root / f"{previous}.json", previous)
                if not old or old["coverage"]["expected"] <= old["coverage"]["minute_complete"] + old["coverage"]["suspended"]:
                    prior = dict(trade_date=previous, status="skipped", reason="无已归档且欠完整分钟的前日名单")
                elif receipt_path.exists():
                    receipt = json.loads(receipt_path.read_text())
                    if (receipt["trigger_date"] != day or receipt["previous"]["trade_date"] != previous
                            or receipt.get("sha256") != data.digest({k:v for k,v in receipt.items() if k!="sha256"})):
                        raise data.EvidenceError("前一开放日补采回执损坏")
                    prior = dict(trade_date=previous, status=receipt["previous"]["status"],
                                 reused=True, receipt=str(receipt_path))
                else:
                    try:
                        prior = summary(capture_day(previous, input_by, dry_run=dry_run, root=root,
                                                    db_path=db_path, now=clock))
                    except (ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
                        prior = dict(trade_date=previous, status="source_failed", reason=str(exc))
                    if not dry_run:
                        receipt = dict(trigger_date=day, requested_at=clock.isoformat(), input_by=input_by,
                                       previous=prior)
                        receipt["sha256"] = data.digest(receipt)
                        atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError, sqlite3.Error) as exc:
            prior = dict(status="source_failed", reason=str(exc))
    status = main["status"]
    if status == "complete" and prior["status"] not in ("complete", "skipped"):
        status = "partial"
    return dict(trade_date=day, status=status, current=summary(main), previous=prior)
