#!/usr/bin/env python3
"""单独补采板块风险本地报告；不重跑post，不写业务库、不推送。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts"))

from services.sector_adjustment_risk.collector import collect
from services.sector_adjustment_risk.renderer import render, validate


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".sector-risk-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(payload, output_dir):
    day = payload["date"]
    validate(payload, day)
    directory = Path(output_dir)
    path = directory/f"{day}.json"
    receipt = "saved"
    try:
        previous = validate(json.loads(path.read_text()), day)
    except (OSError, ValueError, KeyError, TypeError):
        previous = None
    def coverage(p):
        return ({r["code"] for r in p["rows"] if r["status"] != "source_failed"},
                {r["code"] for r in p["rows"] if r.get("minute_status") == "complete"})
    old_daily, old_minute = coverage(previous) if previous else (set(), set())
    new_daily, new_minute = coverage(payload)
    # 必须保住具体板块；同数量A/B换成B/C、日线增加但分钟减少都算退化。
    # 集合相等仍允许刷新同日修正行情；无需凭新增板块才更新证据。
    if previous and (not old_daily <= new_daily or not old_minute <= new_minute):
        attempt = {**payload, "coverage_change": {
            "lost_daily": sorted(old_daily-new_daily), "added_daily": sorted(new_daily-old_daily),
            "lost_minute": sorted(old_minute-new_minute), "added_minute": sorted(new_minute-old_minute)}}
        atomic(directory/f"{day}.attempt.json", json.dumps(attempt, ensure_ascii=False, indent=2, allow_nan=False))
        payload, receipt = previous, "fallback_preserved"
    else:
        atomic(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    fragment, _ = render(payload, day)
    html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>活跃板块调整风险 · '+day+'</title><style>body{font:15px/1.65 system-ui,sans-serif;margin:24px auto;padding:0 16px;max-width:1200px;color:#172b36;background:#f7f8f8}h1{font-size:24px}h3{font-size:19px}details{padding:14px;border:1px solid #cbd5da;background:white}summary{cursor:pointer;font-weight:600}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:850px;font-size:13px}td,th{text-align:left;padding:9px;border-bottom:1px solid #ddd;vertical-align:top}th{background:#edf2f4}</style>'
            '<body><h1>'+escape(day)+' 活跃板块调整风险</h1><p>复盘②板块模块预览 · 数据状态：'+escape(payload['status'])+'</p>'+fragment+'</body></html>')
    atomic(directory/f"{day}.html", html)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--input-by", required=True)
    parser.add_argument("--output-dir", type=Path, default=ROOT/"data/reports/sector-adjustment-risk")
    args = parser.parse_args(argv)
    if not args.input_by.strip():
        parser.error("--input-by 不得为空")
    from main import load_config, setup_providers
    from utils.network_env import without_standard_http_proxy
    with without_standard_http_proxy():
        registry = setup_providers(load_config())
        provider = registry.get_provider("tushare")
        if provider is not None:
            provider.initialize()
        payload = collect(registry, args.date)
    payload["input_by"] = args.input_by
    receipt = save(payload, args.output_dir)
    print(json.dumps({"date": args.date, "status": payload["status"], "receipt": receipt,
                      "coverage": payload.get("coverage"), "json": str(args.output_dir/f"{args.date}.json"),
                      "attempt_json": str(args.output_dir/f"{args.date}.attempt.json") if receipt == "fallback_preserved" else None,
                      "html": str(args.output_dir/f"{args.date}.html")}, ensure_ascii=False))
    return 1 if payload["status"] == "source_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
