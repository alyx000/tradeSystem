#!/usr/bin/env python3
"""在切换盘中监控 runtime 前保护尚未送达的 pending 事件。"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
from pathlib import Path


RULES_PATH = "scripts/services/intraday_monitor/rules.py"


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def default_rule_ids(source: str) -> set[str]:
    """只读解析目标提交 DEFAULT_RULES，不执行目标代码。"""
    tree = ast.parse(source, filename=RULES_PATH)
    declared: dict[str, str] = {}
    default_names: list[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        else:
            continue
        names = [target.id for target in targets if isinstance(target, ast.Name)]
        if not names or value is None:
            continue
        name = names[0]
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "MonitorRule"
        ):
            rule_id = _literal_string(value.args[0]) if value.args else None
            for keyword in value.keywords:
                if keyword.arg == "rule_id":
                    rule_id = _literal_string(keyword.value)
            if rule_id:
                declared[name] = rule_id
        if name == "DEFAULT_RULES" and isinstance(value, (ast.Tuple, ast.List)):
            default_names = [
                item.id for item in value.elts if isinstance(item, ast.Name)
            ]
    if default_names is None:
        raise ValueError("目标提交缺少可解析的 DEFAULT_RULES")
    missing = [name for name in default_names if name not in declared]
    if missing:
        raise ValueError(f"目标提交 DEFAULT_RULES 含不可解析项: {', '.join(missing)}")
    return {declared[name] for name in default_names}


def rules_at_commit(repo: Path, commit: str) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{RULES_PATH}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return default_rule_ids(result.stdout)


def pending_rule_ids(state_path: Path) -> set[str]:
    if not state_path.exists():
        return set()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    pending = payload.get("pending_events") or []
    if not isinstance(pending, list):
        raise ValueError("正式状态 pending_events 不是列表")
    result: set[str] = set()
    for event in pending:
        if not isinstance(event, dict) or not event.get("rule_id"):
            raise ValueError("正式状态存在缺少 rule_id 的 pending 事件")
        result.add(str(event["rule_id"]))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="盘中监控 runtime 切换前 pending 安全检查")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--state-path", type=Path, required=True)
    args = parser.parse_args()

    try:
        target_rule_ids = rules_at_commit(args.repo, args.target_commit)
        pending_ids = pending_rule_ids(args.state_path)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "guard_failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    unknown = sorted(pending_ids - target_rule_ids)
    if unknown:
        print(
            json.dumps(
                {
                    "status": "blocked_pending_rules",
                    "target_commit": args.target_commit,
                    "pending_rule_ids": sorted(pending_ids),
                    "unknown_rule_ids": unknown,
                },
                ensure_ascii=False,
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "safe",
                "target_commit": args.target_commit,
                "pending_rule_ids": sorted(pending_ids),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
