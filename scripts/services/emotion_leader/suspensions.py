"""只读核验同日停牌事实；不回写历史报告、不沿用前日停牌状态。"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path

MISSING_QUOTE = "目标日行情缺失或陈旧"


def load_suspensions(conn, day: str) -> dict:
    """只认最新同日成功快照。日内停牌有 timing，不能豁免日线缺失。

    来源定义：https://tushare.pro/document/2?doc_id=214
    """
    try:
        row = conn.execute(
            "SELECT id,provider,status,row_count,payload_json FROM raw_interface_payloads "
            "WHERE interface_name='regulatory_suspend' AND biz_date=? AND target_date=? "
            "ORDER BY id DESC LIMIT 1", (day, day),
        ).fetchone()
        if not row or row[1] != "tushare:suspend_d" or row[2] != "success":
            return {}
        payload = json.loads(row[4])
        rows = payload.get("rows")
        if (payload.get("biz_date") != day or payload.get("provider") != row[1]
                or payload.get("interface_name") != "regulatory_suspend"
                or payload.get("params", {}).get("trade_date") != day.replace("-", "")
                or not isinstance(rows, list) or len(rows) != row[3]):
            return {}
        result, seen = {}, set()
        for item in rows:
            code = item.get("ts_code", "")
            if (not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code) or code in seen
                    or item.get("code", code) != code
                    or item.get("trade_date") != day.replace("-", "")):
                return {}
            seen.add(code)
            if item.get("suspend_type") == "S" and "suspend_timing" in item and item["suspend_timing"] in (None, ""):
                result[code] = {"date": day, "code": code, "source": row[1],
                                "raw_payload_id": row[0], "suspend_type": "S", "full_day": True}
        return result
    except (sqlite3.Error, ValueError, TypeError, AttributeError):
        return {}


def is_verified_suspension(row: dict, day: str) -> bool:
    evidence = row.get("suspension_evidence")
    return (row.get("metric_status") == "suspended" and isinstance(evidence, dict)
            and evidence.get("date") == day and evidence.get("code") == row.get("code")
            and evidence.get("source") == "tushare:suspend_d" and evidence.get("full_day") is True
            and evidence.get("suspend_type") == "S"
            and type(evidence.get("raw_payload_id")) is int and evidence["raw_payload_id"] > 0
            and row.get("today_pct_chg") is None and row.get("new_peak_today") is not True
            and row.get("current_state") == "停牌")


def reconcile_suspensions(payload: dict, evidence: dict) -> dict:
    """仅解释可核实的缺目标日日线，不吞掉其他来源错误或整日报告失败。"""
    if payload.get("status") not in ("ok", "partial"):
        return payload
    if (not isinstance(payload.get("active"), list) or not isinstance(payload.get("archived", []), list)
            or not isinstance(payload.get("summary"), dict)
            or any(not isinstance(r, dict) for r in payload["active"] + payload.get("archived", []))):
        return payload
    result = copy.deepcopy(payload)
    day = result.get("date")
    errors = result.get("source_errors")
    if not isinstance(errors, list):
        return payload
    previous = payload.get("suspension_reconciliation") or {}
    preserve_partial = bool(previous.get("preserve_partial")) or (payload["status"] == "partial" and not errors)
    rechecked = False
    # 已保存的 suspended 也必须重新对照当前快照；不能凭旧记录编号继续豁免。
    for row in result.get("active", []) + result.get("archived", []):
        if row.get("metric_status") == "suspended":
            row.update(metric_status="source_failed", metric_error=MISSING_QUOTE,
                       current_state="未计算", feedback_type="未计算")
            row.pop("suspension_evidence", None)
            row.pop("metric_note", None)
            error = f"{row.get('code')}:{MISSING_QUOTE}"
            if error not in errors:
                errors.append(error)
            rechecked = True
    if rechecked:
        result["status"] = "partial"
    resolved = []
    for row in result.get("active", []) + result.get("archived", []):
        proof = evidence.get(row.get("code"))
        if (not proof or proof.get("date") != day
                or row.get("metric_status") != "source_failed"
                or row.get("metric_error") != MISSING_QUOTE
                or row.get("metric_as_of") != day
                or row.get("current_state") in ("涨停", "跌停")
                or any(row.get(k) is not None for k in ("today_pct_chg", "current_close_qfq", "new_peak_today"))):
            continue
        row.update(metric_status="suspended", current_state="停牌", feedback_type="停牌",
                   suspension_evidence=proof, metric_note="同日全天停牌，指标不适用，不补零")
        row.pop("metric_error", None)
        resolved.append({"code": row["code"], "name": row.get("name", ""), **proof})
    if not resolved and not rechecked:
        return payload
    removed = {f"{item['code']}:{MISSING_QUOTE}" for item in resolved}
    result["source_errors"] = [error for error in errors if error not in removed]
    active = result["active"]
    summary = result.get("summary", {})
    summary.update(metric_available_count=sum(r.get("metric_status") == "ok" for r in active),
                   suspended_count=sum(is_verified_suspension(r, day) for r in active))
    summary["unresolved_count"] = len(active) - summary["metric_available_count"] - summary["suspended_count"]
    coverage = result.get("coverage") or {}
    # 只在原 partial 的已知错误确实被解决且所有其他边界完整时升级。
    if (not preserve_partial and (set(payload["source_errors"]) & removed or payload["status"] == "ok")
            and not result["source_errors"] and result.get("missing_dates") == []
            and type(coverage.get("expected_open_days")) is int and coverage["expected_open_days"] > 0
            and coverage["expected_open_days"] == coverage.get("loaded_limit_days")
            and all(r.get("metric_status") == "ok" or is_verified_suspension(r, day) for r in active)
            # 增量模式归档行不参与当日计算，cached_archived 是正常状态。
            and all(r.get("metric_status") in ("ok", "cached_archived") or is_verified_suspension(r, day)
                    for r in result.get("archived", []))):
        result["status"] = "ok"
    result["suspension_reconciliation"] = {"original_status": previous.get("original_status", payload["status"]),
                                           "preserve_partial": preserve_partial, "resolved": resolved}
    return result


def reconcile_report_file(payload: dict, db_path, day: str) -> dict:
    if payload.get("date") != day:
        return payload
    if db_path is None or not Path(db_path).is_file():
        return reconcile_suspensions(payload, {})
    try:
        with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as conn:
            return reconcile_suspensions(payload, load_suspensions(conn, day))
    except sqlite3.Error:
        return reconcile_suspensions(payload, {})
