"""
市场数据采集器
将 provider 层的原始数据聚合为结构化的报告数据
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
import yaml

from analyzers.sector_rhythm import SectorRhythmAnalyzer
from providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent.parent.parent

# 网络 / 外部源不可达类失败的特征片段：本机到不了 Yahoo（yfinance）、akshare 远端瞬断、
# tushare 不支持的国际指数等。命中则归一化为可读提示，避免把隐晦栈信息抛给用户。
_UNREACHABLE_SIGNATURES = (
    "RemoteDisconnected",
    "Connection aborted",
    "NoneType' object is not subscriptable",
    "yfinance",
    "未支持的国际指数键",
    "均未获取到亚太指数",
    "未找到 VIX",
    "AkShare/yfinance",
)


def _normalize_source_error(err: str | None) -> str | None:
    """将网络/外部源不可达类的隐晦错误统一为可读提示，保留原因作后缀；其它错误原样返回。"""
    if not err:
        return err
    if any(sig in err for sig in _UNREACHABLE_SIGNATURES):
        return f"数据源暂不可达（外部源/网络限制）：{err}"
    return err


def _augment_weekly_closes(
    weekly_rows: list[dict], date_str: str, day_close: float, n: int = 5
) -> list[float]:
    """已完成周收盘序列 + 当周走动收盘 → 最近 n 根周收盘，增补后不足 n 返回 []。

    根因修复：Tushare index_weekly 是「按已完成周入库」的表，进行中的当周没有行，
    直接取 weekly_rows[-n:] 会漏掉当周 → ma5w 周内冻结、周五才跳。这里把「当周从周一到
    今天的最新日收盘」补成当周 bar，对齐通达信/同花顺周线 MA5（每日更新）口径。源无关：
    AkShare 周线含当周 partial bar（或周五 Tushare 已收官当周）时走「覆盖」分支，避免重复。

    用 ISO 周（isocalendar()[:2]）判定当周，天然处理跨自然年的 ISO-W01 边界。
    """
    cur_iso = datetime.strptime(date_str, "%Y-%m-%d").isocalendar()[:2]
    # 逐行容错：close 必须可解析为「有限数值」才纳入（None / 脏字符串 / NaN 整根丢弃）。
    # last_iso 只跟随「有效 close 那一行」的 trade_date —— 不能用 rows[-1]：若最新一根
    # 的 close 脏被丢，却仍拿它的日期判当周，会让覆盖分支误改上一根有效完成周收盘。
    closes: list[float] = []
    last_iso = None
    for r in sorted(weekly_rows, key=lambda x: x.get("trade_date", "")):
        c = r.get("close")
        if c is None:
            continue
        try:
            cf = float(c)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cf):  # NaN / inf / -inf（都能过 float() 但非有限值，会污染 ma5w）
            continue
        closes.append(cf)
        td = str(r.get("trade_date", "")).replace("-", "")[:8]  # 兼容 YYYYMMDD / YYYY-MM-DD
        try:
            last_iso = datetime.strptime(td, "%Y%m%d").isocalendar()[:2]
        except ValueError:
            last_iso = None  # 有效行但日期不可解析 → 不据此判当周（退化为追加）
    if last_iso == cur_iso and closes:
        closes[-1] = float(day_close)   # 最新有效 bar 即当周（AkShare partial / 周五）→ 覆盖
    else:
        # 含 closes 为空（全脏/全 None）的边界：追加后仍不足 n，下方按 [] 返回，上层告警跳过
        closes.append(float(day_close)) # 最新有效 bar 是上一完成周（Tushare 周一~周四）→ 追加
    return closes[-n:] if len(closes) >= n else []


def apply_margin_db_fallback(conn, market_data: dict, date: str) -> dict:
    """盘前实时融资融券汇总取不到时，回退 DB 最近一次入库的汇总，并标注 as_of / stale。

    tushare 当日融资融券汇总常在盘前 07:00 尚未发布；回退「最近一次入库」的汇总（已是聚合形态）
    比裸 error 更可用。原地修改并返回 market_data；DB 也无数据时保留原 error。
    """
    md = market_data.get("margin_data")
    if not (isinstance(md, dict) and md.get("error")):
        return market_data
    from db import queries as Q

    # inclusive=False：严格取早于 date 的最近一次（融资融券盘前章节语义是「上一交易日」）；
    # 用 <= 会在历史重跑 / 当日已入库时把 D 日数据回填进 D 日盘前，造成口径错配。
    rows = Q.get_latest_raw_interface_rows(
        conn, interface_name="margin", biz_date=date, inclusive=False,
    )
    if rows and isinstance(rows[0], dict):
        summary = dict(rows[0])
        # 最小结构校验：缺 trade_date 或全部汇总字段缺失的畸形行不回填，保留原 error 以利排障。
        _totals = ("total_rzrqye_yi", "total_rzye_yi", "total_rqye_yi")
        if summary.get("trade_date") and any(summary.get(k) is not None for k in _totals):
            summary["as_of"] = summary.get("trade_date")
            summary["stale"] = True
            market_data["margin_data"] = summary
    return market_data


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


from utils import is_st_stock as _is_st_stock


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

            # 排除 ST 后的统计
            stocks_ex_st = [s for s in stocks if not _is_st_stock(s.get("name", ""))]
            board_ladder_ex_st: dict[int, list] = {}
            first_board_count_ex_st = 0
            for s in stocks_ex_st:
                lt = s.get("limit_times", 1)
                board_ladder_ex_st.setdefault(lt, []).append(s)
                if lt == 1:
                    first_board_count_ex_st += 1

            data["count_ex_st"] = len(stocks_ex_st)
            data["consecutive_board_count_ex_st"] = len(stocks_ex_st) - first_board_count_ex_st
            data["highest_board_ex_st"] = max((s.get("limit_times", 1) for s in stocks_ex_st), default=0)
            data["board_ladder_ex_st"] = {
                k: [s.get("name", "") for s in v] for k, v in sorted(board_ladder_ex_st.items(), reverse=True)
            }

            total_touched_ex_st = len(stocks_ex_st) + broken_count
            if total_touched_ex_st > 0:
                data["seal_rate_pct_ex_st"] = round(len(stocks_ex_st) / total_touched_ex_st * 100, 1)
                data["broken_rate_pct_ex_st"] = round(broken_count / total_touched_ex_st * 100, 1)
            else:
                data["seal_rate_pct_ex_st"] = 0
                data["broken_rate_pct_ex_st"] = 0

            # 涨停行业分布：按东财所属行业聚合（count 降序、同 count 按最高连板降序），过滤空/非字符串行业。
            # 含 ST 与 _ex_st 两套口径均入库，供报告/下游按各自指标选用：
            # 报告「连板」类指标（连板数/最高连板/连板梯队/连板天梯）用 ex-ST，
            # 涨停家数/首板细分/封板率/涨停行业分布仍用含 ST。
            data["industry_ranking"] = self._aggregate_limit_up_industry(stocks)
            data["industry_ranking_ex_st"] = self._aggregate_limit_up_industry(stocks_ex_st)

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
            if isinstance(data.get("stocks"), list):
                stocks_ex_st_down = [s for s in data["stocks"] if not _is_st_stock(s.get("name", ""))]
                data["count_ex_st"] = len(stocks_ex_st_down)
            data["_source"] = limit_down.source
            result["limit_down"] = data
        else:
            result["limit_down"] = {"error": limit_down.error}

        # 4b. 跌停结构：连续跌停天数（多日 yaml join；天地板因缺分时数据不做）
        self._enrich_limit_down_structure(result, date)

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

        # 6b. 交易所市场统计 / 连板天梯 / 最强板块
        for method_name, result_key in [
            ("get_daily_info", "daily_info"),
            ("get_limit_step", "limit_step"),
            ("get_limit_cpt_list", "limit_cpt_list"),
        ]:
            r = self.registry.call(method_name, date)
            if r.success and r.data:
                result[result_key] = {"data": r.data, "_source": r.source}
            else:
                logger.debug("%s 获取失败: %s", method_name, r.error)

        # 6c. 板块/大盘资金流向增强
        for method_name, result_key in [
            ("get_sector_moneyflow_ths", "sector_moneyflow_ths"),
            ("get_concept_moneyflow_ths", "concept_moneyflow_ths"),
            ("get_sector_moneyflow_dc", "sector_moneyflow_dc"),
            ("get_concept_moneyflow_dc", "concept_moneyflow_dc"),
            ("get_market_moneyflow_dc", "market_moneyflow_dc"),
        ]:
            r = self.registry.call(method_name, date)
            if r.success and r.data:
                result[result_key] = {"data": r.data, "_source": r.source}
            else:
                logger.debug("%s 获取失败: %s", method_name, r.error)

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

        # 10b. 融资融券明细 / ST 名单
        for method_name, result_key in [
            ("get_margin_detail", "margin_detail"),
            ("get_stock_st", "stock_st"),
        ]:
            r = self.registry.call(method_name, date)
            if r.success and r.data:
                if result_key == "margin_detail" and isinstance(r.data, list):
                    rows = r.data
                    top_rows = sorted(
                        rows,
                        key=lambda item: float(item.get("rzmre", 0) or 0),
                        reverse=True,
                    )[:20]
                    result[result_key] = {
                        "count": len(rows),
                        "top_rzmre": top_rows,
                        "_source": r.source,
                    }
                elif result_key == "stock_st" and isinstance(r.data, list):
                    result[result_key] = {
                        "count": len(r.data),
                        "stocks": r.data[:100],
                        "_source": r.source,
                    }
                else:
                    result[result_key] = {"data": r.data, "_source": r.source}
            else:
                logger.debug("%s 获取失败: %s", method_name, r.error)

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

        # 14. 客观节点信号
        try:
            from analyzers import NodeSignalAnalyzer
            node_analyzer = NodeSignalAnalyzer(BASE_DIR)
            result["node_signals"] = node_analyzer.analyze(result, date)
            logger.info(f"节点信号分析完成，共 {len(result['node_signals'])} 条")
        except Exception as e:
            logger.warning(f"节点信号分析失败: {e}")

        # 15. 成交额前20个股
        try:
            r = self.registry.call("get_top_volume_stocks", date, 20)
            if r.success and r.data:
                result["top_volume_stocks"] = r.data
                logger.info(f"成交额排名采集完成，共 {len(r.data)} 只")
            else:
                logger.warning(f"成交额排名获取失败: {r.error}")
        except Exception as e:
            logger.warning(f"成交额排名采集异常: {e}")

        # 16. ETF 净申购流量
        try:
            r = self.registry.call("get_etf_flow", date)
            if r.success and r.data:
                result["etf_flow"] = r.data
                logger.info(f"ETF 净申购采集完成，共 {len(r.data)} 只")
            else:
                logger.warning(f"ETF 净申购获取失败: {r.error}")
        except Exception as e:
            logger.warning(f"ETF 净申购采集异常: {e}")

        # 17. 港股指数（HSI + HSTECH）
        try:
            r = self.registry.call("get_hk_indices", date)
            if r.success and r.data:
                result["hk_indices"] = r.data
                logger.info("港股指数采集完成")
            else:
                logger.warning(f"港股指数获取失败: {r.error}")
        except Exception as e:
            logger.warning(f"港股指数采集异常: {e}")

        # 18b. 大宗交易
        try:
            r = self.registry.call("get_block_trade", date)
            if r.success and r.data:
                result["block_trade"] = {"data": r.data, "_source": r.source}
                logger.info(f"大宗交易采集完成，共 {len(r.data)} 笔")
            else:
                logger.warning(f"大宗交易获取失败: {r.error}")
        except Exception as e:
            logger.warning(f"大宗交易采集异常: {e}")

        # 18. 研报覆盖统计（复用 research_digest.build_coverage_panel：篇数排行 + 高信号标的补方向徽章/观点）
        try:
            top_covered = self._collect_research_coverage(date)
            if top_covered:
                result["research_coverage_top"] = top_covered
                expanded_n = sum(1 for r in top_covered if r.get("expanded"))
                logger.info(f"研报覆盖统计完成，{len(top_covered)} 只（{expanded_n} 只展开补观点）")
        except Exception as e:
            logger.warning(f"研报覆盖统计失败: {e}")

        logger.info(f"盘后数据采集完成: {date}")
        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _collect_research_coverage(self, date: str) -> list[dict]:
        """研报覆盖排行（复盘网站「当日」面板）：取巨潮当日评级清单，交 build_coverage_panel
        产出篇数排行 + 高信号 Top 标的的评级方向徽章/观点（与研报速读同源，不重复网络）。
        采集失败 / 空 → 返 []（不致命，由调用方决定是否落库）。"""
        report_result = self.registry.call("get_research_report_list", date)
        if not (report_result.success and report_result.data):
            return []
        from services.research_digest.collector import build_coverage_panel
        return build_coverage_panel(report_result.data, self.registry, date)

    @staticmethod
    def _aggregate_limit_up_industry(stocks: list[dict]) -> list[dict]:
        """按 industry(东财所属行业) 聚合涨停股，产出行业分布排名。

        - 过滤空 / 非字符串 industry（provider 缺列时降级为空串，tushare 降级源亦可能为空）。
        - 排序：涨停家数降序，同家数按最高连板降序，便于报告突出强势行业。
        """
        agg: dict[str, dict] = {}
        for s in stocks:
            ind = s.get("industry")
            if not isinstance(ind, str):
                continue
            ind = ind.strip()
            if not ind:
                continue
            bucket = agg.setdefault(
                ind, {"industry": ind, "count": 0, "max_board": 0, "names": []}
            )
            bucket["count"] += 1
            try:
                lt = int(s.get("limit_times", 1) or 1)
            except (TypeError, ValueError):
                lt = 1
            bucket["max_board"] = max(bucket["max_board"], lt)
            bucket["names"].append(s.get("name", ""))
        # 第三键 industry 名做确定性 tie-break：同家数同最高板时顺序稳定，避免报告抖动
        return sorted(agg.values(), key=lambda x: (-x["count"], -x["max_board"], x["industry"]))

    def _get_broken_board_count(self, date: str) -> int:
        """获取炸板数量（曾触及涨停但收盘未封住的股票数）"""
        try:
            import akshare as ak
            df = ak.stock_zt_pool_zbgc_em(date=date.replace("-", ""))
            return len(df) if df is not None and not df.empty else 0
        except Exception as e:
            logger.debug(f"炸板数据获取失败: {e}")
            return 0

    def _enrich_limit_down_structure(self, result: dict, date: str) -> None:
        """计算今日跌停股的连续跌停天数，并产出 down_ladder（>=2 连跌梯队）。

        数据来源：今日 result['limit_down'].stocks + 历史 post-market.yaml 的
        limit_down.stocks（按 code 在相邻交易日是否同为跌停判定连续性）。
        个股在某日跌停列表中即视为当日跌停（兼容 ST/20cm 等不同跌幅限制）。
        天地板需分时/最高最低价，yaml 无该数据，故不做。
        """
        ld = result.get("limit_down")
        if not isinstance(ld, dict):
            return
        today_stocks = ld.get("stocks")
        if not today_stocks:
            return

        daily_dir = BASE_DIR / "daily"
        try:
            dirs = sorted(
                [d for d in daily_dir.iterdir()
                 if d.is_dir() and d.name != "example" and d.name < date],
                key=lambda d: d.name,
                reverse=True,
            )
        except FileNotFoundError:
            dirs = []

        # 倒序加载相邻交易日的跌停 code 集合（最多回看 10 个交易日）。
        # 关键：目录存在但 yaml 缺失 / 不可读时一律 append 空集（而非跳过），让连续性在该日断裂——
        # 数据缺口下宁可少算连跌天数（保守），也不能跨缺口把 T-2 误当 T-1 相邻累计。
        # 已知限制：若某交易日「整个目录」不存在（如当日 cmd_post 从未运行），该日不会出现在
        # dirs 中，本方法无交易日历可据以区分「周末正常跳空」与「缺失交易日」，故此种极端
        # 情况下连续性仍可能被跨越（streak 偏高）。属首版已知边界（天地板亦未做），
        # 待引入交易日历窗口后再收敛。
        prev_down_sets: list[set] = []
        for d in dirs[:10]:
            pm_file = d / "post-market.yaml"
            codes: set = set()
            if pm_file.exists():
                try:
                    with open(pm_file, encoding="utf-8") as f:
                        data = yaml.safe_load(f) or {}
                    raw = data.get("raw_data", data)
                    prev_ld = raw.get("limit_down", {})
                    prev_stocks = prev_ld.get("stocks") if isinstance(prev_ld, dict) else None
                    codes = {s.get("code") for s in (prev_stocks or []) if s.get("code")}
                except Exception:
                    codes = set()
            prev_down_sets.append(codes)

        down_ladder: dict[int, list[str]] = {}
        max_streak = 1
        for stock in today_stocks:
            code = stock.get("code")
            streak = 1  # 今日跌停计 1 日
            if code:
                for codes in prev_down_sets:
                    if code in codes:
                        streak += 1
                    else:
                        break
            stock["consecutive_down"] = streak
            max_streak = max(max_streak, streak)
            if streak >= 2:
                down_ladder.setdefault(streak, []).append(stock.get("name", ""))

        ld["consecutive_down_max"] = max_streak
        ld["down_ladder"] = {
            str(k): v for k, v in sorted(down_ladder.items(), reverse=True)
        }

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
        """计算上证指数日线均线（MA5/10/20/60）和四大指数（沪/深/创业板/科创50）5 周均线"""
        daily_dir = BASE_DIR / "daily"

        sh_data = result.get("indices", {}).get("shanghai", {})
        sh_close_raw = sh_data.get("close")
        today_close = float(sh_close_raw) if sh_close_raw else None

        ma_data: dict = {}
        # 日线 MA5/10/20/60：仅在上证 close 可用时计算。
        # 上证缺失不再整体 return，避免连带深证/创业板的 5 周线一起丢（2026-05-25 实测缺口）。
        if today_close is not None:
            closes: list[float] = [today_close]
            try:
                dirs = sorted(
                    [d for d in daily_dir.iterdir()
                     if d.is_dir() and d.name != "example" and d.name < date],
                    key=lambda d: d.name,
                    reverse=True,
                )
            except FileNotFoundError:
                dirs = []

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

            if len(closes) >= 5:
                for period in [5, 10, 20, 60]:
                    if len(closes) >= period:
                        ma_val = round(sum(closes[:period]) / period, 2)
                        ma_data[f"ma{period}"] = ma_val
                        ma_data[f"above_ma{period}"] = today_close > ma_val

        # 5 周均线：调用 get_index_weekly 获取真实周线，并用 _augment_weekly_closes
        # 把当周走动收盘补进序列（修「周内冻结」根因，详见该函数 docstring）。
        start_date = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=60)).strftime("%Y-%m-%d")
        ma5w_indices = [
            ("shanghai", today_close, ma_data),
            ("shenzhen", None, {}),
            ("chinext", None, {}),
            ("star50", None, {}),
        ]
        for idx_key, day_close, target_dict in ma5w_indices:
            if day_close is None:
                idx_data = result.get("indices", {}).get(idx_key, {})
                day_close = idx_data.get("close")
                if not day_close:
                    continue
                day_close = float(day_close)
            try:
                r = self.registry.call("get_index_weekly", idx_key, start_date, date)
                if r.success and r.data:
                    weekly_closes = _augment_weekly_closes(r.data, date, day_close)
                    if len(weekly_closes) == 5:
                        ma5w = round(sum(weekly_closes) / 5, 2)
                        target_dict["ma5w"] = ma5w
                        target_dict["above_ma5w"] = day_close > ma5w
                    else:
                        logger.warning(
                            "5周均线: %s 增补当周后仍不足 5 根周线，跳过 (rows=%d)",
                            idx_key, len(r.data),
                        )
            except Exception as e:
                logger.debug("5周均线计算失败 %s: %s", idx_key, e)

        all_ma: dict = {}
        if ma_data:
            all_ma["shanghai"] = ma_data
        for idx_key, _day_close, target_dict in ma5w_indices:
            if idx_key != "shanghai" and target_dict:
                all_ma[idx_key] = target_dict

        # 平均股价（通达信 880003）：非市值指数，无 indices 日收盘，唯一可达源是 pytdx
        # 直连通达信（TdxProvider）。必须 call_specific 直连 —— registry.call 会被 tushare 对
        # 未知 code 返回「空列表 + 无 error」的 success 遮蔽（DataResult.success=error==""）。
        # day_close 取周线末根（pytdx 周线最新一根即当周 partial，收盘=今日均价）。
        try:
            r = self.registry.call_specific("tdx", "get_index_weekly", "avg_price", start_date, date)
            # r.data 为空列表时 `and r.data` 即 falsy 跳过（无需再 len()>0，非空列表 sort 后必非空，
            # rows[-1] 不会越界）；tdx 未注册时 call_specific 返回 error → r.success False。
            if r.success and r.data:
                rows = sorted(r.data, key=lambda x: x.get("trade_date", ""))
                avg_close = rows[-1].get("close")
                # 锚点（当周 partial 收盘）必须是有限数值：avg_close 取自 pytdx 周线末根，
                # 未在源头挡 NaN（不同于 akshare get_index_daily）；NaN 锚点会让 ma5w 算成 nan
                # 写库污染，故 None / 非有限值一律跳过 avg_price。
                if avg_close is not None and math.isfinite(float(avg_close)):
                    avg_close = float(avg_close)
                    weekly_closes = _augment_weekly_closes(r.data, date, avg_close)
                    if len(weekly_closes) == 5:
                        ma5w = round(sum(weekly_closes) / 5, 2)
                        all_ma["avg_price"] = {
                            "ma5w": ma5w,
                            "above_ma5w": avg_close > ma5w,
                        }
                    else:
                        logger.warning("5周均线: avg_price 增补当周后仍不足 5 根周线，跳过 (rows=%d)", len(r.data))
        except Exception as e:
            logger.debug("5周均线计算失败 avg_price: %s", e)

        if all_ma:
            result["moving_averages"] = all_ma

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

        # 宏观经济指标（PMI/CPI/M2/LPR/社融，最新一期 + 近 N 期同比走势）
        macro_r = self.registry.call("get_macro_indicators")
        if macro_r.success and macro_r.data:
            result["macro_indicators"] = macro_r.data
        else:
            err = macro_r.error if not macro_r.success else "无数据"
            result["macro_indicators"] = {"error": err}
            logger.warning("宏观经济指标获取失败: %s", err)

        # 网络/外部源不可达类失败统一文案（yfinance→Yahoo、akshare 瞬断、tushare 不支持国际指数）。
        for section in ("global_indices", "global_indices_apac", "commodities", "forex", "risk_indicators"):
            for item in (result.get(section) or {}).values():
                if isinstance(item, dict) and item.get("error"):
                    item["error"] = _normalize_source_error(item["error"])
        for key in ("error", "_error"):
            block = result.get("us_china_assets")
            if isinstance(block, dict) and block.get(key):
                block[key] = _normalize_source_error(block[key])
        if isinstance(result.get("margin_data"), dict) and result["margin_data"].get("error"):
            result["margin_data"]["error"] = _normalize_source_error(result["margin_data"]["error"])

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
