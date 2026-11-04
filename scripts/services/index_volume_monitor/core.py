"""固定收盘网格、严格上一开放日及同窗金额计算（全部只读/纯函数）。"""
from __future__ import annotations
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext

from db.connection import get_readonly_connection
from services.intraday_monitor.guards import shanghai_now
from .rules import INDEX_NAMES, INDEX_VOLUME_GROWTH

MIN_DELAY = timedelta(minutes=1)
MAX_DELAY = timedelta(minutes=5)


def windows_at(now: datetime, *, periods=None) -> list[tuple[int,datetime]]:
    now = shanghai_now(now)
    periods = INDEX_VOLUME_GROWTH.periods if periods is None else periods
    out = []
    for hour, minute in ((9,30),(13,0)):
        start = now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        for step in range(1,9):
            end = start + timedelta(minutes=15*step)
            if MIN_DELAY <= now-end <= MAX_DELAY:
                if 15 in periods:
                    out.append((15,end))
                if step % 2 == 0 and 30 in periods:
                    out.append((30,end))
    return out


def calendar_basis(day: str, db_path=None) -> tuple[str,str | None]:
    try:
        today = date.fromisoformat(day)
        conn = get_readonly_connection(db_path)
        try:
            current = conn.execute("SELECT is_open FROM trade_calendar WHERE exchange='SSE' AND date=?",(day,)).fetchall()
            if len(current) != 1 or current[0]["is_open"] not in (0,1):
                return "blocked_calendar",None
            if current[0]["is_open"] == 0:
                return "non_trade_day",None
            previous = conn.execute("SELECT date FROM trade_calendar WHERE exchange='SSE' AND date<? AND is_open=1 ORDER BY date DESC LIMIT 1",(day,)).fetchone()
            if previous is None:
                return "blocked_calendar",None
            start = date.fromisoformat(previous["date"])
            rows = conn.execute("SELECT date,is_open FROM trade_calendar WHERE exchange='SSE' AND date>=? AND date<=? ORDER BY date",(str(start),day)).fetchall()
            expected = [str(start+timedelta(days=i)) for i in range((today-start).days+1)]
            if [r["date"] for r in rows] != expected or any(r["is_open"] not in (0,1) for r in rows):
                return "blocked_calendar",None
            return "complete",str(start)
        finally:
            conn.close()
    except Exception:
        return "blocked_calendar",None


def event_id(code: str, minutes: int, end: datetime, rule_id=None) -> str:
    return f"index-volume:{rule_id or INDEX_VOLUME_GROWTH.rule_id}:{code}:{minutes}:{end.strftime('%Y-%m-%dT%H:%M')}"


def money(raw) -> Decimal:
    if isinstance(raw,bool) or not re.fullmatch(r"\d{1,19}(?:\.\d{1,8})?",str(raw)):
        raise ValueError("指数成交额非法或缺失")
    return Decimal(str(raw))


def meets_growth(current, previous, growth_pct):
    with localcontext() as ctx:
        ctx.prec = 64  # 19整数位+8小数位的金额及配置乘数保持精确。
        return current*100 >= previous*(100+growth_pct)


def compare_window(code: str, minutes: int, end: datetime, previous_day: str, rows: object,
                   *, rule=INDEX_VOLUME_GROWTH) -> dict:
    if (code not in rule.codes or type(minutes) is not int or not isinstance(rows,list)
            or end.tzinfo is None or end.utcoffset() != timedelta(hours=8)
            or (minutes,end) not in windows_at(end+MIN_DELAY,periods=rule.periods)
            or date.fromisoformat(previous_day) >= end.date()):
        raise ValueError("指数成交额输入契约非法")
    by_time = {}
    for row in rows:
        if (not isinstance(row,dict) or row.get("ts_code") != code
                or type(row.get("period_minutes")) is not int or row["period_minutes"] != 15
                or row.get("amount_unit") != "CNY"):
            raise ValueError("指数成交额身份/周期/单位错位")
        stamp = row.get("trade_time")
        dt = datetime.strptime(stamp,"%Y-%m-%d %H:%M")
        if (dt.strftime("%Y-%m-%d %H:%M") != stamp or stamp in by_time
                or (15,shanghai_now(dt)) not in windows_at(shanghai_now(dt)+MIN_DELAY,periods=(15,30))):
            raise ValueError("指数成交额时间非法或重复")
        by_time[stamp] = money(row.get("amount"))
    labels = [(end-timedelta(minutes=15*i)).strftime("%H:%M") for i in reversed(range(minutes//15))]
    def total(day):
        keys = [f"{day} {label}" for label in labels]
        if any(key not in by_time for key in keys):
            raise ValueError(f"缺少完整{minutes}分钟成交额：{day} {'/'.join(labels)}")
        return sum((by_time[key] for key in keys),Decimal(0))
    current, baseline = total(end.date().isoformat()),total(previous_day)
    if baseline <= 0:
        raise ValueError("上一交易日同窗成交额为0，不能计算增幅")
    growth = (current/baseline-1)*100
    return {"event_id":event_id(code,minutes,end,rule.rule_id), "rule_id":rule.rule_id,
            "code":code,"name":INDEX_NAMES[code],"period_minutes":minutes,
            "trade_date":end.date().isoformat(),"previous_trade_date":previous_day,
            "start_time":(end-timedelta(minutes=minutes)).strftime("%H:%M"),"end_time":end.strftime("%H:%M"),
            "current_amount":str(current),"previous_amount":str(baseline),"amount_unit":"CNY",
            "growth_pct":str(growth),"threshold_pct":rule.growth_pct,
            "matched":meets_growth(current,baseline,rule.growth_pct),
            "window_end":end.isoformat(),"expires_at":(end+MAX_DELAY).isoformat(),
            "source":"eastmoney:index_15min","calculation":"15分钟原值；30分钟为同一交易时段内相邻两根15分钟合计"}


def render_alert(events: list[dict]) -> str:
    lines = ["### 📈 指数分时成交额同比放大",""]
    for e in events:
        current,previous = money(e["current_amount"]),money(e["previous_amount"])
        lines.extend([f"- [事实] **{e['name']}（{e['code']}） {e['period_minutes']}分钟** {e['start_time']}～{e['end_time']}",
                      f"  - {e['trade_date']}：{current/Decimal('1e8'):.4f}亿元；上一交易日 {e['previous_trade_date']} 同窗：{previous/Decimal('1e8'):.4f}亿元",
                      f"  - 增加 **{Decimal(e['growth_pct']):+.2f}%**（触发条件≥{e['threshold_pct']}%）"])
    lines.extend(["","来源：东方财富指数15分钟K线成交额（元）；30分钟为相邻两根合计。不同指数口径可重叠，不相加冒充全市场成交额。",
                  "仅在完整收盘窗口后比较，不含未完成K线；仅为条件提醒，不构成买卖建议。"])
    return "\n".join(lines)
