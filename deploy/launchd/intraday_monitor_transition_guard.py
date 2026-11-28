#!/usr/bin/env python3
"""在切换盘中监控 runtime 前保护尚未送达的 pending 事件。"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from datetime import date
from pathlib import Path


RULE_SOURCES = (
    ("scripts/services/intraday_monitor/rules.py", "MonitorRule", "DEFAULT_RULES", True),
    (
        "scripts/services/intraday_monitor/market_scan.py",
        "MarketScanRule",
        "DEFAULT_MARKET_SCAN_RULES",
        False,
    ),
    (
        "scripts/services/index_volume_monitor/rules.py",
        "IndexVolumeRule",
        "DEFAULT_INDEX_VOLUME_RULES",
        False,
    ),
)


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def default_rule_ids(
    source: str,
    *,
    rules_path: str = RULE_SOURCES[0][0],
    constructor_name: str = RULE_SOURCES[0][1],
    collection_name: str = RULE_SOURCES[0][2],
) -> set[str]:
    """只读解析目标提交 DEFAULT_RULES，不执行目标代码。"""
    tree = ast.parse(source, filename=rules_path)
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
            and value.func.id == constructor_name
        ):
            rule_id = _literal_string(value.args[0]) if value.args else None
            for keyword in value.keywords:
                if keyword.arg == "rule_id":
                    rule_id = _literal_string(keyword.value)
            if rule_id:
                declared[name] = rule_id
        if name == collection_name and isinstance(value, (ast.Tuple, ast.List)):
            default_names = [
                item.id for item in value.elts if isinstance(item, ast.Name)
            ]
    if default_names is None:
        raise ValueError(f"目标提交缺少可解析的 {collection_name}")
    missing = [name for name in default_names if name not in declared]
    if missing:
        raise ValueError(f"目标提交 {collection_name} 含不可解析项: {', '.join(missing)}")
    return {declared[name] for name in default_names}


def rules_at_commit(repo: Path, commit: str) -> set[str]:
    rule_ids: set[str] = set()
    for rules_path, constructor_name, collection_name, required in RULE_SOURCES:
        result = subprocess.run(
            ["git", "-C", str(repo), "show", f"{commit}:{rules_path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            if required:
                raise subprocess.CalledProcessError(
                    result.returncode, result.args, output=result.stdout, stderr=result.stderr
                )
            continue
        rule_ids.update(
            default_rule_ids(
                result.stdout,
                rules_path=rules_path,
                constructor_name=constructor_name,
                collection_name=collection_name,
            )
        )
    return rule_ids


def _show(repo, commit, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{commit}:{path}"],
                                   stderr=subprocess.PIPE, text=True)


def _book_target_rules(repo, commit, book_path, static_ids):
    """有登记时才读取目标版契约，用当前可信验证器回放；从不导入/执行目标提交。"""
    if not book_path.exists():
        return static_ids
    scripts = str(Path(__file__).resolve().parents[2] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from services.intraday_monitor.catalog import CATALOG_VERSION, RuleBook, _changes
    from services.intraday_monitor.rules import MonitorRule

    changes = _changes(book_path)
    if not changes:
        return static_ids
    try:
        tree = ast.parse(_show(repo, commit, "scripts/services/intraday_monitor/catalog.py"))
    except subprocess.CalledProcessError as exc:
        raise ValueError("目标版本不支持现有非空规则登记簿；禁止回滚") from exc
    versions = [node.value for node in tree.body if isinstance(node, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == "CATALOG_VERSION" for t in node.targets)]
    if (len(versions) != 1 or not isinstance(versions[0], ast.Constant)
            or type(versions[0].value) is not int or versions[0].value != CATALOG_VERSION):
        raise ValueError("目标规则登记簿版本不兼容")
    source = _show(repo, commit, RULE_SOURCES[0][0])
    price_ids = default_rule_ids(source)

    def literal(node):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "date"
                and not node.keywords and len(node.args) == 3):
            return date(*(ast.literal_eval(arg) for arg in node.args))
        return ast.literal_eval(node)

    defaults = {}
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        call = node.value
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "MonitorRule"):
            continue
        key = next((_literal_string(k.value) for k in call.keywords if k.arg == "rule_id"),
                   _literal_string(call.args[0]) if call.args else None)
        if key in price_ids:
            defaults[key] = MonitorRule(*(literal(arg) for arg in call.args),
                                        **{kw.arg: literal(kw.value) for kw in call.keywords})
    if defaults.keys() != price_ids:
        raise ValueError("无法静态核验目标提交中的阈值规则")
    book = RuleBook(defaults.values(), changes)
    return (static_ids - price_ids) | {r.rule_id for r in book.enabled()}


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
    parser.add_argument("--state-path", type=Path, action="append", required=True)
    args = parser.parse_args()

    try:
        target_rule_ids = rules_at_commit(args.repo, args.target_commit)
        pending_ids: set[str] = set()
        unknown_ids: set[str] = set()
        for state_path in args.state_path:
            allowed = _book_target_rules(args.repo, args.target_commit,
                                         state_path.with_name("price-rules.json"), target_rule_ids)
            ids = pending_rule_ids(state_path)
            pending_ids.update(ids)
            unknown_ids.update(ids - allowed)
    except (OSError, ValueError, TypeError, OverflowError, SyntaxError, ImportError, subprocess.CalledProcessError) as exc:
        print(json.dumps({"status": "guard_failed", "error": str(exc)}, ensure_ascii=False))
        return 2
    unknown = sorted(unknown_ids)
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
