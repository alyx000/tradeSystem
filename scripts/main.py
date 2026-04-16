#!/usr/bin/env python3
"""
交易系统主入口
支持命令行手动运行和定时任务调度

用法:
    # 运行盘前简报
    python main.py pre

    # 运行盘后报告（会先执行晚间任务：溢价回填、关注池、复盘 Obsidian，再生成全日盘后数据）
    python main.py post

    # 指定日期
    python main.py post --date 2026-03-28

    # 仅重跑晚间任务（定时任务请用 post，无需单独跑 evening）
    python main.py evening

    # 关注池 / Obsidian 导出（也可通过 post、evening 间接执行）
    python main.py watchlist
    python main.py obsidian [--sync-all]

    # 异动监管（写入 SQLite stock_regulatory_monitor；post 在 config 中启用 regulatory_monitor 时也会跑）
    python main.py regulatory [--date YYYY-MM-DD]
    python main.py regulatory --query [--date YYYY-MM-DD] [--type 1|2|all] [--json]  # 仅查库

    # 启动定时调度器（工作日 07:00 pre，20:00 post；post 已含 evening）
    python main.py schedule

    # 检查数据源连通性
    python main.py check

    # 采集阶段默认会临时清除 HTTP_PROXY/HTTPS_PROXY，避免误走本机代理导致 Tushare 超时。
    # 若你必须让采集也走代理：export TRADESYSTEM_USE_HTTP_PROXY=1

    # 预拉取未来 N 天宏观日历到 tracking/calendar_auto.yaml（供盘前合并）
    python main.py prefetch-calendar [--days 14] [--from YYYY-MM-DD]

    # 持仓（SQLite；CODE / NAME 等为占位符，请替换为实际证券代码、简称等）
    python main.py db holdings-add --code CODE --name NAME --shares N --price P --sector SECTOR
    python main.py db holdings-remove --code CODE
    python main.py db holdings-list
    python main.py db holdings-refresh --date YYYY-MM-DD
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

# 项目根目录与脚本目录（数据采集、分析等包均位于 scripts/ 下）
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent

# 确保无论从何处启动（如项目根目录执行 python -m scripts.main），均能解析
# providers、collectors、analyzers、generators 等包
_scripts = str(SCRIPT_DIR)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)

# 加载 .env
load_dotenv(SCRIPT_DIR / ".env")

from utils.network_env import without_standard_http_proxy
from services.holding_signals import build_holding_signals

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(SCRIPT_DIR / "trade_system.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


def _schedule_task_enabled(config: dict, section: str, task_name: str) -> bool:
    try:
        tasks = (config.get("schedule") or {}).get(section) or {}
        lst = tasks.get("tasks") or []
        return task_name in lst
    except Exception:
        return False


def _emit_cli_result(payload: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = payload.get("status", "ok")
    message = payload.get("message", "")
    print(f"[{status}] {message}".strip())
    for key, value in payload.items():
        if key in {"status", "message"}:
            continue
        print(f"  {key}: {value}")


def cmd_ingest(config: dict, args) -> None:
    """采集底座命令。第二阶段先接通注册表与审计读取。"""
    from services.ingest_service import IngestService

    registry = None
    if args.ingest_command in {"run", "run-interface"}:
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()

    service = IngestService(registry=registry)
    command = args.ingest_command
    if command == "list-interfaces":
        payload = {
            "status": "ok",
            "message": "接口注册表",
            "interfaces": service.list_interfaces(),
        }
    elif command == "inspect":
        inspect = service.inspect(
            args.date,
            interface_name=getattr(args, "interface", None),
            stage=getattr(args, "stage", None),
        )
        health = service.health_summary(
            end_date=args.date,
            days=7,
            stage=getattr(args, "stage", None),
            interface_name=getattr(args, "interface", None),
        )
        payload = {
            "status": "ok",
            "message": f"{args.date} 的采集审计",
            "status_label": health.get("status_label"),
            "status_reason": health.get("status_reason"),
            "health": health,
            **inspect,
        }
    elif command == "retry":
        retry = service.retry_summary(
            interface_name=getattr(args, "interface", None),
            stage=getattr(args, "stage", None),
        )
        payload = {
            "status": "ok",
            "message": "可重试错误摘要",
            **retry,
        }
    elif command == "health":
        health = service.health_summary(
            end_date=args.date,
            days=args.days,
            limit=args.limit,
            stage=getattr(args, "stage", None),
        )
        payload = {
            "status": "ok",
            "message": "采集健康摘要",
            **health,
        }
    elif command == "reconcile":
        summary = service.reconcile_stale_runs(stale_minutes=args.stale_minutes)
        payload = {
            "status": "ok",
            "message": "已完成陈旧 running 采集记录清理",
            **summary,
        }
    elif command == "run":
        payload = service.execute_stage(args.stage, args.date, triggered_by="cli", input_by=args.input_by)
    elif command == "run-interface":
        payload = service.execute_interface(args.name, args.date, triggered_by="cli", input_by=args.input_by)
    else:
        payload = {
            "status": "validation_error",
            "message": f"未知 ingest 子命令: {command}",
        }

    payload.setdefault("subcommand", command)
    payload.setdefault("blueprint", str(BASE_DIR / "docs" / "architecture" / "tradesystem-blueprint.md"))
    _emit_cli_result(payload, as_json=getattr(args, "json", False))


def cmd_plan(config: dict, args) -> None:
    """交易计划命令。第三阶段先接通最小 PlanningService。"""
    from services.planning_service import PlanningService

    registry = None
    if args.plan_command == "diagnose" or (args.plan_command == "draft" and getattr(args, "from_review", False)):
        with without_standard_http_proxy():
            registry = setup_providers(config)
            registry.initialize_all()

    service = PlanningService(registry=registry)
    command = args.plan_command
    if command == "draft":
        if getattr(args, "from_review", False):
            try:
                draft_payload = service.draft_from_review(
                    review_date=args.date,
                    trade_date=getattr(args, "trade_date", None),
                    input_by=getattr(args, "input_by", None) or "manual",
                )
            except KeyError:
                payload = {"status": "not_found", "message": "未找到目标复盘，无法生成草稿"}
            else:
                payload = {
                    "status": "ok",
                    "message": "已从复盘生成 observation 和 draft",
                    **draft_payload,
                }
        else:
            observation = service.create_observation(
                trade_date=args.date,
                source_type="manual",
                title=f"{args.date} 计划输入",
                market_facts={"bias": "混沌"},
                sector_facts={"main_themes": []},
                stock_facts=[],
                judgements=[],
                input_by=getattr(args, "input_by", None) or "manual",
            )
            payload = {
                "status": "ok",
                "message": "已创建 observation，并生成最小 draft",
                "observation": observation,
                "draft": service.create_draft(
                    trade_date=args.date,
                    source_observation_ids=[observation["observation_id"]],
                    input_by=getattr(args, "input_by", None) or "manual",
                ),
            }
    elif command == "show-draft":
        draft = service.get_draft(draft_id=args.draft_id, trade_date=args.date)
        payload = {
            "status": "ok" if draft else "not_found",
            "message": "交易草稿" if draft else "未找到交易草稿",
            "draft": draft,
        }
    elif command == "confirm":
        draft = service.get_draft(draft_id=args.draft_id, trade_date=args.date)
        if not draft:
            payload = {"status": "not_found", "message": "未找到可确认的草稿"}
        else:
            payload = {
                "status": "ok",
                "message": "正式计划已创建",
                "plan": service.confirm_plan(
                    draft_id=draft["draft_id"],
                    trade_date=args.date,
                    input_by=getattr(args, "input_by", None) or "manual",
                ),
            }
    elif command == "diagnose":
        plan = service.get_plan(plan_id=args.plan_id, trade_date=args.date)
        payload = {
            "status": "ok" if plan else "not_found",
            "message": "计划诊断" if plan else "未找到交易计划",
            "plan": plan,
            "diagnostics": service.diagnose_plan(plan_id=args.plan_id, trade_date=args.date) if plan else None,
        }
    elif command == "review":
        plan = service.get_plan(plan_id=args.plan_id, trade_date=args.date)
        if not plan:
            payload = {"status": "not_found", "message": "未找到可回写的交易计划"}
        else:
            payload = {
                "status": "ok",
                "message": "计划复盘已写入",
                "review": service.review_plan(
                    plan_id=plan["plan_id"],
                    trade_date=args.date,
                    outcome_summary="待补充",
                    input_by=getattr(args, "input_by", None) or "manual",
                ),
            }
    else:
        payload = {
            "status": "validation_error",
            "message": f"未知 plan 子命令: {command}",
        }

    payload.setdefault("subcommand", command)
    payload.setdefault("blueprint", str(BASE_DIR / "docs" / "architecture" / "tradesystem-blueprint.md"))
    _emit_cli_result(payload, as_json=getattr(args, "json", False))


_COGNITION_COMMANDS = {
    "cognition-add", "cognition-list", "cognition-show",
    "cognition-refine", "cognition-deprecate",
    "instance-add", "instance-batch-add", "instance-pending",
    "instance-validate", "validate",
    "instance-list",
    "review-generate", "review-show", "review-confirm", "review-list",
}


def _parse_optional_json(raw: Optional[str], field_name: str):
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name} 不是合法 JSON: {exc.msg}") from exc


def _parse_tags_arg(raw: Optional[str]):
    """--tags 支持 JSON 数组或逗号分隔字符串，未传返回 None。"""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass
    return [t.strip() for t in s.split(",") if t.strip()]


def _handle_cognition_command(args) -> dict:
    """knowledge 下的 cognition-* / instance-* / review-* 子命令分支。"""
    from services.cognition_service import CognitionService

    service = CognitionService()
    command = args.knowledge_command

    try:
        if command == "cognition-add":
            cognition = service.add_cognition(
                category=args.category,
                title=args.title,
                description=args.description,
                sub_category=getattr(args, "sub_category", None),
                pattern=getattr(args, "pattern", None),
                time_horizon=getattr(args, "time_horizon", None),
                action_template=getattr(args, "action_template", None),
                position_template=getattr(args, "position_template", None),
                conditions_json=getattr(args, "conditions_json", None),
                exceptions_json=getattr(args, "exceptions_json", None),
                invalidation_conditions_json=getattr(args, "invalidation_conditions_json", None),
                evidence_level=getattr(args, "evidence_level", "observation") or "observation",
                conflict_group=getattr(args, "conflict_group", None),
                first_source_note_id=getattr(args, "first_source_note_id", None),
                first_observed_date=getattr(args, "first_observed_date", None),
                tags=_parse_tags_arg(getattr(args, "tags", None)),
                status=getattr(args, "status", "candidate") or "candidate",
                input_by=args.input_by,
            )
            return {
                "status": "ok",
                "message": "认知已录入",
                "cognition": cognition,
            }

        if command == "cognition-list":
            rows = service.list_cognitions(
                status=getattr(args, "status", None),
                category=getattr(args, "category", None),
                sub_category=getattr(args, "sub_category", None),
                evidence_level=getattr(args, "evidence_level", None),
                conflict_group=getattr(args, "conflict_group", None),
                keyword=getattr(args, "keyword", None),
                limit=getattr(args, "limit", 20) or 20,
                offset=getattr(args, "offset", 0) or 0,
            )
            return {"status": "ok", "message": "认知列表", "cognitions": rows}

        if command == "cognition-show":
            cognition = service.get_cognition(args.id)
            return {"status": "ok", "message": "认知详情", "cognition": cognition}

        if command == "cognition-refine":
            cognition = service.refine_cognition(
                args.id,
                input_by=args.input_by,
                description=getattr(args, "description", None),
                pattern=getattr(args, "pattern", None),
                conditions_json=getattr(args, "conditions_json", None),
                action_template=getattr(args, "action_template", None),
                position_template=getattr(args, "position_template", None),
                exceptions_json=getattr(args, "exceptions_json", None),
                invalidation_conditions_json=getattr(args, "invalidation_conditions_json", None),
                evidence_level=getattr(args, "evidence_level", None),
                tags=_parse_tags_arg(getattr(args, "tags", None)),
                status=getattr(args, "status", None),
            )
            return {"status": "ok", "message": "认知已精炼", "cognition": cognition}

        if command == "cognition-deprecate":
            cognition = service.deprecate_cognition(
                args.id, reason=args.reason, input_by=args.input_by
            )
            return {"status": "ok", "message": "认知已弃用", "cognition": cognition}

        if command == "instance-add":
            instance = service.add_instance(
                cognition_id=args.cognition_id,
                observed_date=args.observed_date,
                source_type=args.source_type,
                source_note_id=getattr(args, "source_note_id", None),
                teacher_id=getattr(args, "teacher_id", None),
                teacher_name_snapshot=getattr(args, "teacher_name_snapshot", None),
                source_plan_review_id=getattr(args, "source_plan_review_id", None),
                source_daily_review_date=getattr(args, "source_daily_review_date", None),
                trade_id=getattr(args, "trade_id", None),
                context_summary=getattr(args, "context_summary", None),
                regime_tags_json=getattr(args, "regime_tags_json", None),
                time_horizon=getattr(args, "time_horizon", None),
                action_bias=getattr(args, "action_bias", None),
                position_cap=getattr(args, "position_cap", None),
                avoid_action=getattr(args, "avoid_action", None),
                market_regime=getattr(args, "market_regime", None),
                cross_market_anchor=getattr(args, "cross_market_anchor", None),
                consensus_key=getattr(args, "consensus_key", None),
                parameters_json=getattr(args, "parameters_json", None),
                teacher_original_text=getattr(args, "teacher_original_text", None),
                input_by=args.input_by,
            )
            return {"status": "ok", "message": "实例已录入", "instance": instance}

        if command == "instance-batch-add":
            file_path = Path(args.file)
            if not file_path.is_file():
                return {
                    "status": "validation_error",
                    "message": f"批量文件不存在: {args.file}",
                }
            try:
                items = json.loads(file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return {
                    "status": "validation_error",
                    "message": f"批量文件不是合法 JSON: {exc.msg}",
                }
            if not isinstance(items, list):
                return {
                    "status": "validation_error",
                    "message": "批量文件根节点须为 JSON 数组",
                }
            result = service.batch_add_instances(items, input_by=args.input_by)
            return {
                "status": "ok",
                "message": f"批量写入完成：成功 {len(result['created'])} / 失败 {len(result['failed'])}",
                **result,
            }

        if command == "instance-pending":
            rows = service.list_pending_instances(
                observed_date=getattr(args, "date", None),
                check_ready=bool(getattr(args, "check_ready", False)),
                limit=getattr(args, "limit", 200) or 200,
            )
            return {"status": "ok", "message": "pending 实例列表", "instances": rows}

        if command in {"instance-validate", "validate"}:
            result = service.validate_instance(
                args.instance_id,
                outcome=args.outcome,
                outcome_fact_source=args.outcome_fact_source,
                outcome_detail=getattr(args, "outcome_detail", None),
                outcome_fact_refs_json=getattr(args, "outcome_fact_refs_json", None),
                outcome_date=getattr(args, "outcome_date", None),
                lesson=getattr(args, "lesson", None),
                input_by=args.input_by,
            )
            return {"status": "ok", "message": "实例已验证", **result}

        if command == "instance-list":
            rows = service.list_instances(
                cognition_id=getattr(args, "cognition_id", None),
                outcome=getattr(args, "outcome", None),
                date_from=getattr(args, "date_from", None),
                date_to=getattr(args, "date_to", None),
                limit=getattr(args, "limit", 100) or 100,
            )
            return {"status": "ok", "message": "实例列表", "instances": rows}

        if command == "review-generate":
            review = service.generate_review(
                period_type=args.period_type,
                period_start=args.from_date,
                period_end=args.to_date,
                review_scope=getattr(args, "scope", "calendar_period") or "calendar_period",
                regime_label=getattr(args, "regime_label", None),
                input_by=args.input_by,
            )
            return {"status": "ok", "message": "周期复盘草稿已生成", "review": review}

        if command == "review-show":
            review = service.get_review(args.id)
            return {"status": "ok", "message": "周期复盘详情", "review": review}

        if command == "review-list":
            rows = service.list_reviews(
                period_type=getattr(args, "period_type", None),
                review_scope=getattr(args, "scope", None),
                status=getattr(args, "status", None),
                from_date=getattr(args, "from_date", None),
                to_date=getattr(args, "to_date", None),
                limit=getattr(args, "limit", 20) or 20,
                offset=getattr(args, "offset", 0) or 0,
            )
            return {"status": "ok", "message": "周期复盘列表", "reviews": rows}

        if command == "review-confirm":
            review = service.confirm_review(
                args.id,
                input_by=args.input_by,
                user_reflection=getattr(args, "user_reflection", None),
                action_items_json=getattr(args, "action_items_json", None),
                key_lessons_json=getattr(args, "key_lessons_json", None),
                performance_notes=getattr(args, "performance_notes", None),
            )
            return {"status": "ok", "message": "周期复盘已确认", "review": review}

    except KeyError as exc:
        return {"status": "validation_error", "message": f"对象不存在: {exc}"}
    except ValueError as exc:
        return {"status": "validation_error", "message": str(exc)}

    return {"status": "validation_error", "message": f"未知 knowledge 子命令: {command}"}


def cmd_knowledge(args) -> None:
    """资料与认知命令。

    - 资料（知识资产）：add-note / list / draft-from-asset / draft-from-teacher-note
    - 认知（trading_cognitions 等）：cognition-* / instance-* / review-*（§七 方案签名）
    """
    from services.knowledge_service import KnowledgeService

    command = args.knowledge_command

    if command in _COGNITION_COMMANDS:
        payload = _handle_cognition_command(args)
        payload.setdefault("subcommand", command)
        payload.setdefault("blueprint", str(BASE_DIR / "docs" / "architecture" / "tradesystem-blueprint.md"))
        _emit_cli_result(payload, as_json=getattr(args, "json", False))
        return

    service = KnowledgeService()
    if command == "add-note":
        try:
            asset = service.add_asset(
                asset_type=args.asset_type,
                title=args.title,
                content=args.content,
                source=args.source,
                tags=json.loads(args.tags) if args.tags else [],
            )
        except ValueError as exc:
            payload = {"status": "validation_error", "message": str(exc)}
        else:
            payload = {
                "status": "ok",
                "message": "资料已录入",
                "asset": asset,
            }
    elif command == "list":
        try:
            assets = service.list_assets(
                limit=args.limit,
                offset=getattr(args, "offset", 0) or 0,
                asset_type=getattr(args, "asset_type", None),
                keyword=getattr(args, "keyword", None),
                created_from=getattr(args, "created_from", None),
                created_to=getattr(args, "created_to", None),
            )
        except ValueError as exc:
            payload = {"status": "validation_error", "message": str(exc)}
        else:
            payload = {
                "status": "ok",
                "message": "资料列表",
                "assets": assets,
            }
    elif command == "draft-from-asset":
        try:
            draft_payload = service.draft_from_asset(
                asset_id=args.asset_id,
                trade_date=args.date,
                input_by=getattr(args, "input_by", None) or "manual",
            )
        except KeyError as exc:
            payload = {"status": "validation_error", "message": f"资料不存在: {exc}"}
        except ValueError as exc:
            payload = {"status": "validation_error", "message": str(exc)}
        else:
            payload = {
                "status": "ok",
                "message": "已从资料生成 observation 和 draft",
                **draft_payload,
            }
    elif command == "draft-from-teacher-note":
        try:
            draft_payload = service.draft_from_teacher_note(
                note_id=args.note_id,
                trade_date=args.date,
                input_by=getattr(args, "input_by", None) or "manual",
            )
        except KeyError as exc:
            payload = {"status": "validation_error", "message": f"老师笔记不存在: {exc}"}
        except ValueError as exc:
            payload = {"status": "validation_error", "message": str(exc)}
        else:
            payload = {
                "status": "ok",
                "message": "已从老师笔记生成 observation 和 draft",
                **draft_payload,
            }
    else:
        payload = {
            "status": "validation_error",
            "message": f"未知 knowledge 子命令: {command}",
        }

    payload.setdefault("subcommand", command)
    payload.setdefault("blueprint", str(BASE_DIR / "docs" / "architecture" / "tradesystem-blueprint.md"))
    _emit_cli_result(payload, as_json=getattr(args, "json", False))


def load_config() -> dict:
    config_path = SCRIPT_DIR / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_providers(config: dict):
    """初始化数据源注册中心"""
    from providers import ProviderRegistry, TushareProvider, AkshareProvider

    registry = ProviderRegistry()

    # Tushare
    ts_config = config.get("providers", {}).get("tushare", {})
    if ts_config.get("enabled"):
        ts = TushareProvider({
            "token": os.getenv("TUSHARE_TOKEN", ""),
            **ts_config,
        })
        ts.priority = ts_config.get("priority", 1)
        registry.register(ts)

    # AkShare
    ak_config = config.get("providers", {}).get("akshare", {})
    if ak_config.get("enabled"):
        ak = AkshareProvider(ak_config)
        ak.priority = ak_config.get("priority", 2)
        registry.register(ak)

    return registry


def setup_pushers(config: dict):
    """
    初始化推送渠道
    
    配置职责边界:
    - .env 文件：仅用于存储敏感 token 和 Webhook URL（DISCORD_WEBHOOK_*, WECHAT_WEBHOOK）
    - config.yaml：用于存储非敏感配置（频道映射、开关状态等）
    """
    from pushers import DiscordPusher, WechatPusher, QQBotPusher, MultiPusher

    multi = MultiPusher()

    push_config = config.get("push", {})

    # Discord
    # Webhook URL 从 .env 加载，频道映射从 config.yaml 加载
    dc_config = push_config.get("discord", {})
    if dc_config.get("enabled"):
        dc = DiscordPusher({
            "webhook_pre": os.getenv("DISCORD_WEBHOOK_PRE", ""),
            "webhook_post": os.getenv("DISCORD_WEBHOOK_POST", ""),
            "webhook_alert": os.getenv("DISCORD_WEBHOOK_ALERT", ""),
            "channels": dc_config.get("channels", {}),
        })
        dc.initialize()
        multi.register(dc)

    # QQ Bot
    # 配置全部来自 config.yaml（无敏感信息）
    qq_config = push_config.get("qq", {})
    if qq_config.get("enabled"):
        qq = QQBotPusher({
            "channels": qq_config.get("channels", {}),
        })
        qq.initialize()
        multi.register(qq)

    # 企业微信
    # Webhook URL 从 .env 加载
    wx_config = push_config.get("wechat", {})
    if wx_config.get("enabled"):
        wx = WechatPusher({
            "webhook_url": os.getenv("WECHAT_WEBHOOK", ""),
        })
        wx.initialize()
        multi.register(wx)

    return multi


def cmd_check(config: dict):
    """检查数据源连通性"""
    logger.info("=== 数据源连通性检查 ===")
    with without_standard_http_proxy():
        registry = setup_providers(config)
        results = registry.initialize_all()
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        print(f"  {name}: {status}")

    # 检查推送
    logger.info("=== 推送渠道检查 ===")
    multi = setup_pushers(config)
    if not multi._pushers:
        print("  未配置任何推送渠道")
    else:
        for p in multi._pushers:
            print(f"  {p.name}: {'OK' if p.enabled else 'FAIL'}")


def cmd_prefetch_calendar(config: dict, days: int, from_date: Optional[str]):
    """预拉取宏观日历并写入 tracking/calendar_auto.yaml"""
    logger.info(f"=== 预拉取宏观日历 days={days} from={from_date or 'today'} ===")
    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors.market import prefetch_calendar

        n_fetch, n_total = prefetch_calendar(
            registry,
            days=days,
            from_date=from_date,
            base_dir=BASE_DIR,
        )
    print(f"预拉取完成：API 返回 {n_fetch} 条，calendar_auto.yaml 共 {n_total} 条事件")
    logger.info(f"calendar_auto 已更新：拉取 {n_fetch} 条，文件合计 {n_total} 条")


def cmd_pre(config: dict, target_date: str):
    """执行盘前简报"""
    logger.info(f"=== 盘前简报 {target_date} ===")

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    if dt.weekday() >= 5:
        logger.warning(f"⚠️ {target_date} 为周末，跳过执行")
        return

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from utils.trade_date import is_trade_day, ensure_trade_calendar
        from db.connection import get_connection
        try:
            _conn = get_connection()
            ensure_trade_calendar(_conn, registry)
            if is_trade_day(target_date, conn=_conn, registry=registry) is False:
                logger.warning(f"⚠️ {target_date} 为非交易日（法定假日），跳过执行")
                _conn.close()
                return
            _conn.close()
        except Exception:
            pass

        from collectors import MarketCollector, HoldingsCollector, WatchlistCollector
        from generators import ReportGenerator
        from utils.trade_date import get_prev_trade_date

        prev_date = get_prev_trade_date(registry, target_date)
        prev_prev_date = get_prev_trade_date(registry, prev_date) if prev_date else None

        # 采集
        market_collector = MarketCollector(registry)
        market_data = market_collector.collect_pre_market(
            target_date=target_date,
            prev_trade_date=prev_date,
            prev_prev_trade_date=prev_prev_date,
        )

        # SQLite：上一交易日盘面摘要 + 昨日复盘结论（无则跳过）
        try:
            from db.connection import get_connection
            from db import queries as DbQ

            conn = get_connection()
            try:
                snap_row = DbQ.get_prev_daily_market(conn, target_date)
                if snap_row:
                    market_data["prev_session_snapshot"] = {
                        "date": snap_row.get("date"),
                        "sh_index_close": snap_row.get("sh_index_close"),
                        "sh_index_change_pct": snap_row.get("sh_index_change_pct"),
                        "total_amount": snap_row.get("total_amount"),
                        "limit_up_count": snap_row.get("limit_up_count"),
                        "limit_down_count": snap_row.get("limit_down_count"),
                        "seal_rate": snap_row.get("seal_rate"),
                        "broken_rate": snap_row.get("broken_rate"),
                        "northbound_net": snap_row.get("northbound_net"),
                    }
                if prev_date:
                    rev = DbQ.get_daily_review(conn, prev_date)
                    blurbs = DbQ.extract_review_conclusion_lines(rev)
                    if blurbs:
                        market_data["prev_review_conclusion"] = blurbs
            finally:
                conn.close()
        except Exception as e:
            logger.warning("盘前简报 DB 补充（T-1 盘面/复盘摘要）失败: %s", e)

        holdings_collector = HoldingsCollector(registry)
        holdings_collector.load()
        # 查最近3天公告
        d = datetime.strptime(target_date, "%Y-%m-%d")
        start = (d - timedelta(days=3)).strftime("%Y-%m-%d")
        holdings_anns = holdings_collector.collect_holdings_announcements(start, target_date)

        watchlist_collector = WatchlistCollector(registry)
        watchlist_anns = watchlist_collector.collect_watchlist_announcements(start, target_date)

        # 采集信息面（互动易/研报/个股新闻）
        holdings_info = {}
        watchlist_info = {}
        try:
            holdings_info = holdings_collector.collect_stock_info(target_date)
        except Exception as e:
            logger.warning(f"持仓信息面采集失败: {e}")
        try:
            watchlist_info = watchlist_collector.collect_watchlist_info(target_date)
        except Exception as e:
            logger.warning(f"关注池信息面采集失败: {e}")

        holdings_limit_overrides: dict[str, dict] = {}
        for code, info in holdings_info.items():
            limit_prices = info.get("limit_prices")
            if limit_prices:
                from db.dual_write import _normalize_stock_code_for_match

                norm = _normalize_stock_code_for_match(code)
                if norm:
                    holdings_limit_overrides[norm] = limit_prices

        try:
            from db.connection import get_db

            with get_db() as conn:
                holdings_signals = build_holding_signals(
                    conn,
                    target_date,
                    holdings=holdings_collector._holdings,
                    limit_price_overrides=holdings_limit_overrides,
                )
        except Exception as e:
            logger.warning("持仓信号摘要构建失败: %s", e)
            holdings_signals = {"date": target_date, "items": []}

        # 生成报告
        generator = ReportGenerator()
        md_text, yaml_path = generator.generate_pre_market(
            date=target_date,
            market_data=market_data,
            holdings_announcements=holdings_anns,
            watchlist_announcements=watchlist_anns,
            news=market_data.get("news", []),
            calendar_events=market_data.get("calendar_events", []),
            holdings_info=holdings_info,
            watchlist_info=watchlist_info,
            holdings_signals=holdings_signals,
        )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

    # 推送
    multi = setup_pushers(config)
    if multi._pushers:
        multi.send_report("pre_market", f"盘前简报 {target_date}", md_text)


def cmd_post(config: dict, target_date: str):
    """执行盘后报告：先晚间任务（溢价/关注池/复盘 Obsidian），再全日盘后采集与推送。"""
    cmd_evening(config, target_date)

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    if dt.weekday() >= 5:
        logger.warning(f"⚠️ {target_date} 为周末，跳过盘后采集")
        return

    logger.info(f"=== 盘后报告 {target_date} ===")

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from utils.trade_date import is_trade_day, ensure_trade_calendar
        from db.connection import get_connection
        try:
            _conn = get_connection()
            ensure_trade_calendar(_conn, registry)
            if is_trade_day(target_date, conn=_conn, registry=registry) is False:
                logger.warning(f"⚠️ {target_date} 为非交易日（法定假日），跳过盘后采集")
                _conn.close()
                return
            _conn.close()
        except Exception:
            pass

        from collectors import MarketCollector, HoldingsCollector, WatchlistCollector
        from generators import ReportGenerator

        # 采集市场数据
        market_collector = MarketCollector(registry)
        raw_data = market_collector.collect_post_market(target_date)

        # 采集持仓数据 + 盘后公告（持仓来自 SQLite active）
        holdings_collector = HoldingsCollector(registry)
        holdings_collector.load()
        holdings_data = holdings_collector.collect_holdings_data(target_date)
        try:
            holdings_data = holdings_collector.enrich_with_ma(holdings_data, target_date)
        except Exception as e:
            logger.warning(f"持仓均线/板块数据补充失败: {e}")
        holdings_summary = HoldingsCollector.compute_summary(holdings_data)

        holdings_anns = {}
        try:
            holdings_anns = holdings_collector.collect_holdings_announcements(target_date, target_date)
        except Exception as e:
            logger.warning(f"持仓盘后公告采集失败: {e}")

        holdings_info = {}
        try:
            holdings_info = holdings_collector.collect_stock_info(target_date)
        except Exception as e:
            logger.warning(f"持仓盘后信息面采集失败: {e}")

        if _schedule_task_enabled(config, "post_market", "regulatory_monitor"):
            try:
                from collectors.regulatory import RegulatoryCollector

                reg = RegulatoryCollector(registry)
                reg_result = reg.collect(target_date)
                logger.info(reg.format_report(reg_result))
            except Exception as e:
                logger.warning(f"异动监管采集失败: {e}")

        watchlist_data = {}
        try:
            wl_collector = WatchlistCollector(registry)
            watchlist_data = wl_collector.get_watchlist_summary()
        except Exception as e:
            logger.warning(f"关注池盘后数据加载失败: {e}")

        # 交叉检查：持仓与关注池重复
        holdings_codes = set(h.get("code", "") for h in holdings_data if "error" not in h)
        wl_codes = set(s.get("code", "") for s in watchlist_data.get("tier1", []))
        overlap = holdings_codes & wl_codes
        if overlap:
            logger.info(f"持仓与关注池 tier1 重叠: {overlap}")

        # 生成报告
        generator = ReportGenerator()
        md_text, yaml_path = generator.generate_post_market(
            date=target_date,
            raw_data=raw_data,
            holdings_data=holdings_data,
            holdings_announcements=holdings_anns,
            watchlist_data=watchlist_data,
            holdings_summary=holdings_summary,
            holdings_info=holdings_info,
        )

    print(md_text)
    logger.info(f"数据已保存: {yaml_path}")

    try:
        from pathlib import Path

        from db.dual_write import sync_daily_market_to_db, sync_holdings_quotes_from_post_market

        pm = Path(yaml_path)
        if pm.is_file():
            with pm.open(encoding="utf-8") as f:
                envelope = yaml.safe_load(f) or {}
            if sync_daily_market_to_db(target_date, envelope):
                logger.info("daily_market 已同步到 SQLite: %s", target_date)
            else:
                logger.warning("daily_market 同步失败（已记入 pending_writes）: %s", target_date)
            nh = sync_holdings_quotes_from_post_market(target_date, envelope)
            if nh > 0:
                logger.info("持仓现价已从盘后 YAML 同步 %d 条到 SQLite", nh)
    except Exception as e:
        logger.warning("daily_market / 持仓现价 同步跳过: %s", e)

    # 推送
    multi = setup_pushers(config)
    if multi._pushers:
        multi.send_report("post_market", f"盘后数据报告 {target_date}", md_text)

    # 盘后数据写入完成后，同步导出到 Obsidian（post-market.yaml 此时已存在）
    try:
        from generators.obsidian_export import ObsidianExporter
        exporter = ObsidianExporter()
        post_path = exporter.export_post_market(target_date)
        if post_path:
            logger.info(f"Obsidian 导出完成（盘后数据）：{post_path}")
    except Exception as e:
        logger.warning(f"Obsidian 盘后数据导出失败：{e}")

    # IngestService 快照采集：写入 market_fact_snapshots / raw_interface_payloads
    # registry 已在 without_standard_http_proxy() 块内初始化，此处复用
    try:
        from services.ingest_service import IngestService
        ingest_svc = IngestService(registry=registry)
        for stage in ("post_core", "post_extended"):
            result = ingest_svc.execute_stage(stage, target_date, triggered_by="post_cmd", input_by=None)
            ok = sum(1 for v in result.get("interfaces", {}).values() if v.get("status") == "success")
            total = len(result.get("interfaces", {}))
            logger.info(f"IngestService {stage} 完成：{ok}/{total} 接口成功")
    except Exception as e:
        logger.warning(f"IngestService 快照采集失败（不影响主流程）：{e}")


def _cmd_regulatory_query(target_date: str, *, as_json: bool, type_filter: str) -> None:
    """从 SQLite 读取 stock_regulatory_monitor，不调用数据源。"""
    import json as json_lib

    from db.connection import get_db
    from db.migrate import migrate
    from db import queries as Q

    with get_db() as conn:
        migrate(conn)
        rows = Q.list_regulatory_monitor_api(conn, target_date, type_filter)
    if as_json:
        print(json_lib.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    label = f"=== stock_regulatory_monitor {target_date} ==="
    print(label)
    if not rows:
        print("(无记录)")
        return
    for r in rows:
        rt = int(r.get("regulatory_type") or 0)
        if rt == 1:
            tag = "已监管"
        elif rt == 2:
            tag = "潜在"
        elif rt == 3:
            tag = "重点监控"
        else:
            tag = f"T{rt}"
        score = r.get("risk_score")
        sc = f" score={score}" if score is not None else ""
        print(f"{r['ts_code']} {r['name']} [{tag}] L{r['risk_level']}{sc}")
        reason = str(r.get("reason") or "")
        print(f"  {reason[:200]}{'…' if len(reason) > 200 else ''}")


def cmd_regulatory(config: dict, args) -> None:
    """异动监管：采集写入 DB；加 --query 时仅查库。"""
    target_date = args.date
    if getattr(args, "query", False):
        _cmd_regulatory_query(
            target_date,
            as_json=bool(getattr(args, "json", False)),
            type_filter=getattr(args, "regulatory_type_filter", "all"),
        )
        return
    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()
        from collectors.regulatory import RegulatoryCollector

        col = RegulatoryCollector(registry)
        result = col.collect(target_date)
        print(col.format_report(result))


def cmd_evening(config: dict, target_date: str):
    """
    晚间任务（由 post 在定时流程中自动先执行，也可手动单独运行）：
    1. 溢价率回填（T-1 涨停→T 开盘溢价）
    2. 关注池行情更新 + 到价提醒
    3. Obsidian 导出 review.yaml（post-market 在随后 post 阶段导出）
    """
    logger.info(f"=== 晚间任务 {target_date} ===")

    dt = datetime.strptime(target_date, "%Y-%m-%d")
    if dt.weekday() >= 5:
        logger.warning(f"⚠️ {target_date} 为周末，跳过执行")
        return

    premium_report_text: Optional[str] = None
    premium_prev_date: Optional[str] = None
    wl_report_text: Optional[str] = None
    wl_alerts: list = []

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from utils.trade_date import is_trade_day, ensure_trade_calendar
        from db.connection import get_connection as _get_conn
        try:
            _conn = _get_conn()
            ensure_trade_calendar(_conn, registry)
            if is_trade_day(target_date, conn=_conn, registry=registry) is False:
                logger.warning(f"⚠️ {target_date} 为非交易日（法定假日），跳过执行")
                _conn.close()
                return
        except Exception:
            pass

        from utils.trade_date import get_prev_trade_date

        prev_date = get_prev_trade_date(registry, target_date)

        # 1. 溢价率回填
        try:
            from collectors import PremiumCollector
            premium_collector = PremiumCollector(registry)
            premium_result = premium_collector.collect(target_date, prev_date)
            if premium_result:
                premium_report_text = premium_collector.format_report(premium_result)
                premium_prev_date = prev_date
                logger.info(
                    f"溢价率回填完成，首板高开率："
                    f"{premium_result['first_board'].get('open_up_rate', '-')}"
                )
        except Exception as e:
            logger.error(f"溢价率回填失败：{e}")

        # 2. 关注池采集 + 到价提醒
        try:
            from collectors import WatchlistCollector
            wl_collector = WatchlistCollector(registry)
            wl_result = wl_collector.collect(target_date)
            if wl_result:
                wl_report_text = wl_collector.format_report(wl_result)
                logger.info(
                    f"关注池更新完成，tier1={len(wl_result.get('tier1', []))}只，"
                    f"提醒={len(wl_result.get('alerts', []))}条"
                )
                wl_alerts = wl_result.get("alerts", []) or []
        except Exception as e:
            logger.error(f"关注池采集失败：{e}")

    multi = setup_pushers(config)
    if multi._pushers and premium_report_text and premium_prev_date:
        multi.send_report("post_market", f"溢价率回填 {premium_prev_date}", premium_report_text)
    if multi._pushers and wl_report_text:
        multi.send_report("post_market", f"关注池日报 {target_date}", wl_report_text)
    if multi._pushers and wl_alerts:
        alert_lines = [f"⚠ 关注池到价提醒 {target_date}"]
        for a in wl_alerts:
            alert_lines.append(f"• {a.get('message', '(无消息)')}")
        multi.send_alert("\n".join(alert_lines))

    # 3. Obsidian 导出 review.yaml（post-market 在同次 post 流程后半段导出）
    try:
        from generators.obsidian_export import ObsidianExporter
        exporter = ObsidianExporter()
        review_path = exporter.export_daily_review(target_date)
        if review_path:
            logger.info(f"Obsidian 导出完成（复盘）：{review_path}")
        else:
            logger.info(f"Obsidian 复盘导出跳过：{target_date}/review.yaml 不存在或尚未填写")
    except Exception as e:
        logger.error(f"Obsidian 导出失败：{e}")


def cmd_watchlist(config: dict, target_date: str):
    """手动触发关注池采集 + 到价提醒"""
    logger.info(f"=== 关注池采集 {target_date} ===")

    with without_standard_http_proxy():
        registry = setup_providers(config)
        registry.initialize_all()

        from collectors import WatchlistCollector
        wl_collector = WatchlistCollector(registry)
        result = wl_collector.collect(target_date)
        report = wl_collector.format_report(result)
    print(report)

    multi = setup_pushers(config)
    if multi._pushers and report:
        multi.send_report("post_market", f"关注池日报 {target_date}", report)


def cmd_obsidian(config: dict, target_date: str, sync_all: bool = False):
    """手动触发 Obsidian 导出"""
    from generators.obsidian_export import ObsidianExporter
    exporter = ObsidianExporter()

    if sync_all:
        daily_dir = exporter.ts_dir / "daily"
        if not daily_dir.exists():
            logger.error(f"daily 目录不存在：{daily_dir}")
            return
        dates = sorted([d.name for d in daily_dir.iterdir() if d.is_dir() and d.name != "example"])
        logger.info(f"全量同步 {len(dates)} 个日期...")
        for d in dates:
            exporter.export_all(d)
        logger.info("全量同步完成")
    else:
        results = exporter.export_all(target_date)
        exported = {k: v for k, v in results.items() if v and k != "date"}
        if exported:
            logger.info(f"导出完成：{exported}")
        else:
            logger.warning(f"未找到可导出的文件（{target_date}）")


def cmd_schedule(config: dict):
    """启动定时调度器"""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        print("请安装 apscheduler: pip install apscheduler")
        return

    scheduler = BlockingScheduler(timezone="Asia/Shanghai")

    # 盘前: 每个工作日 07:00
    scheduler.add_job(
        lambda: cmd_pre(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0),
        id="pre_market",
        name="盘前简报",
    )

    # 盘后: 每个工作日 20:00（内含晚间任务：溢价回填/关注池/复盘 Obsidian）
    scheduler.add_job(
        lambda: cmd_post(config, date.today().isoformat()),
        CronTrigger(day_of_week="mon-fri", hour=20, minute=0),
        id="post_market",
        name="盘后报告",
    )

    logger.info("定时调度器已启动")
    logger.info("  盘前简报: 周一~周五 07:00")
    logger.info("  盘后报告: 周一~周五 20:00（含溢价回填/关注池/复盘与全日盘后）")
    logger.info("按 Ctrl+C 停止")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("调度器已停止")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="交易系统数据采集与报告")
    subparsers = parser.add_subparsers(dest="command")

    # check
    subparsers.add_parser("check", help="检查数据源连通性")

    # prefetch-calendar
    prefetch_parser = subparsers.add_parser(
        "prefetch-calendar",
        help="预拉取未来多日宏观日历到 tracking/calendar_auto.yaml",
    )
    prefetch_parser.add_argument("--days", type=int, default=14, help="从起始日起连续拉取的天数（默认 14）")
    prefetch_parser.add_argument("--from", dest="from_date", default=None, help="起始日 YYYY-MM-DD（默认今天）")

    # pre
    pre_parser = subparsers.add_parser("pre", help="生成盘前简报")
    pre_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # post
    post_parser = subparsers.add_parser("post", help="生成盘后报告（含晚间任务）")
    post_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # regulatory
    regulatory_parser = subparsers.add_parser(
        "regulatory",
        help="异动监管：采集写入 stock_regulatory_monitor；--query 仅查 SQLite",
    )
    regulatory_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    regulatory_parser.add_argument(
        "--query",
        action="store_true",
        help="不采集，只从数据库读取当日（--date）已存记录",
    )
    regulatory_parser.add_argument(
        "--type",
        dest="regulatory_type_filter",
        choices=["1", "2", "3", "all"],
        default="all",
        help="与 --query 联用：1=已监管 2=潜在 3=重点监控(stk_alert) all=全部",
    )
    regulatory_parser.add_argument(
        "--json",
        action="store_true",
        help="与 --query 联用：输出 JSON",
    )

    # evening
    evening_parser = subparsers.add_parser("evening", help="仅晚间任务（定时请用 post；post 会自动先执行本流程）")
    evening_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # watchlist
    watchlist_parser = subparsers.add_parser("watchlist", help="关注池行情更新 + 到价提醒")
    watchlist_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")

    # obsidian
    obsidian_parser = subparsers.add_parser("obsidian", help="导出数据到 Obsidian Vault")
    obsidian_parser.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    obsidian_parser.add_argument("--sync-all", action="store_true", help="全量同步所有日期")

    # schedule
    subparsers.add_parser("schedule", help="启动定时调度器")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="采集底座命令（架构骨架）")
    ingest_subparsers = ingest_parser.add_subparsers(dest="ingest_command")

    ingest_run = ingest_subparsers.add_parser("run", help="按 stage 运行采集任务")
    ingest_run.add_argument("--stage", required=True, choices=["pre_core", "post_core", "post_extended", "watchlist", "backfill"])
    ingest_run.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    ingest_run.add_argument("--input-by", default="manual", help="触发来源：manual/openclaw/copaw/cursor")
    ingest_run.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_run_interface = ingest_subparsers.add_parser("run-interface", help="运行单个已注册接口")
    ingest_run_interface.add_argument("--name", required=True, help="接口注册名")
    ingest_run_interface.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    ingest_run_interface.add_argument("--input-by", default="manual", help="触发来源：manual/openclaw/copaw/cursor")
    ingest_run_interface.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_list = ingest_subparsers.add_parser("list-interfaces", help="列出接口注册表")
    ingest_list.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_inspect = ingest_subparsers.add_parser("inspect", help="查看某日采集运行状态")
    ingest_inspect.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    ingest_inspect.add_argument("--stage", choices=["pre_core", "post_core", "post_extended", "watchlist", "backfill"], help="按阶段过滤")
    ingest_inspect.add_argument("--interface", help="按接口注册名过滤")
    ingest_inspect.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_retry = ingest_subparsers.add_parser("retry", help="重试失败采集项")
    ingest_retry.add_argument("--stage", choices=["pre_core", "post_core", "post_extended", "watchlist", "backfill"], help="按阶段过滤")
    ingest_retry.add_argument("--interface", help="按接口注册名过滤")
    ingest_retry.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_health = ingest_subparsers.add_parser("health", help="查看近 N 天采集健康摘要")
    ingest_health.add_argument("--date", default=date.today().isoformat(), help="结束日期 YYYY-MM-DD")
    ingest_health.add_argument("--days", type=int, default=7, help="统计天数")
    ingest_health.add_argument("--limit", type=int, default=10, help="失败接口排行条数")
    ingest_health.add_argument("--stage", choices=["pre_core", "post_core", "post_extended", "watchlist", "backfill"], help="按阶段过滤")
    ingest_health.add_argument("--json", action="store_true", help="输出 JSON")

    ingest_reconcile = ingest_subparsers.add_parser("reconcile", help="清理陈旧 running 采集记录")
    ingest_reconcile.add_argument("--stale-minutes", type=int, default=5, help="超过多少分钟仍为 running 视为陈旧")
    ingest_reconcile.add_argument("--json", action="store_true", help="输出 JSON")

    # plan
    plan_parser = subparsers.add_parser("plan", help="交易计划命令（架构骨架）")
    plan_subparsers = plan_parser.add_subparsers(dest="plan_command")

    plan_draft = plan_subparsers.add_parser("draft", help="生成交易草稿")
    plan_draft.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    plan_draft.add_argument("--from-review", action="store_true", help="从对应复盘生成次日计划草稿")
    plan_draft.add_argument("--trade-date", default=None, help="目标计划日期 YYYY-MM-DD（默认推算次一交易日）")
    plan_draft.add_argument("--input-by", default=None, help="输入来源，如 manual/openclaw/cursor")
    plan_draft.add_argument("--json", action="store_true", help="输出 JSON")

    plan_show = plan_subparsers.add_parser("show-draft", help="查看交易草稿")
    plan_show.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    plan_show.add_argument("--draft-id", default=None, help="草稿 ID")
    plan_show.add_argument("--json", action="store_true", help="输出 JSON")

    plan_confirm = plan_subparsers.add_parser("confirm", help="确认正式交易计划")
    plan_confirm.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    plan_confirm.add_argument("--draft-id", default=None, help="草稿 ID")
    plan_confirm.add_argument("--input-by", default=None, help="输入来源，如 manual/openclaw/cursor")
    plan_confirm.add_argument("--json", action="store_true", help="输出 JSON")

    plan_diagnose = plan_subparsers.add_parser("diagnose", help="诊断交易计划")
    plan_diagnose.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    plan_diagnose.add_argument("--plan-id", default=None, help="计划 ID")
    plan_diagnose.add_argument("--json", action="store_true", help="输出 JSON")

    plan_review = plan_subparsers.add_parser("review", help="回写计划复盘")
    plan_review.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    plan_review.add_argument("--plan-id", default=None, help="计划 ID")
    plan_review.add_argument("--input-by", default=None, help="输入来源，如 manual/openclaw/cursor")
    plan_review.add_argument("--json", action="store_true", help="输出 JSON")

    # knowledge
    knowledge_parser = subparsers.add_parser("knowledge", help="资料与提炼命令（架构骨架）")
    knowledge_subparsers = knowledge_parser.add_subparsers(dest="knowledge_command")

    knowledge_add = knowledge_subparsers.add_parser("add-note", help="录入资料/笔记（老师观点请用 db add-note）")
    knowledge_add.add_argument(
        "--asset-type",
        default="manual_note",
        choices=["news_note", "manual_note"],
        help="资料类型（不含 teacher_note / course_note；老师观点走 db add-note）",
    )
    knowledge_add.add_argument("--title", required=True, help="标题")
    knowledge_add.add_argument("--content", required=True, help="正文")
    knowledge_add.add_argument("--source", default=None, help="来源")
    knowledge_add.add_argument("--tags", default=None, help="标签 JSON array")
    knowledge_add.add_argument("--json", action="store_true", help="输出 JSON")

    knowledge_list = knowledge_subparsers.add_parser("list", help="列出资料")
    knowledge_list.add_argument("--limit", type=int, default=20, help="返回条数")
    knowledge_list.add_argument("--offset", type=int, default=0, help="偏移")
    knowledge_list.add_argument(
        "--asset-type",
        default=None,
        choices=["news_note", "manual_note"],
        help="按类型筛选（与 GET /api/knowledge/assets 一致）",
    )
    knowledge_list.add_argument("--keyword", default=None, help="标题/正文/来源关键词")
    knowledge_list.add_argument("--created-from", default=None, dest="created_from", help="创建日起 YYYY-MM-DD")
    knowledge_list.add_argument("--created-to", default=None, dest="created_to", help="创建日止 YYYY-MM-DD")
    knowledge_list.add_argument("--json", action="store_true", help="输出 JSON")

    knowledge_draft = knowledge_subparsers.add_parser("draft-from-asset", help="从资料生成草稿")
    knowledge_draft.add_argument("--asset-id", required=True, help="资料 ID")
    knowledge_draft.add_argument("--date", default=date.today().isoformat(), help="日期 YYYY-MM-DD")
    knowledge_draft.add_argument("--input-by", default=None, help="录入方（默认 manual）")
    knowledge_draft.add_argument("--json", action="store_true", help="输出 JSON")

    knowledge_draft_tn = knowledge_subparsers.add_parser(
        "draft-from-teacher-note", help="从老师笔记（teacher_notes）生成草稿"
    )
    knowledge_draft_tn.add_argument("--note-id", type=int, required=True, help="teacher_notes.id")
    knowledge_draft_tn.add_argument("--date", default=date.today().isoformat(), help="交易日 YYYY-MM-DD")
    knowledge_draft_tn.add_argument("--input-by", default=None, help="录入方")
    knowledge_draft_tn.add_argument("--json", action="store_true", help="输出 JSON")

    # 认知层（§七 方案签名）──────────────────────────────────────
    cog_add = knowledge_subparsers.add_parser(
        "cognition-add", help="新增可复用交易认知（trading_cognitions，默认 status=candidate）"
    )
    cog_add.add_argument("--category", required=True, help="一级分类（见 cognition_taxonomy.yaml）")
    cog_add.add_argument("--title", required=True, help="认知标题")
    cog_add.add_argument("--description", required=True, help="人类可读说明")
    cog_add.add_argument("--sub-category", default=None, help="二级分类")
    cog_add.add_argument("--pattern", default=None, help="模板槽位，格式 当{条件}时，{判断}→{结论}")
    cog_add.add_argument(
        "--evidence-level",
        default="observation",
        choices=["observation", "hypothesis", "principle"],
        help="证据等级（默认 observation）",
    )
    cog_add.add_argument("--time-horizon", default=None, help="时间尺度 intraday/swing/mid_term/...")
    cog_add.add_argument("--action-template", default=None, help="动作模板")
    cog_add.add_argument("--position-template", default=None, help="仓位模板（文本或 JSON）")
    cog_add.add_argument("--conditions-json", default=None, help="条件 JSON 字符串")
    cog_add.add_argument("--exceptions-json", default=None, help="例外 JSON 字符串")
    cog_add.add_argument("--invalidation-conditions-json", default=None, help="失效条件 JSON 字符串")
    cog_add.add_argument("--conflict-group", default=None, help="冲突分组标签")
    cog_add.add_argument("--first-source-note-id", type=int, default=None, help="首个来源 teacher_notes.id")
    cog_add.add_argument("--first-observed-date", default=None, help="首次观察日 YYYY-MM-DD")
    cog_add.add_argument("--tags", default=None, help="标签（JSON 数组或逗号分隔）")
    cog_add.add_argument(
        "--status",
        default="candidate",
        choices=["candidate", "active"],
        help="初始状态（默认 candidate；deprecated / merged 须走各自命令）",
    )
    cog_add.add_argument("--input-by", required=True, help="录入方 cursor/claude/web/manual")
    cog_add.add_argument("--json", action="store_true", help="输出 JSON")

    cog_list = knowledge_subparsers.add_parser("cognition-list", help="列出认知")
    cog_list.add_argument(
        "--status", default=None,
        choices=["candidate", "active", "deprecated", "merged"],
        help="按状态过滤",
    )
    cog_list.add_argument("--category", default=None, help="按一级分类过滤")
    cog_list.add_argument("--sub-category", default=None, help="按二级分类过滤")
    cog_list.add_argument(
        "--evidence-level", default=None,
        choices=["observation", "hypothesis", "principle"],
        help="按证据等级过滤",
    )
    cog_list.add_argument("--conflict-group", default=None, help="按冲突分组过滤")
    cog_list.add_argument("--keyword", default=None, help="title/description/pattern 关键词")
    cog_list.add_argument("--limit", type=int, default=20, help="返回条数")
    cog_list.add_argument("--offset", type=int, default=0, help="偏移")
    cog_list.add_argument("--json", action="store_true", help="输出 JSON")

    cog_show = knowledge_subparsers.add_parser("cognition-show", help="查看单条认知（含实例统计）")
    cog_show.add_argument("--id", required=True, help="cognition_id")
    cog_show.add_argument("--json", action="store_true", help="输出 JSON")

    cog_refine = knowledge_subparsers.add_parser(
        "cognition-refine", help="精炼认知（仅更新非空字段，自动 version+=1）"
    )
    cog_refine.add_argument("--id", required=True, help="cognition_id")
    cog_refine.add_argument("--description", default=None)
    cog_refine.add_argument("--pattern", default=None)
    cog_refine.add_argument("--conditions-json", default=None)
    cog_refine.add_argument("--action-template", default=None)
    cog_refine.add_argument("--position-template", default=None)
    cog_refine.add_argument("--exceptions-json", default=None)
    cog_refine.add_argument("--invalidation-conditions-json", default=None)
    cog_refine.add_argument(
        "--evidence-level", default=None,
        choices=["observation", "hypothesis", "principle"],
    )
    cog_refine.add_argument("--tags", default=None, help="标签（JSON 数组或逗号分隔）")
    cog_refine.add_argument(
        "--status", default=None,
        choices=["candidate", "active", "deprecated"],
        help="refine 不能设为 merged（merged 须走 merge 流程）",
    )
    cog_refine.add_argument("--input-by", required=True, help="录入方")
    cog_refine.add_argument("--json", action="store_true", help="输出 JSON")

    cog_dep = knowledge_subparsers.add_parser(
        "cognition-deprecate", help="将认知置为 deprecated 并记录原因"
    )
    cog_dep.add_argument("--id", required=True, help="cognition_id")
    cog_dep.add_argument("--reason", required=True, help="弃用原因（将追加到 tags）")
    cog_dep.add_argument("--input-by", required=True, help="录入方")
    cog_dep.add_argument("--json", action="store_true", help="输出 JSON")

    inst_add = knowledge_subparsers.add_parser(
        "instance-add", help="新增认知实例（cognition_instances）"
    )
    inst_add.add_argument("--cognition-id", required=True, help="父认知 cognition_id")
    inst_add.add_argument("--observed-date", required=True, help="观察日 YYYY-MM-DD")
    inst_add.add_argument("--source-type", required=True, help="teacher_note / plan_review / daily_review / ...")
    inst_add.add_argument("--source-note-id", type=int, default=None, help="teacher_notes.id")
    inst_add.add_argument("--teacher-id", type=int, default=None, help="teachers.id")
    inst_add.add_argument("--teacher-name-snapshot", default=None, help="老师名称快照")
    inst_add.add_argument("--source-plan-review-id", default=None, help="关联 plan_reviews.review_id")
    inst_add.add_argument("--source-daily-review-date", default=None, help="关联 daily_reviews.date")
    inst_add.add_argument("--trade-id", type=int, default=None, help="关联 trades.id")
    inst_add.add_argument("--context-summary", default=None, help="背景摘要")
    inst_add.add_argument("--regime-tags-json", default=None, help="情绪/主线 JSON")
    inst_add.add_argument("--time-horizon", default=None, help="时间尺度")
    inst_add.add_argument("--action-bias", default=None, help="动作倾向")
    inst_add.add_argument("--position-cap", type=float, default=None, help="仓位上限")
    inst_add.add_argument("--avoid-action", default=None, help="禁止动作")
    inst_add.add_argument("--market-regime", default=None, help="市场环境")
    inst_add.add_argument("--cross-market-anchor", default=None, help="跨市场信号锚点")
    inst_add.add_argument("--consensus-key", default=None, help="共识聚合键")
    inst_add.add_argument("--parameters-json", default=None, help="实例参数 JSON")
    inst_add.add_argument("--teacher-original-text", default=None, help="原文证据")
    inst_add.add_argument("--input-by", required=True, help="录入方")
    inst_add.add_argument("--json", action="store_true", help="输出 JSON")

    inst_batch = knowledge_subparsers.add_parser(
        "instance-batch-add", help="批量新增实例（--file 为 JSON 数组）"
    )
    inst_batch.add_argument("--file", required=True, help="JSON 数组文件路径，每条字段与 instance-add 一致")
    inst_batch.add_argument("--input-by", required=True, help="录入方")
    inst_batch.add_argument("--json", action="store_true", help="输出 JSON")

    inst_pending = knowledge_subparsers.add_parser(
        "instance-pending", help="列出 pending 实例（--check-ready 只看可验证的）"
    )
    inst_pending.add_argument("--date", default=None, help="观察日 YYYY-MM-DD（不填则全量）")
    inst_pending.add_argument(
        "--check-ready", action="store_true",
        help="仅返回 observed_date<今日 的实例（表示盘后可验证）",
    )
    inst_pending.add_argument("--limit", type=int, default=200, help="返回条数上限")
    inst_pending.add_argument("--json", action="store_true", help="输出 JSON")

    # instance-validate + 别名 validate（方案 §七 原文）
    for validate_name in ("instance-validate", "validate"):
        inst_validate = knowledge_subparsers.add_parser(
            validate_name,
            help="验证实例 outcome（必须带 outcome-fact-source）",
        )
        inst_validate.add_argument("--instance-id", required=True, help="实例 ID")
        inst_validate.add_argument(
            "--outcome", required=True,
            choices=["validated", "invalidated", "partial", "not_applicable"],
        )
        inst_validate.add_argument(
            "--outcome-fact-source", required=True,
            help="事实源，格式 '<table>:<YYYY-MM-DD>'",
        )
        inst_validate.add_argument("--outcome-detail", default=None, help="验证说明")
        inst_validate.add_argument("--outcome-fact-refs-json", default=None, help="多事实源 JSON 数组")
        inst_validate.add_argument("--outcome-date", default=None, help="确认日 YYYY-MM-DD（缺省取今日）")
        inst_validate.add_argument("--lesson", default=None, help="本次教训")
        inst_validate.add_argument("--input-by", required=True, help="录入方")
        inst_validate.add_argument("--json", action="store_true", help="输出 JSON")

    inst_list = knowledge_subparsers.add_parser("instance-list", help="列出实例（支持按 outcome/date 过滤）")
    inst_list.add_argument("--cognition-id", default=None, help="按父认知过滤")
    inst_list.add_argument(
        "--outcome", default=None,
        choices=["pending", "validated", "invalidated", "partial", "not_applicable"],
    )
    inst_list.add_argument("--date-from", default=None, help="观察日起 YYYY-MM-DD")
    inst_list.add_argument("--date-to", default=None, help="观察日止 YYYY-MM-DD")
    inst_list.add_argument("--limit", type=int, default=100, help="返回条数上限")
    inst_list.add_argument("--json", action="store_true", help="输出 JSON")

    rev_gen = knowledge_subparsers.add_parser(
        "review-generate", help="生成周期复盘草稿（periodic_reviews；Phase 1b 只做聚合查询）"
    )
    rev_gen.add_argument(
        "--period-type", required=True,
        choices=["weekly", "monthly", "quarterly", "yearly"],
    )
    rev_gen.add_argument("--from", dest="from_date", required=True, help="起始 YYYY-MM-DD")
    rev_gen.add_argument("--to", dest="to_date", required=True, help="结束 YYYY-MM-DD")
    rev_gen.add_argument(
        "--scope", default="calendar_period",
        choices=["calendar_period", "event_window", "regime_window"],
    )
    rev_gen.add_argument("--regime-label", default=None, help="阶段标签（如 修复右侧窗）")
    rev_gen.add_argument("--input-by", required=True, help="录入方")
    rev_gen.add_argument("--json", action="store_true", help="输出 JSON")

    rev_show = knowledge_subparsers.add_parser("review-show", help="查看周期复盘")
    rev_show.add_argument("--id", required=True, help="review_id")
    rev_show.add_argument("--json", action="store_true", help="输出 JSON")

    rev_list = knowledge_subparsers.add_parser(
        "review-list", help="列出周期复盘（periodic_reviews；支持 period_type/status/日期区间过滤）"
    )
    rev_list.add_argument(
        "--period-type", default=None,
        choices=["weekly", "monthly", "quarterly", "yearly"],
        help="按周期类型过滤",
    )
    rev_list.add_argument(
        "--scope", default=None,
        choices=["calendar_period", "event_window", "regime_window"],
        help="按 review_scope 过滤",
    )
    rev_list.add_argument(
        "--status", default=None,
        choices=["draft", "confirmed"],
        help="按状态过滤",
    )
    rev_list.add_argument("--from", dest="from_date", default=None, help="period_start 起 YYYY-MM-DD")
    rev_list.add_argument("--to", dest="to_date", default=None, help="period_end 止 YYYY-MM-DD")
    rev_list.add_argument("--limit", type=int, default=20, help="返回条数")
    rev_list.add_argument("--offset", type=int, default=0, help="偏移")
    rev_list.add_argument("--json", action="store_true", help="输出 JSON")

    rev_confirm = knowledge_subparsers.add_parser(
        "review-confirm", help="确认周期复盘（draft → confirmed）"
    )
    rev_confirm.add_argument("--id", required=True, help="review_id")
    rev_confirm.add_argument("--user-reflection", default=None, help="用户反思文本")
    rev_confirm.add_argument("--action-items-json", default=None, help="行动项 JSON")
    rev_confirm.add_argument("--key-lessons-json", default=None, help="关键教训 JSON")
    rev_confirm.add_argument("--performance-notes", default=None, help="表现注记")
    rev_confirm.add_argument("--input-by", required=True, help="录入方")
    rev_confirm.add_argument("--json", action="store_true", help="输出 JSON")

    # db
    from db.cli import register_db_subparser
    register_db_subparser(subparsers)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    config = load_config()

    if args.command == "check":
        cmd_check(config)
    elif args.command == "prefetch-calendar":
        cmd_prefetch_calendar(config, args.days, args.from_date)
    elif args.command == "pre":
        cmd_pre(config, args.date)
    elif args.command == "post":
        cmd_post(config, args.date)
    elif args.command == "regulatory":
        cmd_regulatory(config, args)
    elif args.command == "evening":
        cmd_evening(config, args.date)
    elif args.command == "watchlist":
        cmd_watchlist(config, args.date)
    elif args.command == "obsidian":
        cmd_obsidian(config, args.date, sync_all=args.sync_all)
    elif args.command == "schedule":
        cmd_schedule(config)
    elif args.command == "ingest":
        cmd_ingest(config, args)
    elif args.command == "plan":
        cmd_plan(config, args)
    elif args.command == "knowledge":
        cmd_knowledge(args)
    elif args.command == "db":
        from db.cli import handle_db_command
        handle_db_command(args)


if __name__ == "__main__":
    main()
