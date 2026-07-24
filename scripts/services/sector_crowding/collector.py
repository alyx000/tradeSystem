"""sector_crowding 采集层：sw_daily 行业行情 + 两市总额守卫 + 资金流代理三路。"""
from __future__ import annotations

import logging
import math
from datetime import date as calendar_date, timedelta

# 复用 volume-watch 已实战校准的三段守卫常量（只 import 常量不改其文件）
from services.volume_concentration.collector import (
    MARKET_SZ_SH_RATIO_FLOOR,
    MARKET_TOTAL_DROP_WARN_RATIO,
    MARKET_TOTAL_FLOOR_BILLION,
)

from . import repo

logger = logging.getLogger(__name__)

# sw_daily amount 单位换算除数 → 亿元。2026-07-18 真机实测校准:L1 amount 总和
# 265,411,140(万元)÷10000 ≈ 2.65 万亿,与当日全市场量级吻合;与 get_sector_rankings
# 的 amount/10000 口径一致。
AMOUNT_TO_BILLION = 10000.0
ACTIVITY_PROBE_START = "1990-01-01"
# 2026-07-24 真机核对：SW2021 L2 分类表共 134 码，其中 is_pub=1 为 124。
# 总码表保留退役历史码，正常只增不应缩；低于已验证基线一律视作稳定部分返回。
SW2021_L2_CATALOG_COUNT_FLOOR = 134
SW2021_L2_PUBLISHED_COUNT_FLOOR = 120
_L2_CATALOG_CACHE_KEY = "_sector_crowding_l2_catalog"
_L2_NAME_CACHE_KEY = "_sector_crowding_l2_names"


