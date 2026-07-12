"""三位一体因子评分、人工确认、T+1 回验与影子指标 CLI。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from db.connection import get_connection
from db.migrate import migrate
from api.routes.review import build_review_prefill
from services.trinity_factor.review_input import (
    normalize_review_steps,
    validate_trade_date,
)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    review = subparsers.add_parser("review", help="每日复盘因子评分与回验")
    sub = review.add_subparsers(dest="review_factor_command")

    score = sub.add_parser("factor-score", help="规则门控并执行双层 LLM 评分")
    score.add_argument("--date", required=True, help="复盘交易日 YYYY-MM-DD")
    score.add_argument("--steps-file", help="第1-6步 JSON 文件；默认读取 daily_reviews")
    score.add_argument("--no-llm", action="store_true", help="仅执行规则门并输出降级建议")
    score.add_argument("--retry-of-run-id", help="显式重试的父 score_run_id")
    score.add_argument("--input-by", required=True, help="评分请求者（审计字段）")
    score.add_argument("--json", action="store_true", help="输出 JSON")

    confirm = sub.add_parser("factor-confirm", help="确认、改选或标记看不懂")
    confirm.add_argument("--date", required=True, help="复盘交易日 YYYY-MM-DD")
    confirm.add_argument("--run-id", required=True, help="score_run_id")
    confirm.add_argument("--decision-file", required=True, help="人工决定 JSON 文件")
    confirm.add_argument("--input-by", required=True, help="确认操作者")
    confirm.add_argument("--json", action="store_true", help="输出 JSON")

    evaluate = sub.add_parser("factor-evaluate", help="确认严格 T+1 回验")
    evaluate.add_argument("--date", required=True, help="回验交易日 YYYY-MM-DD")
    evaluate.add_argument("--source-date", required=True, help="来源复盘日 YYYY-MM-DD")
    evaluate.add_argument("--run-id", help="来源 score_run_id；默认取确认或最新运行")
    evaluate.add_argument(
        "--outcome",
        required=True,
        choices=["hit", "partial", "miss", "missing_data", "not_applicable"],
    )
    evaluate.add_argument("--note", help="人工回验备注")
    evaluate.add_argument("--input-by", required=True, help="确认操作者")
    evaluate.add_argument("--json", action="store_true", help="输出 JSON")

    metrics = sub.add_parser("factor-metrics", help="查看影子期指标")
    metrics.add_argument("--days", type=int, default=20, help="有效交易日数，默认20")
    metrics.add_argument("--json", action="store_true", help="输出 JSON")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    del config
    command = getattr(args, "review_factor_command", None)
    try:
        if command == "factor-score":
            result = _factor_score(args)
        elif command == "factor-confirm":
            result = _factor_confirm(args)
        elif command == "factor-evaluate":
            result = _factor_evaluate(args)
        elif command == "factor-metrics":
            result = _factor_metrics(args)
        else:
            raise ValueError(
                "用法：python3 main.py review "
                "factor-score|factor-confirm|factor-evaluate|factor-metrics"
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"[review-factor] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    _emit(result, as_json=bool(getattr(args, "json", False)))


def _factor_score(args: argparse.Namespace) -> dict[str, Any]:
    from db import queries as Q
    from services.trinity_factor.service import TrinityFactorService

    trade_date = validate_trade_date(args.date)
    steps = None
    if args.steps_file is not None:
        supplied = _read_json_file(args.steps_file)
        if not isinstance(supplied, dict):
            raise ValueError("steps-file must contain a JSON object")
        steps = supplied.get("steps", supplied)
        if steps is not None and not isinstance(steps, dict):
            raise ValueError("steps must be a JSON object")
    conn = _connection()
    try:
        if steps is None:
            steps = Q.get_daily_review(conn, trade_date) or {}
        return TrinityFactorService().score(
            conn,
            trade_date=trade_date,
            prefill=build_review_prefill(conn, trade_date),
            review_steps=normalize_review_steps(steps),
            no_llm=args.no_llm,
            retry_of_run_id=args.retry_of_run_id,
            input_by=args.input_by,
        )
    finally:
        conn.close()


def _factor_confirm(args: argparse.Namespace) -> dict[str, Any]:
    from db import queries as Q
    from services.trinity_factor.cycle import confirm_factor_decision
    from services.trinity_factor.service import build_score_input_digest

    trade_date = validate_trade_date(args.date)
    decision = _read_json_file(args.decision_file)
    if not isinstance(decision, dict):
        raise ValueError("decision-file must contain a JSON object")
    conn = _connection()
    try:
        if conn.in_transaction:
            raise RuntimeError("factor confirmation requires a clean transaction boundary")
        conn.execute("BEGIN IMMEDIATE")
        current_review = Q.get_daily_review(conn, trade_date) or {}
        confirmed = confirm_factor_decision(
            conn,
            trade_date=trade_date,
            score_run_id=args.run_id,
            decision=decision,
            input_by=args.input_by,
            current_input_digest=build_score_input_digest(
                trade_date=trade_date,
                prefill=build_review_prefill(conn, trade_date),
                review_steps=normalize_review_steps(current_review),
                strict_prev_trade_date=Q.get_prev_trade_date_from_db(
                    conn,
                    trade_date,
                ),
            ),
        )
        conn.commit()
        return confirmed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _factor_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    from services.trinity_factor.cycle import (
        confirm_t1_evaluation,
        suggest_t1_evaluation,
    )

    evaluation_date = validate_trade_date(args.date)
    source_date = validate_trade_date(args.source_date)
    conn = _connection()
    try:
        suggestion = suggest_t1_evaluation(
            conn,
            evaluation_trade_date=evaluation_date,
            source_review_date=source_date,
            score_run_id=args.run_id,
            prefill=build_review_prefill(conn, evaluation_date),
        )
        return confirm_t1_evaluation(
            conn,
            suggestion=suggestion,
            confirmed_outcome=args.outcome,
            evaluation_note=args.note,
            input_by=args.input_by,
        )
    finally:
        conn.close()


def _factor_metrics(args: argparse.Namespace) -> dict[str, Any]:
    from services.trinity_factor.cycle import build_factor_metrics

    conn = _connection()
    try:
        return build_factor_metrics(conn, days=args.days)
    finally:
        conn.close()


def _connection():
    conn = get_connection()
    migrate(conn)
    return conn


def _read_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _emit(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    status = payload.get("status") or payload.get("confirmed_outcome") or "ok"
    run_id = payload.get("score_run_id") or payload.get("evaluation_id") or ""
    print(f"[{status}] {run_id}".strip())
