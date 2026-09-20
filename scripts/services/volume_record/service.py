"""盘后天量采集，完整名单与缺口原子归档；无业务库、池、计划或推送写入。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta
import fcntl
import json
import logging
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from db.connection import get_readonly_connection
from utils.network_env import without_standard_http_proxy
from .history import build_baseline, code, day, fingerprint, load_baseline, metric_spec, number, rows_by_day

ROOT = Path(__file__).resolve().parents[3]
LOG = logging.getLogger(__name__)


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def run_lock(directory, dry_run):
    if dry_run:
        yield
        return
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def calendar_days(start, end, db_path):
    conn = get_readonly_connection(db_path)
    try:
        rows = conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' "
                            "AND date BETWEEN ? AND ? ORDER BY date", (start, end)).fetchall()
    finally:
        conn.close()
    expected = [(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)]
    if [r[0] for r in rows] != expected or any(r[1] not in (0, 1) for r in rows):
        raise ValueError("SSE交易日历缺失或不完整")
    return {r[0]: r[1] for r in rows}


def require(result, source, *, nonempty=True):
    if (not result.success or not isinstance(result.data, (dict, list))
            or (nonempty and not result.data) or result.source != source):
        raise ValueError(f"{source}来源失败、空响应或口径不同:{result.error}")
    return result.data


def universe_inputs(registry, target, min_market_count, *, current_day, metric="volume"):
    spec = metric_spec(metric)
    rows = require(registry.call_specific("tushare", "get_stock_universe_as_of", target),
                   "tushare:stock_basic:L+D+P:as_of")
    identities = {}
    for row in rows:
        stock = code(row.get("ts_code"))
        if stock in identities:
            raise ValueError("证券身份重复")
        listed = day(row.get("list_date"))
        delisted = day(row["delist_date"]) if row.get("delist_date") else None
        if not row.get("name"):
            raise ValueError("证券名称缺失")
        identities[stock] = dict(code=stock, name=row["name"], list_date=listed,
                                 active=listed <= target and (delisted is None or delisted > target))
    st = require(registry.call_specific("tushare", "get_stock_st", target), "tushare:stock_st")
    st_codes = set()
    for row in st:
        stock = code(row.get("ts_code"))
        if stock in st_codes or day(row.get("trade_date")) != target:
            raise ValueError("ST名单重复或日期不符")
        st_codes.add(stock)
    raw = require(registry.call_specific("tushare", "get_market_daily_quotes", target), "tushare:daily")
    quotes = {}
    for row in raw:
        stock = code(row.get("ts_code"))
        if stock in quotes or day(row.get("trade_date")) != target or stock not in identities:
            raise ValueError("全市场日线重复、日期错位或无法匹配身份")
        quotes[stock] = {**row, spec["field"]: number(row.get(spec["field"]))}
    # name 是采集时静态简称；历史日不能用未来退市名称回删，上市/退市日期和当日ST源决定身份。
    eligible = {k: v for k, v in identities.items() if v["active"] and k not in st_codes
                and not k.startswith(("200", "201", "900"))
                and (target != current_day or "退" not in v["name"])}
    if len(quotes) < min_market_count or len(eligible) < min_market_count:
        raise ValueError("全市场行情或证券宇宙不足最低数量")
    if len(set(quotes) & set(eligible)) / len(eligible) < 0.98:
        raise ValueError("全市场行情覆盖低于98%")
    return eligible, quotes


def verified_suspensions(registry, target, cache):
    if target not in cache:
        rows = require(registry.call_specific("tushare", "get_suspend_list", target),
                       "tushare:suspend_d", nonempty=False)
        codes, seen = set(), set()
        for row in rows:
            stock = code(row.get("ts_code"))
            if day(row.get("trade_date")) != target or stock in seen:
                raise ValueError("停牌来源日期错位或重复")
            seen.add(stock)
            if row.get("suspend_type") == "S" and "suspend_timing" in row and row["suspend_timing"] in (None, ""):
                codes.add(stock)
        cache[target] = codes
    return cache[target]


def prior_observation_exclusions(registry, identities, quotes, target, db_path, limit, metric="volume"):
    """此前任一天量>=今日足以否定严格新高；此处永远不确认新高。"""
    spec = metric_spec(metric)
    excluded, diagnostics = {}, []
    if limit <= 0:
        return excluded, diagnostics
    try:
        conn = get_readonly_connection(db_path)
        try:
            days = [r[0] for r in conn.execute(
                "SELECT date FROM trade_calendar WHERE exchange='SSE' AND is_open=1 "
                "AND date < ? ORDER BY date DESC LIMIT ?", (target, limit))]
        finally:
            conn.close()
    except Exception as exc:
        return excluded, [f"calendar:{exc}"]
    for index, prior_day in enumerate(days, 1):
        try:
            prior_day = day(prior_day)
            if prior_day >= target:
                raise ValueError("反例日期必须早于目标日")
            rows = require(registry.call_specific("tushare", "get_market_daily_quotes", prior_day),
                           "tushare:daily")
            observed = {}
            for row in rows:
                stock = code(row.get("ts_code"))
                if stock in observed or day(row.get("trade_date")) != prior_day:
                    raise ValueError("反例行情重复或日期错位")
                observed[stock] = number(row.get(spec["field"]))
            # 整日校验成功后才采用；稀疏行情仅降低优化效果，不制造完整历史。
            for stock, volume in observed.items():
                if (stock in identities and stock in quotes and stock not in excluded
                        and identities[stock]["list_date"] <= prior_day
                        and volume >= quotes[stock][spec["field"]]):
                    excluded[stock] = dict(code=stock, prior_date=prior_day,
                                           **{f"prior_{spec['value']}": volume, spec["value"]: quotes[stock][spec["field"]]},
                                           source="tushare:daily")
        except Exception as exc:
            # 反例源失败不影响原来的全历史认证；未排除的股票全部走原路径。
            diagnostics.append(f"{prior_day}:{exc}")
        if index % 10 == 0 or index == len(days):
            LOG.info("历史天量反例核验 %s/%s，已明确排除 %s 只", index, len(days), len(excluded))
    return excluded, diagnostics


def evaluate_stock(provider, registry, identity, quote, target, calendar, baseline, suspension_cache, metric="volume"):
    spec = metric_spec(metric)
    listed, stock = identity["list_date"], identity["code"]
    start = max(listed, target[:8] + "01")
    result = provider.get_stock_daily_range(stock, start, target)
    rows = rows_by_day(require(result, "tushare:daily"), stock, start, target, metric)
    if target not in rows or rows[target] != quote[spec["field"]]:
        raise ValueError("个股日线与全市场当日所选量额不一致")
    expected = {d for d, flag in calendar.items() if flag and start <= d <= target}
    if set(rows) - expected:
        raise ValueError("日线落在非开放日")
    for missing in sorted(expected - set(rows)):
        if stock not in verified_suspensions(registry, missing, suspension_cache):
            raise ValueError(f"本月缺日线且无全天停牌证明:{missing}")
    if baseline is None and min(rows) != listed:
        raise ValueError("上市首日行情缺失")
    prior = {d: vol for d, vol in rows.items() if d < target}
    if baseline:
        prior[baseline["max_date"]] = baseline[spec["maximum"]]
    if not prior:
        return None  # 上市首日只形成基线，不能称作突破此前纪录。
    peak_date = max(sorted(prior), key=prior.get)
    peak = prior[peak_date]
    if quote[spec["field"]] <= peak:
        return None
    return dict(code=stock, name=identity["name"], list_date=listed,
                **{spec["value"]: quote[spec["field"]], spec["previous"]: peak}, previous_max_date=peak_date,
                multiple=round(quote[spec["field"]] / peak, 6), pct_chg=quote.get("pct_chg"),
                history_first_date=listed, history_through=target,
                history_rows=(baseline["observations"] if baseline else 0) + len(rows),
                duplicate_rows_collapsed=(baseline.get("duplicate_rows_collapsed", 0) if baseline else 0) + rows.duplicate_count,
                source="tushare:daily+monthly", industry="未分类")


def render(report):
    if "metrics" in report:
        return render_dual(report)
    spec = metric_spec(report.get("metric", "volume"))
    lines = [f"# 创历史天量个股 · {spec['label']} · {report['date']}", "",
             f"- 状态：**{report['status']}**；已核验命中：{report['matched_count'] if report['matched_count'] is not None else '未计算'}",
             f"- [事实·计算] 沪深北A股，剔ST/退市/B股；原始{spec['label']}（{spec['unit']}）严格超过上市以来此前最大值，持平不计，上市首日不计。",
             "- 原始量额均不复权；成交量受送转扩股影响，成交额同时受价格影响；两者独立判断。",
             "- 历史完成月按日/月总量核验；申万二级为采集时点快照，未分类不影响量能判定。",
             "- 名称为采集时点简称；历史日按上市/退市日期与当日ST名单筛选，不回推历史简称。历史基线复用认证时的来源快照。",
             "- 完整归档以同名JSON收据为准；Markdown是可重建的展示文件。",
             f"- 覆盖：{report.get('coverage', {})}；完整市场名单：{'是' if report.get('is_exhaustive') else '否'}",
             "- 来源：[Tushare日线](https://tushare.pro/document/1?doc_id=27)、[月线](https://tushare.pro/document/2?doc_id=145)。", ""]
    if report.get("error"):
        lines.append(f"- 错误：{report['error']}；不能解释为无天量个股。")
    lines += [f"| 申万二级 | 代码 | 名称 | 当日{spec['label']}（{spec['unit']}） | 此前最大值（{spec['unit']}） | 原纪录日期 | 倍数 |",
              "|---|---|---|---:|---:|---|---:|"]
    for row in report["stocks"]:
        safe = lambda value: str(value).replace("|", "／").replace("\n", " ")
        lines.append(f"| {safe(row['industry'])} | {row['code']} | {safe(row['name'])} | {row[spec['value']]:,.2f} | "
                     f"{row[spec['previous']]:,.2f} | {row['previous_max_date']} | {row['multiple']:.3f} |")
    if report["gaps"]:
        lines += ["", "## 缺口（不得按零处理）"]
        lines += [f"- {gap}" for gap in report["gaps"]]
    return "\n".join(lines) + "\n"


def run(target, input_by, *, registry, root=ROOT, db_path=None, dry_run=False, codes=None,
        now=None, min_market_count=4000, recent_prefilter_days=60, metric="volume"):
    spec = metric_spec(metric)
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    directory = Path(root) / "data/reports/volume-record"
    if metric == "amount":
        directory = directory / "amount"
    report = dict(version=spec["version"], metric=metric, date=target, input_by=input_by, generated_at=now.isoformat(),
                  status="source_failed", matched_count=None, stocks=[], gaps=[], failed_codes=[], is_exhaustive=False,
                  definition=dict(metric="raw_daily_" + spec["field"], unit=spec["unit"], comparison="strictly_greater",
                                  history="since_listing", first_listing_day="excluded",
                                  name_basis="current_stock_basic_snapshot",
                                  historical_delisting_filter="list_date_and_delist_date",
                                  history_revision_policy="certified_source_snapshot_at_collection"))
    try:
        target = day(target)
        if not input_by or not input_by.strip() or now.tzinfo is None:
            raise ValueError("请求者缺失或运行时刻无时区")
        now = now.astimezone(ZoneInfo("Asia/Shanghai"))
        if target > now.date().isoformat():
            raise ValueError("目标日尚未到达")
        if codes and not dry_run:
            raise ValueError("抽样仅允许dry-run，不能覆盖全市场报告")
        calendar = calendar_days(target[:8] + "01", target, db_path)
        if calendar[target] == 0 or (target == now.date().isoformat() and now.hour < 16):
            return {**report, "status": "skipped", "reason": "非交易日或尚未收盘"}
        with run_lock(directory, dry_run), without_standard_http_proxy():
            old = None
            if not dry_run:
                try:
                    old = json.loads((directory / f"{target}.json").read_text())
                    digest = old.pop("sha256")
                    if (old.get("version") != spec["version"] or old.get("date") != target
                            or digest != fingerprint(old)):
                        old = None
                except (OSError, ValueError, KeyError, TypeError):
                    old = None
                # 读取损坏才允许重采；修复Markdown失败不能令完整JSON失去保护。
                if old and old.get("status") == "complete":
                    atomic_write(directory / f"{target}.md", render(old))
                    return {**old, "sha256": digest, "reused": True}
            identities, quotes = universe_inputs(registry, target, min_market_count,
                                                 current_day=now.date().isoformat(), metric=metric)
            provider = registry.get_provider("tushare")
            if provider is None:
                raise ValueError("Tushare不可用")
            all_count = len(identities)
            if codes:
                if set(codes) - set(identities):
                    raise ValueError("抽样代码不在合格A股宇宙")
                identities = {k: identities[k] for k in codes}
            end = (date.fromisoformat(target).replace(day=1) - timedelta(days=1)).isoformat()
            cache_dir = directory / "baseline"
            suspension_cache = {}
            excluded, diagnostics = prior_observation_exclusions(
                registry, identities, quotes, target, db_path, recent_prefilter_days, metric)
            report["prior_observation_exclusions"] = list(excluded.values())
            report["prefilter_diagnostics"] = diagnostics
            LOG.info("历史天量合格宇宙 %s，近期反例排除 %s，剩余待核验 %s", len(identities),
                     len(excluded), len(identities) - len(excluded))
            evaluated = suspended = first_day = 0
            for index, (stock, identity) in enumerate(sorted(identities.items()), 1):
                try:
                    if stock not in quotes:
                        if stock not in verified_suspensions(registry, target, suspension_cache):
                            raise ValueError("当日缺行情，未证明全天停牌")
                        suspended += 1
                        continue
                    if identity["list_date"] == target:
                        first_day += 1
                        continue
                    if stock in excluded:
                        evaluated += 1
                        continue
                    LOG.info("历史天量[%s]全历史核验 %s，当前命中 %s，缺口 %s", metric, stock,
                             len(report["stocks"]), len(report["gaps"]))
                    baseline = load_baseline(cache_dir, stock, identity["list_date"], end, metric)
                    # 旧基线本身只能否定，不能直接确认；确认必须拼接通过核验的后缀直到T。
                    # build_baseline 复用的是已逐月认证的完整上市前缀，而非任意低水位/滚动窗。
                    # 不重复拉取这一不可变来源快照；上游追溯修订需要独立重建基线，不能混入当天增量。
                    if baseline and baseline[spec["maximum"]] >= quotes[stock][spec["field"]]:
                        evaluated += 1
                        continue
                    baseline = build_baseline(provider, stock, identity["list_date"], end, baseline, metric)
                    if baseline and not dry_run:
                        content = {"baseline": baseline, "sha256": fingerprint(baseline), "input_by": input_by}
                        atomic_write(cache_dir / stock / f"{end}.json", json.dumps(content, ensure_ascii=False))
                    match = evaluate_stock(provider, registry, identity, quotes[stock], target,
                                           calendar, baseline, suspension_cache, metric)
                    evaluated += 1
                    if match:
                        report["stocks"].append(match)
                except Exception as exc:
                    report["gaps"].append(f"{stock}:{exc}")
                    report["failed_codes"].append(stock)
                if index % 100 == 0:
                    LOG.info("历史天量进度 %s/%s，已核验 %s，缺口 %s", index, len(identities), evaluated, len(report["gaps"]))
            if report["stocks"]:
                try:
                    result = registry.call("get_stock_sw_industry_map")
                    if not result.success or not isinstance(result.data, dict) or not result.data:
                        raise ValueError("申万二级来源缺失")
                    for row in report["stocks"]:
                        row["industry"] = (result.data.get(row["code"]) or {}).get("sw_l2") or "未分类"
                    if any(row["industry"] == "未分类" for row in report["stocks"]):
                        raise ValueError("部分命中缺少申万二级归属")
                except Exception as exc:
                    report["gaps"].append(str(exc))
            report["stocks"].sort(key=lambda r: (r["industry"], r["code"]))
            report.update(status="partial" if report["gaps"] or codes else "complete",
                          matched_count=len(report["stocks"]), is_exhaustive=not report["gaps"] and not codes,
                          coverage=dict(universe=all_count, requested=len(identities), evaluated=evaluated,
                                        suspended=suspended, first_listing_day=first_day,
                                        excluded_by_prior_observation=len(excluded)))
            if evaluated == 0 and report["gaps"]:
                report.update(status="source_failed", matched_count=None)
            if not dry_run:
                report["sha256"] = fingerprint(report)
                # 刷新失败不能抹掉此前已核验的部分事实；另存本次失败，不声称刷新成功。
                lost_due_to_failure = (set(report["failed_codes"]) &
                                       {r["code"] for r in old.get("stocks", [])}) if old else set()
                if old and (lost_due_to_failure or
                            (old.get("matched_count", 0) and report["status"] == "source_failed")):
                    atomic_write(directory / f"{target}.attempt.json", json.dumps(report, ensure_ascii=False, indent=2))
                    return {**report, "previous_report_preserved": True}
                # JSON是唯一完成收据，必须在展示文件成功后最后发布；失败不能留下新的complete收据。
                atomic_write(directory / f"{target}.md", render(report))
                atomic_write(directory / f"{target}.json", json.dumps(report, ensure_ascii=False, indent=2))
            return report
    except Exception as exc:
        # 计算完成不等于归档完成；CLI必须对JSON/Markdown写入失败给出非零退出。
        report["calculation_status"] = report["status"]
        report["status"] = "source_failed"
        report["is_exhaustive"] = False
        report.pop("sha256", None)
        report["error"] = str(exc)
        # 失败收据独立保存，绝不覆盖既有完整/部分有效报告；锁冲突不写共享路径。
        if not dry_run and not isinstance(exc, BlockingIOError) and input_by and target == report["date"]:
            try:
                if date.fromisoformat(target).isoformat() == target:
                    atomic_write(directory / f"{target}.attempt.json", json.dumps(report, ensure_ascii=False, indent=2))
            except (ValueError, OSError):
                pass
        return report


class _RunSourceCache:
    """一次双口径运行共用原始响应，各消费方拿副本，不互相污染。"""

    def __init__(self, source):
        self.source = source
        self.cache = {}
        self.providers = {}

    def _call(self, method, *args, **kwargs):
        from copy import deepcopy
        key = (method, args, tuple(sorted(kwargs.items())))
        if key not in self.cache:
            result = getattr(self.source, method)(*args, **kwargs)
            if isinstance(result, list) or getattr(result, "success", False):
                self.cache[key] = result
            else:
                return result
        return deepcopy(self.cache[key])

    def call_specific(self, *args):
        return self._call("call_specific", *args)

    def call(self, *args):
        return self._call("call", *args)

    def get_provider(self, name):
        if name not in self.providers:
            provider = self.source.get_provider(name)
            self.providers[name] = _RunSourceCache(provider) if provider is not None else None
        return self.providers[name]

    def get_stock_daily_range(self, *args):
        return self._call("get_stock_daily_range", *args)

    def _query_records(self, *args, **kwargs):
        return self._call("_query_records", *args, **kwargs)


def render_dual(report):
    count = lambda value: "未计算" if value is None else str(value)
    lines = [f"# 历史天量双口径 · {report['date']}", "",
             f"- 总状态：**{report['status']}**；两个口径分别判断，不要求同时满足。",
             f"- 已核验成交量新高：{count(report['matched_counts'].get('volume'))} 只；"
             f"成交额新高：{count(report['matched_counts'].get('amount'))} 只；"
             f"两者同时创新高：{count(report.get('both_matched_count'))} 只。",
             "- 成交量单位为手；成交额原值为千元。均严格大于上市以来此前最高值，持平与上市首日不计。",
             "- partial 仅表示已核验名单，存在历史缺口，不代表完整市场结果。", ""]
    if report.get("error"):
        lines += [f"- 归档/运行错误：{report['error']}", ""]
    both = report.get("both_records", [])
    lines += ["## 已核验两者同时创新高", ""]
    lines += ([f"- {r['name']}（{r['code']}）" for r in both] if both else
              ["尚无已核验双创结果；具体状态见两个口径。"])
    for metric in ("volume", "amount"):
        if metric in report["metrics"]:
            lines += ["", render(report["metrics"][metric]).replace("# ", "## ", 1)]
    return "\n".join(lines) + "\n"


def run_dual(target, input_by, *, registry, root=ROOT, dry_run=False, **kwargs):
    """分别保存量/额收据，再发布双口径总报告；旧成交量归档与基线兼容。"""
    report = dict(version="lifetime-raw-dual-v1", date=target, input_by=input_by,
                  status="source_failed", metrics={}, matched_counts={}, matched_count=None,
                  both_matched_count=None, both_records=[], stocks=[], gaps=[], is_exhaustive=False)
    directory = Path(root) / "data/reports/volume-record/dual"
    try:
        target = day(target)
        report["date"] = target
        if not input_by or not input_by.strip():
            raise ValueError("请求者缺失")
        now = kwargs.pop("now", None) or datetime.now(ZoneInfo("Asia/Shanghai"))
        report["generated_at"] = now.isoformat()
        registry = _RunSourceCache(registry)
        with run_lock(directory, dry_run):
            old = None
            if not dry_run:
                try:
                    old = json.loads((directory / f"{target}.json").read_text())
                    digest = old.pop("sha256")
                    if (old.get("version") != report["version"] or old.get("date") != target
                            or fingerprint(old) != digest):
                        old = None
                except (OSError, ValueError, KeyError, TypeError):
                    old = None
            for metric in ("volume", "amount"):
                report["metrics"][metric] = run(target, input_by, registry=registry, root=root,
                    dry_run=dry_run, now=now, metric=metric, **kwargs)
            results = report["metrics"]
            statuses = {r["status"] for r in results.values()}
            if statuses == {"skipped"}:
                report.update(status="skipped", reason="两个口径均非采集时段/非交易日")
                return report
            report["status"] = ("complete" if statuses == {"complete"} else
                                "source_failed" if statuses == {"source_failed"} else "partial")
            report["is_exhaustive"] = all(r.get("is_exhaustive") for r in results.values())
            known = {}
            for metric, result in results.items():
                report["matched_counts"][metric] = result["matched_count"]
                report["gaps"].extend(f"{metric}:{g}" for g in result.get("gaps", []))
                if result.get("error"):
                    report["gaps"].append(f"{metric}:{result['error']}")
                for row in result["stocks"]:
                    known.setdefault(row["code"], dict(code=row["code"], name=row["name"], metrics=[]))
                    known[row["code"]]["metrics"].append(metric)
            report["stocks"] = sorted(known.values(), key=lambda r: r["code"])
            report["both_records"] = [r for r in report["stocks"] if len(r["metrics"]) == 2]
            if any(r["matched_count"] is not None for r in results.values()):
                report["matched_count"] = len(known)  # 已核验并集，不冒充某一口径数量。
            if all(r["matched_count"] is not None for r in results.values()):
                report["both_matched_count"] = len(report["both_records"])
            if not dry_run:
                report["sha256"] = fingerprint(report)
                # 子口径已判定刷新降级时，总报告也不得抹去此前命中。
                lost = False
                if old:
                    for metric, result in results.items():
                        previous = {r["code"] for r in old.get("metrics", {}).get(metric, {}).get("stocks", [])}
                        current = {r["code"] for r in result["stocks"]}
                        if result.get("previous_report_preserved") or (previous - current and
                                (result["status"] == "source_failed" or
                                 (previous - current) & set(result.get("failed_codes", [])))):
                            lost = True
                if lost:
                    atomic_write(directory / f"{target}.attempt.json", json.dumps(report, ensure_ascii=False, indent=2))
                    return {**report, "previous_report_preserved": True}
                atomic_write(directory / f"{target}.md", render_dual(report))
                atomic_write(directory / f"{target}.json", json.dumps(report, ensure_ascii=False, indent=2))
            return report
    except Exception as exc:
        report.update(calculation_status=report["status"], status="source_failed", is_exhaustive=False, error=str(exc))
        report.pop("sha256", None)
        if not dry_run and not isinstance(exc, BlockingIOError) and input_by:
            try:
                if date.fromisoformat(target).isoformat() == target:
                    atomic_write(directory / f"{target}.attempt.json", json.dumps(report, ensure_ascii=False, indent=2))
            except (ValueError, OSError):
                pass
        return report
