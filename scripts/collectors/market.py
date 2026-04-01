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


def _calendar_importance_rank(ev: dict) -> int:
    imp = str(ev.get("importance", ev.get("impact", ""))).strip().lower()
    if imp in ("高", "3", "high"):
        return 0
    if imp in ("中", "2", "medium", "med"):
        return 1
    return 2


def filter_calendar_for_pre_market(merged: list[dict], max_items: int = 15) -> list[dict]:
    """仅保留高/中重要性，至多 max_items；不用低重要性凑满额度。"""
    rated = [e for e in merged if _calendar_importance_rank(e) <= 1]
    rated.sort(key=_calendar_importance_rank)
    return rated[:max_items]


def _attach_margin_day_over_day(curr: dict, prev: dict | None) -> None:
    """在 curr 上就地附加相对上一交易日的融资余额变动；prev 无效时静默跳过。"""
    if not prev or prev.get("error") or curr.get("error"):
        return
    try:
        pdate = prev.get("trade_date")
        if not pdate or not curr.get("trade_date"):
            return
        curr["margin_compare_date"] = pdate
        for key in ("total_rzye_yi", "total_rqye_yi", "total_rzrqye_yi"):
            if key in curr and key in prev:
                a, b = float(curr[key]), float(prev[key])
                delta_key = "delta_" + key
                curr[delta_key] = round(a - b, 2)
        prev_by_ex = {
            str(e.get("exchange_id", "")): e
            for e in (prev.get("exchanges") or [])
            if e.get("exchange_id") is not None
        }
        for ex in curr.get("exchanges") or []:
            eid = str(ex.get("exchange_id", ""))
            p = prev_by_ex.get(eid)
            if not p:
                continue
            for fld in ("rzye_yi", "rqye_yi", "rzrqye_yi"):
                if fld in ex and fld in p:
                    ex["delta_" + fld] = round(float(ex[fld]) - float(p[fld]), 2)
    except (TypeError, ValueError, KeyError):
        logger.debug("融资融券日环比计算跳过（数据不完整）")


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
        index_list = [
            ("shanghai", "shanghai"),
            ("shenzhen", "shenzhen"),
            ("chinext", "chinext"),
            ("star50", "star50"),
            ("csi300", "000300.SH"),
            ("csi1000", "000852.SH"),
        ]
        for key, code in index_list:
            r = self.registry.call("get_index_daily", code, date)
            if r.success:
                indices[key] = r.data
                indices[key]["_source"] = r.source
            else:
                logger.warning(f"指数 {key} 获取失败: {r.error}")
                indices[key] = {"error": r.error}
        result["indices"] = indices

        # 2. 成交额 + 历史对比
        vol = self.registry.call("get_market_volume", date)
        if vol.success:
            vol_data = vol.data
            vol_data["_source"] = vol.source
            self._enrich_volume_comparison(vol_data, date)
            result["total_volume"] = vol_data
        else:
            result["total_volume"] = {"error": vol.error}

        # 3. 涨停数据 + 封板率/炸板率 + 按涨幅分类
        limit_up = self.registry.call("get_limit_up_list", date)
        if limit_up.success:
            data = limit_up.data
            stocks = data.get("stocks", [])
            board_ladder = {}
            first_board_count = 0
            first_board_10cm = 0
            first_board_20cm = 0
            first_board_30cm = 0
            for s in stocks:
                lt = s.get("limit_times", 1)
                board_ladder.setdefault(lt, []).append(s)
                if lt == 1:
                    first_board_count += 1
                    pct = abs(s.get("pct_chg", s.get("change_pct", 0)))
                    if pct > 25:
                        first_board_30cm += 1
                    elif pct > 15:
                        first_board_20cm += 1
                    else:
                        first_board_10cm += 1
            data["first_board_count"] = first_board_count
            data["first_board_10cm"] = first_board_10cm
            data["first_board_20cm"] = first_board_20cm
            data["first_board_30cm"] = first_board_30cm
            data["consecutive_board_count"] = len(stocks) - first_board_count
            data["highest_board"] = max((s.get("limit_times", 1) for s in stocks), default=0)
            data["board_ladder"] = {
                k: [s["name"] for s in v] for k, v in sorted(board_ladder.items(), reverse=True)
            }

            # 封板率/炸板率：涨停数 vs（涨停数 + 炸板数）
            broken_count = self._get_broken_board_count(date)
            total_touched = len(stocks) + broken_count
            data["broken_count"] = broken_count
            if total_touched > 0:
                data["seal_rate_pct"] = round(len(stocks) / total_touched * 100, 1)
                data["broken_rate_pct"] = round(broken_count / total_touched * 100, 1)
            else:
                data["seal_rate_pct"] = 0
                data["broken_rate_pct"] = 0

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

        # 5. 板块排名（涨幅前30 + 跌幅前5）
        for stype in ["industry", "concept"]:
            r = self.registry.call("get_sector_rankings", date, stype)
            if r.success:
                raw = r.data
                if isinstance(raw, dict) and "top" in raw:
                    result[f"sector_{stype}"] = {
                        "data": raw["top"],
                        "bottom": raw.get("bottom", []),
                        "_source": r.source,
                    }
                else:
                    result[f"sector_{stype}"] = {"data": raw, "_source": r.source}
            else:
                result[f"sector_{stype}"] = {"error": r.error}

        # 6. 板块资金净流入
        fund_flow = self.registry.call("get_sector_fund_flow", date)
        if fund_flow.success and fund_flow.data:
            result["sector_fund_flow"] = {"data": fund_flow.data, "_source": fund_flow.source}
        else:
            logger.warning(f"板块资金流向获取失败: {fund_flow.error}")

        # 7. 北向资金
        nb = self.registry.call("get_northbound", date)
        if nb.success:
            result["northbound"] = nb.data
            result["northbound"]["_source"] = nb.source
        else:
            result["northbound"] = {"error": nb.error}

        # 7b. 北向十大活跃股
        nb_top = self.registry.call("get_northbound_top_stocks", date)
        if nb_top.success and nb_top.data:
            nb_block = result.get("northbound", {})
            if "error" not in nb_block:
                nb_block["top_active_stocks"] = nb_top.data.get("top_active", [])

        # 8. 龙虎榜
        dt = self.registry.call("get_dragon_tiger", date)
        if dt.success:
            result["dragon_tiger"] = {"data": dt.data, "_source": dt.source}
        else:
            result["dragon_tiger"] = {"error": dt.error}

        # 9. 市场宽度（涨跌家数）
        breadth = self.registry.call("get_market_breadth", date)
        if breadth.success and breadth.data:
            result["breadth"] = breadth.data
            result["breadth"]["_source"] = breadth.source
        else:
            logger.warning(f"市场宽度获取失败: {breadth.error}")

        # 10. 融资融券
        margin = self.registry.call("get_margin_data", date)
        if margin.success and margin.data:
            result["margin_data"] = margin.data
            result["margin_data"]["_source"] = margin.source
        else:
            logger.debug(f"融资融券数据获取失败（T+1 延迟正常）: {margin.error}")

        # 11. 指数均线
        self._compute_index_ma(result, date)

        # 12. 板块节奏分析
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

        # 13. 风格化因子分析
        try:
            from analyzers import StyleAnalyzer
            style_analyzer = StyleAnalyzer()
            result["style_factors"] = style_analyzer.analyze(result, date)
            logger.info("风格化因子分析完成")
        except Exception as e:
            logger.warning(f"风格化因子分析失败: {e}")

        logger.info(f"盘后数据采集完成: {date}")
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _get_broken_board_count(self, date: str) -> int:
        """获取炸板数量（曾触及涨停但收盘未封住的股票数）"""
        try:
            import akshare as ak
            df = ak.stock_zt_pool_zbgc_em(date=date.replace("-", ""))
            return len(df) if df is not None and not df.empty else 0
        except Exception as e:
            logger.debug(f"炸板数据获取失败: {e}")
            return 0

    def _enrich_volume_comparison(self, vol_data: dict, date: str) -> None:
        """从历史 post-market.yaml 读取近20日成交额，计算对比指标"""
        daily_dir = BASE_DIR / "daily"
        history_volumes: list[float] = []

        try:
            dirs = sorted(
                [d for d in daily_dir.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            return

        for d in dirs[:20]:
            pm_file = d / "post-market.yaml"
            if not pm_file.exists():
                continue
            try:
                with open(pm_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw = data.get("raw_data", data)
                tv = raw.get("total_volume", {})
                tb = tv.get("total_billion", 0)
                if tb and tb > 0:
                    history_volumes.append(float(tb))
            except Exception:
                continue

        today_vol = vol_data.get("total_billion", 0)
        if not today_vol or not history_volumes:
            return

        if history_volumes:
            yesterday_vol = history_volumes[0]
            if yesterday_vol > 0:
                vol_data["vs_yesterday_pct"] = round(
                    (today_vol - yesterday_vol) / yesterday_vol * 100, 1
                )

        if len(history_volumes) >= 5:
            ma5 = sum(history_volumes[:5]) / 5
            vol_data["ma5_billion"] = round(ma5, 2)
            vol_data["vs_ma5"] = "高于" if today_vol > ma5 else ("低于" if today_vol < ma5 else "持平")

        if len(history_volumes) >= 20:
            ma20 = sum(history_volumes[:20]) / 20
            vol_data["ma20_billion"] = round(ma20, 2)
            vol_data["vs_ma20"] = "高于" if today_vol > ma20 else ("低于" if today_vol < ma20 else "持平")

    def _compute_index_ma(self, result: dict, date: str) -> None:
        """计算上证指数均线（MA5/10/20/60）和5周均线"""
        daily_dir = BASE_DIR / "daily"
        closes: list[float] = []

        # 今日收盘价
        sh_data = result.get("indices", {}).get("shanghai", {})
        today_close = sh_data.get("close", 0)
        if not today_close:
            return
        today_close = float(today_close)
        closes.append(today_close)

        try:
            dirs = sorted(
                [d for d in daily_dir.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            return

        for d in dirs[:70]:
            pm_file = d / "post-market.yaml"
            if not pm_file.exists():
                continue
            try:
                with open(pm_file, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw = data.get("raw_data", data)
                sh = raw.get("indices", {}).get("shanghai", {})
                c = sh.get("close", 0)
                if c and c > 0:
                    closes.append(float(c))
            except Exception:
                continue

        if len(closes) < 5:
            return

        ma_data = {}
        for period in [5, 10, 20, 60]:
            if len(closes) >= period:
                ma_val = round(sum(closes[:period]) / period, 2)
                ma_data[f"ma{period}"] = ma_val
                ma_data[f"above_ma{period}"] = today_close > ma_val

        # 5周均线近似：取最近25个交易日，每5天取一个周收盘
        if len(closes) >= 25:
            weekly_closes = [closes[i] for i in [4, 9, 14, 19, 24] if i < len(closes)]
            if len(weekly_closes) == 5:
                ma5w = round(sum(weekly_closes) / 5, 2)
                ma_data["ma5w"] = ma5w
                ma_data["above_ma5w"] = today_close > ma5w

        if ma_data:
            result["moving_averages"] = {"shanghai": ma_data}

    def collect_pre_market(
        self,
        target_date: str | None = None,
        prev_trade_date: str | None = None,
        prev_prev_trade_date: str | None = None,
    ) -> dict:
        """
        盘前数据采集（07:00 执行）
        采集外盘、亚太股指、美股中国金龙、大宗商品、汇率（含离岸人民币）、风险指标（VIX/美债）、
        宏观日历（高/中为主）、上一交易日融资融券汇总（可选相对上上日环比）。

        :param target_date: 简报日期（与 main.py --date 一致）；默认当天
        :param prev_trade_date: 上一交易日，用于融资融券；由 cmd_pre 传入 get_prev_trade_date
        :param prev_prev_trade_date: 上上一交易日，用于融资余额日环比；无则仅展示绝对值
        """
        logger.info("开始盘前数据采集")
        date_str = target_date or datetime.now().strftime("%Y-%m-%d")
        result = {
            "generated_at": datetime.now().isoformat(),
            "generated_by": "openclaw",
        }

        # 外盘数据（美股 + A50）
        global_indices = {}
        for name in ["dow_jones", "nasdaq", "sp500", "a50"]:
            r = self.registry.call("get_global_index", name)
            if r.success:
                global_indices[name] = r.data
            else:
                global_indices[name] = {"error": r.error}
        result["global_indices"] = global_indices

        # 亚太股指（日经 + 韩国综指）
        global_indices_apac = {}
        for name in ["nikkei", "kospi"]:
            r = self.registry.call("get_global_index", name)
            if r.success:
                global_indices_apac[name] = r.data
            else:
                global_indices_apac[name] = {"error": r.error}
        result["global_indices_apac"] = global_indices_apac

        # 美股侧中国资产叙事：纳斯达克中国金龙 ETF（与 A50 期货并列由报告展示）
        us_china = {}
        tickers_r = self.registry.call("get_us_tickers_overnight", ["HXC"])
        if tickers_r.success and tickers_r.data:
            us_china = tickers_r.data
        else:
            err = tickers_r.error if not tickers_r.success else "无数据"
            us_china = {"_error": err}
        result["us_china_assets"] = us_china

        # 大宗商品
        commodities = {}
        for name in ["gold", "crude_oil", "copper"]:
            r = self.registry.call("get_commodity", name)
            if r.success:
                commodities[name] = r.data
            else:
                commodities[name] = {"error": r.error}
        result["commodities"] = commodities

        # 汇率（在岸中间价 + 离岸人民币 + 美元指数）
        forex = {}
        for pair in ["usd_cny", "usd_cnh", "usd_index"]:
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

        # 全市场快讯已取消；YAML 仍保留 news: [] 以兼容旧模板
        result["news"] = []

        # 融资融券（上一完整交易日汇总 + 可选日环比）
        if prev_trade_date:
            margin_r = self.registry.call("get_margin_data", prev_trade_date)
            if margin_r.success and margin_r.data:
                result["margin_data"] = dict(margin_r.data)
                if prev_prev_trade_date:
                    prev_m = self.registry.call("get_margin_data", prev_prev_trade_date)
                    if prev_m.success and prev_m.data:
                        _attach_margin_day_over_day(result["margin_data"], prev_m.data)
                    else:
                        logger.info(
                            "融资融券上一日数据不可用，跳过日环比: %s",
                            prev_m.error if not prev_m.success else "无数据",
                        )
            else:
                err = margin_r.error if not margin_r.success else "无数据"
                result["margin_data"] = {"error": err}
                logger.warning(f"融资融券汇总获取失败: {err}")
        else:
            result["margin_data"] = {}

        # 宏观日历（手动 YAML + 预拉取 calendar_auto.yaml + AkShare 当天实时）
        cal_r = self.registry.call("get_macro_calendar", date_str)
        ak_events = cal_r.data if (cal_r.success and cal_r.data) else []
        merged_cal = _merge_calendar(ak_events, date_str, BASE_DIR)
        result["calendar_events"] = filter_calendar_for_pre_market(merged_cal)

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
