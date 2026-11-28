"""盘中阈值规则登记、续期、下线及逐规则状态展示。"""
from __future__ import annotations

import json

from services.intraday_monitor.catalog import manage_rule
from services.intraday_monitor.health import DELIVERY, RESULTS


def register(commands):
    rule = commands.add_parser("rule", help="新增普通价位规则，或按 ID 续期、下线既有规则")
    actions = rule.add_subparsers(dest="rule_action", required=True)
    add = actions.add_parser("add", help="登记有期限的固定价提醒")
    add.add_argument("--code", required=True, help="完整代码，如 601398.SH")
    add.add_argument("--name", help="首次接入的股票必须给名称，已有标的可省略")
    add.add_argument("--direction", required=True, choices=("below", "above", "near"))
    add.add_argument("--threshold", type=float, required=True)
    add.add_argument("--inclusive", action="store_true", help="含等号，默认严格穿线；near 固定含边界")
    add.add_argument("--proximity-pct", type=float, help="near 必须指定容差百分数，如 1")
    add.add_argument("--start", help="可选起算日 YYYY-MM-DD；休市顺延，不能回填过去")
    add.add_argument("--rule-id", help="可选 custom- 开头的唯一 ID；默认自动生成")
    extend = actions.add_parser("extend", help="在有效期末追加交易日；已过期则重新起算")
    extend.add_argument("--expected-until", help="核对原截止日 YYYY-MM-DD，防止重复追加")
    disable = actions.add_parser("disable", help="下线但保留原规则与审计")
    for cmd in (add, extend):
        cmd.add_argument("--days", required=True, type=int, help="有效交易日数（1～250）")
    for cmd in (extend, disable):
        cmd.add_argument("--rule-id", required=True, help="从 status --all 取得完整规则 ID")
    for cmd in (add, extend, disable):
        cmd.add_argument("--input-by", required=True, help="本次规则变更请求者")
        cmd.add_argument("--dry-run", action="store_true", help="预览，不落盘、不推送")
        cmd.add_argument("--json", action="store_true")


def handle(args):
    fields = ("input_by", "rule_id", "code", "name", "direction", "threshold",
              "inclusive", "proximity_pct", "days", "start", "expected_until", "dry_run")
    result = manage_rule(args.rule_action, **{k: getattr(args, k) for k in fields if hasattr(args, k)})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["status"] == "error":
        print(result["error"])
    else:
        print(f"{'预览（未写入）' if args.dry_run else '已登记'}：{args.rule_action} "
              f"{result['rule_id']}｜{result['valid_from'] or '不限'} 至 {result['valid_until'] or '长期'}")
    return 1 if result["status"] == "error" else 0


def display(rows):
    print("名称 / 代码｜规则｜当天检查结果及缺口｜当天发送｜最近成功计算 / 最近有效行情（上海时间）｜有效至")
    for row in rows:
        day = row.get("today")
        if day:
            outcome = (f"{RESULTS.get(day.get('last_result'), '未核验')}；检查 {day['checks']} 次，"
                       f"行情缺 {day['quote_gaps']} 次，计算缺 {day['calculation_gaps']} 次")
            if day.get("last_reason"):
                outcome += f"（{day['last_reason']}）"
            delivery = f"{day['sent']} 次" + (
                f"；最近事件{DELIVERY.get(day['last_delivery'], '未核验')}" if day.get('last_delivery') else "")
        else:
            outcome, delivery = "无当日检查记录，缺口未统计", "未核验"
        last_quote = row.get("last_valid_quote_at") or row["last_quote_at"]
        threshold = row.get("threshold")
        condition = (f"{threshold:g}{row.get('unit', '')}" if threshold is not None
                     else row.get("threshold_label", ""))
        if row.get("direction") == "near":
            condition += f"±{row['proximity_pct']:g}%"
        lifecycle = "已下线；" if row.get("disabled") else ""
        print(f"{row['name']} / {row['code']}｜{row.get('action', '')}{condition}（{row['rule_id']}）｜{outcome}｜{delivery}｜"
              f"{row['last_success_at'] or '未核验'} / {last_quote or '未核验'}｜"
              f"{lifecycle}{row['valid_until'] or '长期'}")
