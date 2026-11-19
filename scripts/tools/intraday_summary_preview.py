"""从只读报告归档生成本地预览，不采集、不推送、不修改运行状态。"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.intraday_summary.archive import parse_report, read_previous_report
from services.intraday_summary.preview import render_compact_preview
from services.intraday_summary.html_report import render_html_report
from services.intraday_summary.state import DEFAULT_REPORT_ROOT, atomic_write_text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--slot", default="15:00", choices=["10:00", "10:30", "11:00", "11:30", "13:30", "14:00", "14:30", "15:00"])
    parser.add_argument("--input-by", required=True)
    args = parser.parse_args()
    if not args.input_by.strip():
        parser.error("--input-by 不得为空")

    day = args.date.isoformat()
    source = DEFAULT_REPORT_ROOT / day / f"{args.slot.replace(':', '')}.md"
    source_bytes = source.read_bytes()
    payload = parse_report(source_bytes.decode("utf-8"), day=day, slot=args.slot)
    if payload["status"] != "complete":
        parser.error("旧版归档非完整状态，无法无损恢复全部缺口；请保留原报告")
    previous = read_previous_report(DEFAULT_REPORT_ROOT, day=day, slot=payload["previous_slot"])
    previous_path = source.with_name(f"{payload['previous_slot'].replace(':', '')}.md")
    evidence = [f"- 请求者：{args.input_by}", "- 生成方式：只读归档再排版；未重新取数、未推送。", f"- 本期来源：{source}", f"- 本期 SHA256：{hashlib.sha256(source_bytes).hexdigest()}"]
    if previous_path.exists():
        evidence += [f"- 上一期来源：{previous_path}", f"- 上一期 SHA256：{hashlib.sha256(previous_path.read_bytes()).hexdigest()}"]
    report = render_compact_preview(payload, previous, "\n".join(evidence))
    output = source.parent / "preview" / f"{source.stem}-v3"
    document = render_html_report(report, preview=True)
    atomic_write_text(output.with_suffix(".md"), report)
    atomic_write_text(output.with_suffix(".html"), document)
    print(output.with_suffix(".html"))
    print(output.with_suffix(".md"))


if __name__ == "__main__":
    main()
