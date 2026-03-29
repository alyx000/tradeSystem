"""
历史板块数据回填脚本

用途：拉取过去 N 个交易日的行业/概念板块日涨跌数据，
      写入 daily/YYYY-MM-DD/post-market.yaml，
      供 SectorRhythmAnalyzer 进行有效的节奏分析。

说明：
    - 只拉「当前涨幅榜前 N 名」板块的历史，每日排名是在这 N 个子集内重排，非全市场名次。
    - 历史 K 无领涨股，top_stock 为空。

用法：
    cd scripts && python3 backfill_sectors.py
    python3 backfill_sectors.py --top 30 --days 10
    python3 backfill_sectors.py --no-concept
    python3 backfill_sectors.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import yaml

warnings.filterwarnings("ignore")

BASE_DIR = Path(__file__).resolve().parent.parent
DAILY_DIR = BASE_DIR / "daily"

# -----------------------------------------------------------------------
# AkShare 工具
# -----------------------------------------------------------------------

def get_akshare():
    try:
        import akshare as ak
        return ak
    except ImportError:
        print("错误：未安装 akshare，请先运行 pip install akshare")
        sys.exit(1)


def _norm_date(s) -> str:
    """统一为 YYYY-MM-DD。"""
    s = str(s).strip().replace("/", "-")[:10]
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def get_trade_dates(ak, start_date: str, end_date: str) -> list[str]:
    """返回 [start_date, end_date] 范围内的交易日列表（YYYY-MM-DD 格式）。"""
    import pandas as pd
    df = ak.tool_trade_date_hist_sina()
    df["d"] = pd.to_datetime(df["trade_date"], errors="coerce")
    s0, s1 = pd.Timestamp(start_date), pd.Timestamp(end_date)
    df = df[(df["d"] >= s0) & (df["d"] <= s1)].sort_values("d")
    return [x.strftime("%Y-%m-%d") for x in df["d"].dropna()]


def fetch_sector_names(ak, sector_type: str, top_n: int) -> list[str]:
    """获取当前行业/概念板块排名前 top_n 的板块名。"""
    print(f"  获取 {sector_type} 板块名称列表...", end=" ", flush=True)
    if sector_type == "industry":
        df = ak.stock_board_industry_name_em()
    else:
        df = ak.stock_board_concept_name_em()
    # 按涨跌幅降序取前 top_n，确保覆盖今日最活跃板块
    df_sorted = df.sort_values("涨跌幅", ascending=False)
    names = df_sorted["板块名称"].head(top_n).tolist()
    print(f"共 {len(names)} 个")
    return names


def fetch_sector_history(ak, name: str, sector_type: str,
                         start_date: str, end_date: str) -> list[dict]:
    """
    拉取某个板块的历史日K数据。
    返回: [{date, change_pct, volume_billion}, ...]
    """
    try:
        if sector_type == "industry":
            df = ak.stock_board_industry_hist_em(
                symbol=name, period="日k",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
        else:
            df = ak.stock_board_concept_hist_em(
                symbol=name, period="daily",
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )
        if df is None or df.empty:
            return []
        results = []
        for _, row in df.iterrows():
            results.append({
                "date": _norm_date(row["日期"]),
                "change_pct": float(row.get("涨跌幅", 0) or 0),
                # 成交额单位：元 → 亿
                "volume_billion": float(row.get("成交额", 0) or 0) / 1e8,
            })
        return results
    except Exception as e:
        return []


# -----------------------------------------------------------------------
# 数据写入
# -----------------------------------------------------------------------

def load_or_init_post_market(date: str) -> dict:
    """加载已有的 post-market.yaml，若不存在则返回最小结构。"""
    path = DAILY_DIR / date / "post-market.yaml"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data
    return {
        "date": date,
        "generated_at": datetime.now().isoformat(),
        "raw_data": {},
        "holdings_data": [],
    }


def save_post_market(date: str, data: dict) -> None:
    day_dir = DAILY_DIR / date
    day_dir.mkdir(parents=True, exist_ok=True)
    path = day_dir / "post-market.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)


def merge_sector_rankings(
    all_sector_history: dict[str, list[dict]],
    trade_dates: list[str],
    sector_type: str,
    dry_run: bool,
) -> None:
    """
    将各板块历史数据按日期重组 → 每天排名 → 写入 post-market.yaml。

    all_sector_history: {name: [{date, change_pct, volume_billion}, ...]}
    """
    yaml_key = f"sector_{sector_type}"

    for date in trade_dates:
        # 收集该日所有板块数据
        day_rows: list[dict] = []
        for name, records in all_sector_history.items():
            for rec in records:
                if _norm_date(rec["date"]) == date:
                    day_rows.append({
                        "name": name,
                        "change_pct": rec["change_pct"],
                        "volume_billion": rec["volume_billion"],
                        "top_stock": "",  # 历史接口无此字段
                    })
                    break

        if not day_rows:
            continue

        # 按涨跌幅降序排列（模拟当日排名），与采集器一致保留前30
        day_rows.sort(key=lambda x: x["change_pct"], reverse=True)
        day_rows = day_rows[:30]

        if dry_run:
            continue

        # 写入文件
        market_data = load_or_init_post_market(date)
        raw = market_data.setdefault("raw_data", {})
        raw["date"] = date
        raw[yaml_key] = {
            "_source": "akshare:backfill",
            "data": day_rows,
        }
        market_data["raw_data"] = raw
        market_data["date"] = date
        save_post_market(date, market_data)


# -----------------------------------------------------------------------
# 分析结果打印
# -----------------------------------------------------------------------

def print_rhythm_results(results: list[dict], sector_type: str) -> None:
    label = "行业" if sector_type == "industry" else "概念"
    print(f"\n{'='*80}")
    print(f"  {label}板块节奏分析结果")
    print(f"{'='*80}")
    header = f"{'板块名':15s} {'今日排名':8s} {'连续上榜':8s} {'5日累计':8s} {'阶段':8s} {'置信度':6s} 关键信号"
    print(header)
    print("-" * 100)
    for r in results:
        rank = f"#{r['rank_today']}" if r.get("rank_today") else "-"
        consec = f"{r.get('consecutive_in_top30', 0)}天"
        cumul = r.get("cumulative_pct_5d", 0) or 0
        cumul_str = f"+{cumul:.1f}%" if cumul >= 0 else f"{cumul:.1f}%"
        phase = r.get("phase", "?")
        conf = r.get("confidence", "?")
        ev = r.get("evidence", [])
        signal_str = ev[0] if ev else "-"
        print(f"{r['name']:15s} {rank:8s} {consec:8s} {cumul_str:8s} {phase:8s} {conf:6s} {signal_str}")


# -----------------------------------------------------------------------
# 主流程
# -----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="回填历史板块排名数据")
    parser.add_argument("--top", type=int, default=50,
                        help="每种板块类型拉前 N 名（默认50）")
    parser.add_argument("--days", type=int, default=20,
                        help="回填历史天数（默认20）")
    parser.add_argument("--no-concept", action="store_true",
                        help="跳过概念板块（速度提升一倍）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印分析结果，不写入文件")
    args = parser.parse_args()

    ak = get_akshare()

    # 计算日期范围
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=args.days * 2)).strftime("%Y-%m-%d")
    print(f"\n回填范围: {start_date} ~ {end_date}，最多 {args.days} 个交易日")

    # 获取交易日列表
    print("获取交易日历...", end=" ", flush=True)
    trade_dates = get_trade_dates(ak, start_date, end_date)
    trade_dates = trade_dates[-args.days:]
    print(f"共 {len(trade_dates)} 个交易日: {trade_dates[0]} ~ {trade_dates[-1]}")

    sector_types = ["industry"] if args.no_concept else ["industry", "concept"]

    for sector_type in sector_types:
        label = "行业" if sector_type == "industry" else "概念"
        print(f"\n--- {label}板块 (前{args.top}个) ---")

        # 获取板块名列表
        names = fetch_sector_names(ak, sector_type, args.top)

        # 拉取历史数据
        all_history: dict[str, list[dict]] = {}
        failed = []
        for i, name in enumerate(names):
            print(f"  [{i+1:3d}/{len(names)}] {name:20s}", end="", flush=True)
            t0 = time.time()
            records = fetch_sector_history(ak, name, sector_type, trade_dates[0], trade_dates[-1])
            elapsed = time.time() - t0
            if records:
                all_history[name] = records
                dates_got = [r["date"] for r in records]
                print(f" → {len(records)} 天 ({elapsed:.1f}s)")
            else:
                failed.append(name)
                print(f" → 失败 ({elapsed:.1f}s)")
            # 避免请求过于密集
            time.sleep(0.3)

        if failed:
            print(f"  失败板块 ({len(failed)} 个): {', '.join(failed[:10])}")

        # 写入文件
        if not args.dry_run:
            print(f"\n写入 daily/ 目录...", end=" ", flush=True)
            merge_sector_rankings(all_history, trade_dates, sector_type, dry_run=False)
            print("完成")

        # 运行节奏分析并打印
        print("\n运行节奏分析...")
        sys.path.insert(0, str(Path(__file__).parent))
        from analyzers.sector_rhythm import SectorRhythmAnalyzer

        analyzer = SectorRhythmAnalyzer(BASE_DIR, history_days=args.days)

        # 构造 today_raw_data（用 all_history 中最后一天的数据）
        last_date = trade_dates[-1]
        today_sector_rows = []
        for name, records in all_history.items():
            for rec in records:
                if _norm_date(rec["date"]) == last_date:
                    today_sector_rows.append({
                        "name": name,
                        "change_pct": rec["change_pct"],
                        "volume_billion": rec["volume_billion"],
                        "top_stock": "",
                    })
                    break
        today_sector_rows.sort(key=lambda x: x["change_pct"], reverse=True)

        today_raw = {
            "date": last_date,
            f"sector_{sector_type}": {"data": today_sector_rows},
        }

        # 读取 main-theme 额外追踪板块
        extra_names = analyzer.load_main_theme_names()
        results = analyzer.analyze(today_raw, sector_type, extra_names=extra_names)
        print_rhythm_results(results, sector_type)

    if not args.dry_run:
        print(f"\n数据已写入 {DAILY_DIR}/")
        print("后续每天运行 `python3 main.py post` 后，节奏分析会自动累积并更新。\n")


if __name__ == "__main__":
    main()
