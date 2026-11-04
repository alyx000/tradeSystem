"""四指数15/30分钟成交额同比，独立状态与限窗outbox；不依赖股票全市场覆盖。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from services.intraday_monitor.guards import shanghai_now
from services.intraday_monitor.state import locked_state, load_state, save_state
from .core import calendar_basis, windows_at, compare_window, event_id, render_alert, money, meets_growth, MIN_DELAY, MAX_DELAY
from .rules import INDEX_NAMES
from . import rules as rule_config

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
                or type(period) is not int or (period,end) not in windows_at(end+MIN_DELAY,periods=(15,30))
                or e["rule_id"] != rule.rule_id or code not in INDEX_NAMES
                or e["name"] != INDEX_NAMES[code] or e["event_id"] != event_id(code,period,end,rule.rule_id)
                or e["trade_date"] != end.date().isoformat()
                or date.fromisoformat(e["previous_trade_date"]) >= end.date()
                or e["start_time"] != (end-timedelta(minutes=period)).strftime("%H:%M")
                or e["end_time"] != end.strftime("%H:%M")
                or e["matched"] is not True or e["threshold_pct"] != rule.growth_pct
                or str((current/previous-1)*100) != e["growth_pct"]
                or datetime.fromisoformat(e["expires_at"]) != end+MAX_DELAY
                or e["source"] != "eastmoney:index_15min" or e["amount_unit"] != "CNY"
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
        result = registry.call_specific("eastmoney","get_index_intraday_bars",code,previous,day)
        if not result.success or result.source != "eastmoney:index_15min":
            raise ValueError(getattr(result,"error","") or "指数K线来源非法")
        return result.data,None
    except Exception as exc:
        return None,f"{code}: {type(exc).__name__}: {exc}"


def _run_locked(registry, state, path, reports, slots, now, previous, input_by, dry_run, factory, clock, rule):
    _validate(state,rule)
    state["kind"] = "index-volume"
    allowed = {event_id(code,period,end,rule.rule_id) for period,end in slots for code in rule.codes}
    _expire(state,now,allowed)
    if any(e["previous_trade_date"] != previous for e in state["pending_events"]):
        raise ValueError("待发事件不是严格上一交易日同窗")
    checked = set(state.get("checked_event_ids",[]))
    needed = [code for code in rule.codes if any(event_id(code,p,e,rule.rule_id) not in checked for p,e in slots)]
    evaluations,errors,events = [],[],[]
    provider_error = None
    if needed:
        try:
            provider = registry.get_provider("eastmoney")
            if provider is None or not provider.supports("get_index_intraday_bars"):
                raise ValueError("东方财富指数分时能力未启用")
            if not getattr(provider,"_initialized",False) and not provider.initialize():
                raise ValueError("东方财富指数分时初始化失败")
        except Exception as exc:
            provider_error = f"{type(exc).__name__}: {exc}"
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
                    if error:
                        raise ValueError(error)
                    e = compare_window(code,period,end,previous,rows,rule=rule)
                    e["input_by"] = input_by
                    e["captured_at"] = now.isoformat()
                    evaluations.append(e)
                    checked.add(key)
                    if e["matched"] and key not in state["sent_event_ids"]:
                        events.append(e)
                except (ValueError,TypeError,KeyError,OverflowError) as exc:
                    errors.append(f"{code}/{period}分钟: {exc}")
    finished = shanghai_now(clock())
    # 超时、跨槽、跨日一律不得把刚取回的旧窗口补发；也不推进其已检查基线。
    if windows_at(finished,periods=rule.periods) != slots:
        return {"status":"expired_window","events":[],"errors":["取数完成时已超过窗口，未告警/未推进检查基线"],"pushed":False}
    covered = len(checked & allowed)
    status = "complete" if covered == len(allowed) else ("partial" if covered else "source_failed")
    if dry_run:
        return {"status":"dry_run","data_status":status,"events":events,"errors":errors,
                "evaluations":evaluations,"pushed":False,"previous_trade_date":previous}
    if not needed and not state["pending_events"]:
        save_state(path,state)  # 仅可能退休之前窗口的pending。
        return {"status":"already_checked","events":[],"errors":[],"pushed":False,"pending_count":0}
    end = slots[0][1]
    report_path = reports / now.date().isoformat() / f"{end.strftime('%H%M')}-{uuid4().hex}.json"
    receipt = {"status":status,"input_by":input_by,"captured_at":now.isoformat(),
               "previous_trade_date":previous,"evaluations":evaluations,"errors":errors}
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
            "previous_trade_date":previous,"report_path":str(report_path),"quotes_checked":len(needed)}


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
            return _run_locked(state=state,**kwargs)
    except (OSError,ValueError,TypeError,KeyError,OverflowError) as exc:
        return {"status":"state_error","events":[],"errors":[f"{type(exc).__name__}: {exc}"],"pushed":False}
