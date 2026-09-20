"""历史天量采集 CLI；默认落本地并推送钉钉。"""
from datetime import datetime
import json
import logging
from zoneinfo import ZoneInfo

from services.volume_record import service, notification
from utils.network_env import without_standard_http_proxy


def register_subparser(subparsers):
    parser = subparsers.add_parser("volume-record", help="上市以来成交量与成交额新纪录采集")
    sub = parser.add_subparsers(dest="volume_record_command", required=True)
    daily = sub.add_parser("daily", help="全市场采集、归档并推送天量报告")
    daily.add_argument("--no-push", action="store_true", help="只采集和归档，不推送")
    daily.add_argument("--date", default=None)
    daily.add_argument("--input-by", required=True)
    daily.add_argument("--dry-run", action="store_true", help="不写基线或报告")
    daily.add_argument("--codes", nargs="+", help="仅dry-run可用的抽样完整代码")
    daily.add_argument("--json", action="store_true")
    daily.add_argument("--metric", choices=("both", "volume", "amount"), default="both",
                       help="默认同时采集成交量和成交额；可单独补采一个口径")

    push = sub.add_parser("push", help="推送已归档报告，不重新采集；相同内容不重复发送")
    push.add_argument("--date", required=True)
    push.add_argument("--input-by", required=True)
    push.add_argument("--metric", choices=("both", "volume", "amount"), default="both")
    push.add_argument("--json", action="store_true")


def handle_command(config, args):
    if args.volume_record_command == "push":
        receipt = notification.push_saved(args.date, args.input_by, metric=args.metric)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        if not receipt["sent"]:
            raise SystemExit(2)
        return
    if args.codes and not args.dry_run:
        raise SystemExit("--codes仅允许与--dry-run一起使用")
    from main import setup_providers
    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()
        metric = getattr(args, "metric", "both")
        runner = service.run_dual if metric == "both" else service.run
        options = {} if metric == "both" else {"metric": metric}
        result = runner(args.date or datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                        args.input_by, registry=registry, dry_run=args.dry_run, codes=args.codes, **options)
    delivery = notification.push_result(result, args.input_by, metric=metric,
                                        enabled=not args.dry_run and not getattr(args, "no_push", False))
    print(json.dumps({"collection": result, "delivery": delivery}, ensure_ascii=False, indent=2)
          if args.json else service.render(result) + f"\n钉钉推送：{delivery['status']}\n")
    if result["status"] not in ("complete", "skipped") or delivery['status'] in ('failed', 'blocked'):
        raise SystemExit(2)


def run_for_post(target_date, registry):
    result = service.run_dual(target_date, "system_post", registry=registry)
    delivery = notification.push_result(result, "system_post")
    log = logging.getLogger(__name__)
    if delivery['status'] in ('failed', 'blocked'):
        log.warning("历史天量钉钉推送未完成：%s", delivery)
    else:
        log.info("历史天量钉钉推送：%s", delivery['status'])
    return result
