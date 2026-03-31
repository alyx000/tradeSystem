"""
AkShare 数据提供者（免费，作为 tushare 的补充和降级）
文档: https://akshare.akfamily.xyz/

盘前外盘/大宗/汇率：优先使用东方财富全球指数现货 index_global_spot_em、
全球期货现货 futures_global_spot_em；已移除失效的 index_investing_global。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

import requests

from .base import DataProvider, DataResult

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
            "get_stock_news",
            "get_stock_announcements",
            "get_investor_qa",
            "get_research_reports",
            "get_macro_calendar",
            "get_macro_calendar_range",
            "is_trade_day",
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
                # 先尝试 index_global_spot_em
                df = self._global_spot_em()
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
                # 回退：bond_zh_us_rate
                try:
                    df_bond = self.ak.bond_zh_us_rate(start_date="20200101")
                    if df_bond is not None and not df_bond.empty:
                        df_bond = df_bond.sort_values(df_bond.columns[0])
                        # 找 10 年期列
                        col_10y = next(
                            (c for c in df_bond.columns if "10" in str(c)), None
                        )
                        if col_10y:
                            row = df_bond.iloc[-1]
                            prev_row = df_bond.iloc[-2] if len(df_bond) >= 2 else row
                            close_val = _to_float_price(row[col_10y])
                            prev_val = _to_float_price(prev_row[col_10y])
                            change_bps = round((close_val - prev_val) * 100, 2)
                            return DataResult(
                                data={
                                    "name": "美债10年期收益率",
                                    "close": close_val,
                                    "change_bps": change_bps,
                                },
                                source="akshare:bond_zh_us_rate",
                            )
                except Exception:
                    pass
                return DataResult(
                    data=None, source=self.name, error="AkShare: 未找到美债10年期数据"
                )

            # 亚太股指：优先 yfinance，避免本机访问东财 push2.eastmoney.com 触发 ProxyError
            apac_yf: dict[str, list[tuple[str, str]]] = {
                "hsi": [("^HSI", "恒生指数")],
                # ^HSTECH 在 Yahoo 常 404；HSTECH.HK 为恒生科技指数现货报价
                "hstech": [("HSTECH.HK", "恒生科技"), ("3033.HK", "恒生科技(ETF)")],
                "nikkei": [("^N225", "日经225")],
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
        """美股 ETF 隔夜涨跌：yfinance 最近两个交易日收盘推算涨跌幅（与 VIX 一致）。"""
        labels = {
            "KWEB": "KWEB（中概互联网ETF）",
            "FXI": "FXI（中国大盘ETF）",
        }
        try:
            import yfinance as yf
        except ImportError as e:
            return DataResult(data=None, source=self.name, error=f"yfinance 不可用: {e}")

        out: dict[str, dict] = {}
        try:
            for t in tickers:
                sym = str(t).strip().upper()
                hist = yf.Ticker(sym).history(period="5d")
                if hist is None or hist.empty or "Close" not in hist.columns:
                    out[sym] = {"error": "无K线数据", "name": labels.get(sym, sym)}
                    continue
                hist = hist.sort_index()
                close_val = float(hist["Close"].iloc[-1])
                prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else close_val
                change_pct = (
                    round((close_val - prev_close) / prev_close * 100, 2) if prev_close else 0.0
                )
                out[sym] = {
                    "name": labels.get(sym, sym),
                    "close": round(close_val, 2),
                    "change_pct": change_pct,
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
        try:
            df = self.ak.stock_sector_fund_flow_rank(indicator="今日")
            if df.empty:
                return DataResult(data=None, source=self.name, error="无资金流向数据")
            records = []
            for _, row in df.head(20).iterrows():
                records.append({
                    "name": row.get("名称", ""),
                    "net_inflow_billion": float(row.get("主力净流入-净额", 0)) / 1e8,
                    "change_pct": float(row.get("涨跌幅", 0)),
                })
            return DataResult(data=records, source="akshare:sector_fund_flow")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 涨跌停数据 ----

    def get_limit_up_list(self, date: str) -> DataResult:
        """获取涨停股列表"""
        try:
            df = self.ak.stock_zt_pool_em(date=date.replace("-", ""))
            if df.empty:
                return DataResult(data=None, source=self.name, error=f"无涨停数据: {date}")
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
                    "seal_amount": float(row.get("封单额", 0)),
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
        """获取北向资金"""
        try:
            df = self.ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
            if df.empty:
                return DataResult(data=None, source=self.name, error="无北向资金数据")
            # 取最近一行
            row = df.iloc[-1]
            data = {
                "date": str(row.get("日期", "")),
                "net_buy_billion": float(row.get("当日资金流入", 0)) / 1e4,
            }
            return DataResult(data=data, source="akshare:northbound")
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

    def get_investor_qa(self, stock_code: str, start_date: str, end_date: str) -> DataResult:
        """巨潮互动易问答：stock_irm_cninfo(symbol=代码)。"""
        try:
            code_num = stock_code.split(".")[0]
            df = self.ak.stock_irm_cninfo(symbol=code_num)
            if df is None or df.empty:
                return DataResult(data=[], source="akshare:stock_irm_cninfo")

            cols = set(df.columns)
            # 优先使用已知精确列名，退而采用模糊匹配（避免"提问者""问题编号"等覆盖正确映射）
            q_col = "问题" if "问题" in cols else next(
                (c for c in df.columns if "问题" in str(c) and "编号" not in str(c) and "者" not in str(c)), None
            )
            a_col = "回答内容" if "回答内容" in cols else next(
                (c for c in df.columns if "回答内容" in str(c)), None
            )
            date_col = "提问时间" if "提问时间" in cols else next(
                (c for c in df.columns if "提问时间" in str(c) or "发布时间" in str(c)), None
            )

            if not q_col:
                return DataResult(data=[], source="akshare:stock_irm_cninfo")

            start_compact = start_date.replace("-", "")
            end_compact = end_date.replace("-", "")

            results: list[dict] = []
            for _, row in df.iterrows():
                raw_date = str(row[date_col]).strip() if date_col else ""
                date_compact = raw_date.replace("-", "").replace(" ", "")[:8]
                if date_compact and not (start_compact <= date_compact <= end_compact):
                    continue

                question = str(row[q_col]).strip() if q_col else ""
                answer = str(row[a_col]).strip() if a_col else ""
                if not question or question in ("nan", "None"):
                    continue
                results.append({
                    "question": question[:300],
                    "answer": answer[:500] if answer not in ("nan", "None") else "",
                    "date": raw_date[:19],
                })
                if len(results) >= 10:
                    break

            return DataResult(data=results, source="akshare:stock_irm_cninfo")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))

    # ---- 研报评级 ----

    def get_research_reports(self, stock_code: str) -> DataResult:
        """个股研报：stock_research_report_em(symbol=代码)，来源东方财富。"""
        try:
            code_num = stock_code.split(".")[0]
            try:
                df = self.ak.stock_research_report_em(symbol=code_num)
            except (AttributeError, TypeError, ValueError):
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
        """判断是否为交易日"""
        try:
            trade_dates = self.ak.tool_trade_date_hist_sina()
            target = date.replace("-", "")
            is_open = target in trade_dates["trade_date"].astype(str).values
            return DataResult(data=is_open, source="akshare:trade_cal")
        except Exception as e:
            return DataResult(data=None, source=self.name, error=str(e))
