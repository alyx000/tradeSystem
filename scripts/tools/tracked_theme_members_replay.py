"""从两个事实日的已存归档复核重点题材；只向独立回验报告目录导出。"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from analyzers.strong_stock_feedback_history import _envelope
from analyzers.tracked_theme_members import collect
from services.handwritten_review import valid_date


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=valid_date)
    parser.add_argument("--input-by", required=True, help="回验请求者")
    parser.add_argument("--save", action="store_true", help="原子保存独立日期回验，不修改原盘后信封")
    parser.add_argument("--json", action="store_true", help="输出含来源与名单的完整 JSON")
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error("--input-by 不得为空")
    root = SCRIPTS.parent
    daily = root / "daily"
    envelope = _envelope(daily, args.date, {})
    if not isinstance(envelope, dict):
        parser.error("该事实日盘后归档缺失或损坏")
    raw = envelope["raw_data"]
    block = collect(None, args.date, raw, daily_dir=daily, db_path=root / "data/trade.db")
    result = dict(mode="archive_only_replay", input_by=args.input_by, source=str(daily / args.date / "post-market.yaml"),
                  original_generated_at=raw.get("generated_at") or envelope.get("generated_at"), result=block)
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.save:
        path = root / "data/reports/tracked-theme-members" / (args.date + ".json")
        if path.exists() and block["status"] == "source_failed":
            parser.error("本次来源失败，保留已有回验报告")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as tmp:
            tmp.write(output)
        try:
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)
        print(path, file=sys.stderr)
    if args.json:
        print(output, end="")
    else:
        print(args.date, block["status"], "已核验成员", sum(r["status"] == "complete" for r in block["rows"]), "个题材")
        for row in block["rows"]:
            print(row["name"], row["code"] or "身份待核", row["count"] if row.get("count") is not None else row["reason"])
        for gap in block["gaps"]:
            print(gap)
    return 1 if block["status"] == "source_failed" else 0


if __name__ == "__main__":
    sys.exit(main())
