"""
市场数据采集器
将 provider 层的原始数据聚合为结构化的报告数据
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
import yaml

from analyzers.sector_rhythm import SectorRhythmAnalyzer
from providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent.parent.parent

CALENDAR_AUTO_HEADER = """# ============================================
# 预拉取宏观日历（自动生成）
# 由 python main.py prefetch-calendar 写入，请勿手动编辑
# ============================================
#
"""


def _merge_calendar(ak_events: list[dict] | None, date: str, base_dir: Path) -> list[dict]:
    """
    合并三层日历：手动 calendar.yaml > 预拉取 calendar_auto.yaml > AkShare 当天实时。
    - ak_events: AkShare get_macro_calendar 当天列表（可为 None/空）
    - date: YYYY-MM-DD
    按 event 名去重，先出现的优先级高；source 为 static / auto / akshare。
    """
    merged: list[dict] = []
    seen: set[str] = set()

    def _append(ev: dict, source: str) -> None:
        key = str(ev.get("event", ""))
        if not key or key in seen:
            return
        seen.add(key)
        row = {**ev, "source": source}
        row.setdefault("actual", "")
        merged.append(row)

    # 1. 手动 calendar.yaml
    static_path = base_dir / "tracking" / "calendar.yaml"
    if static_path.exists():
        try:
            with open(static_path, encoding="utf-8") as f:
                cal_data = yaml.safe_load(f) or {}
            for ev in (cal_data.get("events") or []):
                ev_date = str(ev.get("date", ""))
                if ev_date != date:
                    continue
                _append({
                    "event": str(ev.get("event", "")),
                    "time": str(ev.get("time", "")),
                    "importance": str(ev.get("impact", ev.get("importance", ""))),
                    "category": str(ev.get("category", "")),
                    "region": str(ev.get("region", "")),
                    "expected": str(ev.get("expected", "")),
                    "prior": str(ev.get("prior", "")),
                    "actual": "",
                }, "static")
        except Exception as e:
            logger.warning(f"读取 tracking/calendar.yaml 失败: {e}")

    # 2. 预拉取 calendar_auto.yaml（按 date 过滤当天）
    auto_path = base_dir / "tracking" / "calendar_auto.yaml"
    if auto_path.exists():
        try:
            with open(auto_path, encoding="utf-8") as f:
                cal_data = yaml.safe_load(f) or {}
            for ev in (cal_data.get("events") or []):
                ev_date = str(ev.get("date", ""))
                if ev_date != date:
                    continue
                imp = str(ev.get("impact", ev.get("importance", "")))
                _append({
                    "event": str(ev.get("event", "")),
                    "time": str(ev.get("time", "")),
                    "importance": imp,
                    "category": str(ev.get("category", "")),
                    "region": str(ev.get("region", "")),
                    "expected": str(ev.get("expected", "")),
                    "prior": str(ev.get("prior", "")),
                    "actual": str(ev.get("actual", "")),
                }, "auto")
        except Exception as e:
            logger.warning(f"读取 tracking/calendar_auto.yaml 失败: {e}")

    # 3. AkShare 当天实时
    for ev in (ak_events or []):
        _append({**ev}, "akshare")

    return merged


def _normalize_auto_event(ev: dict) -> dict:
    """写入 calendar_auto.yaml 前的字段整理（可序列化）。"""
    def _s(v) -> str:
        if v is None:
            return ""
        s = str(v).strip()
        if s.lower() == "nan":
            return ""
        return s

    return {
        "date": _s(ev.get("date")),
        "event": _s(ev.get("event")),
        "time": _s(ev.get("time")),
        "region": _s(ev.get("region")),
        "importance": _s(ev.get("importance")),
        "category": _s(ev.get("category")),
        "expected": _s(ev.get("expected")),
        "prior": _s(ev.get("prior")),
        "actual": _s(ev.get("actual")),
        "source": "auto",
    }


def prefetch_calendar(
    registry: ProviderRegistry,
    *,
    days: int = 14,
    from_date: str | None = None,
    base_dir: Path = BASE_DIR,
) -> tuple[int, int]:
    """
    预拉取 [from_date, from_date+days-1] 区间的经济日历，写入 tracking/calendar_auto.yaml。
    与已有条目按 (date, event) 合并更新，不删除区间外的历史条目。
    返回 (API 返回条数, 写入后文件内事件总数)。
    """
    if days < 1:
        raise ValueError("days 必须 >= 1")
    if from_date is None:
        from_date = datetime.now().strftime("%Y-%m-%d")
    start = datetime.strptime(from_date, "%Y-%m-%d").date()
    end = start + timedelta(days=days - 1)
    to_date = end.isoformat()

    r = registry.call("get_macro_calendar_range", from_date, to_date)
    fetched: list[dict] = []
    if r.success and r.data:
        fetched = list(r.data)
    else:
        logger.warning(f"get_macro_calendar_range 失败或无数据: {r.error}")

    auto_path = base_dir / "tracking" / "calendar_auto.yaml"
    by_key: dict[tuple[str, str], dict] = {}
    if auto_path.exists():
        try:
            with open(auto_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            for ev in (raw.get("events") or []):
                norm = _normalize_auto_event(ev)
                k = (norm["date"], norm["event"])
                if k[0] and k[1]:
                    by_key[k] = norm
        except Exception as e:
            logger.warning(f"读取已有 calendar_auto.yaml 失败: {e}")

    for ev in fetched:
        norm = _normalize_auto_event(ev)
        k = (norm["date"], norm["event"])
        if k[0] and k[1]:
            by_key[k] = norm

    merged_list = sorted(by_key.values(), key=lambda x: (x.get("date", ""), x.get("time", "")))

    auto_path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        {"events": merged_list},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    with open(auto_path, "w", encoding="utf-8") as f:
        f.write(CALENDAR_AUTO_HEADER)
        f.write(body)

    return len(fetched), len(merged_list)


class MarketCollector:
    """市场数据采集器，聚合多个 provider 的数据"""

    def __init__(self, registry: ProviderRegistry):
        self.registry = registry
        self._rhythm_analyzer = SectorRhythmAnalyzer(BASE_DIR, history_days=20)

    def collect_post_market(self, date: str) -> dict:
        """
        盘后数据采集（15:30 执行）
        返回结构化数据，对应 post-market.yaml 模板
        """
        logger.info(f"开始盘后数据采集: {date}")
        result = {
            "date": date,
            "generated_at": datetime.now().isoformat(),
            "generated_by": "openclaw",
        }

        # 1. 指数数据
        indices = {}
        for name in ["shanghai", "shenzhen", "chinext", "star50"]:
            r = self.registry.call("get_index_daily", name, date)
            if r.success:
                indices[name] = r.data
                indices[name]["_source"] = r.source
            else:
                logger.warning(f"指数 {name} 获取失败: {r.error}")
                indices[name] = {"error": r.error}
        result["indices"] = indices

        # 2. 成交额
        vol = self.registry.call("get_market_volume", date)
        if vol.success:
            result["total_volume"] = vol.data
            result["total_volume"]["_source"] = vol.source
        else:
            result["total_volume"] = {"error": vol.error}

        # 3. 涨停数据
        limit_up = self.registry.call("get_limit_up_list", date)
        if limit_up.success:
            data = limit_up.data
            stocks = data.get("stocks", [])
            # 计算连板梯队
            board_ladder = {}
            first_board_count = 0
            for s in stocks:
                lt = s.get("limit_times", 1)
                board_ladder.setdefault(lt, []).append(s)
                if lt == 1:
                    first_board_count += 1
            data["first_board_count"] = first_board_count
            data["consecutive_board_count"] = len(stocks) - first_board_count
            data["highest_board"] = max((s.get("limit_times", 1) for s in stocks), default=0)
            data["board_ladder"] = {
                k: [s["name"] for s in v] for k, v in sorted(board_ladder.items(), reverse=True)
            }
            data["_source"] = limit_up.source
            result["limit_up"] = data
        else:
            result["limit_up"] = {"error": limit_up.error}

        # 4. 跌停数据
        limit_down = self.registry.call("get_limit_down_list", date)
        if limit_down.success:
            data = limit_down.data
            if isinstance(data, list):
                data = {"count": len(data), "stocks": data}
            data["_source"] = limit_down.source
            result["limit_down"] = data
        else:
            result["limit_down"] = {"error": limit_down.error}

        # 5. 板块排名
        for stype in ["industry", "concept"]:
            r = self.registry.call("get_sector_rankings", date, stype)
            if r.success:
                result[f"sector_{stype}"] = {"data": r.data, "_source": r.source}
            else:
                result[f"sector_{stype}"] = {"error": r.error}

        # 6. 北向资金
        nb = self.registry.call("get_northbound", date)
        if nb.success:
            result["northbound"] = nb.data
            result["northbound"]["_source"] = nb.source
        else:
            result["northbound"] = {"error": nb.error}

        # 7. 龙虎榜
        dt = self.registry.call("get_dragon_tiger", date)
        if dt.success:
            result["dragon_tiger"] = {"data": dt.data, "_source": dt.source}
        else:
            result["dragon_tiger"] = {"error": dt.error}

        # 8. 板块节奏分析
        try:
            extra_names = self._rhythm_analyzer.load_main_theme_names()
            for stype in ["industry", "concept"]:
                rhythm = self._rhythm_analyzer.analyze(
                    today_raw_data=result,
                    sector_type=stype,
                    extra_names=extra_names,
                    today_date=date,
                )
                result[f"sector_rhythm_{stype}"] = rhythm
            logger.info("板块节奏分析完成")
        except Exception as e:
            logger.warning(f"板块节奏分析失败，已跳过: {e}")

        logger.info(f"盘后数据采集完成: {date}")
        return result

    def collect_pre_market(self) -> dict:
        """
        盘前数据采集（07:00 执行）
        采集外盘、大宗商品、汇率、风险指标（VIX/美债）、财经新闻、宏观日历
        """
        logger.info("开始盘前数据采集")
        today = datetime.now().strftime("%Y-%m-%d")
        result = {
            "generated_at": datetime.now().isoformat(),
            "generated_by": "openclaw",
        }

        # 外盘数据
        global_indices = {}
        for name in ["dow_jones", "nasdaq", "sp500", "a50"]:
            r = self.registry.call("get_global_index", name)
            if r.success:
                global_indices[name] = r.data
            else:
                global_indices[name] = {"error": r.error}
        result["global_indices"] = global_indices

        # 大宗商品
        commodities = {}
        for name in ["gold", "crude_oil", "copper"]:
            r = self.registry.call("get_commodity", name)
            if r.success:
                commodities[name] = r.data
            else:
                commodities[name] = {"error": r.error}
        result["commodities"] = commodities

        # 汇率
        forex = {}
        for pair in ["usd_cny", "usd_index"]:
            r = self.registry.call("get_forex", pair)
            if r.success:
                forex[pair] = r.data
            else:
                forex[pair] = {"error": r.error}
        result["forex"] = forex

        # 风险指标：VIX + 美债10年期
        risk_indicators = {}
        for name in ["vix", "us10y"]:
            r = self.registry.call("get_global_index", name)
            if r.success:
                risk_indicators[name] = r.data
            else:
                logger.warning(f"风险指标 {name} 获取失败: {r.error}")
                risk_indicators[name] = {"error": r.error}
        result["risk_indicators"] = risk_indicators

        # 财经新闻
        news_r = self.registry.call("get_market_news", today)
        result["news"] = news_r.data if news_r.success and news_r.data else []
        if not news_r.success:
            logger.warning(f"财经新闻获取失败: {news_r.error}")

        # 宏观日历（手动 YAML + 预拉取 calendar_auto.yaml + AkShare 当天实时）
        cal_r = self.registry.call("get_macro_calendar", today)
        ak_events = cal_r.data if (cal_r.success and cal_r.data) else []
        result["calendar_events"] = _merge_calendar(ak_events, today, BASE_DIR)

        logger.info("盘前数据采集完成")
        return result

    def collect_stock_data(self, stock_codes: list[str], date: str) -> list[dict]:
        """采集指定个股的日线数据"""
        results = []
        for code in stock_codes:
            r = self.registry.call("get_stock_daily", code, date)
            if r.success:
                results.append(r.data)
            else:
                results.append({"code": code, "error": r.error})
        return results
