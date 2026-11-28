"""四指数5/15分钟成交额同比，独立状态与限窗outbox；不依赖股票全市场覆盖。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from services.intraday_monitor.guards import shanghai_now
from services.intraday_monitor.state import locked_state, load_state, save_state
from .core import calendar_basis, windows_at, compare_window, event_id, render_alert, money, meets_growth, MIN_DELAY, MAX_DELAY
from .rules import INDEX_NAMES
from . import rules as rule_config
from .opening_environment import opening_observation
from .bar_archive import archive_fetched
from .archive_fallback import ArchiveFallback

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STATE_PATH = ROOT / "data/runs/intraday-monitor/index-volume-state.json"
DEFAULT_REPORT_ROOT = ROOT / "data/reports/index-volume-monitor"
HISTORY_LIMIT = 4096


def _validate(state, rule):
    if state.get("kind") not in (None,"index-volume"):
        raise ValueError("指数成交额状态身份非法")
    if state.get("kind") is None and any(state.get(k) for k in ("rules","pending_events","sent_event_ids","expired_event_ids","last_fetch_at")):
        raise ValueError("拒绝把其他监控账本当作指数成交额状态")
    for key in ("checked_event_ids","sent_event_ids","expired_event_ids"):
        if not isinstance(state.get(key,[]),list) or any(not isinstance(v,str) for v in state.get(key,[])):
            raise ValueError("指数成交额状态账本字段非法")
    pending_ids = set()
    for e in state["pending_events"]:
        if not isinstance(e, dict):
            raise ValueError("待发事件必须为对象")
        end = datetime.fromisoformat(e["window_end"])
        code,period = e["code"],e["period_minutes"]
        current,previous = money(e["current_amount"]),money(e["previous_amount"])
        if previous <= 0:
            raise ValueError("待发事件比较基数必须为正数")
        if (end.tzinfo is None or end.utcoffset() != timedelta(hours=8)
                or type(period) is not int or (period,end) not in windows_at(end+MIN_DELAY,periods=rule.periods)
                or e["rule_id"] != rule.rule_id or code not in INDEX_NAMES
                or e["name"] != INDEX_NAMES[code] or e["event_id"] != event_id(code,period,end,rule.rule_id)
                or e["trade_date"] != end.date().isoformat()
                or date.fromisoformat(e["previous_trade_date"]) >= end.date()
                or e["start_time"] != (end-timedelta(minutes=period)).strftime("%H:%M")
                or e["end_time"] != end.strftime("%H:%M")
                or e["matched"] is not True or e["threshold_pct"] != rule.growth_pct
                or str((current/previous-1)*100) != e["growth_pct"]
                or datetime.fromisoformat(e["expires_at"]) != end+MAX_DELAY
                or e["source"] != "eastmoney:index_5min" or e["amount_unit"] != "CNY"
                or not meets_growth(current,previous,rule.growth_pct)):
            raise ValueError("待发指数成交额事件损坏")
        if e["event_id"] in state["sent_event_ids"] or e["event_id"] in pending_ids:
            raise ValueError("待发事件重复或已存在于已送达账本")
        pending_ids.add(e["event_id"])


def _expire(state, now, allowed):
    keep,expired = [],[]
    for e in state["pending_events"]:
        if e["event_id"] in allowed and now <= datetime.fromisoformat(e["expires_at"]):
            keep.append(e)
        else:
            expired.append(e["event_id"])
    state["pending_events"] = keep
    state["expired_event_ids"] = (state["expired_event_ids"]+expired)[-HISTORY_LIMIT:]


def _default_pusher():
    from pushers.dingtalk_pusher import DingTalkPusher
    return DingTalkPusher(config={})


def _fetch(registry, code, previous, day):
    try:
        result = registry.call_specific("eastmoney","get_index_intraday_bars",code,previous,day,period_minutes=5)
        if result.source != "eastmoney:index_5min":
            return None,"指数K线来源非法"
        if not result.success:
            return None,result.error
        if not isinstance(result.data,list) or not result.data or any(
            not isinstance(row,dict) or type(row.get("period_minutes")) is not int
            or row["period_minutes"] != 5 for row in result.data
        ):
            return None,"指数成交额监控必须使用原生5分钟K线"
        return result.data,None
    except Exception as exc:
        return None,f"{code}: 指数K线调用异常 {type(exc).__name__}"



def _compare_with_fallback(code, period, end, previous, rows, error, rule, fallback):
    if error:
        if error.startswith(("payload_invalid", "指数K线来源非法", "指数成交额监控必须")):
            raise ValueError(error)
        primary_error = error
    else:
        try:
            return compare_window(code, period, end, previous, rows, rule=rule)
        except ValueError as exc:
            # 本次身份、单位或重复行错误保留失败，只对缺少完整比较窗补根。
            primary_error = str(exc)
            if not primary_error.startswith("缺少完整"):
                raise
    try:
        return fallback.compare(code, period, end, previous, rows or [], primary_error, rule)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ValueError(f"{primary_error}；同源归档不可用：{exc}") from None


def _run_locked(registry, state, path, reports, slots, now, previous, input_by, dry_run, factory, clock, rule,
                fetched=None):
    _validate(state,rule)
    state["kind"] = "index-volume"
    allowed = {event_id(code,period,end,rule.rule_id) for period,end in slots for code in rule.codes}
    _expire(state,now,allowed)
    if any(e["previous_trade_date"] != previous for e in state["pending_events"]):
        raise ValueError("待发事件不是严格上一交易日同窗")
    checked = set(state.get("checked_event_ids",[]))
    needed = [code for code in rule.codes if any(event_id(code,p,e,rule.rule_id) not in checked for p,e in slots)]
    evaluations,errors,events = [],[],[]
    results = {}
    provider_error = None
    if needed:
        fallback = ArchiveFallback(reports / "ohlcv-5m", now)
        try:
            provider = registry.get_provider("eastmoney")
            if provider is None or not provider.supports("get_index_intraday_bars"):
                provider_error = "东方财富指数分时能力未启用"
            elif not getattr(provider,"_initialized",False) and not provider.initialize():
                provider_error = "东方财富指数分时初始化失败"
        except Exception as exc:
            provider_error = f"东方财富指数分时初始化异常 {type(exc).__name__}"
        if provider_error:
            results = {code:(None,provider_error) for code in needed}
        else:
            with ThreadPoolExecutor(max_workers=4) as pool:
                # 每票独立会话/有界重试，最多4个worker；不因一票失败中断其他指数。
                futures = {code:pool.submit(_fetch,registry,code,previous,now.date().isoformat()) for code in needed}
                results = {code:f.result() for code,f in futures.items()}
        for code in needed:
            rows,error = results[code]
            for period,end in slots:
                key = event_id(code,period,end,rule.rule_id)
                if key in checked:
                    continue
                try:
                    if provider_error:
                        raise ValueError(provider_error)
                    e = _compare_with_fallback(code, period, end, previous, rows, error, rule, fallback)
                    e["input_by"] = input_by
                    e["captured_at"] = now.isoformat()
                    evaluations.append(e)
                    checked.add(key)
                    if e["matched"] and key not in state["sent_event_ids"]:
                        events.append(e)
                except (ValueError,TypeError,KeyError,OverflowError) as exc:
                    errors.append(f"{code}/{period}分钟: {exc}")
    finished = shanghai_now(clock())
    expired = windows_at(finished,periods=rule.periods) != slots
    if fetched is not None and results:
        fetched.update(rows=results, captured_at=finished.isoformat(),
                       acquisition="delayed_monitor_fetch" if expired else "monitor_fetch")
    # 迟到的原值按实际取得时间进入侧档；已过期的窗口不发送、不推进已检查基线。
    if expired:
        return {"status":"expired_window","events":[],"errors":["取数完成时已超过窗口，未告警/未推进检查基线"],"pushed":False}
    covered = len(checked & allowed)
    status = "complete" if covered == len(allowed) else ("partial" if covered else "source_failed")
    # 10点仍用本轮5分钟行情计算前半小时环境；与5/15分钟提醒周期独立。
    opening = {}
    opening_end = next((end for period,end in slots if end.strftime("%H:%M") == "10:00"), None)
    if opening_end is not None:
        opening["opening_environment"] = opening_observation(
            results, rule.codes, opening_end, previous, rule=rule,
            input_by=input_by, captured_at=now.isoformat())
    if dry_run:
        return {"status":"dry_run","data_status":status,"events":events,"errors":errors,
                "evaluations":evaluations,"pushed":False,"previous_trade_date":previous,**opening}
    if not needed and not state["pending_events"]:
        save_state(path,state)  # 仅可能退休之前窗口的pending。
        return {"status":"already_checked","events":[],"errors":[],"pushed":False,"pending_count":0,**opening}
    end = slots[0][1]
    report_path = reports / now.date().isoformat() / f"{end.strftime('%H%M')}-{uuid4().hex}.json"
    receipt = {"status":status,"input_by":input_by,"captured_at":now.isoformat(),
               "previous_trade_date":previous,"evaluations":evaluations,"errors":errors,**opening}
    save_state(report_path,receipt)  # 复用0600/同目录原子JSON写；每次attempt独立。
    state["checked_event_ids"] = list(dict.fromkeys([*state.get("checked_event_ids",[]),
                                                    *(e["event_id"] for e in evaluations)]))[-HISTORY_LIMIT:]
    pending_ids = {e["event_id"] for e in state["pending_events"]}
    for e in events:
        if e["event_id"] not in pending_ids:
            state["pending_events"].append({**e,"report_path":str(report_path)})
    state["last_attempt"] = {"input_by":input_by,"captured_at":now.isoformat(),
                             "status":status,"report_path":str(report_path)}
    save_state(path,state)
    pushed = False
    if state["pending_events"]:
        send_now = shanghai_now(clock())
        if windows_at(send_now,periods=rule.periods) != slots:
            _expire(state,send_now,set())
            save_state(path,state)
            return {"status":"expired_window","events":[],"errors":["发送前窗口已过期"],"pushed":False}
        try:
            pusher = factory()
            initialized = pusher.initialize()
            # 初始化也可能耗时，真正调用发送前再检查一次。
            if windows_at(shanghai_now(clock()),periods=rule.periods) != slots:
                _expire(state,shanghai_now(clock()),set())
                save_state(path,state)
                return {"status":"expired_window","events":[],"errors":["推送初始化后窗口已过期"],"pushed":False}
            if not initialized or not pusher.send_markdown(title="指数分时成交额同比放大",content=render_alert(state["pending_events"])):
                raise RuntimeError("钉钉初始化或发送失败")
            state["sent_event_ids"] = (state["sent_event_ids"]+[e["event_id"] for e in state["pending_events"]])[-HISTORY_LIMIT:]
            state["pending_events"] = []
            pushed = True
        except Exception as exc:
            status = "push_failed"
            errors.append(f"钉钉发送失败: {type(exc).__name__}")
        save_state(path,state)
    return {"status":status,"events":events,"errors":errors,"pushed":pushed,
            "pending_count":len(state["pending_events"]),"evaluations":evaluations,
            "previous_trade_date":previous,"report_path":str(report_path),"quotes_checked":len(needed),**opening}


def run_index_volume(registry, *, input_by="USER", dry_run=False, state_path=DEFAULT_STATE_PATH,
                     report_root=DEFAULT_REPORT_ROOT, db_path=None, clock=None, pusher_factory=None):
    if not isinstance(input_by,str) or not input_by.strip():
        return {"status":"invalid_request","events":[],"errors":["input_by不能为空"],"pushed":False}
    active = rule_config.DEFAULT_INDEX_VOLUME_RULES
    if not active:
        return {"status":"no_rules","events":[],"errors":[],"pushed":False}
    if len(active) != 1 or not isinstance(active[0],rule_config.IndexVolumeRule):
        return {"status":"invalid_request","events":[],"errors":["指数成交额仅接受一份有效配置"],"pushed":False}
    rule = active[0]
    clock = clock or shanghai_now
    now = shanghai_now(clock())
    slots = windows_at(now,periods=rule.periods)
    if not slots:
        return {"status":"outside_slot","events":[],"errors":[],"pushed":False}
    status,previous = calendar_basis(now.date().isoformat(),db_path)
    if status != "complete":
        return {"status":status,"events":[],"errors":["交易日历缺失或不可读"] if status=="blocked_calendar" else [],"pushed":False}
    path = Path(state_path)
    kwargs = dict(registry=registry,path=path,reports=Path(report_root),slots=slots,now=now,
                  previous=previous,input_by=input_by.strip(),dry_run=dry_run,
                  factory=pusher_factory or _default_pusher,clock=clock,rule=rule)
    try:
        if dry_run:
            state = load_state(path)
            return _run_locked(state=state,**kwargs)
        with locked_state(path) as state:
            fetched = {}
            result = _run_locked(state=state, fetched=fetched, **kwargs)
            if fetched:
                # 原账本及发送流程先完成；归档失败独立留痕，不触发新的请求或投递。
                result["ohlcv_archive"] = archive_fetched(
                    Path(report_root) / "ohlcv-5m", fetched["rows"], previous=previous, end=slots[0][1],
                    captured_at=fetched["captured_at"], input_by=input_by.strip(),
                    origin={"acquisition": fetched["acquisition"]})
        if slots[0][1].strftime("%H:%M") == "15:00":
            from .coverage import FIRST_5M_DAY, receipt_coverage
            if now.date() >= FIRST_5M_DAY:
                summary = receipt_coverage(Path(report_root), now.date(), now.date(), now=now, db_path=db_path)
                result["realtime_coverage"] = {key: value for key, value in summary.items() if key != "days"}
        return result
    except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:
        return {"status":"state_error","events":[],"errors":[f"{type(exc).__name__}: {exc}"],"pushed":False}
