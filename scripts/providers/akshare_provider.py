"""
AkShare 数据提供者（免费，作为 tushare 的补充和降级）
文档: https://akshare.akfamily.xyz/

盘前外盘/大宗/汇率：优先使用东方财富全球指数现货 index_global_spot_em、
全球期货现货 futures_global_spot_em；已移除失效的 index_investing_global。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

import pandas as pd

import requests

from .base import DataProvider, DataResult, DataType, Timeliness

logger = logging.getLogger(__name__)


def _to_float_pct(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().replace("%", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _to_float_price(val: Any) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _to_float_or_none(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_clean_str(val: Any) -> str:
    """NaN / None / pd.NA → 空串，其余 str() 去空白。

    不能用 `str(val or "")`：pandas NaN 为 truthy 会漏到 str() 变成字面 'nan'，
    pd.NA 做布尔运算还会抛 'ambiguous'。统一走 pd.isna 判定，再兜底过滤脏值文本，
    避免下游行业聚合产出伪行业桶。
    """
    try:
        if val is None or pd.isna(val):
            return ""
    except (TypeError, ValueError):
        pass  # 非标量（如 list）：pd.isna 返回数组，落到下方 str()
    s = str(val).strip()
    return "" if s.lower() in ("nan", "<na>", "none") else s


_US_MARKET_CLOSE_HOUR_ET = 16  # 美股 16:00 ET 收盘（半日市忽略，盘前简报在 19:00 ET 运行不受影响）


def _us_eastern_now():
    """美东当前 tz-aware datetime。优先 zoneinfo；缺 IANA 数据（如部分 launchd 环境）退固定 UTC-5。

    固定偏移不处理夏令时（夏季美东实为 UTC-4），仅用于「bar 是否当日 + 是否已过收盘」判定；
    盘前简报约美东 19:00 运行，离午夜与收盘点都很远，1 小时误差不影响判定——比静默退化
    （跳过成形 bar 剔除、可能用未收盘价算出反号隔夜涨跌）安全得多。
    """
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception as e:
        from datetime import timedelta, timezone

        logger.warning("zoneinfo 美东时区不可用，退到固定 UTC-5 估算美东时间: %s", e)
        return datetime.now(timezone(timedelta(hours=-5)))


def _overnight_from_hist(hist: pd.DataFrame, now_et) -> tuple[float, float, str] | None:
    """从日线历史取「最近一个已收盘美股交易日」的收盘 + 隔夜涨跌幅 + as_of。

    只剔除「仍在盘中/盘前未收盘」的当日 bar——末根日期是美东今日且现在还没到 16:00 ET 收盘
    （或末根日期在未来）。关键：盘前简报跑在美东约 19:00（北京次日 07:00），此时当日美股已
    收盘，当日 bar 就是要的"隔夜"数据，不能因"日期==今天"误剔（否则少取一个交易日，
    如 05-29 简报本应取 05-28 却退成 05-27）。盘中手动跑时今日 bar 仍是成形价，照常剔除。
    历史不足两个已收盘交易日时返回 None（让调用方诚实报"无隔夜对比"，不编 0.0%）。

    now_et: 美东当前 tz-aware datetime；None 则不剔除（退化）。
    """
    if hist is None or hist.empty or "Close" not in hist.columns:
        return None
    hist = hist.sort_index().dropna(subset=["Close"])
    if now_et is not None and len(hist) >= 2:
        last_date = hist.index[-1].date()
        et_date = now_et.date()
        still_open = last_date == et_date and now_et.hour < _US_MARKET_CLOSE_HOUR_ET
        if last_date > et_date or still_open:
            hist = hist.iloc[:-1]
    if len(hist) < 2:
        return None
    close_val = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2])
    change_pct = round((close_val - prev_close) / prev_close * 100, 2) if prev_close else 0.0
    as_of = hist.index[-1].strftime("%Y-%m-%d")
    return close_val, change_pct, as_of


# ---- 美股分析师评级变动（yfinance upgrades_downgrades）窗口/方向过滤纯函数 ----
# 真方向变化的 Action；main=维持 / reit=重申 视为无变动，不算评级变动。
_US_RATING_DIRECTION_ACTIONS = frozenset({"up", "down", "init", "reinit"})
# 某标的最新评级事件距窗口末 > 此天数 → 视为"源头永久冻结"（如 META 实测仅到 2024-09-30），
# 与"窗口内恰无变动"分别打日志，便于精选池体检剔除（见实施计划 H5）。
_US_RATING_FROZEN_DAYS = 120


def _grade_date(idx):
    """yfinance GradeDate（pandas Timestamp，可能 tz-aware 美东）→ datetime.date。

    tz-aware 先 tz_convert 到美东再取 .date()，杜绝时区错位把昨夜评级算到错误日历日（H2）。
    """
    try:
        ts = pd.Timestamp(idx)
    except Exception:
        return None
    if ts is None or pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        try:
            ts = ts.tz_convert("America/New_York")
        except Exception:
            pass
    return ts.date()


def _to_rating_date(val):
    """'YYYY-MM-DD' / date / datetime / Timestamp → datetime.date；失败返 None。"""
    from datetime import date as _date
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, _date):
        return val
    try:
        return pd.Timestamp(val).date()
    except Exception:
        return None


def _filter_us_rating_window(df, window_start, window_end, *, direction_only=True):
    """从 yfinance upgrades_downgrades 取窗口内 [window_start, window_end] 的评级事件。

    window_start/window_end: datetime.date，闭区间；GradeDate 经 _grade_date tz 归一为美东日历日再比。
    direction_only=True 只保留 Action∈{up,down,init,reinit}（真方向变化），丢 main/reit。
    陈旧标的（末行 GradeDate 远早于窗口，如 META 2024）在此被过滤为空——设计内行为。
    返回 list[dict]：firm/from_grade/to_grade/action/grade_date/current_pt/prior_pt。
    """
    if df is None or len(df) == 0:
        return []
    out = []
    for idx, row in df.iterrows():
        d = _grade_date(idx)
        if d is None or d < window_start or d > window_end:
            continue
        action = str(row.get("Action", "")).strip().lower()
        if direction_only and action not in _US_RATING_DIRECTION_ACTIONS:
            continue
        out.append({
            "firm": str(row.get("Firm", "")).strip(),
            "from_grade": str(row.get("FromGrade", "")).strip(),
            "to_grade": str(row.get("ToGrade", "")).strip(),
            "action": action,
            "grade_date": d.strftime("%Y-%m-%d"),
            # 反驳 codex 阶段1 S-1：currentPriceTarget/priorPriceTarget 确为 yfinance>=1.x
            # upgrades_downgrades 真实列（实测 yfinance 1.2.0：列=[Firm,ToGrade,FromGrade,Action,
            # priceTargetAction,currentPriceTarget,priorPriceTarget]，NVDA 最新行 425.0/360.0）。
            # 旧版 yfinance 仅 4 列才无目标价——本仓锁定 1.x。raw 留存供溯源，渲染层剔除（红线）。
            "current_pt": _to_float_or_none(row.get("currentPriceTarget")),
            "prior_pt": _to_float_or_none(row.get("priorPriceTarget")),
        })
    return out


def _most_recent_grade_date(df):
    """upgrades_downgrades 最新一行的 GradeDate（date），用于判断标的是否整体冻结。"""
    if df is None or len(df) == 0:
        return None
    dates = [d for d in (_grade_date(i) for i in df.index) if d is not None]
    return max(dates) if dates else None


def _normalize_amount_to_billion(val: Any) -> float:
    parsed = _to_float_or_none(val)
    if parsed is None:
        return 0.0
    if abs(parsed) >= 1_000_000:
        return round(parsed / 1e8, 2)
    return round(parsed, 2)


def _first_text(row: pd.Series, *columns: str) -> str:
    for col in columns:
        if col not in row.index:
            continue
        value = str(row.get(col, "")).strip()
        if value and value.lower() != "nan":
            return value
    return ""


def _first_float(row: pd.Series, *columns: str) -> float | None:
    for col in columns:
        if col not in row.index:
            continue
        parsed = _to_float_or_none(row.get(col))
        if parsed is not None:
            return parsed
    return None


class AkshareProvider(DataProvider):
    name = "akshare"
    priority = 2

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.ak = None
        self._df_global_spot_em: Optional[pd.DataFrame] = None
        self._df_futures_global_spot_em: Optional[pd.DataFrame] = None

    def initialize(self) -> bool:
        try:
            import akshare as ak
            self.ak = ak
            ak.tool_trade_date_hist_sina()
            self._df_global_spot_em = None
            self._df_futures_global_spot_em = None
            self._initialized = True
            return True
        except Exception as e:
            logger.error(f"AkShare 初始化失败: {e}")
            return False

    def get_capabilities(self) -> list[str]:
        return [
            "get_index_daily",
            "get_market_volume",
            "get_stock_daily",
            "get_stock_ma",
            "get_disclosure_dates",
            "get_limit_up_list",
            "get_limit_down_list",
            "get_sector_rankings",
            "get_sector_fund_flow",
            "get_market_breadth",
            "get_northbound",
            "get_global_index",
            "get_us_tickers_overnight",
            "get_commodity",
            "get_forex",
            "get_market_news",
            "get_sector_moneyflow_dc",
            "get_concept_moneyflow_dc",
            "get_stock_news",
            "get_stock_announcements",
            "get_investor_qa",
            "get_research_reports",
            "get_research_report_list",
            "get_us_rating_changes",
            "get_macro_calendar",
            "get_macro_calendar_range",
            "get_macro_indicators",
            "is_trade_day",
            "get_etf_flow",
            "get_hk_indices",
            "get_index_weekly",
        ]

    # ------------------------------------------------------------------
    # 缓存：单次盘前采集会多次调用，避免重复拉全表
    # ------------------------------------------------------------------

    def _global_spot_em(self) -> pd.DataFrame:
        if self._df_global_spot_em is None:
            self._df_global_spot_em = self.ak.index_global_spot_em()
        return self._df_global_spot_em

    def _futures_global_spot_em(self) -> pd.DataFrame:
        if self._df_futures_global_spot_em is None:
            self._df_futures_global_spot_em = self.ak.futures_global_spot_em()
        return self._df_futures_global_spot_em

    def _row_global_spot_by_name(self, zh_name: str) -> Optional[pd.Series]:
        df = self._global_spot_em()
        exact = df[df["名称"] == zh_name]
        if not exact.empty:
            return exact.iloc[0]
        sub = df[df["名称"].str.contains(zh_name, na=False)]
        if not sub.empty:
            return sub.iloc[0]
        return None

    def _global_index_from_hist_em(self, symbol: str) -> Optional[dict]:
        """index_global_hist_em：无涨跌幅列时用最近两日收盘推算涨跌幅。"""
        try:
            df = self.ak.index_global_hist_em(symbol=symbol)
        except Exception:
            return None
        if df is None or df.empty or "最新价" not in df.columns:
            return None
        df = df.sort_values("日期" if "日期" in df.columns else df.columns[0])
        if len(df) < 1:
            return None
        last = df.iloc[-1]
        close = _to_float_price(last.get("最新价", 0))
        pct = 0.0
        if len(df) >= 2:
            prev_close = _to_float_price(df.iloc[-2].get("最新价", 0))
            if prev_close:
                pct = round((close - prev_close) / prev_close * 100, 4)
        nm = str(last.get("名称", symbol))
        return {"name": nm, "close": close, "change_pct": pct}

    def _index_from_yfinance(self, yahoo_symbol: str, display_name: str) -> DataResult | None:
        """用 yfinance 取指数最近收盘与涨跌幅；东财不可用时（如本机 ProxyError）作亚太股指主源。"""
        try:
            import yfinance as yf
        except ImportError:
            return None
        try:
            hist = yf.Ticker(yahoo_symbol).history(period="10d")
            if hist is None or hist.empty or "Close" not in hist.columns:
                return None
            hist = hist.sort_index()
            # 末根 K 线常因当日未收盘/数据缺失为 NaN（^KS11 实测），跳过 NaN 取最近有效收盘
            hist = hist.dropna(subset=["Close"])
            if hist.empty:
                return None
            close_val = round(float(hist["Close"].iloc[-1]), 2)
            prev_val = round(float(hist["Close"].iloc[-2]), 2) if len(hist) >= 2 else close_val
            change_pct = round((close_val - prev_val) / prev_val * 100, 2) if prev_val else 0.0
            return DataResult(
                data={
                    "name": display_name,
                    "close": close_val,
                    "change_pct": change_pct,
                },
                source=f"yfinance:{yahoo_symbol}",
            )
        except Exception as e:
            logger.warning(f"yfinance 指数 {yahoo_symbol} 获取失败: {e}")
            return None

    def _hk_index_from_yfinance(self, symbol: str, date: str) -> dict | None:
        """港股指数回退：优先指数现货，必要时退到对应 ETF。"""
        try:
            import yfinance as yf
        except ImportError:
            return None

        plans: dict[str, list[tuple[str, str]]] = {
            "HSI": [("^HSI", "恒生指数")],
            # HSTECH.HK 历史数据有时只有 1 行，保留 3033.HK 作为更稳的回退
            "HSTECH": [("HSTECH.HK", "恒生科技指数"), ("3033.HK", "恒生科技指数")],
        }

        best_entry: dict | None = None
        best_score = -1
        target_date = str(date)

        for yahoo_symbol, display_name in plans.get(symbol, []):
            try:
                hist = yf.Ticker(yahoo_symbol).history(period="1mo")
            except Exception as e:
                logger.warning(f"港股指数 yfinance 回退失败 {symbol}/{yahoo_symbol}: {e}")
                continue
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue

            hist = hist.reset_index()
            date_col = hist.columns[0]
            hist["__date"] = pd.to_datetime(hist[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
            matched = hist.index[hist["__date"] == target_date].tolist()
            row_idx = int(matched[-1]) if matched else int(hist.index[-1])
            row = hist.iloc[row_idx]

            close = _to_float_or_none(row.get("Close"))
            if close is None:
                continue
            prev_close = _to_float_or_none(hist.iloc[row_idx - 1].get("Close")) if row_idx > 0 else None
            change_pct = (
                round((close - prev_close) / prev_close * 100, 2)
                if prev_close not in (None, 0)
                else None
            )
            entry = {
                "code": symbol,
                "name": display_name,
                "close": round(close, 2),
                "open": _to_float_or_none(row.get("Open")),
                "high": _to_float_or_none(row.get("High")),
                "low": _to_float_or_none(row.get("Low")),
                "change_pct": change_pct,
                "_source": f"yfinance:{yahoo_symbol}",
            }
            for field in ("open", "high", "low"):
                if entry[field] is not None:
                    entry[field] = round(float(entry[field]), 2)

            score = (2 if matched else 0) + (1 if change_pct is not None else 0)
            if score > best_score:
                best_entry = entry
                best_score = score
            if score >= 3:
                break

        return best_entry

    def _parse_sector_fund_flow_rows(
        self,
        df: pd.DataFrame,
        *,
        name_columns: tuple[str, ...],
        net_columns: tuple[str, ...],
        change_columns: tuple[str, ...],
        limit: int = 20,
    ) -> list[dict]:
        if df is None or df.empty:
            return []

        records: list[dict] = []
        for _, row in df.head(limit).iterrows():
            name = _first_text(row, *name_columns)
            if not name:
                continue
            net_raw = _first_float(row, *net_columns)
            change_raw = _first_float(row, *change_columns)
            records.append({
                "name": name,
                "net_inflow_billion": _normalize_amount_to_billion(net_raw),
                "change_pct": round(change_raw, 2) if change_raw is not None else 0.0,
            })
        return records

    # ---- 外盘数据 ----

    def get_global_index(self, index_name: str) -> DataResult:
        """
        全球指数：index_global_spot_em 按中文名称匹配；
        A50：futures_global_spot_em 中「A50期指当月连续」等；
        VIX：yfinance ^VIX；
        US10Y：index_global_spot_em，失败则 bond_zh_us_rate()；
        恒生/恒生科技/日经：优先 yfinance（^HSI、HSTECH.HK、^N225 等），失败再试东财，减轻本机 ProxyError。
        部分指数可回退 index_global_hist_em（推算涨跌幅）。
        """
        name_map = {
            "dow_jones": "道琼斯",
            "nasdaq": "纳斯达克",
            "sp500": "标普500",
            "hsi": "恒生指数",
            "hstech": "恒生科技",
            "nikkei": "日经225",
            "kospi": "韩国综指",
            "ftse": "英国富时100",
            "dax": "德国DAX30",
            "a50": "__A50_FUTURES__",
            "vix": "__VIX__",
            "us10y": "__US10Y__",
        }
        zh_name = name_map.get(index_name, index_name)

        try:
            if index_name == "a50":
                fut = self._futures_global_spot_em()
                # 优先连续合约，否则取有成交价的 A50 期指合约
                for prefer in ("A50期指当月连续",):
                    m = fut[fut["名称"] == prefer]
                    if not m.empty and pd.notna(m.iloc[0].get("最新价")):
                        row = m.iloc[0]
                        break
                else:
                    m = fut[
                        (fut["名称"].str.contains("A50期指", na=False))
                        & (fut["最新价"].notna())
                    ]
                    if m.empty:
                        return DataResult(
                            data=None,
                            source=self.name,
                            error="AkShare: 未找到 A50 期货现货数据",
                        )
                    row = m.sort_values("最新价", ascending=False).iloc[0]
                data = {
                    "name": str(row.get("名称", "A50")),
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:futures_global_spot_em")

            if index_name == "vix":
                # AkShare 无 US VIX 接口，使用 yfinance
                try:
                    import yfinance as yf
                    hist = yf.Ticker("^VIX").history(period="3d")
                    if hist is not None and not hist.empty:
                        hist = hist.sort_index()
                        close_val = round(float(hist["Close"].iloc[-1]), 2)
                        prev_val = round(float(hist["Close"].iloc[-2]), 2) if len(hist) >= 2 else close_val
                        change_pct = round((close_val - prev_val) / prev_val * 100, 2) if prev_val else 0.0
                        return DataResult(
                            data={
                                "name": "VIX恐慌指数",
                                "close": close_val,
                                "change_pct": change_pct,
                            },
                            source="yfinance:^VIX",
                        )
                except Exception as e:
                    logger.warning(f"yfinance VIX 获取失败: {e}")
                return DataResult(
                    data=None, source=self.name, error="未找到 VIX 数据（yfinance 不可用）"
                )

            if index_name == "us10y":
                # 先尝试 index_global_spot_em（东财全球指数现货）。该端点不稳定，
                # 抛 JSONDecodeError 时不能让异常冒泡到外层 except——否则下面的
                # bond_zh_us_rate 回退形同死代码（实测 2026-05-26 端点宕，us10y 整体失败）。
                try:
                    df = self._global_spot_em()
                except Exception as spot_err:
                    logger.warning(
                        "us10y index_global_spot_em 不可用，回退 bond_zh_us_rate: %s", spot_err
                    )
                    df = None
                if df is not None:
                    for kw in ("美国10年期国债", "美10年期国债", "美债10年"):
                        sub = df[df["名称"].str.contains(kw, na=False)]
                        if not sub.empty and pd.notna(sub.iloc[0].get("最新价")):
                            row = sub.iloc[0]
                            close_val = _to_float_price(row.get("最新价"))
                            change_pct = _to_float_pct(row.get("涨跌幅"))
                            return DataResult(
                                data={
                                    "name": "美债10年期收益率",
                                    "close": close_val,
                                    "change_pct": change_pct,
                                    # 将涨跌幅近似转为基点（收益率×涨跌幅%×100）
                                    "change_bps": round(close_val * change_pct, 2),
                                },
                                source="akshare:index_global_spot_em",
                            )
                # 回退：bond_zh_us_rate（中/美多档收益率宽表）
                try:
                    df_bond = self.ak.bond_zh_us_rate(start_date="20200101")
                    if df_bond is not None and not df_bond.empty:
                        date_col = df_bond.columns[0]
                        df_bond = df_bond.sort_values(date_col)
                        # 必须显式锁定「美国国债收益率10年」：原 `"10" in col` 会先命中
                        # 「中国国债收益率10年」误报中债收益率；排除「10年-2年」利差列。
                        col_10y = next(
                            (
                                c
                                for c in df_bond.columns
                                if "美国" in str(c) and "10年" in str(c) and "-" not in str(c)
                            ),
                            None,
                        )
                        if col_10y:
                            # 末几行常因美债数据滞后/美股休市为 NaN，取最近一个有效交易日，
                            # 否则 iloc[-1] 会落在当日空行→ close 归零、日期错位。
                            valid = df_bond[df_bond[col_10y].notna()]
                            if not valid.empty:
                                row = valid.iloc[-1]
                                prev_row = valid.iloc[-2] if len(valid) >= 2 else row
                                close_val = _to_float_price(row[col_10y])
                                prev_val = _to_float_price(prev_row[col_10y])
                                change_bps = round((close_val - prev_val) * 100, 2)
                                return DataResult(
                                    data={
                                        "name": "美债10年期收益率",
                                        "close": close_val,
                                        "change_bps": change_bps,
                                        "as_of": str(row[date_col]),
                                    },
                                    source="akshare:bond_zh_us_rate",
                                )
                except Exception as bond_err:
                    logger.warning("us10y bond_zh_us_rate 回退失败: %s", bond_err)
                return DataResult(
                    data=None, source=self.name, error="AkShare: 未找到美债10年期数据"
                )

            # 亚太股指：优先 yfinance，避免本机访问东财 push2.eastmoney.com 触发 ProxyError
            apac_yf: dict[str, list[tuple[str, str]]] = {
                "hsi": [("^HSI", "恒生指数")],
                # ^HSTECH 在 Yahoo 常 404；HSTECH.HK 为恒生科技指数现货报价
                "hstech": [("HSTECH.HK", "恒生科技"), ("3033.HK", "恒生科技(ETF)")],
                "nikkei": [("^N225", "日经225")],
                "kospi": [("^KS11", "韩国综指")],
            }
            if index_name in apac_yf:
                for ysym, label in apac_yf[index_name]:
                    yf_r = self._index_from_yfinance(ysym, label)
                    if yf_r is not None:
                        return yf_r
                try:
                    row = self._row_global_spot_by_name(zh_name)
                    if row is not None and pd.notna(row.get("最新价")):
                        data = {
                            "name": str(row.get("名称", zh_name)),
                            "close": _to_float_price(row.get("最新价")),
                            "change_pct": _to_float_pct(row.get("涨跌幅")),
                        }
                        return DataResult(data=data, source="akshare:index_global_spot_em")
                except Exception as em_err:
                    logger.warning(f"东财全球指数 {index_name} 回退失败: {em_err}")
                return DataResult(
                    data=None,
                    source=self.name,
                    error=f"AkShare/yfinance 均未获取到亚太指数: {index_name}",
                )

            # 美股三大指数：优先 yfinance（东财 index_global_spot_em 现货端点不稳定），
            # yfinance 不可用时落到下方东财兜底块。
            us_yf: dict[str, tuple[str, str]] = {
                "dow_jones": ("^DJI", "道琼斯"),
                "nasdaq": ("^IXIC", "纳斯达克"),
                "sp500": ("^GSPC", "标普500"),
            }
            if index_name in us_yf:
                ysym, label = us_yf[index_name]
                yf_r = self._index_from_yfinance(ysym, label)
                if yf_r is not None:
                    return yf_r

            if zh_name not in ("__A50_FUTURES__", "__VIX__", "__US10Y__"):
                row = self._row_global_spot_by_name(zh_name)
                if row is not None and pd.notna(row.get("最新价")):
                    data = {
                        "name": str(row.get("名称", zh_name)),
                        "close": _to_float_price(row.get("最新价")),
                        "change_pct": _to_float_pct(row.get("涨跌幅")),
                    }
                    return DataResult(data=data, source="akshare:index_global_spot_em")

            return DataResult(
                data=None,
                source=self.name,
                error=f"AkShare: 未匹配全球指数: {index_name}",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_us_tickers_overnight(self, tickers: list[str]) -> DataResult:
        """美股 ETF 隔夜涨跌：yfinance 最近一个已收盘美股交易日 + 上一交易日推算涨跌幅。"""
        labels = {
            "KWEB": "KWEB（中概互联网ETF）",
            "FXI": "FXI（中国大盘ETF）",
            "HXC": "HXC（纳斯达克中国金龙ETF·PGJ）",
        }
        # 金龙：Yahoo 的指数符号 ^HXC 只给单点实时报价、无历史 K 线（隔夜涨跌幅恒算成 0%），
        # 改用 PGJ（Invesco 纳斯达克金龙中国 ETF，跟踪同一指数）取可靠日线。KWEB/FXI 原样查询。
        # 输出键仍用原 ticker（HXC/KWEB/FXI），不动 collectors/report 消费方。
        yahoo_symbol = {"HXC": "PGJ"}
        try:
            import yfinance as yf
        except ImportError as e:
            return DataResult(data=None, source=self.name, error=f"yfinance 不可用: {e}")

        now_et = _us_eastern_now()
        out: dict[str, dict] = {}
        try:
            for t in tickers:
                sym = str(t).strip().upper()
                # period=1mo：跨长周末 + 美国节假日时 5d 可能不足两个已收盘交易日。
                hist = yf.Ticker(yahoo_symbol.get(sym, sym)).history(period="1mo")
                parsed = _overnight_from_hist(hist, now_et)
                if parsed is None:
                    out[sym] = {"error": "无隔夜对比数据", "name": labels.get(sym, sym)}
                    continue
                close_val, change_pct, as_of = parsed
                out[sym] = {
                    "name": labels.get(sym, sym),
                    "close": round(close_val, 2),
                    "change_pct": change_pct,
                    "as_of": as_of,
                }
            return DataResult(data=out, source="yfinance")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_commodity(self, name: str) -> DataResult:
        """大宗商品：futures_global_spot_em 按名称关键词取第一条有效报价。"""
        plans: dict[str, list[str]] = {
            "gold": ["迷你黄金当月连续", "迷你黄金", "COMEX黄金", "微型黄金"],
            "crude_oil": ["迷你原油当月连续", "迷你原油", "布伦特原油"],
            "copper": ["COMEX铜"],
        }
        keywords = plans.get(name, [name])
        try:
            fut = self._futures_global_spot_em()
            for kw in keywords:
                if kw.endswith("当月连续"):
                    sub = fut[fut["名称"] == kw]
                else:
                    sub = fut[fut["名称"] == kw]
                    if sub.empty:
                        sub = fut[fut["名称"].str.contains(kw, na=False)]
                sub = sub[sub["最新价"].notna()]
                if sub.empty:
                    continue
                row = sub.iloc[0]
                data = {
                    "name": str(row.get("名称", kw)),
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:futures_global_spot_em")
            return DataResult(
                data=None,
                source=self.name,
                error=f"AkShare: 无商品数据: {name}",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_forex(self, pair: str) -> DataResult:
        """汇率：美元指数来自 index_global_spot_em；USD/CNY 优先 美元人民币中间价（forex_spot_em）。"""
        try:
            if pair == "usd_index":
                row = self._row_global_spot_by_name("美元指数")
                if row is None or pd.isna(row.get("最新价")):
                    got = self._global_index_from_hist_em("美元指数")
                    if got:
                        return DataResult(data=got, source="akshare:index_global_hist_em")
                    return DataResult(data=None, source=self.name, error="未找到美元指数")
                data = {
                    "name": "美元指数",
                    "close": _to_float_price(row.get("最新价")),
                    "change_pct": _to_float_pct(row.get("涨跌幅")),
                }
                return DataResult(data=data, source="akshare:index_global_spot_em")

            if pair == "usd_cny":
                df = self.ak.forex_spot_em()
                for label in ("美元人民币中间价", "美元兑离岸人民币"):
                    m = df[df["名称"] == label]
                    if not m.empty:
                        row = m.iloc[0]
                        data = {
                            "name": label,
                            "close": _to_float_price(row.get("最新价")),
                            "change_pct": _to_float_pct(row.get("涨跌幅")),
                        }
                        return DataResult(data=data, source="akshare:forex_spot_em")
                return DataResult(data=None, source=self.name, error="未找到 USD/CNY 汇率")

            if pair == "usd_cnh":
                df = self.ak.forex_spot_em()
                m = df[df["名称"] == "美元兑离岸人民币"]
                if not m.empty:
                    row = m.iloc[0]
                    data = {
                        "name": "USD/CNH（离岸人民币）",
                        "close": _to_float_price(row.get("最新价")),
                        "change_pct": _to_float_pct(row.get("涨跌幅")),
                    }
                    return DataResult(data=data, source="akshare:forex_spot_em")
                got = self._index_from_yfinance("CNH=X", "USD/CNH（离岸）")
                if got is not None:
                    return got
                return DataResult(data=None, source=self.name, error="未找到离岸人民币汇率")

            return DataResult(data=None, source=self.name, error=f"不支持: {pair}")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 板块数据（akshare 更方便） ----

    def get_sector_rankings(self, date: str, sector_type: str = "industry") -> DataResult:
        """获取板块涨跌幅排名（涨幅前30 + 跌幅前5）"""
        try:
            if sector_type == "industry":
                df = self.ak.stock_board_industry_name_em()
            else:
                df = self.ak.stock_board_concept_name_em()

            if df.empty:
                return DataResult(data=None, source=self.name, error="无板块数据")

            def _parse_row(row):
                vol_raw = row.get("总成交额", 0)
                if vol_raw is None or (isinstance(vol_raw, float) and pd.isna(vol_raw)):
                    vol_raw = 0
                vol_billion = float(vol_raw) / 1e8 if float(vol_raw) > 100 else float(vol_raw)
                return {
                    "name": row.get("板块名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0) or 0),
                    "volume_billion": round(vol_billion, 2),
                    "top_stock": row.get("领涨股票", ""),
                }

            top_records = [_parse_row(row) for _, row in df.head(30).iterrows()]

            bottom_records = []
            if len(df) > 5:
                for _, row in df.tail(5).iterrows():
                    bottom_records.append(_parse_row(row))
                bottom_records.sort(key=lambda x: x["change_pct"])

            result = {"top": top_records, "bottom": bottom_records}
            return DataResult(data=result, source=f"akshare:board_{sector_type}")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_sector_fund_flow(self, date: str) -> DataResult:
        """获取板块资金流向"""
        errors: list[str] = []

        try:
            df = self.ak.stock_sector_fund_flow_rank(
                indicator="今日", sector_type="行业资金流"
            )
            records = self._parse_sector_fund_flow_rows(
                df,
                name_columns=("名称", "板块名称", "行业"),
                net_columns=("今日主力净流入-净额", "主力净流入-净额", "净额"),
                change_columns=("今日涨跌幅", "涨跌幅", "行业-涨跌幅"),
            )
            if records:
                return DataResult(data=records, source="akshare:sector_fund_flow_rank")
            errors.append("stock_sector_fund_flow_rank 无有效记录")
        except Exception as e:
            errors.append(f"stock_sector_fund_flow_rank: {e}")

        try:
            df = self.ak.stock_fund_flow_industry(symbol="即时")
            records = self._parse_sector_fund_flow_rows(
                df,
                name_columns=("行业", "名称"),
                net_columns=("净额", "主力净流入-净额"),
                change_columns=("行业-涨跌幅", "涨跌幅"),
            )
            if records:
                return DataResult(data=records, source="akshare:stock_fund_flow_industry")
            errors.append("stock_fund_flow_industry 无有效记录")
        except Exception as e:
            errors.append(f"stock_fund_flow_industry: {e}")

        return DataResult(data=None, source=self.name, error="; ".join(errors))

    def get_sector_moneyflow_dc(self, date: str) -> DataResult:
        """AkShare 降级：东财行业板块资金流排名。"""
        try:
            df = self.ak.stock_sector_fund_flow_rank(
                indicator="今日", sector_type="行业资金流"
            )
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error="无行业资金流数据")
            records = []
            for _, row in df.iterrows():
                net_raw = float(row.get("主力净流入-净额", 0) or 0)
                records.append({
                    "name": str(row.get("板块名称", "")),
                    "net_amount_yi": round(net_raw / 1e8, 2),
                    "pct_change": float(row.get("涨跌幅", 0) or 0),
                    "content_type": "行业",
                })
            return DataResult(data=records, source="akshare:sector_fund_flow_rank:industry")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_concept_moneyflow_dc(self, date: str) -> DataResult:
        """AkShare 降级：东财概念板块资金流排名。"""
        try:
            df = self.ak.stock_sector_fund_flow_rank(
                indicator="今日", sector_type="概念资金流"
            )
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error="无概念资金流数据")
            records = []
            for _, row in df.iterrows():
                net_raw = float(row.get("主力净流入-净额", 0) or 0)
                records.append({
                    "name": str(row.get("板块名称", "")),
                    "net_amount_yi": round(net_raw / 1e8, 2),
                    "pct_change": float(row.get("涨跌幅", 0) or 0),
                    "content_type": "概念",
                })
            return DataResult(data=records, source="akshare:sector_fund_flow_rank:concept")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 指数周线 ----

    # AkShare 指数代码映射（与 get_index_weekly 共用）
    _INDEX_CODE_MAP = {
        "shanghai": "000001",
        "shenzhen": "399001",
        "chinext": "399006",
        "star50": "000688",
    }

    _SINA_INDEX_SYMBOL = {
        "shanghai": "sh000001",
        "shenzhen": "sz399001",
        "chinext": "sz399006",
        "star50": "sh000688",
    }

    def _sina_index_symbol(self, index_code: str) -> str:
        """指数 key / ts-code → sina 符号（sh/sz + 6 位码）。"""
        if index_code in self._SINA_INDEX_SYMBOL:
            return self._SINA_INDEX_SYMBOL[index_code]
        code = str(index_code or "").strip()
        if "." in code:
            num, suf = code.split(".", 1)
            return f"{'sh' if suf.upper() == 'SH' else 'sz'}{num}"
        return f"sz{code}" if code.startswith("399") else f"sh{code}"

    def _index_daily_sina(self, index_code: str, date: str) -> DataResult | None:
        """sina stock_zh_index_daily 取指数日线：OHLC 精确、volume 股→手÷100、pct 自算。

        sina 日线无成交额列，amount_billion 返回 None（由东财兜底或 tushare 主源补）。
        非真 DataFrame（如测试 MagicMock）或无目标日匹配 → 返回 None 让上层退东财。
        """
        try:
            symbol = self._sina_index_symbol(index_code)
            df = self.ak.stock_zh_index_daily(symbol=symbol)
            if not isinstance(df, pd.DataFrame) or df.empty:
                return None
            df = df.copy()
            df["__dt"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.sort_values("__dt").reset_index(drop=True)  # 防御乱序：prev_close 必须是真前一交易日
            df["__d"] = df["__dt"].dt.strftime("%Y-%m-%d")
            matches = df.index[df["__d"] == date].tolist()
            if not matches:
                return None
            i = matches[-1]
            row = df.iloc[i]
            close_raw = row["close"]
            if pd.isna(close_raw):
                return None
            prev_close = float(df.iloc[i - 1]["close"]) if i > 0 and pd.notna(df.iloc[i - 1]["close"]) else None
            close = float(close_raw)
            change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
            vol_raw = row.get("volume")
            volume = round(float(vol_raw) / 100, 0) if pd.notna(vol_raw) else None
            data = {
                "code": symbol,
                "open": round(float(row["open"]), 2) if pd.notna(row.get("open")) else None,
                "high": round(float(row["high"]), 2) if pd.notna(row.get("high")) else None,
                "low": round(float(row["low"]), 2) if pd.notna(row.get("low")) else None,
                "close": round(close, 2),
                "change_pct": change_pct,
                "volume": volume,
                "amount_billion": None,  # sina 日线无成交额列
            }
            return DataResult(data=data, source="akshare:stock_zh_index_daily")
        except Exception as e:
            logger.debug("sina 指数日线失败 %s %s: %s", index_code, date, e)
            return None

    def get_index_daily(self, index_code: str, date: str) -> DataResult:
        """指数日线降级：sina `stock_zh_index_daily` 优先，东财 `index_zh_a_hist` 兜底。"""
        sina = self._index_daily_sina(index_code, date)
        if sina is not None and sina.success:
            return sina
        return self._index_daily_em(index_code, date)

    def _index_daily_em(self, index_code: str, date: str) -> DataResult:
        """获取指数单日日线（AkShare index_zh_a_hist daily）作为 tushare 降级。

        输出字段对齐 tushare get_index_daily，关键差异：AkShare 成交额单位为元，
        需 /1e8 转为亿（tushare 为千元 /1e5）。
        """
        try:
            # 命名 key（shanghai…）走映射；ts-code（000300.SH）剥离后缀取 6 位码，
            # 让 csi300/csi1000 也能享受降级，而不只是 4 个命名指数。
            symbol = self._INDEX_CODE_MAP.get(index_code, index_code.split(".")[0])
            d = date.replace("-", "")
            df = self.ak.index_zh_a_hist(symbol=symbol, period="daily", start_date=d, end_date=d)
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error=f"无数据: {symbol} {date}")
            # 必须精确匹配目标交易日，不得回退到邻近日期当作当日事实（事实层污染防护）
            matched = df[df["日期"].astype(str).str.replace("-", "", regex=False) == d]
            if matched.empty:
                return DataResult(data=None, source=self.name, error=f"无目标日期数据: {symbol} {date}")
            row = matched.iloc[0]
            # close 是 MA / 节点信号的核心比较量，NaN 不得穿透成 success
            close_raw = row["收盘"]
            if pd.isna(close_raw):
                return DataResult(data=None, source=self.name, error=f"收盘价无效(NaN): {symbol} {date}")

            def _num(col, default=None):
                if col not in df.columns or pd.isna(row[col]):
                    return default
                return float(row[col])

            amount = _num("成交额")
            data = {
                "code": symbol,
                "open": _num("开盘", 0.0),
                "high": _num("最高", 0.0),
                "low": _num("最低", 0.0),
                "close": float(close_raw),
                "change_pct": _num("涨跌幅"),
                "volume": _num("成交量", 0.0),
                "amount_billion": round(amount / 1e8, 2) if amount is not None else None,
            }
            return DataResult(data=data, source="akshare:index_zh_a_hist")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    @staticmethod
    def _today_cn() -> str:
        """A 股「今日」按上海时区判定，避免 launchd/UTC 环境本地时区漂移误判。"""
        try:
            from zoneinfo import ZoneInfo

            return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        except Exception:
            return datetime.now().strftime("%Y-%m-%d")

    def _market_volume_sina_realtime(self, date: str) -> DataResult | None:
        """当日两市成交额：sina 实时指数现货 stock_zh_index_spot_sina（成交额单位元）。

        仅当 date 为今日（上海时区）才适用（实时快照只反映当前交易日）；
        非今日或取数失败返回 None。
        """
        if date != self._today_cn():
            return None
        try:
            df = self.ak.stock_zh_index_spot_sina()
            if not isinstance(df, pd.DataFrame) or df.empty or "成交额" not in df.columns:
                return None

            def _amt_yi(symbol: str) -> float | None:
                m = df[df["代码"] == symbol]
                if m.empty:
                    return None
                v = _to_float_or_none(m.iloc[0].get("成交额"))
                return round(v / 1e8, 2) if v is not None else None

            sh = _amt_yi("sh000001")
            sz = _amt_yi("sz399001")
            # 盘前未开盘时实时成交额可能为 0，不可冒充全日；<=0 退东财兜底。
            if sh is None or sz is None or sh <= 0 or sz <= 0:
                return None
            return DataResult(
                data={
                    "shanghai_billion": sh,
                    "shenzhen_billion": sz,
                    "total_billion": round(sh + sz, 2),
                },
                source="akshare:stock_zh_index_spot_sina",
            )
        except Exception as e:
            logger.debug("sina 实时两市成交额失败 %s: %s", date, e)
            return None

    def get_market_volume(self, date: str) -> DataResult:
        """两市总成交额降级：当日走 sina 实时现货，历史/兜底取东财指数日线成交额加总（亿）。"""
        realtime = self._market_volume_sina_realtime(date)
        if realtime is not None and realtime.success:
            return realtime
        try:
            # sina 日线无成交额列，历史成交额必须走东财日线（_index_daily_em），
            # 不能用 get_index_daily（已 sina 优先、amount_billion 为 None）。
            sh = self._index_daily_em("shanghai", date)
            sz = self._index_daily_em("shenzhen", date)
            if not sh.success or not sz.success:
                err = sh.error if not sh.success else sz.error
                return DataResult(data=None, source=self.name, error=f"无成交额数据: {date} ({err})")
            sh_amount = sh.data.get("amount_billion")
            sz_amount = sz.data.get("amount_billion")
            if sh_amount is None or sz_amount is None:
                return DataResult(data=None, source=self.name, error=f"成交额字段缺失: {date}")
            data = {
                "shanghai_billion": round(sh_amount, 2),
                "shenzhen_billion": round(sz_amount, 2),
                "total_billion": round(sh_amount + sz_amount, 2),
            }
            return DataResult(data=data, source="akshare:index_zh_a_hist")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 个股日线（sina）----

    def _sina_stock_symbol(self, code: str) -> str:
        """个股代码 → sina 符号（sh/sz/bj + 6 位码）。"""
        c = str(code or "").strip().upper()
        digits = "".join(ch for ch in c if ch.isdigit())[:6]
        if not digits:
            return c.lower()
        if "." in c:
            suf = c.split(".", 1)[1]
            return f"{ {'SH': 'sh', 'SZ': 'sz', 'BJ': 'bj'}.get(suf, 'sz') }{digits}"
        if digits.startswith(("60", "68", "90", "51", "52", "53", "56", "58")):
            return f"sh{digits}"
        if digits.startswith(("43", "82", "83", "87", "88", "89", "92")):
            return f"bj{digits}"
        return f"sz{digits}"

    def get_stock_daily(self, stock_code: str, date: str) -> DataResult:
        """个股日线降级：sina `stock_zh_a_daily`（OHLC/成交额精确，volume 股→手÷100、
        换手率 小数→%×100、pct/振幅自算）。输出字段对齐 tushare get_stock_daily。"""
        try:
            symbol = self._sina_stock_symbol(stock_code)
            df = self.ak.stock_zh_a_daily(symbol=symbol, adjust="")
            if not isinstance(df, pd.DataFrame) or df.empty:
                return DataResult(data=None, source=self.name, error=f"无数据: {symbol} {date}")
            df = df.copy()
            df["__dt"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.sort_values("__dt").reset_index(drop=True)  # 防御乱序：prev_close 必须是真前一交易日
            df["__d"] = df["__dt"].dt.strftime("%Y-%m-%d")
            matches = df.index[df["__d"] == date].tolist()
            if not matches:
                return DataResult(data=None, source=self.name, error=f"无目标日期数据: {symbol} {date}")
            i = matches[-1]
            row = df.iloc[i]
            if pd.isna(row.get("close")):
                return DataResult(data=None, source=self.name, error=f"收盘价无效(NaN): {symbol} {date}")
            close = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            prev_close = float(df.iloc[i - 1]["close"]) if i > 0 and pd.notna(df.iloc[i - 1]["close"]) else None
            change_pct = round((close - prev_close) / prev_close * 100, 2) if prev_close else None
            amplitude_pct = round((high - low) / prev_close * 100, 2) if prev_close else None
            vol_raw = row.get("volume")
            volume = round(float(vol_raw) / 100, 0) if pd.notna(vol_raw) else None
            amount = _to_float_or_none(row.get("amount"))
            amount_billion = round(amount / 1e8, 4) if amount is not None else None
            turnover_raw = _to_float_or_none(row.get("turnover"))
            turnover_rate = round(turnover_raw * 100, 4) if turnover_raw is not None else None
            data = {
                "code": stock_code,
                "open": round(float(row["open"]), 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "change_pct": change_pct,
                "volume": volume,
                "amount_billion": amount_billion,
                "turnover_rate": turnover_rate,
                "amplitude_pct": amplitude_pct,
            }
            return DataResult(data=data, source="akshare:stock_zh_a_daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_stock_ma(self, stock_code: str, date: str, periods: list[int] | None = None) -> DataResult:
        """个股均线降级：sina `stock_zh_a_daily` 历史自算（收盘均值口径，与 tushare 一致）。

        只用截止目标日（含）的历史，volume_ma5 取最近 5 日均量并 股→手÷100。
        """
        periods = periods or [5, 10, 20]
        try:
            symbol = self._sina_stock_symbol(stock_code)
            df = self.ak.stock_zh_a_daily(symbol=symbol, adjust="")
            if not isinstance(df, pd.DataFrame) or df.empty:
                return DataResult(data=None, source=self.name, error=f"无历史数据: {symbol}")
            df = df.copy()
            df["__dt"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["__dt"])
            df = df[df["__dt"] <= pd.to_datetime(date)].sort_values("__dt")
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无历史数据: {symbol}")
            closes = df["close"].astype(float)
            data: dict = {}
            for p in periods:
                if len(df) >= p:
                    data[f"ma{p}"] = round(float(closes.tail(p).mean()), 2)
            if len(df) >= 5 and "volume" in df.columns:
                vols = df["volume"].astype(float).tail(5) / 100
                data["volume_ma5"] = round(float(vols.mean()), 2)
            return DataResult(data=data, source="akshare:stock_zh_a_daily")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def _index_weekly_sina(self, index_code: str, start_date: str, end_date: str) -> DataResult | None:
        """sina 日线按自然周（W-FRI）聚合出周线：open=周首、high=周内最高、low=周内最低、
        close=周末、trade_date=当周最后交易日。非真 DataFrame / 区间无数据 → None 退东财。"""
        try:
            symbol = self._sina_index_symbol(index_code)
            df = self.ak.stock_zh_index_daily(symbol=symbol)
            if not isinstance(df, pd.DataFrame) or df.empty:
                return None
            df = df.copy()
            df["__dt"] = pd.to_datetime(df["date"], errors="coerce")
            sd = pd.to_datetime(start_date)
            ed = pd.to_datetime(end_date)
            df = df.dropna(subset=["__dt", "close"])
            df = df[(df["__dt"] >= sd) & (df["__dt"] <= ed)]
            if df.empty:
                return None
            df = df.sort_values("__dt")
            df["__wk"] = df["__dt"].dt.to_period("W-FRI")
            rows: list[dict] = []
            for _, g in df.groupby("__wk"):
                g = g.sort_values("__dt")
                last = g.iloc[-1]
                rows.append({
                    "trade_date": last["__dt"].strftime("%Y%m%d"),
                    "close": round(float(last["close"]), 2),
                    "open": round(float(g.iloc[0]["open"]), 2),
                    "high": round(float(g["high"].max()), 2),
                    "low": round(float(g["low"].min()), 2),
                })
            rows.sort(key=lambda x: x["trade_date"])
            return DataResult(data=rows, source="akshare:stock_zh_index_daily:weekly")
        except Exception as e:
            logger.debug("sina 指数周线失败 %s: %s", index_code, e)
            return None

    def get_index_weekly(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        """指数周线降级：sina 日线 resample(W-FRI) 优先，东财 index_zh_a_hist(weekly) 兜底。"""
        sina = self._index_weekly_sina(index_code, start_date, end_date)
        if sina is not None and sina.success and sina.data:
            return sina
        return self._index_weekly_em(index_code, start_date, end_date)

    def _index_weekly_em(self, index_code: str, start_date: str, end_date: str) -> DataResult:
        """获取指数周线数据（AkShare index_zh_a_hist weekly）"""
        try:
            symbol = self._INDEX_CODE_MAP.get(index_code, index_code.split(".")[0])
            sd = start_date.replace("-", "")
            ed = end_date.replace("-", "")
            df = self.ak.index_zh_a_hist(symbol=symbol, period="weekly", start_date=sd, end_date=ed)
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:index_zh_a_hist_weekly")
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "trade_date": str(row["日期"]).replace("-", ""),
                    "close": float(row["收盘"]),
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                })
            return DataResult(data=rows, source="akshare:index_zh_a_hist_weekly")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 涨跌停数据 ----

    def get_limit_up_list(self, date: str) -> DataResult:
        """获取涨停股列表"""
        try:
            df = self.ak.stock_zt_pool_em(date=date.replace("-", ""))
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无涨停数据: {date}")
            # 列名漂移可观测性：东财偶改列名，缺列会被 _to_clean_str 静默降级为空行业，
            # 行业归因会悄悄消失。显式告警，便于排查「功能静默失效 vs 真无数据」。
            for _col in ("所属行业", "涨停统计"):
                if _col not in df.columns:
                    logger.warning("stock_zt_pool_em 缺列「%s」，涨停行业归因将降级为空（列名可能漂移）", _col)
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0)),
                    "amount_billion": float(row.get("成交额", 0)) / 1e8,
                    "turnover_rate": float(row.get("换手率", 0)),
                    "first_time": str(row.get("首次封板时间", "")),
                    "last_time": str(row.get("最后封板时间", "")),
                    "limit_times": int(row.get("连板数", 1)),
                    "seal_amount": float(row.get("封板资金", 0)),  # 东财列名为「封板资金」(原始元)，与 tushare fd_amount 同量级
                    "industry": _to_clean_str(row.get("所属行业", "")),  # 东财行业分类（题材文本归因为硬缺口，此处仅行业级）
                    "limit_stat": _to_clean_str(row.get("涨停统计", "")),  # N天M板，形如「5/3」
                })
            return DataResult(
                data={"count": len(stocks), "stocks": stocks},
                source="akshare:stock_zt_pool_em",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_limit_down_list(self, date: str) -> DataResult:
        """获取跌停股列表"""
        try:
            df = self.ak.stock_zt_pool_dtgc_em(date=date.replace("-", ""))
            if df.empty:
                return DataResult(data=[], source="akshare:stock_zt_pool_dtgc_em")
            stocks = []
            for _, row in df.iterrows():
                stocks.append({
                    "code": row.get("代码", ""),
                    "name": row.get("名称", ""),
                    "change_pct": float(row.get("涨跌幅", 0)),
                })
            return DataResult(
                data={"count": len(stocks), "stocks": stocks},
                source="akshare:stock_zt_pool_dtgc_em",
            )
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 北向资金 ----

    def get_northbound(self, date: str) -> DataResult:
        """获取北向资金（akshare 历史数据降级路径）。

        旧实现调用 stock_hsgt_north_net_flow_in_em（akshare 1.18.x 已移除，触发
        AttributeError），改用现存的 stock_hsgt_hist_em 历史数据接口。

        注意几个经实跑核对（akshare 1.18.49）确认的语义：
        - 该接口返回全量历史（分页 1000/页，比旧 realtime 接口重；仅在 tushare
          北向失败时才走此降级路径，可接受）；按请求 date **精确匹配**，不再盲取
          最近一行，避免把另一天的数据误标成目标日期。
        - 「当日成交净买额」列单位即为**亿元**（如 2024-08-16 为 -67.75），直接作为
          net_buy_billion，无需再换算。
        - 沪深交易所自 2024-08-16 起停更北向资金每日净流入：之后日期要么无该行、
          要么该列为 NaN。NaN 必须视为「无数据」返回 error，**不得伪装成 0.0 净流入**。
        """
        try:
            df = self.ak.stock_hsgt_hist_em(symbol="北向资金")
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error="无北向资金数据")
            match = df[df["日期"].astype(str) == date]
            if match.empty:
                return DataResult(
                    data=None, source=self.name, error=f"无北向资金数据: {date}"
                )
            row = match.iloc[0]
            net = _to_float_or_none(row.get("当日成交净买额"))  # 单位：亿元
            if net is None:  # 停更日 NaN → 无数据，不伪装成 0.0
                return DataResult(
                    data=None, source=self.name, error=f"无北向资金数据: {date}"
                )
            data = {
                "date": str(row.get("日期", "")),
                "net_buy_billion": round(net, 2),
            }
            return DataResult(data=data, source="akshare:stock_hsgt_hist_em")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 财经新闻 ----

    def get_market_news(self, date: str, limit: int = 15) -> DataResult:
        """
        采集财经快讯。
        使用 stock_news_em(symbol="快讯")，过滤 date 及前一自然日的内容。
        返回 list[dict]，每条含 title / summary / time / source。
        """
        from datetime import datetime, timedelta

        try:
            df = self.ak.stock_news_em(symbol="快讯")
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_news_em")

            # 统一列名（akshare 不同版本列名略有差异）
            col_map: dict[str, str] = {}
            for col in df.columns:
                lc = str(col)
                if "标题" in lc or "title" in lc.lower():
                    col_map["title"] = col
                elif "内容" in lc or "摘要" in lc or "content" in lc.lower():
                    col_map["summary"] = col
                elif "时间" in lc or "日期" in lc or "date" in lc.lower():
                    col_map["time"] = col
                elif "来源" in lc or "source" in lc.lower():
                    col_map["source"] = col

            target_date = datetime.strptime(date, "%Y-%m-%d").date()
            prev_date = target_date - timedelta(days=1)

            results: list[dict] = []
            for _, row in df.iterrows():
                raw_time = str(row.get(col_map.get("time", ""), ""))
                # 解析时间字符串，格式可能为 "2026-03-29 07:30:00" 或 "03-29 07:30"
                item_date = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M"):
                    try:
                        parsed = datetime.strptime(raw_time[:len(fmt) + 2].strip(), fmt)
                        if fmt.startswith("%m-"):
                            parsed = parsed.replace(year=target_date.year)
                        item_date = parsed.date()
                        break
                    except ValueError:
                        continue

                if item_date is None or not (prev_date <= item_date <= target_date):
                    continue  # 只取昨天和今天的新闻；时间戳无法解析或超出范围时丢弃

                title = str(row.get(col_map.get("title", ""), "")).strip()
                if not title:
                    continue
                results.append({
                    "title": title,
                    "summary": str(row.get(col_map.get("summary", ""), ""))[:200].strip(),
                    "time": raw_time,
                    "source": str(row.get(col_map.get("source", ""), "东方财富")).strip(),
                })
                if len(results) >= limit:
                    break

            return DataResult(data=results, source="akshare:stock_news_em")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 个股新闻 ----

    def get_stock_news(self, stock_code: str, date: str, limit: int = 10) -> DataResult:
        """按个股代码拉取东财新闻，复用 stock_news_em(symbol=代码)。"""
        from datetime import datetime, timedelta

        try:
            code_num = stock_code.split(".")[0]
            df = self.ak.stock_news_em(symbol=code_num)
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_news_em")

            col_map: dict[str, str] = {}
            for col in df.columns:
                lc = str(col)
                if "标题" in lc or "title" in lc.lower():
                    col_map["title"] = col
                elif "内容" in lc or "摘要" in lc or "content" in lc.lower():
                    col_map["summary"] = col
                elif "时间" in lc or "日期" in lc or "date" in lc.lower():
                    col_map["time"] = col
                elif "来源" in lc or "source" in lc.lower():
                    col_map["source"] = col

            target_date = datetime.strptime(date, "%Y-%m-%d").date()
            cutoff = target_date - timedelta(days=3)

            results: list[dict] = []
            for _, row in df.iterrows():
                raw_time = str(row.get(col_map.get("time", ""), ""))
                item_date = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m-%d %H:%M"):
                    try:
                        parsed = datetime.strptime(raw_time[:len(fmt) + 2].strip(), fmt)
                        if fmt.startswith("%m-"):
                            parsed = parsed.replace(year=target_date.year)
                        item_date = parsed.date()
                        break
                    except ValueError:
                        continue

                if item_date is None or not (cutoff <= item_date <= target_date):
                    continue

                title = str(row.get(col_map.get("title", ""), "")).strip()
                if not title:
                    continue
                results.append({
                    "title": title,
                    "summary": str(row.get(col_map.get("summary", ""), ""))[:200].strip(),
                    "time": raw_time,
                    "source": str(row.get(col_map.get("source", ""), "东方财富")).strip(),
                })
                if len(results) >= limit:
                    break

            return DataResult(data=results, source="akshare:stock_news_em")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 公告 ----

    def get_stock_announcements(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """个股公告：东方财富公告 API（np-anotice-stock）。"""
        try:
            code_num = stock_code.split(".")[0]
            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            params = {
                "sr": "-1",
                "page_size": "20",
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": code_num,
                "f_node": "0",
                "s_node": "0",
            }
            if start_date and end_date:
                sd = start_date.replace("-", "")
                ed = end_date.replace("-", "")
                params["begin_time"] = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
                params["end_time"] = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

            resp = requests.get(url, params=params, timeout=10,
                                headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json().get("data", {}) or {}
            items = data.get("list") or []

            results: list[dict] = []
            for item in items:
                ann_date = str(item.get("notice_date", ""))[:10]
                title = item.get("title", "")
                info_code = item.get("art_code", "")
                ann_url = (
                    f"https://data.eastmoney.com/notices/detail/{code_num}/{info_code}.html"
                    if info_code else ""
                )
                results.append({
                    "title": title,
                    "ann_date": ann_date,
                    "url": ann_url,
                })
                if len(results) >= 10:
                    break

            return DataResult(data=results, source="akshare:eastmoney_ann")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 互动易 ----

    _CNINFO_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

    def _fetch_irm_cninfo_raw(self, code_num: str) -> list[dict]:
        """直接调用巨潮 IRM API 获取原始问答行，绕过 akshare 空结果崩溃。"""
        import requests as _req

        headers = {"User-Agent": self._CNINFO_UA}

        org_url = "https://irm.cninfo.com.cn/newircs/index/queryKeyboardInfo"
        r = _req.post(
            org_url,
            params={"_t": "1691144074"},
            data={"keyWord": code_num},
            headers=headers,
            timeout=8,
        )
        r.raise_for_status()
        org_data = r.json()
        data_list = org_data.get("data")
        if not data_list or not isinstance(data_list, list) or len(data_list) == 0:
            return []
        org_id = data_list[0].get("secid")
        if not org_id:
            return []

        q_url = "https://irm.cninfo.com.cn/newircs/company/question"
        r2 = _req.post(
            q_url,
            params={
                "_t": "1691142650",
                "stockcode": code_num,
                "orgId": org_id,
                "pageSize": "30",
                "pageNum": "1",
                "keyWord": "",
                "startDay": "",
                "endDay": "",
            },
            headers=headers,
            timeout=8,
        )
        r2.raise_for_status()
        rows = r2.json().get("rows") or []
        return rows if isinstance(rows, list) else []

    def get_investor_qa(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """巨潮互动易问答：直接调用 cninfo API（绕过 akshare 空结果崩溃）。"""
        _SOURCE = "cninfo:irm_direct"
        try:
            code_num = stock_code.split(".")[0]
            rows = self._fetch_irm_cninfo_raw(code_num)
            if not rows:
                return DataResult(data=[], source=_SOURCE)

            from datetime import datetime, timezone, timedelta

            tz_cst = timezone(timedelta(hours=8))
            start_compact = start_date.replace("-", "")
            end_compact = end_date.replace("-", "")

            results: list[dict] = []
            for row in rows:
                question = (row.get("mainContent") or "").strip()
                if not question:
                    continue
                answer = (row.get("attachedContent") or "").strip()
                pub_ms = row.get("pubDate")
                date_str = ""
                if pub_ms:
                    try:
                        dt = datetime.fromtimestamp(int(pub_ms) / 1000, tz=tz_cst)
                    except (ValueError, TypeError, OSError):
                        pass
                    else:
                        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                        if not (start_compact <= dt.strftime("%Y%m%d") <= end_compact):
                            continue
                results.append({
                    "question": question[:300],
                    "answer": answer[:500] if answer else "",
                    "date": date_str,
                })
                if len(results) >= 10:
                    break
            return DataResult(data=results, source=_SOURCE)
        except Exception as e:
            return DataResult(data=None, source=_SOURCE, error=str(e))

    # ---- 研报评级 ----

    def get_research_reports(self, stock_code: str) -> DataResult:
        """个股研报：stock_research_report_em(symbol=代码)，来源东方财富。"""
        try:
            code_num = stock_code.split(".")[0]
            try:
                df = self.ak.stock_research_report_em(symbol=code_num)
            except (AttributeError, TypeError, ValueError, KeyError):
                # KeyError('infoCode')：零研报票 akshare 内部取空表的 infoCode 列抛错，
                # 属"无数据"而非"失败"——按空结果处理，避免冒充 error 触发 registry 误判降级。
                df = None

            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_research_report_em")

            results: list[dict] = []
            for _, row in df.iterrows():
                date_val = row.get("日期", "")
                date_str = str(date_val)[:10] if date_val else ""
                results.append({
                    "institution": str(row.get("机构", "")).strip(),
                    "rating": str(row.get("东财评级", "")).strip(),
                    "title": str(row.get("报告名称", "")).strip()[:120],
                    "target_price": 0,
                    "date": date_str,
                })
                if len(results) >= 5:
                    break

            return DataResult(data=results, source="akshare:stock_research_report_em")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_research_report_list(self, date: str) -> DataResult:
        """全市场当日研报评级（巨潮 stock_rank_forecast_cninfo）"""
        try:
            date_compact = date.replace("-", "")
            try:
                df = self.ak.stock_rank_forecast_cninfo(date=date_compact)
            except (TypeError, ValueError, KeyError):
                return DataResult(data=[], source="akshare:stock_rank_forecast_cninfo")
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_rank_forecast_cninfo")
            results = []
            for _, row in df.iterrows():
                code = str(row.get("证券代码", "")).strip()
                if not code:
                    continue
                results.append({
                    "stock_code": code,
                    "stock_name": str(row.get("证券简称", "")).strip(),
                    "institution": str(row.get("研究机构简称", "")).strip(),
                    "rating": str(row.get("投资评级", "")).strip(),
                    "date": str(row.get("发布日期", ""))[:10],
                    # v6 扩列（additive，原 5 基础键不变）：源头自带评级方向，直接复用不自算（F6）
                    "rating_change": str(row.get("评级变化", "")).strip(),
                    "prev_rating": str(row.get("前一次投资评级", "")).strip(),
                    # 目标价入 raw 供溯源；渲染层剔除不输出（红线：约束生成不约束取数）
                    "target_price_low": _to_float_or_none(row.get("目标价格-下限")),
                    "target_price_high": _to_float_or_none(row.get("目标价格-上限")),
                })
            return DataResult(data=results, source="akshare:stock_rank_forecast_cninfo")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    def get_us_rating_changes(self, tickers, date_window) -> DataResult:
        """美股分析师评级变动：yfinance upgrades_downgrades + 窗口/方向过滤（逐只循环）。

        date_window: (start, end)，'YYYY-MM-DD' 或 date，闭区间（美东日历，见 H2）。
        - 只取真方向变化（Action up/down/init/reinit），维持/重申丢弃（方向稀疏，F7）。
        - 陈旧陷阱：某标的最新评级远早于窗口（如 META 仅到 2024）→ 窗口过滤为空；
          距今 > _US_RATING_FROZEN_DAYS 时单独标"疑似冻结"日志（供精选池体检，H5）。
        - 单 ticker 拉取异常不中断其余（M9）。
        - target price 入 raw（current_pt/prior_pt）供溯源，渲染层剔除（红线）。
        """
        try:
            import yfinance as yf
        except ImportError as e:
            return DataResult(data=None, source=self.name, error=f"yfinance 不可用: {e}")
        if not date_window or len(date_window) != 2:
            return DataResult(data=None, source=self.name,
                              error=f"date_window 需要恰好 2 元素 (start, end)，得 {date_window!r}")
        win_start = _to_rating_date(date_window[0])
        win_end = _to_rating_date(date_window[1])
        if win_start is None or win_end is None:
            return DataResult(data=None, source=self.name, error=f"date_window 日期非法: {date_window!r}")
        out: list[dict] = []
        attempted = 0
        fetch_failures = 0
        for t in tickers or []:
            sym = str(t).strip().upper()
            if not sym:
                continue
            attempted += 1
            try:
                ud = yf.Ticker(sym).upgrades_downgrades
            except Exception as e:
                fetch_failures += 1
                logger.warning("get_us_rating_changes %s 拉取失败，跳过: %s", sym, e)
                continue
            events = _filter_us_rating_window(ud, win_start, win_end)
            if not events:
                recent = _most_recent_grade_date(ud)
                if recent is not None and (win_end - recent).days > _US_RATING_FROZEN_DAYS:
                    logger.info(
                        "get_us_rating_changes %s 疑似冻结（最新评级 %s，距窗口末 %d 天），建议池子体检剔除",
                        sym, recent, (win_end - recent).days,
                    )
                continue
            for ev in events:
                ev["ticker"] = sym
                ev["source_quality"] = "high"
                out.append(ev)
        # M-1：全部 ticker 抓取失败（如 yahoo 限流 429）≠「窗口内无变动」，返 error 让下游区分，
        # 不让"静默空列表"被当成"今日无评级变动"误报。
        if attempted > 0 and fetch_failures == attempted:
            return DataResult(data=None, source=self.name,
                              error=f"全部 {attempted} 只美股评级拉取失败（疑似 yahoo 限流/网络）")
        return DataResult(data=out, source="yfinance")

    # ---- 财报披露计划 ----

    def _ts_code_from_symbol(self, symbol: str) -> str:
        """6 位代码补交易所后缀，口径与 TushareProvider._normalize_stock_code 一致。"""
        digits = "".join(ch for ch in str(symbol or "") if ch.isdigit())[:6]
        if not digits:
            return str(symbol or "").strip().upper()
        if digits.startswith(("43", "82", "83", "87", "88", "89", "92")):
            return f"{digits}.BJ"
        if digits.startswith(("60", "68", "90", "51", "52", "53", "56", "58")):
            return f"{digits}.SH"
        return f"{digits}.SZ"

    def _quarter_end_for_date(self, date_str: str) -> str:
        """取目标日所在时点「最近一个已结束的报告期」末，与 TushareProvider 一致。"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if dt.month <= 3:
            return f"{dt.year - 1}1231"
        if dt.month <= 6:
            return f"{dt.year}0331"
        if dt.month <= 9:
            return f"{dt.year}0630"
        return f"{dt.year}0930"

    @staticmethod
    def _period_from_quarter_end(quarter_end: str) -> str:
        """季度末 YYYYMMDD → 巨潮 stock_report_disclosure 的 period 串。"""
        year, mmdd = quarter_end[:4], quarter_end[4:]
        return {
            "1231": f"{year}年报",
            "0331": f"{year}一季",
            "0630": f"{year}半年报",
            "0930": f"{year}三季",
        }.get(mmdd, f"{year}年报")

    @staticmethod
    def _fmt_cn_date(val) -> str:
        """巨潮日期单元（datetime.date / NaT / 字符串）规范化为 YYYYMMDD；空值返回空串。"""
        if val is None:
            return ""
        try:
            if pd.isna(val):
                return ""
        except (TypeError, ValueError):
            pass
        if hasattr(val, "strftime"):
            return val.strftime("%Y%m%d")
        return str(val).replace("-", "").replace("/", "")[:8]

    def get_disclosure_dates(self, date: str) -> DataResult:
        """财报披露计划降级：巨潮 stock_report_disclosure（cninfo，非东财，本机稳定）。

        输出字段对齐 TushareProvider.get_disclosure_dates，便于 registry 降级透明：
        code/ts_code、pre_date（首次预约）、actual_date（实际披露）、ann_date、
        report_end/end_date（报告期末 YYYYMMDD）。
        """
        try:
            quarter_end = self._quarter_end_for_date(date)
            period = self._period_from_quarter_end(quarter_end)
            df = self.ak.stock_report_disclosure(market="沪深京", period=period)
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_report_disclosure")
            records: list[dict] = []
            for _, row in df.iterrows():
                symbol = str(row.get("股票代码", "") or "").strip()
                if not symbol:
                    continue
                ts_code = self._ts_code_from_symbol(symbol)
                pre_date = self._fmt_cn_date(row.get("首次预约"))
                actual_date = self._fmt_cn_date(row.get("实际披露"))
                records.append({
                    "ts_code": ts_code,
                    "code": ts_code,
                    "name": str(row.get("股票简称", "") or "").strip(),
                    "pre_date": pre_date,
                    "ann_date": actual_date or pre_date,
                    "actual_date": actual_date,
                    "end_date": quarter_end,
                    "report_end": quarter_end,
                })
            return DataResult(data=records, source="akshare:stock_report_disclosure")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 宏观日历 ----

    def get_macro_calendar(self, date: str) -> DataResult:
        """
        采集当日宏观经济日历事件，使用百度股市通经济日历。
        news_economic_baidu(date=YYYYMMDD) 列：日期/时间/地区/事件/公布/预期/前值/重要性
        重要性：1=低 / 2=中 / 3=高
        """
        try:
            date_compact = date.replace("-", "")
            df = self.ak.news_economic_baidu(date=date_compact)
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:news_economic_baidu")

            importance_map = {"1": "低", "2": "中", "3": "高"}
            results: list[dict] = []
            for _, row in df.iterrows():
                imp_raw = str(row.get("重要性", "")).strip()
                importance = importance_map.get(imp_raw, imp_raw)
                actual_raw = row.get("公布", "")
                actual = "" if (actual_raw is None or (hasattr(actual_raw, '__class__') and str(type(actual_raw)) == "<class 'float'>")) else str(actual_raw).strip()
                expected_raw = row.get("预期", "")
                expected = "" if (expected_raw is None or str(expected_raw) == "nan") else str(expected_raw).strip()
                prior_raw = row.get("前值", "")
                prior = "" if (prior_raw is None or str(prior_raw) == "nan") else str(prior_raw).strip()
                results.append({
                    "event": str(row.get("事件", "")).strip(),
                    "time": str(row.get("时间", "")).strip(),
                    "region": str(row.get("地区", "")).strip(),
                    "importance": importance,
                    "actual": actual,
                    "expected": expected,
                    "prior": prior,
                })

            return DataResult(data=results, source="akshare:news_economic_baidu")
        except Exception as e:
            logger.warning(f"news_economic_baidu 失败: {e}，返回空列表")
            return DataResult(data=[], source=self.name)

    def get_macro_calendar_range(self, from_date: str, to_date: str) -> DataResult:
        """
        批量拉取 from_date ~ to_date 范围内的经济日历事件。
        每天调用一次 news_economic_baidu，结果合并后返回。
        每条记录附加 date 字段（YYYY-MM-DD）。
        """
        from datetime import date as _date, timedelta
        try:
            start = _date.fromisoformat(from_date)
            end = _date.fromisoformat(to_date)
        except ValueError as e:
            return DataResult(data=None, source=self.name, error=f"日期格式错误: {e}")

        all_events: list[dict] = []
        d = start
        while d <= end:
            date_str = d.isoformat()
            r = self.get_macro_calendar(date_str)
            if r.success and r.data:
                for ev in r.data:
                    all_events.append({"date": date_str, **ev})
            d += timedelta(days=1)

        return DataResult(data=all_events, source="akshare:news_economic_baidu_range")

    # ---- 宏观经济指标 ----

    _MACRO_SPECS: tuple[tuple[str, str, str], ...] = (
        ("pmi", "采购经理人指数 PMI", "macro_china_pmi"),
        ("cpi", "居民消费价格指数 CPI", "macro_china_cpi"),
        ("m2", "货币供应量 M2", "macro_china_money_supply"),
        ("lpr", "贷款市场报价利率 LPR", "macro_china_lpr"),
        ("shrzgm", "社会融资规模增量", "macro_china_shrzgm"),
    )

    @staticmethod
    def _macro_period_key(val: Any) -> str:
        """把周期标签归一为可比较的定宽数字键，兼容多种 akshare 周期格式。

        规则：提取数字组，首组（年）原样，其后各组（月/日）左补零到 2 位，最多取年+月+日。
        例：'2024年1月'→'202401'、'2024年10月'→'202410'、'202401'→'202401'、
        '2024-05-01 00:00:00'→'20240501'、'20240501'→'20240501'。
        同一指标格式一致时，归一后键等长，字典序即时间序；无数字则返回空串。
        """
        if val is None:
            return ""
        groups = re.findall(r"\d+", str(val))
        if not groups:
            return ""
        key = groups[0]
        for g in groups[1:3]:
            key += g.zfill(2)
        return key

    @staticmethod
    def _macro_clean_cell(val: Any) -> Any:
        """单元格归一化：NaN/None→None；数值→float；其余→去空白字符串。"""
        if val is None:
            return None
        try:
            if pd.isna(val):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return float(val)
        parsed = _to_float_or_none(val)
        if parsed is not None:
            return parsed
        return _to_clean_str(val)

    def _macro_indicator_payload(self, df: pd.DataFrame, periods: int) -> dict | None:
        """把单个宏观指标 DataFrame 归一化为 {period_col, latest, trend}。

        约定：首列为周期列（月份/日期）。akshare 不同接口行序不一致（部分最新在前、
        部分最旧在前），故先按周期列「数字提取」做时间升序排序，再取末尾 periods 行，
        确保 `latest` 始终是最近一期、`trend` 为最近 N 期升序。每行保留全部原始列
        （已清洗），并补充统一的 `period` 键便于下游渲染。
        """
        if df is None or getattr(df, "empty", True):
            return None
        cols = [str(c) for c in df.columns]
        if not cols:
            return None
        period_col = cols[0]
        n = max(1, int(periods or 1))
        first_col = df.columns[0]
        ordered = df
        # 按周期列归一键做时间升序，兼容 akshare 各接口的升/降序差异（eastmoney 多为降序、
        # mofcom 为字典序），确保 tail(n) 取到最近 N 期。仅当归一键等长（同一指标格式一致）
        # 才排序，避免非日期周期列误排；归一后等长可覆盖非零填充中文月份（2024年1月→202401）。
        try:
            keys = df[first_col].map(self._macro_period_key)
            nonempty_lens = {len(k) for k in keys if k}
            if len(nonempty_lens) == 1:
                ordered = df.assign(_macro_sort=keys).sort_values(
                    "_macro_sort", kind="stable"
                ).drop(columns=["_macro_sort"])
        except Exception as e:  # 排序异常不致命，退回原序
            logger.debug("宏观指标周期排序失败，退回原序: %s", e)
            ordered = df
        tail = ordered.tail(n)
        trend: list[dict] = []
        for _, raw in tail.iterrows():
            row: dict = {}
            for col in df.columns:
                # 周期列（首列，如月份/日期）保持字符串，避免 "202503" 被误转成数值
                if str(col) == period_col:
                    row[str(col)] = _to_clean_str(raw[col])
                else:
                    row[str(col)] = self._macro_clean_cell(raw[col])
            row["period"] = row.get(period_col)
            trend.append(row)
        if not trend:
            return None
        return {"period_col": period_col, "latest": trend[-1], "trend": trend}

    def get_macro_indicators(self, date: str = "", periods: int = 6) -> DataResult:
        """宏观经济指标（PMI/CPI/M2/LPR/社融），各取最近 periods 期及同比走势。

        `date` 仅作 as_of 语义占位（ingest 经 registry.call 透传 target_date），
        akshare 的 macro_china_* 均无参数。每个子指标独立 try，单一后端不可达
        不致整体失败；仅当 5 个全失败时返回 error。
        """
        if self.ak is None:
            return DataResult(data=None, source=self.name, error="akshare 未初始化")
        data: dict = {}
        errors: list[str] = []
        for key, label, func_name in self._MACRO_SPECS:
            try:
                func = getattr(self.ak, func_name, None)
                if func is None:
                    raise AttributeError(f"akshare 无接口 {func_name}")
                df = func()
                payload = self._macro_indicator_payload(df, periods)
                if payload is None:
                    data[key] = {"name": label, "source": func_name, "error": "无数据"}
                    errors.append(f"{key}: empty")
                else:
                    payload["name"] = label
                    payload["source"] = func_name
                    data[key] = payload
            except Exception as e:  # 单指标失败不影响其余
                logger.warning("宏观指标 %s(%s) 获取失败: %s", key, func_name, e)
                data[key] = {"name": label, "source": func_name, "error": str(e)}
                errors.append(f"{key}: {e}")
        if len(errors) == len(self._MACRO_SPECS):
            return DataResult(
                data=None,
                source=self.name,
                error="所有宏观指标获取失败: " + "; ".join(errors),
                timeliness=Timeliness.DELAYED,
            )
        return DataResult(
            data=data,
            source="akshare:macro_china_*",
            data_type=DataType.FACT,
            timeliness=Timeliness.DELAYED,
            note=("部分指标失败: " + "; ".join(errors)) if errors else "",
        )

    # ---- 市场宽度 ----

    def get_market_breadth(self, date: str) -> DataResult:
        """涨跌家数统计（东方财富全市场快照）"""
        try:
            df = self.ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return DataResult(data=None, source=self.name, error="无全市场快照数据")

            pct_col = None
            for col in df.columns:
                if "涨跌幅" in str(col):
                    pct_col = col
                    break
            if pct_col is None:
                return DataResult(data=None, source=self.name, error="未找到涨跌幅列")

            pcts = pd.to_numeric(df[pct_col], errors="coerce").dropna()
            advance = int((pcts > 0).sum())
            decline = int((pcts < 0).sum())
            flat = int((pcts == 0).sum())
            total = advance + decline + flat
            ratio = round(advance / decline, 2) if decline > 0 else float("inf")

            data = {
                "advance": advance,
                "decline": decline,
                "flat": flat,
                "total": total,
                "advance_ratio": ratio,
            }
            return DataResult(data=data, source="akshare:stock_zh_a_spot_em")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 交易日历 ----

    def is_trade_day(self, date: str) -> DataResult:
        """判断是否为交易日。

        tool_trade_date_hist_sina 的 trade_date 列是 datetime.date 对象，astype(str)
        得到带横杠的 '2026-05-26'；两侧统一去横杠归一化再比较，避免格式不一致导致
        所有日期恒为 False（原 bug：只去了入参一侧的横杠）。
        """
        try:
            trade_dates = self.ak.tool_trade_date_hist_sina()
            target = date.replace("-", "")
            cal = trade_dates["trade_date"].astype(str).str.replace("-", "", regex=False)
            is_open = bool((cal == target).any())
            return DataResult(data=is_open, source="akshare:trade_cal")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- ETF 净申购 ----

    # 跟踪的重点 ETF 列表（code → 名称）
    _ETF_WATCHLIST: dict[str, str] = {
        "510300": "沪深300ETF(华泰)",
        "159919": "沪深300ETF(嘉实)",
        "588000": "科创50ETF",
        "512880": "证券ETF",
    }

    def get_etf_flow(self, date: str) -> DataResult:
        """
        获取重点 ETF 的日度净申购数据。

        通过比较最近两个交易日的份额变化计算净申购量，
        正值表示净申购（机构买入），负值表示净赎回。
        """
        results: list[dict] = []
        errors: list[str] = []

        for code, name in self._ETF_WATCHLIST.items():
            try:
                df = self.ak.fund_etf_fund_info_em(fund=code)
                if df is None or df.empty:
                    errors.append(f"{code} 无数据")
                    continue

                date_col = None
                for col in ["净值日期", "日期", "date"]:
                    if col in df.columns:
                        date_col = col
                        break
                if date_col is None:
                    errors.append(f"{code} 无日期列")
                    continue

                df = df.sort_values(date_col)
                if len(df) < 2:
                    errors.append(f"{code} 历史数据不足两行")
                    continue

                share_col = None
                for col in ["累计净值", "份额", "基金份额(万份)"]:
                    if col in df.columns:
                        share_col = col
                        break

                size_col = None
                for col in ["资产净值", "基金规模(亿元)"]:
                    if col in df.columns:
                        size_col = col
                        break

                last_row = df.iloc[-1]
                prev_row = df.iloc[-2]

                entry: dict = {"code": code, "name": name}

                if share_col:
                    try:
                        last_shares = float(str(last_row[share_col]).replace(",", ""))
                        prev_shares = float(str(prev_row[share_col]).replace(",", ""))
                        entry["total_shares_billion"] = round(last_shares / 1e4, 4)
                        entry["shares_change_billion"] = round(
                            (last_shares - prev_shares) / 1e4, 4
                        )
                    except (ValueError, TypeError):
                        pass

                if size_col:
                    try:
                        size_val = str(last_row[size_col]).replace(",", "")
                        entry["fund_size_billion"] = round(float(size_val), 2)
                    except (ValueError, TypeError):
                        pass

                results.append(entry)
            except Exception as e:
                errors.append(f"{code} 异常: {e}")
                logger.debug(f"ETF {code} 数据获取失败: {e}")

        if not results and errors:
            return DataResult(
                data=None, source=self.name,
                error="; ".join(errors),
            )

        return DataResult(
            data=results,
            source="akshare:fund_etf_fund_info_em",
            note="; ".join(errors) if errors else "",
        )

    # ---- 港股指数 ----

    _HK_INDEX_MAP: dict[str, tuple[str, str]] = {
        "HSI": ("恒生指数", "hsi"),
        "HSTECH": ("恒生科技指数", "hstech"),
    }

    def get_hk_indices(self, date: str) -> DataResult:
        """
        获取港股恒生指数和恒生科技指数的日线数据。

        使用 akshare stock_hk_index_daily_em，按日期过滤取当日数据。
        """
        result: dict[str, dict] = {}
        errors: list[str] = []

        for symbol, (zh_name, key) in self._HK_INDEX_MAP.items():
            try:
                df = self.ak.stock_hk_index_daily_em(symbol=zh_name)
                if df is None or df.empty:
                    raise ValueError("无数据")

                date_col = None
                for col in ["日期", "date", "Date"]:
                    if col in df.columns:
                        date_col = col
                        break
                if date_col is None:
                    raise ValueError("无日期列")

                df[date_col] = pd.to_datetime(df[date_col]).dt.strftime("%Y-%m-%d")
                row = df[df[date_col] == date]

                if row.empty:
                    df_sorted = df.sort_values(date_col)
                    if df_sorted.empty:
                        raise ValueError("无历史数据")
                    row = df_sorted.tail(1)

                r = row.iloc[0]

                def _col(*names: str) -> float | None:
                    """命中列且可解析则返回数值；列不存在或解析失败返回 None（与真实 0.0 区分）。"""
                    for n in names:
                        if n in r.index:
                            try:
                                return round(float(r[n]), 2)
                            except (TypeError, ValueError):
                                pass
                    return None

                close = _col("收盘", "close", "Close", "最新价")
                open_ = _col("开盘", "open", "Open")
                high = _col("最高", "high", "High")
                low = _col("最低", "low", "Low")

                if close is None:
                    raise ValueError("无有效收盘价")

                change_pct: float | None = None
                for pct_col in ["涨跌幅", "change_pct", "涨跌额"]:
                    if pct_col in r.index:
                        try:
                            v = float(r[pct_col])
                            if abs(v) < 100:
                                change_pct = round(v, 2)
                                break
                        except (TypeError, ValueError):
                            pass

                result[key] = {
                    "code": symbol,
                    "name": zh_name,
                    "close": close,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "change_pct": change_pct,
                    "_source": "akshare:stock_hk_index_daily_em",
                }
            except Exception as e:
                fallback = self._hk_index_from_yfinance(symbol, date)
                if fallback is not None:
                    result[key] = fallback
                    continue
                errors.append(f"{symbol} 异常: {e}")
                logger.debug(f"港股指数 {symbol} 获取失败: {e}")

        if not result and errors:
            return DataResult(
                data=None, source=self.name,
                error="; ".join(errors),
            )

        source_set = {
            str(item.get("_source", "")).strip()
            for item in result.values()
            if isinstance(item, dict) and str(item.get("_source", "")).strip()
        }
        if not source_set:
            source = self.name
        elif len(source_set) == 1:
            source = next(iter(source_set))
        else:
            source = "mixed:" + ",".join(sorted(source_set))

        return DataResult(
            data=result,
            source=source,
            note="; ".join(errors) if errors else "",
        )
