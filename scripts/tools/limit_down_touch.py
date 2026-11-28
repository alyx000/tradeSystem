"""按既有研究归档的五项原始证据重算非ST跌停触价，仅生成独立日期报告。"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import pickle
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from analyzers import limit_down_touch as touch, verified_limit_down as daily
from services.board_break_intraday.store import lock
from services.dated_market_archive import read_source
from services.handwritten_review import valid_date
from services.volume_record.service import atomic_write


def replay(root, day):
    touch.closed_session(day, root / "data/trade.db")
    source = root / "data/reports/volume-buildup/runs" / day
    manifest = json.loads((source / "report.json").read_text())
    if manifest["date"] != day:
        raise ValueError("研究归档事实日错位")
    files = dict(quotes="cache/market.pkl", st="cache/stock_st.pkl", universe="universe.pkl",
                 limits=f"limits-{day}.pkl", suspended=f"suspensions-{day}.pkl")
    receipts, inputs = {}, {}
    for k, f in files.items():
        inputs[k], receipts[k] = read_source(source, manifest, day, f, daily.METHODS[k], touch.SOURCES[k])
    for k in ("universe", "st"):
        # stock_basic 只能说明取数日已经可见的名称，不能拿今天名称回填历史。
        captured = datetime.fromisoformat(receipts[k]["captured_at"]).astimezone(touch.TZ)
        if captured.date().isoformat() != day:
            raise ValueError("证券身份或ST来源缺少当日原始快照")
    aligned = daily.aligned(day, **inputs)
    checked = touch.calculate(day, aligned, daily.closed_list(day, aligned), touch.SOURCES)
    return checked, receipts


def report(day, block, requester, receipts, replayed_at):
    lines = [f"# {day} 非ST跌停触价", "", touch.summary(block), "",
             f"来源交易日 {day}。{touch.SCOPE}有效样本 {block['sample_count']} 只。{touch.NOTE}",
             "", "| 名称 | 代码 | 当日最低 | 当日最高 | 实际跌停价 | 收盘价 | 收盘状态 |",
             "|---|---|---:|---:|---:|---:|---|"]
    for row in block["stocks"]:
        name = row["name"].replace("|", "｜")
        lines.append(f"| {name} | {row['code']} | {row['low']:.2f} | {row['high']:.2f} | {row['down_limit']:.2f} | "
                     f"{row['close']:.2f} | {touch._detail(row)} |")
    lines += ["", "## 来源", "", f"原始行情事实日 {day}。本次回验于 {replayed_at}，请求者 {requester}。原始文件的 SHA-256 与取数回执保存在 JSON。", ""]
    for receipt in receipts.values():
        lines.append(f"- {receipt['source']}，取得于 {receipt['captured_at']}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=valid_date)
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args()
    if not args.input_by.strip():
        parser.error("请求者为空")
    try:
        block, receipts = replay(ROOT, args.date)
        block = touch.verified(block, args.date)
        if args.save:
            folder = ROOT / "data/reports/limit-down-touch"
            replayed_at = datetime.now(touch.TZ).isoformat()
            data = dict(input_by=args.input_by, mode="archived_sources", trade_date=args.date, replayed_at=replayed_at,
                        sources=receipts, limit_down_touch=block)
            with lock(folder, args.date):
                atomic_write(folder / (args.date + ".json"), json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
                atomic_write(folder / (args.date + ".md"), report(args.date, block, args.input_by, receipts, replayed_at))
        print(args.date, touch.summary(block))
    except (FileNotFoundError, KeyError, IndexError, TypeError, ValueError, OSError, EOFError, pickle.UnpicklingError) as exc:
        parser.exit(2, f"{args.date} 跌停触价未核验：{exc}。\n")


if __name__ == "__main__":
    main()