def _finite_num(v) -> bool:
    """有限数值守卫:pandas 缺值即 NaN 浮点,is None 挡不住;NaN 落库会写成非标 JSON token。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _amount_billion(amount) -> float | None:
    """amount(万元)→亿元的单点换算(daily 与 backfill 共用,防口径分叉);非有限值 → None。"""
    return round(amount / AMOUNT_TO_BILLION, 2) if _finite_num(amount) else None


def _clean_close(close) -> float | None:
    """close 非有限值置 None:NaN 经 json.dumps 会落成非标 token,严格 JSON 消费端直接炸。"""
    return close if _finite_num(close) else None


def _published_flag(value) -> bool | None:
    """index_classify.is_pub → bool；未知值不猜测。"""
    if value == 1 or value == "1":
        return True
    if value == 0 or value == "0":
        return False
    return None


def _l2_catalog(provider) -> dict[str, bool]:
    """返回申万 L2 全代码及当前发布状态。

    `_ensure_sw_l2_codes()` 当前会包含 `is_pub=0` 的历史退役代码；daily 若把它直接
    当当前宇宙，会永久误报缺码。优先读取同一官方分类表的 `is_pub`，仅测试桩或旧
    provider 缺该字段时退回既有代码集，并把退回代码保守视作当前发布。
    """
    cached = getattr(provider, "__dict__", {}).get(_L2_CATALOG_CACHE_KEY)
    if isinstance(cached, dict):
        return dict(cached)

    fallback_codes = set(provider._ensure_sw_l2_codes() or set())
    if not fallback_codes:
        logger.warning("[sector-crowding] L2 既有码表为空,中止分类表校验")
        return {}
    if len(fallback_codes) < SW2021_L2_CATALOG_COUNT_FLOOR:
        logger.warning(
            "[sector-crowding] L2 既有码表低于已验证完整性基线"
            "(all=%d/%d),中止",
            len(fallback_codes),
            SW2021_L2_CATALOG_COUNT_FLOOR,
        )
        return {}
    try:
        df = provider.pro.index_classify(level="L2", src="SW2021")
        columns = set(getattr(df, "columns", []))
        if df is not None and not getattr(df, "empty", True) and {
            "index_code", "is_pub"
        } <= columns:
            catalog: dict[str, bool] = {}
            for row in df.to_dict("records"):
                code = row.get("index_code")
                published = _published_flag(row.get("is_pub"))
                if not isinstance(code, str) or not code.strip() or published is None:
                    raise ValueError("index_classify L2 含非法 index_code/is_pub")
                code = code.strip()
                if code in catalog and catalog[code] is not published:
                    raise ValueError(f"index_classify L2 重复码状态冲突:{code}")
                catalog[code] = published
            if catalog:
                if set(catalog) != fallback_codes:
                    logger.warning(
                        "[sector-crowding] index_classify L2 两次读取代码集合不一致"
                        "(cached=%d,direct=%d),中止",
                        len(fallback_codes),
                        len(catalog),
                    )
                    return {}
                published_count = sum(catalog.values())
                if (
                    len(catalog) < SW2021_L2_CATALOG_COUNT_FLOOR
                    or published_count < SW2021_L2_PUBLISHED_COUNT_FLOOR
                ):
                    logger.warning(
                        "[sector-crowding] index_classify L2 低于已验证完整性基线"
                        "(all=%d/%d,published=%d/%d),中止",
                        len(catalog),
                        SW2021_L2_CATALOG_COUNT_FLOOR,
                        published_count,
                        SW2021_L2_PUBLISHED_COUNT_FLOOR,
                    )
                    return {}
                provider.__dict__[_L2_CATALOG_CACHE_KEY] = dict(catalog)
                provider.__dict__[_L2_NAME_CACHE_KEY] = {
                    str(row.get("index_code")).strip(): str(row.get("industry_name"))
                    for row in df.to_dict("records")
                    if isinstance(row.get("index_code"), str)
                    and row.get("index_code").strip()
                    and isinstance(row.get("industry_name"), str)
                    and row.get("industry_name").strip()
                }
                return catalog
    except Exception as e:
        logger.warning("[sector-crowding] L2 分类发布状态读取失败: %s", e)
        # 分类表部分返回时不能退回较小集合并标 complete；有独立完整 fallback 时，
        # 保留其代码范围但把发布状态保守视为 active，使缺码只会 fail-closed。
    fallback = {code: True for code in fallback_codes}
    # 降级状态没有 is_pub，不能缓存；同进程下一次调用仍应有机会恢复完整分类表。
    return fallback


def _compact_trade_date(value) -> str | None:
    """外部 trade_date 归一为 YYYYMMDD；非法值返回 None。"""
    compact = str(value)
    return compact if len(compact) == 8 and compact.isdigit() else None


def fetch_sector_daily(provider, date: str) -> dict | None:
    """当日申万 L1+L2 快照。L1 缺失且 parent_map 可靠才合成（meta 标 synthesized）。

    sw_daily 实测含 L3 与"申万50"等特殊指数,不在 L1/L2 码表内的行必须过滤(防混级双计)。
    """
    d = date.replace("-", "")
    try:
        df = provider.pro.sw_daily(trade_date=d)
    except Exception as e:
        logger.warning("[sector-crowding] sw_daily 失败: %s", e)
        return None
    if df is None or df.empty:
        return None
    l1_codes = provider._ensure_sw_l1_codes() or set()
    l2_catalog = _l2_catalog(provider)
    l2_codes = set(l2_catalog)
    if not l2_catalog:
        # L2 是本任务的核心事实宇宙；码表失败时只落 L1 会让标签接口把“无 L2”
        # 误当正常快照。宁可整日不落，等待下一次重跑。
        logger.warning("[sector-crowding] L2 码表为空(拉取失败?),中止当日采集")
        return None
    if not l1_codes:
        # 码表拉取失败被惰性缓存为空集(进程内不重试,仓库既有模式):原生 L1 行会被过滤、
        # 走合成降级(name=code/close 缺席)。留日志便于排障,daily 单次进程影响面小。
        logger.warning("[sector-crowding] L1 码表为空(拉取失败?),原生 L1 行将被过滤并降级合成")
    sectors = []
    for row in df.to_dict("records"):
        code = row.get("ts_code")
        level = "L1" if code in l1_codes else ("L2" if code in l2_codes else None)
        if level is None:
            continue
        row_date = _compact_trade_date(row.get("trade_date"))
        if row_date != d:
            logger.warning(
                "[sector-crowding] %s sw_daily 返回错日/非法行(code=%s,trade_date=%r),中止落库",
                date,
                code,
                row.get("trade_date"),
            )
            return None
        sectors.append({
            "code": code, "name": row.get("name"), "level": level,
            "close": _clean_close(row.get("close")),
            "amount_billion": _amount_billion(row.get("amount")),
        })
    if not sectors:
        return None
    observed_l2_codes = {
        sector["code"] for sector in sectors if sector.get("level") == "L2"
    }
    missing_expected_l2_codes = []
    for code, currently_published in l2_catalog.items():
        if code in observed_l2_codes:
            continue
        try:
            active_but_missing = _code_expected_in_interval(
                provider,
                code,
                date,
                date,
                currently_published,
            )
        except Exception as e:
            logger.warning(
                "[sector-crowding] %s 无法判定 L2 %s 有效期(%s),中止落库",
                date,
                code,
                e,
            )
            return None
        if active_but_missing:
            missing_expected_l2_codes.append(code)
    if missing_expected_l2_codes:
        # sw_daily 非空并不代表完整：部分返回若落库，会把缺失板块静默从每日标签清单
        # 中删掉。分类状态与历史有效期共同定义本次受控 as-of 宇宙，缺应到码即 fail-closed。
        logger.warning(
            "[sector-crowding] %s L2 快照不完整(observed=%d/expected=%d,missing=%s),中止落库",
            date,
            len(observed_l2_codes),
            len(observed_l2_codes) + len(missing_expected_l2_codes),
            ",".join(missing_expected_l2_codes[:10]),
        )
        return None
    sectors, l1_status = resolve_l1(sectors, provider._ensure_sw_l1_parent_map)
    expected_l2_codes = sorted(observed_l2_codes)
    return {
        "sectors": sectors,
        "meta": {
            "l1_status": l1_status,
            "l2_expected_count": len(expected_l2_codes),
            "l2_observed_count": len(observed_l2_codes),
            "l2_expected_codes": expected_l2_codes,
            "l2_universe_complete": True,
            "l2_catalog_count": len(l2_catalog),
            "l2_published_count": sum(l2_catalog.values()),
            "l2_universe_basis": "index_classify_is_pub+sw_daily_observed_bounds",
            "source": "tushare:sw_daily",
        },
    }


def resolve_l1(sectors: list[dict], parent_map_getter) -> tuple[list[dict], str]:
    """L1 状态机单一真源（daily 与 backfill 共用）:native / synthesized / missing。

    映射不可靠(getter 返回空)时禁止合成(spec v2 严重1:合成路径条件启用)。"""
    if any(s.get("level") == "L1" for s in sectors):
        return sectors, "native"
    parent_map = parent_map_getter() or {}
    if parent_map:
        return sectors + synthesize_l1(sectors, parent_map), "synthesized"
    return sectors, "missing"


def synthesize_l1(l2_sectors: list[dict], parent_map: dict) -> list[dict]:
    """L2 成交额按 parent_code 归并成 L1。close 不可加总 → None（斜率维度缺席）。

    daily 与 backfill 共用同一分支逻辑(Explore review 中1):回填不合成会导致合成 L1
    永无历史序列 → 分位/双高对 L1 长期失效。"""
    agg: dict = {}
    for s in l2_sectors:
        if s.get("level") != "L2" or not _finite_num(s.get("amount_billion")):
            continue  # NaN 参与加总会把整个合成 L1 毒成 NaN(单行脏值放大为整行业缺席)
        parent = parent_map.get(s.get("code"))
        if not parent:
            continue
        ent = agg.setdefault(parent, {"code": parent, "name": parent, "level": "L1",
                                      "close": None, "amount_billion": 0.0})
        ent["amount_billion"] += s["amount_billion"]
    for ent in agg.values():
        ent["amount_billion"] = round(ent["amount_billion"], 2)  # 出循环一次 round,免截断误差累积
    return list(agg.values())


def fetch_market_total(conn, registry, date: str):
    """两市总额（get_market_volume）+ 三段守卫；prev 基准读本任务自己的表。

    prev 刻意不走 volume_concentration 的同名查询:两表覆盖日期不同(本表回填后历史更长、
    对方守卫失败日落 NULL 的日期集合也不同),跨表基准会随对方任务故障漂移。"""
    result = registry.call("get_market_volume", date)
    if not (result.success and result.data):
        return None, None
    data = result.data
    total = data.get("total_billion")
    # NaN 前置拦截:三段守卫全是 < 比较,NaN 比较恒 False 会穿透全部守卫、绕过
    # missing_data 标注渲染出 "nan 亿"(memory:降级链"成功但含脏值"事故同型)
    if total is not None and not _finite_num(total):
        logger.warning("[sector-crowding] %s 两市成交额为非有限值(%r),落 NULL(source=%s)",
                       date, total, result.source)
        return None, None
    if total is None or total < MARKET_TOTAL_FLOOR_BILLION:
        if total is not None:
            logger.warning("[sector-crowding] %s 两市成交额 %.0f 亿低于绝对地板 %.0f 亿,落 NULL(source=%s)",
                           date, total, MARKET_TOTAL_FLOOR_BILLION, result.source)
        return None, None
    sh, sz = data.get("shanghai_billion"), data.get("shenzhen_billion")
    if sh is not None and sz is not None and sh > 0 and sz < sh * MARKET_SZ_SH_RATIO_FLOOR:
        logger.warning("[sector-crowding] %s 深市腿 %.0f 亿 < 沪市腿 %.0f 亿×%.1f,疑口径退化,落 NULL(source=%s)",
                       date, sz, sh, MARKET_SZ_SH_RATIO_FLOOR, result.source)
        return None, None
    prev = repo.get_latest_market_total_before(conn, date)
    if prev and total < prev * (1 - MARKET_TOTAL_DROP_WARN_RATIO):
        logger.warning("[sector-crowding] %s 两市成交额 %.0f 亿较前值 %.0f 亿骤降逾 %.0f%%,请人工复核(仅告警,照常落库)",
                       date, total, prev, MARKET_TOTAL_DROP_WARN_RATIO * 100)
    return total, result.source


CHUNK_YEARS = 4  # 回填分片窗口:7.5年≈1820行/码贴近镜像2000行静默截断上限,必须分片
# 单码瞬时超时重试:~330 次请求里镜像随机掉 1 个(实测两轮各废一整轮 15 分钟),
# 重试吸收瞬时抖动;重试穷尽仍失败才记 codes_failed 触发 fail-closed(保底语义不变)
CODE_FETCH_RETRIES = 3
CODE_FETCH_RETRY_SLEEP_SECONDS = 2.0
TRUNCATION_ROW_FLOOR = 2000  # 单片返回行数达到该值=疑似截断(镜像单页上限)


class BackfillTruncationError(Exception):
    """单片返回 ≥2000 行=疑似静默截断,拒绝落库(memory: index_member_all 同坑)。"""


def fetch_code_history(provider, code: str, start: str, end: str) -> list[dict]:
    """按 ≤CHUNK_YEARS 年窗口分片拉单码区间日线，升序返回 {date, close, amount_billion}。"""
    out = []
    chunk_start = start
    while chunk_start <= end:
        cy = int(chunk_start[:4])
        chunk_end = min(f"{cy + CHUNK_YEARS - 1}-12-31", end)
        df = provider.pro.sw_daily(
            ts_code=code,
            start_date=chunk_start.replace("-", ""),
            end_date=chunk_end.replace("-", ""),
        )
        if df is not None and len(df) >= TRUNCATION_ROW_FLOOR:
            raise BackfillTruncationError(
                f"{code} {chunk_start}~{chunk_end} 返回 {len(df)} 行,疑似截断")
        if df is not None and not df.empty:
            for row in df.to_dict("records"):
                td = _compact_trade_date(row.get("trade_date"))
                # pandas 列含缺值会整列 int→float64:str() 出 "20200101.0"/"nan",
                # 直接切片会批量生成畸形日期键落库且永不与 daily 行对齐 → 跳行留日志
                if td is None:
                    logger.warning("[sector-crowding backfill] %s 畸形 trade_date %r,跳过该行",
                                   code, row.get("trade_date"))
                    continue
                row_date = f"{td[:4]}-{td[4:6]}-{td[6:]}"
                if not chunk_start <= row_date <= chunk_end:
                    raise RuntimeError(
                        f"{code} 返回请求分片外日期 {row_date}"
                        f"(expected {chunk_start}~{chunk_end})"
                    )
                row_code = row.get("ts_code")
                if row_code != code:
                    raise RuntimeError(
                        f"{code} 返回缺失或不匹配代码行 {row_code!r}"
                    )
                out.append({
                    "date": row_date,
                    "close": _clean_close(row.get("close")),
                    "amount_billion": _amount_billion(row.get("amount")),
                })
        chunk_start = f"{cy + CHUNK_YEARS}-01-01"
    out.sort(key=lambda r: r["date"])
    return out


def _fetch_code_history_with_retry(provider, code: str, start: str, end: str) -> list[dict]:
    """fetch_code_history 加瞬时故障重试。截断异常不重试(数据问题非网络问题)。"""
    import time

    last_exc: Exception | None = None
    for attempt in range(1, CODE_FETCH_RETRIES + 1):
        try:
            return fetch_code_history(provider, code, start, end)
        except BackfillTruncationError:
            raise
        except Exception as e:
            last_exc = e
            if attempt < CODE_FETCH_RETRIES:
                logger.info("[sector-crowding backfill] %s 第 %d 次失败(%s),%.0fs 后重试",
                            code, attempt, e, CODE_FETCH_RETRY_SLEEP_SECONDS)
                time.sleep(CODE_FETCH_RETRY_SLEEP_SECONDS)
    raise last_exc  # type: ignore[misc]


def _sw_name_map(provider) -> dict:
    """code → 申万行业中文名(index_classify industry_name)。失败返 {}(name 退回 code)。"""
    m: dict = dict(
        getattr(provider, "__dict__", {}).get(_L2_NAME_CACHE_KEY) or {}
    )
    try:
        # L2 已随 _l2_catalog 同次读取并缓存；这里只补 L1，避免第三次拉同一 L2 表。
        df = provider.pro.index_classify(level="L1", src="SW2021")
        if df is not None and not getattr(df, "empty", True) and "industry_name" in df.columns:
            m.update(dict(zip(df["index_code"], df["industry_name"])))
    except Exception as e:
        logger.warning("[sector-crowding backfill] 行业名映射获取失败,name 退回 code: %s", e)
    return m


def _probe_history_exists(provider, code: str, start: str, end: str) -> bool:
    """轻量有效期探针：区间内是否存在至少一根行情，异常重试后向上抛。

    探针只判断“存在”，即便接口命中分页上限也不影响结论；`None` 属未知响应而非
    合法空表，必须 fail-closed。
    """
    if start > end:
        return False
    import time

    last_exc: Exception | None = None
    consecutive_empty = 0
    for attempt in range(1, CODE_FETCH_RETRIES + 1):
        try:
            df = provider.pro.sw_daily(
                ts_code=code,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
            if df is None:
                raise RuntimeError("sw_daily 返回 None")
            if df.empty:
                # empty 也可能是镜像瞬时空响应；该 False 会用于放行生效前/退出后
                # 空窗，必须连续稳定出现后才能采信。
                consecutive_empty += 1
                if consecutive_empty < CODE_FETCH_RETRIES:
                    continue
                return False
            consecutive_empty = 0
            if "trade_date" not in df.columns:
                raise RuntimeError("sw_daily 探针响应缺 trade_date")
            valid_dates = []
            for raw_date in df["trade_date"].tolist():
                compact = str(raw_date)
                if len(compact) == 8 and compact.isdigit():
                    valid_dates.append(
                        f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
                    )
            if any(start <= d <= end for d in valid_dates):
                return True
            raise RuntimeError(
                f"sw_daily 探针非空但无区间内有效日期({start}~{end})"
            )
        except Exception as e:
            last_exc = e
            consecutive_empty = 0
            if attempt < CODE_FETCH_RETRIES:
                logger.info(
                    "[sector-crowding] %s 有效期探针第 %d 次失败(%s),%.0fs 后重试",
                    code,
                    attempt,
                    e,
                    CODE_FETCH_RETRY_SLEEP_SECONDS,
                )
                time.sleep(CODE_FETCH_RETRY_SLEEP_SECONDS)
    raise last_exc or RuntimeError(
        f"{code} 有效期探针未取得稳定空响应或正向证据"
    )


def _code_expected_in_interval(
    provider,
    code: str,
    start: str,
    end: str,
    currently_published: bool,
) -> bool:
    """整段无行情时，该码是否仍应属于区间有效宇宙（True=缺失是错误）。

    行情存在性定义历史有效期。当前发布码先查区间前：已有历史即应到；未有历史再查
    区间后，仅“未来才首次出现”可排除。当前退役码反向先查区间后：之后再无行情即可
    排除；若之后有行情，再以区间前是否也有行情判断是否横跨本区间。该顺序让每日最新
    快照对退役码无需发历史探针。
    """
    start_day = calendar_date.fromisoformat(start)
    end_day = calendar_date.fromisoformat(end)
    today = calendar_date.today()
    before_end = (start_day - timedelta(days=1)).isoformat()
    after_start = (end_day + timedelta(days=1)).isoformat()
    if currently_published:
        has_before = _probe_history_exists(
            provider,
            code,
            ACTIVITY_PROBE_START,
            before_end,
        )
        if has_before:
            return True
        has_after = _probe_history_exists(
            provider, code, after_start, today.isoformat()
        )
        return not has_after

    has_after = _probe_history_exists(
        provider, code, after_start, today.isoformat()
    )
    if not has_after:
        return False
    return _probe_history_exists(
        provider, code, ACTIVITY_PROBE_START, before_end
    )


def _open_trade_dates(provider, start: str, end: str) -> list[str]:
    """用上交所权威交易日历建立回填日期脊柱；按自然年分片并校验完整响应。"""
    start_day = calendar_date.fromisoformat(start)
    end_day = calendar_date.fromisoformat(end)
    if start_day > end_day:
        raise ValueError(f"交易日历区间倒置:{start}~{end}")

    import time

    open_dates: set[str] = set()
    for year in range(start_day.year, end_day.year + 1):
        chunk_start = max(start_day, calendar_date(year, 1, 1))
        chunk_end = min(end_day, calendar_date(year, 12, 31))
        expected_natural_dates: set[str] = set()
        cursor = chunk_start
        while cursor <= chunk_end:
            expected_natural_dates.add(cursor.isoformat())
            cursor += timedelta(days=1)

        last_exc: Exception | None = None
        for attempt in range(1, CODE_FETCH_RETRIES + 1):
            try:
                df = provider.pro.trade_cal(
                    exchange="SSE",
                    start_date=chunk_start.strftime("%Y%m%d"),
                    end_date=chunk_end.strftime("%Y%m%d"),
                )
                if df is None or df.empty:
                    raise RuntimeError("trade_cal 返回空")
                if not {"cal_date", "is_open"} <= set(df.columns):
                    raise RuntimeError("trade_cal 缺 cal_date/is_open")
                states: dict[str, bool] = {}
                for row in df.to_dict("records"):
                    compact = _compact_trade_date(row.get("cal_date"))
                    if compact is None:
                        raise RuntimeError(
                            f"trade_cal 含非法 cal_date:{row.get('cal_date')!r}"
                        )
                    d = f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
                    flag = _published_flag(row.get("is_open"))
                    if d not in expected_natural_dates or flag is None:
                        raise RuntimeError(
                            f"trade_cal 含区间外日期或非法 is_open:{d}/{row.get('is_open')!r}"
                        )
                    if d in states and states[d] is not flag:
                        raise RuntimeError(f"trade_cal 日期状态冲突:{d}")
                    states[d] = flag
                if set(states) != expected_natural_dates:
                    raise RuntimeError(
                        "trade_cal 自然日覆盖不完整"
                        f"(observed={len(states)},expected={len(expected_natural_dates)})"
                    )
                open_dates.update(d for d, is_open in states.items() if is_open)
                break
            except Exception as e:
                last_exc = e
                if attempt < CODE_FETCH_RETRIES:
                    logger.info(
                        "[sector-crowding backfill] 交易日历 %s~%s 第 %d 次失败(%s),%.0fs 后重试",
                        chunk_start,
                        chunk_end,
                        attempt,
                        e,
                        CODE_FETCH_RETRY_SLEEP_SECONDS,
                    )
                    time.sleep(CODE_FETCH_RETRY_SLEEP_SECONDS)
        else:
            raise last_exc  # type: ignore[misc]
    return sorted(open_dates)


def fetch_history_by_date(provider, start: str, end: str) -> tuple[dict, list[str]]:
    """回填阶段①（采集层）:码表枚举 → 逐码分片拉取 → 按日期聚合。

    单码失败记账继续;截断异常向上抛不吞(疑似截断宁可整体失败也不落半截)。
    回填行 name 用 index_classify 中文名(映射失败退回 code——报告/趋势按名渲染依赖它)。"""
    l1 = provider._ensure_sw_l1_codes() or set()
    l2_catalog = _l2_catalog(provider)
    l2 = set(l2_catalog)
    if not l1 or not l2:
        # 码表空集=拉取失败(真机实测 L1=31/L2=134 恒非空)。不抛错会静默写出半截历史,
        # 且这些日期随后被 get_existing_dates 判"已有"锁死,重跑无法自愈 → 整体中止
        raise RuntimeError(
            f"sector-crowding backfill: 申万码表为空(L1={len(l1)}/L2={len(l2)}),疑拉取失败,中止回填")
    open_trade_dates = _open_trade_dates(provider, start, end)
    open_trade_date_set = set(open_trade_dates)
    name_map = _sw_name_map(provider)
    by_date: dict = {}
    codes_failed: list[str] = []
    l2_bar_dates: dict[str, set[str]] = {}
    for code, level in [(c, "L1") for c in sorted(l1)] + [(c, "L2") for c in sorted(l2)]:
        try:
            bars = _fetch_code_history_with_retry(provider, code, start, end)
            off_calendar_dates = {
                bar["date"] for bar in bars if bar["date"] not in open_trade_date_set
            }
            if off_calendar_dates:
                raise RuntimeError(
                    f"{code} 返回非开放日行情:{','.join(sorted(off_calendar_dates)[:5])}"
                )
            if level == "L2" and not bars:
                currently_published = l2_catalog[code]
                if not _code_expected_in_interval(
                    provider,
                    code,
                    start,
                    end,
                    currently_published,
                ):
                    # 目标区间尚未生效、已经退出，或分类表明确未发布且全历史无行情。
                    l2_bar_dates[code] = set()
                    continue
                raise RuntimeError(
                    f"{code} 目标区间空且有效期不支持排除"
                    f"(published={currently_published})"
                )
        except BackfillTruncationError:
            raise
        except Exception as e:
            logger.warning("[sector-crowding backfill] %s 失败(重试 %d 次后): %s",
                           code, CODE_FETCH_RETRIES, e)
            codes_failed.append(code)
            continue
        if level == "L2":
            l2_bar_dates[code] = {bar["date"] for bar in bars}
        for bar in bars:
            by_date.setdefault(bar["date"], []).append(
                {"code": code, "name": name_map.get(code, code), "level": level,
                 "close": bar["close"], "amount_billion": bar["amount_billion"]})

    # 单码非空仍可能缺日：必须以独立交易日历作脊柱，不能用被校验行情的日期并集，
    # 否则全体 L2 同时漏一天时脊柱会一起收缩、永久漏检。
    all_snapshot_dates = open_trade_dates
    for code, dates in l2_bar_dates.items():
        if not dates:
            continue
        first_date, last_date = min(dates), max(dates)
        currently_published = l2_catalog[code]
        if all_snapshot_dates and first_date > all_snapshot_dates[0]:
            has_history_before = _probe_history_exists(
                provider,
                code,
                ACTIVITY_PROBE_START,
                (calendar_date.fromisoformat(first_date) - timedelta(days=1)).isoformat(),
            )
            if has_history_before and code not in codes_failed:
                logger.warning(
                    "[sector-crowding backfill] %s 区间首端缺行情,中止回填",
                    code,
                )
                codes_failed.append(code)
                continue
        if all_snapshot_dates and last_date < all_snapshot_dates[-1]:
            # 当前仍发布的码必应覆盖到区间末端，不需要先发结果无用的 after 探针。
            has_history_after = (
                True
                if currently_published
                else _probe_history_exists(
                    provider,
                    code,
                    (calendar_date.fromisoformat(last_date) + timedelta(days=1)).isoformat(),
                    calendar_date.today().isoformat(),
                )
            )
            if has_history_after and code not in codes_failed:
                logger.warning(
                    "[sector-crowding backfill] %s 区间末端缺行情,中止回填",
                    code,
                )
                codes_failed.append(code)
                continue
        expected_active_dates = {
            d for d in all_snapshot_dates if first_date <= d <= last_date
        }
        missing_active_dates = expected_active_dates - dates
        if missing_active_dates and code not in codes_failed:
            logger.warning(
                "[sector-crowding backfill] %s 有效期内缺 %d 个快照日,中止回填",
                code,
                len(missing_active_dates),
            )
            codes_failed.append(code)
    return by_date, codes_failed


def fetch_proxy(registry, date: str) -> dict:
    """资金流代理三路，各自独立失败不拖垮整体。

    moneyflow 三级顺序贴 spec #7:ths→dc→akshare fund_flow。前两个 capability akshare
    也声明了 dc,registry 会自动跨 provider 降级;第三级覆盖"仅 akshare fund_flow 可用"
    的残余场景。"""
    errors: list[str] = []
    moneyflow, mf_source = None, None
    for cap in ("get_sector_moneyflow_ths", "get_sector_moneyflow_dc", "get_sector_fund_flow"):
        r = _try_call(registry, cap, date, errors)
        if r is not None:
            moneyflow, mf_source = _normalize_moneyflow(r.data), r.source
            break
    etf_r = _try_call(registry, "get_etf_flow", date, errors)
    margin_r = _try_call(registry, "get_margin_data", date, errors)
    return {"moneyflow": moneyflow, "moneyflow_source": mf_source,
            "etf": _normalize_etf(etf_r.data) if etf_r else None,
            "margin": _clean_margin(margin_r.data) if margin_r else None, "errors": errors}


def _normalize_etf(records: list) -> list[dict]:
    """ETF 代理归一(与 moneyflow 对称):数值字段非有限即置 None,防 NaN 落库/渲染 +nan 亿份。"""
    out = []
    for row in records or []:
        out.append({
            "code": row.get("code"), "name": row.get("name"),
            "total_shares_billion": row.get("total_shares_billion")
            if _finite_num(row.get("total_shares_billion")) else None,
            "shares_change_billion": row.get("shares_change_billion")
            if _finite_num(row.get("shares_change_billion")) else None,
        })
    return out


def _clean_margin(data: dict | None) -> dict | None:
    """两融代理清洗:白名单重建输出,数值字段非有限置 None,主值非有限整体置 None。

    不原样透传外部 dict:嵌套字段(exchanges 等)含 NaN 会随 proxy_json 落成非标 JSON
    (codex 门2 轮2 中);未消费字段一律不带。"""
    if not isinstance(data, dict) or not _finite_num(data.get("total_rzrqye_yi")):
        return None
    return {
        "trade_date": data.get("trade_date"),
        "requested_date": data.get("requested_date"),
        "market_scope": data.get("market_scope"),
        "total_rzrqye_yi": data["total_rzrqye_yi"],
        "total_rzye_yi": data.get("total_rzye_yi")
        if _finite_num(data.get("total_rzye_yi")) else None,
        "total_rqye_yi": data.get("total_rqye_yi")
        if _finite_num(data.get("total_rqye_yi")) else None,
    }


def _try_call(registry, cap: str, date: str, errors: list):
    """registry.call 薄封装:成功返 result 对象,失败按统一格式记账返 None。"""
    r = registry.call(cap, date)
    if r.success and r.data:
        return r
    errors.append(f"{cap}: {getattr(r, 'error', None) or 'no data'}")
    return None


def _normalize_moneyflow(records: list) -> list[dict]:
    """归一不同源字段形态：统一输出 {name, net_amount_yi}，脏值剔除。"""
    out = []
    for row in records:
        name = row.get("name") or row.get("industry") or ""
        val = row.get("net_amount_yi")
        if val is None:
            val = row.get("net_inflow_billion")
        try:
            val = float(val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(val):
            continue  # float("nan")/inf 不抛异常但会毒化排序比较并落成非标 JSON
        if name:
            out.append({"name": name, "net_amount_yi": round(val, 2)})
    return out
