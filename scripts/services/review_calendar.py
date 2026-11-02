"""只读复盘日期：开放日用同日，休市日须有连续日历证明才展示历史。"""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3


def review_fact_date(payload, report_date):
    context = payload.get("review_context") if isinstance(payload, dict) else None
    if context is None:
        return report_date
    if not isinstance(context, dict) or context.get("report_date") != report_date:
        raise ValueError("复盘复核日不一致")
    fact_date = payload.get("date")
    start, end = date.fromisoformat(fact_date), date.fromisoformat(report_date)
    if not 0 < (end-start).days <= 31:
        raise ValueError("复盘休市窗口非法")
    expected = [[(start+timedelta(days=i)).isoformat(), int(i == 0)]
                for i in range((end-start).days+1)]
    if context.get("calendar") != expected:
        raise ValueError("复盘休市窗口缺日或含开放日")
    return fact_date


def load_review_context(db_path, report_date):
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+"?mode=ro", uri=True)) as conn:
        target = conn.execute("SELECT is_open FROM trade_calendar WHERE date=?", (report_date,)).fetchone()
        if target is None or target[0] not in (0, 1):
            raise ValueError("报告日日历缺失或非法")
        if target[0] == 1:
            return report_date, None
        latest = conn.execute("SELECT max(date) FROM trade_calendar WHERE date<=? AND is_open=1", (report_date,)).fetchone()[0]
        if latest is None:
            raise ValueError("最近开放日缺失")
        window = [list(row) for row in conn.execute(
            "SELECT date,is_open FROM trade_calendar WHERE date BETWEEN ? AND ? ORDER BY date",
            (latest, report_date))]
    context = {"report_date": report_date, "calendar": window}
    review_fact_date({"date": latest, "review_context": context}, report_date)
    return latest, context
