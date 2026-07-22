"""交易日工具（供盘前/晚间任务复用，避免 main 与 collectors 循环导入）"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 向前查找上一交易日的最大扫描天数。旧值 7 在 A 股长假（国庆 ~8 天、春节叠加前后
# 周末可达 ~11 天）后的首个交易日会扫不到真正的上一开市日，错误回退到「昨天」（假期内），
# 下游缺口验证候选窗会塌缩并静默漏掉假期公告（codex review 2026-06-12）。15 天覆盖
# 所有 A 股法定长假 + 余量。
_PREV_TRADE_SCAN_DAYS = 15


def get_prev_trade_date(registry, today: str) -> str:
    """
    向前最多查找 _PREV_TRADE_SCAN_DAYS 天，找到最近一个交易日（不含 today）。
    若 provider 不可用或扫描窗内均休市则回退到昨天。
    """
    for delta in range(1, _PREV_TRADE_SCAN_DAYS + 1):
        candidate = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=delta)).strftime("%Y-%m-%d")
        r = registry.call("is_trade_day", candidate)
        if r.success and r.data:
            return candidate
    return (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def is_trade_day(date: str, *, conn: sqlite3.Connection | None = None, registry=None) -> bool | None:
    """统一的交易日判断：DB 优先 → provider 在线查 → 周末降级。

    返回 True/False 表示确定，None 表示无法判断（纯周末/工作日降级时仍返回 bool）。
    """
    from db import queries as Q

    if conn is not None:
        cached = Q.is_trade_day_from_db(conn, date)
        if cached is not None:
            return cached

    if registry is not None:
        try:
            r = registry.call("is_trade_day", date)
            if r.success and r.data is not None:
                if conn is not None:
                    try:
                        Q.upsert_trade_calendar(conn, [{"date": date, "is_open": int(r.data)}])
                    except Exception:
                        pass
                return bool(r.data)
        except Exception:
            pass

    dt = datetime.strptime(date, "%Y-%m-%d")
    return dt.weekday() < 5


def ensure_trade_calendar(
    conn: sqlite3.Connection,
    registry,
    year: int | None = None,
    *,
    force: bool = False,
) -> int:
    """确保指定年份的交易日历已导入 DB；force 时强制刷新以修复缺口。"""
    from db import queries as Q

    if year is None:
        year = datetime.now().year

    if not force and Q.trade_calendar_year_covered(conn, year):
        return 0

    date_str = f"{year}-06-15"
    try:
        r = registry.call("get_trade_calendar", date_str)
    except Exception as e:
        logger.warning("获取 %d 年交易日历失败: %s", year, e)
        return 0

    if not r.success or not r.data:
        logger.warning("获取 %d 年交易日历无数据: %s", year, r.error)
        return 0

    rows = []
    for item in r.data:
        cal_date_raw = str(item.get("cal_date", item.get("trade_date", "")))
        if len(cal_date_raw) == 8:
            cal_date = f"{cal_date_raw[:4]}-{cal_date_raw[4:6]}-{cal_date_raw[6:8]}"
        else:
            cal_date = cal_date_raw
        is_open = int(item.get("is_open", 0))
        rows.append({"date": cal_date, "is_open": is_open})

    if not rows:
        return 0

    count = Q.upsert_trade_calendar(conn, rows)
    logger.info("导入 %d 年交易日历 %d 条", year, count)
    return count


# 收盘安全线(Asia/Shanghai):A股 15:00 收盘,15:30 起视为"当日已收盘"
CLOSE_CUTOFF = (15, 30)


def resolve_latest_closed_trade_date(conn: sqlite3.Connection, registry,
                                     now: "datetime | None" = None) -> "str | None":
    """严格口径的"当前最新已收盘交易日"。返回 None = blocked（调用方必须不推送）。

    与本文件 get_prev_trade_date / is_trade_day 的 fail-open 兜底（回退昨天 / weekday
    近似）不同：本函数只信 DB trade_calendar 的确定 open-day 集合（先 ensure_trade_calendar
    拉齐目标年，覆盖不足即 blocked），供 value-watch 等"历史日期绝不推送"的事件闸门使用
    （spec v8：误用 fail-open 工具会在长假/日历源失败时误推或漏推）。

    - now 统一转 Asia/Shanghai；naive 输入视为上海本地时间。
    - 当日为确认交易日且 now >= 15:30 → 返回当日；否则返回日历中严格早于当日的最近 open 日。
    - 1 月初上一交易日可能落在前一年：当年查无更早 open 日时补拉前一年日历再查。
    - **事务边界**（同 is_non_trading_day）：冷路径 ensure_trade_calendar 会在传入连接上
      commit 日历行；异常路径会 rollback。调用方须在自身事务边界外调用，且 dry-run
      （内存副本）模式应自行豁免，避免在真实库写日历缓存。
    """
    from zoneinfo import ZoneInfo

    from db import queries as Q

    tz = ZoneInfo("Asia/Shanghai")
    if now is None:
        now = datetime.now(tz)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    today = now.date().isoformat()

    def _year_complete(year: int) -> bool:
        # 门2 high-2(round2 精确化):行数 COUNT 是弱代理——schema 的 GLOB 约束不验真实
        # 日期,"缺 7/17 + 混入 2026-02-30"计数仍够会穿透闸门。strict 要求库内该年日期
        # 集合 ⊇ 真实自然日集合(tushare trade_cal 返回整年含休市日);不完整先 force
        # 刷新,仍不完整才 blocked。集合比较一年 365 个短字符串,成本可忽略。
        from datetime import date as _date, timedelta as _td

        d0, d1 = _date(year, 1, 1), _date(year, 12, 31)
        expected = {(d0 + _td(days=i)).isoformat() for i in range((d1 - d0).days + 1)}

        def _have() -> set:
            return {r[0] for r in conn.execute(
                "SELECT date FROM trade_calendar WHERE date BETWEEN ? AND ?",
                (f"{year}-01-01", f"{year}-12-31"),
            ).fetchall()}

        if expected.issubset(_have()):
            return True
        ensure_trade_calendar(conn, registry, year=year, force=True)
        return expected.issubset(_have())

    try:
        if not _year_complete(now.year):
            return None
        today_open = Q.is_trade_day_from_db(conn, today)
        if today_open is None:
            return None
        if today_open and (now.hour, now.minute) >= CLOSE_CUTOFF:
            return today
        def _valid_candidate(d: "str | None") -> bool:
            # round3 high-1:issubset 不排斥"额外"非法行(如 2026-02-30,is_open=1),它们
            # 可能被 SELECT 为最近 open 日;候选必须是可解析的真实日期才可信
            if d is None:
                return False
            from datetime import date as _date
            try:
                _date.fromisoformat(d)
                return True
            except ValueError:
                return False

        prev = Q.get_prev_trade_date_from_db(conn, today)
        if prev is None and now.month == 1:
            if not _year_complete(now.year - 1):
                return None
            prev = Q.get_prev_trade_date_from_db(conn, today)
        if not _valid_candidate(prev):
            logger.warning("resolve_latest_closed_trade_date: 非法日历候选 %r,blocked", prev)
            return None
        # round3 high-1:跨年候选(如 1 月返回去年 12 月,或当年年初库里只有零星旧行)必须
        # 先验证候选所在年完整,防前一年仅存一条陈旧 open 行时把过期日期当最新收盘日
        if prev[:4] != str(now.year) and not _year_complete(int(prev[:4])):
            return None
        return prev
    except Exception as e:
        # blocked=不推送是合法终态,但必须留痕:否则"日历源失败"与"代码回归导致永久
        # blocked"在调用方看来完全一样,静默漏推无从排障(门1 A-2)。
        logger.warning("resolve_latest_closed_trade_date blocked by exception: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def is_non_trading_day(conn: sqlite3.Connection, registry, date: str) -> bool:
    """非交易日（周末/法定假日）守卫判定，供盘后类 CLI（落库/推送）跳过非交易日触发复用。

    仅当交易日历**明确**判定为非交易日时返回 True（应跳过）；判定失败 / 无日历缓存 → 返回
    False（fail-open，照常执行，与 main.py pre/post 一致；周末经 is_trade_day 的 weekday 兜底必被拦）。

    背景：launchd plist 节假日仍触发，非交易日数据源常返上一交易日的陈旧数据（且不带日期校验），
    会按当日落库 + 误推送。守卫按目标日期年份预取日历（支持 --date 跨年历史校准）。

    日历写入随传入的 conn 走：调用方在 dry-run（内存副本 / 不落库）模式下应**自行豁免**本守卫，
    避免在真实库上写日历缓存而破坏 dry-run 无副作用语义。

    异常 fail-open 前会 conn.rollback()：日历导入是逐行 execute、末尾才 commit，若 provider 返回
    脏日历致中途抛错，已执行的半截日历行会留在事务里，被调用方后续业务 commit 一并提交、污染交易日
    缓存（codex 门2 finding）。回滚保证 fail-open 时连接上无本守卫产生的待提交写入。
    """
    try:
        ensure_trade_calendar(conn, registry, year=int(date[:4]))
        return is_trade_day(date, conn=conn, registry=registry) is False
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False
