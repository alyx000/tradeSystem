"""固定价规则登记簿。只通过管理入口追加变更，旧规则及发送账本不迁移。"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing, nullcontext
from dataclasses import replace
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

from db.connection import get_readonly_connection
from .guards import shanghai_now
from .rules import DEFAULT_RULES, MonitorRule
from .state import DEFAULT_STATE_PATH, save_state, state_lock

CATALOG_VERSION = 1  # 部署 guard 从目标 Git 对象静态读取，不执行目标代码。


def catalog_path(state_path=DEFAULT_STATE_PATH):
    return Path(state_path).with_name("price-rules.json")


def _open_window(start, days, db_path):
    if type(days) is not int or not 1 <= days <= 250:
        raise ValueError("有效期须为 1～250 个交易日")
    expected, opened = start, []
    try:
        with closing(get_readonly_connection(db_path)) as conn:
            rows = conn.execute("SELECT date, is_open FROM trade_calendar "
                                "WHERE exchange='SSE' AND date>=? ORDER BY date", (start.isoformat(),))
            for day, is_open in rows:
                if day != expected.isoformat() or is_open not in (0, 1):
                    break
                if is_open:
                    opened.append(expected)
                    if len(opened) == days:
                        return opened[0], opened[-1]
                expected += timedelta(days=1)
    except sqlite3.Error as exc:
        raise ValueError("上交所交易日历不可读") from exc
    raise ValueError(f"上交所交易日历覆盖不足：{expected}；未更改规则")


def _text(value, label, limit=80):
    if (not isinstance(value, str) or not 0 < len(value.strip()) <= limit
            or not value.isprintable()):
        raise ValueError(f"{label}不能为空、过长或含控制字符")
    return value.strip()


def _positive(value, label):
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{label}须为有限正数")
    return value


def _instrument(code, name, defaults):
    code = _text(code, "完整证券代码").upper()
    known = next((rule for rule in defaults if rule.code == code), None)
    if known:
        if name is not None and _text(name, "名称") != known.instrument_name:
            raise ValueError(f"名称与既有代码不符：{code} 为 {known.instrument_name}")
        label, unit = known.value_label, known.value_unit
        if known.value_mode != "price" and unit == "%":
            label, unit = "点位", "点"
        if label == "点位" and not unit:
            unit = "点"
        return code, known.instrument_name, known.provider, label, unit
    if not re.fullmatch(r"(?:(?:60[0135]|688)[0-9]{3}\.SH|(?:00[0123]|30[01])[0-9]{3}\.SZ|"
                        r"(?:43|82|83|87|88|92)[0-9]{4}\.BJ)", code):
        raise ValueError("仅支持已接入的指数或带正确交易所后缀的 A 股代码")
    return code, _text(name, "新证券名称", 40), "sina", "价格", "元"


class RuleBook:
    def __init__(self, defaults, changes):
        self.defaults = tuple(defaults)
        self.rules = {r.rule_id: r for r in self.defaults}
        self.disabled = set()
        for item in changes:
            self.apply(item)

    def enabled(self):
        return tuple(r for key, r in self.rules.items() if key not in self.disabled)

    def apply(self, item):
        if not isinstance(item, dict):
            raise ValueError("登记记录非法")
        key = _text(item.get("rule_id"), "规则 ID", 120)
        _text(item.get("input_by"), "请求者")
        _text(item.get("at"), "审计时间")
        action = item.get("action")
        if action == "disable":
            if key not in self.rules or key in self.disabled:
                raise ValueError("规则不存在或已经下线")
            self.disabled.add(key)
            return
        if action not in ("add", "extend"):
            raise ValueError("不支持的登记操作")
        start, end = (date.fromisoformat(_text(item.get(k), "有效日")) for k in ("start", "end"))
        if start > end:
            raise ValueError("起止日期倒置")
        if action == "add":
            if key in self.rules or not re.fullmatch(r"custom-[a-z0-9][a-z0-9._-]{0,100}", key):
                raise ValueError("新增规则 ID 须以 custom- 开头，且不能与已有或已下线规则重复")
            code, name, provider, label, unit = _instrument(item.get("code"), item.get("name"), self.rules.values())
            direction, inclusive = item.get("direction"), item.get("inclusive")
            if direction not in ("above", "below", "near") or type(inclusive) is not bool:
                raise ValueError("方向或边界非法")
            pct = item.get("proximity_pct")
            if direction == "near" and inclusive is not True:
                raise ValueError("临近范围固定含边界")
            action_label = "临近" if direction == "near" else None
            if inclusive and direction != "near":
                action_label = {"above": "达到或高于", "below": "达到或低于"}[direction]
            threshold = _positive(item.get("threshold"), "阈值")
            if Decimal(str(threshold)).normalize().as_tuple().exponent < -2:
                raise ValueError("普通价位阈值最多两位小数，避免提醒展示与判断不一致")
            rule = MonitorRule(key, name, code, threshold,
                               direction=direction, provider=provider, inclusive=inclusive,
                               proximity_pct=pct, action_label=action_label,
                               value_label=label, value_unit=unit, valid_from=start, valid_until=end)
        else:
            if key not in self.rules or key in self.disabled:
                raise ValueError("规则不存在或已经下线")
            previous = self.rules[key]
            if previous.valid_until is None or end <= previous.valid_until:
                raise ValueError("不能续期长期规则或缩短有效期")
            rule = replace(previous, valid_from=previous.valid_from if item.get("keep_start") is True else start,
                           valid_until=end)
        # 不引入同标的、同条件、有效期重叠的第二条提醒。
        for other in self.enabled():
            if (other.rule_id != key and other.code == rule.code and other.threshold_mode == rule.threshold_mode
                    and other.value_mode == rule.value_mode and other.direction == rule.direction
                    and other.threshold == rule.threshold and other.inclusive == rule.inclusive
                    and other.proximity_pct == rule.proximity_pct
                    and other.threshold_window == rule.threshold_window and other.threshold_period == rule.threshold_period
                    and max(rule.valid_from or date.min, other.valid_from or date.min)
                    <= min(end, other.valid_until or date.max)):
                raise ValueError(f"与已有规则 {other.rule_id} 的条件和有效期重叠")
        self.rules[key] = rule


def _changes(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if (not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != CATALOG_VERSION
            or not isinstance(raw.get("changes"), list)):
        raise ValueError("规则登记簿格式或版本异常；不会回退到旧规则")
    return raw["changes"]


def read_book(path, defaults=DEFAULT_RULES):
    return RuleBook(defaults, _changes(path))


def manage_rule(action, *, input_by, rule_id=None, code=None, name=None, direction=None, threshold=None,
                inclusive=False, proximity_pct=None, days=None, start=None, expected_until=None, dry_run=False,
                now=None, db_path=None, path=None, defaults=DEFAULT_RULES):
    """追加单条审计变更。预览不加锁、不建目录；正式登记与正式检查共用登记簿锁。"""
    path = Path(path) if path is not None else catalog_path()
    try:
        actor = _text(input_by, "请求者")
        with nullcontext() if dry_run else state_lock(path):
            now = shanghai_now(now)  # 等锁可能跨过 15:00，以真正登记时刻确定首日。
            changes = _changes(path)
            book = RuleBook(defaults, changes)
            item = {"action": action, "input_by": actor, "at": now.isoformat(), "rule_id": rule_id}
            earliest = now.date() + timedelta(days=now.time() >= time(15))
            if action == "add":
                since = date.fromisoformat(start) if start is not None else earliest
                if since < earliest:
                    raise ValueError("起算日不能早于本次可监控日期")
                code, name, *_ = _instrument(code, name, book.rules.values())
                value = _positive(threshold, "阈值")
                first, last = _open_window(since, days, db_path)
                if rule_id is None:
                    suffix = f"-p{proximity_pct}" if direction == "near" else "-incl" if inclusive else ""
                    item["rule_id"] = f"custom-{code.lower()}-{direction}-{value}{suffix}-{first}".replace("+", "")
                item.update(code=code, name=name, direction=direction, threshold=value,
                            inclusive=True if direction == "near" else inclusive,
                            proximity_pct=proximity_pct, start=str(first), end=str(last))
            elif action == "extend":
                rule = book.rules.get(rule_id)
                if not rule or rule_id in book.disabled or not rule.valid_until:
                    raise ValueError("仅能续期有截止日且未下线的规则")
                if expected_until is not None and str(rule.valid_until) != expected_until:
                    raise ValueError(f"截止日已变化：当前为 {rule.valid_until}，未重复续期")
                first, last = _open_window(max(earliest, rule.valid_until + timedelta(days=1)), days, db_path)
                item.update(start=str(first), end=str(last), keep_start=rule.valid_until >= now.date())
            book.apply(item)
            if not dry_run:
                save_state(path, {"version": CATALOG_VERSION, "changes": changes + [item]})
            rule = book.rules[item["rule_id"]]
            return {"status": "preview" if dry_run else "ok", **item,
                    "valid_from": rule.valid_from.isoformat() if rule.valid_from else None,
                    "valid_until": rule.valid_until.isoformat() if rule.valid_until else None}
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        return {"status": "error", "error": str(exc)}
