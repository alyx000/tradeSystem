"""按上一开放日晚的复盘选题留存当日同花顺概念成员；当日排名只作背景。"""
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

from analyzers.strong_stock_feedback_history import _envelope
from analyzers.theme_launch import SOURCE, _members, rankings
from db.connection import get_readonly_connection
from services.handwritten_review import DEFAULT_ARCHIVE, load_entry_before
from services.volume_concentration.capacity_history import _open_days


KEY = "tracked_theme_members"
SCHEMA = "tracked-theme-members-v1"
CATALOG = "tushare:ths_index:concept_snapshot"
SHANGHAI = ZoneInfo("Asia/Shanghai")
TI_CODE = re.compile(r"[0-9]{6}\.TI")
# 有明确身份的少数常用简称/官方名称；任一条均须在事实日重新核对代码及官方全称。
KNOWN_CONCEPTS = {"PCB": ("885959.TI", "PCB概念"), "CPO": ("886033.TI", "共封装光学(CPO)"),
                  "染料": ("885633.TI", "染料")}
FOCUS = re.compile(r"^\s*(?:[-*]\s*)?(?:次日跟踪题材|跟踪细分题材)[：:]\s*(.*?)\s*$")


def _requests(entry):
    """原文/次日条件显式指令优先；否则仅取既有题材分组的编号标题，正文不参与。"""
    mentions = {}
    explicit = False
    explicitly_empty = False

    def add(term, line, source):
        term = term.strip().strip("`").strip()
        if term and term not in mentions:
            mentions[term] = dict(requested=term, quote=line, source=source)

    for source, body in (("user_handwritten", entry["raw"]),
                         ("next_session", entry["sections"]["次日具体条件"])):
        for line in body.splitlines():
            match = FOCUS.fullmatch(line)
            if match:
                explicit = True
                if not match[1].strip():
                    raise ValueError("跟踪题材指令为空，无法判定是否停止")
                for term in re.split(r"[、,，;；]", match[1]):
                    if term.strip() in {"无", "无跟踪题材"}:
                        explicitly_empty = True
                    else:
                        add(term, line, source)
        if explicit:
            if explicitly_empty == bool(mentions):
                raise ValueError("跟踪题材指令冲突或未给出有效名称")
            return list(mentions.values()), source
    active, found = False, False
    for line in entry["sections"]["固定对象跨日反馈"].splitlines():
        if line.startswith("#"):
            active = bool(re.fullmatch(r"#{3,6}\s+题材分组与新增观察(?:（[^）]*）)?\s*", line))
            found = found or active
        if not active:
            continue
        title = re.fullmatch(r"\s*\*\*\d+[.、]\s*(.+?)\*\*\s*", line)
        if not title:
            continue
        label = re.split(r"[⬆⬇↕↑↓：:]", title[1], maxsplit=1)[0]
        # 例如「海外 AI 硬件（CPO／PCB）」；括号内自由点评不当作题材。
        subgroups = re.findall(r"（([^（）]+)）", label)
        label = re.sub(r"（.*$", "", label)
        parts = [label] + [s for s in subgroups if re.fullmatch(r"[\w ]{1,16}(?:[/／、][\w ]{1,16})+", s)]
        for part in parts:
            for term in re.split(r"[／/、＋+]", part):
                add(term, line, "handwritten_theme_group")
    if not found or not mentions:
        raise ValueError("前夜复盘没有可核验的跟踪题材或题材分组标题")
    return list(mentions.values()), "handwritten_theme_group"


def _catalog_rows(snapshot, day):
    if not isinstance(snapshot, dict) or snapshot.get("date") != day:
        raise ValueError("同日完整概念目录缺失")
    stamp = datetime.fromisoformat(snapshot["fetched_at"])
    rows, pages = snapshot["rows"], snapshot["pages"]
    if (stamp.tzinfo is None or stamp.astimezone(SHANGHAI).date().isoformat() != day
            or not isinstance(rows, list) or not rows or not isinstance(pages, list)
            or not 2 <= len(pages) <= 6 or pages[-1] != 0
            or any(type(n) is not int or not 1 <= n <= 1000 for n in pages[:-1]) or sum(pages) != len(rows)):
        raise ValueError("同日概念目录分页未闭合")
    catalog = {}
    for row in rows:
        code, name = row["ts_code"], row["name"]
        if (not isinstance(code, str) or not TI_CODE.fullmatch(code) or code in catalog
                or row.get("type") != "N" or row.get("exchange") != "A"
                or not isinstance(name, str) or not name.strip()):
            raise ValueError("同花顺概念目录身份冲突")
        catalog[code] = name.strip()
    return catalog


def _daily_identities(raw, day):
    """同日概念资金流可证实指定代码；榜表不完整，不能证明纯名称在全目录唯一。"""
    section = raw.get("concept_moneyflow_ths") or {}
    rows = section.get("data")
    if section.get("_source") != "tushare:moneyflow_cnt_ths" or not isinstance(rows, list):
        return {}
    found = {}
    for row in rows:
        code, name = row.get("ts_code"), row.get("name")
        if (row.get("trade_date") != day.replace("-", "") or not isinstance(code, str)
                or not TI_CODE.fullmatch(code) or code in found or not isinstance(name, str) or not name.strip()):
            return {}
        found[code] = name.strip()
    return found


def _resolve(term, catalog):
    if term in KNOWN_CONCEPTS:
        code, official = KNOWN_CONCEPTS[term]
        return code if catalog.get(code) == official else None
    # 编码和名称同时给出时两者必须相符；不自动剥离“概念”后缀。
    match = re.fullmatch(r"(?:(.*?)\s+)?`?([0-9]{6}\.TI)`?", term)
    if match:
        name, code = match.groups()
        return code if code in catalog and (not name or name == catalog[code]) else None
    matches = [code for code, name in catalog.items() if name == term]
    return matches[0] if len(matches) == 1 else None


def _stored(raw, code, day):
    """仅回用可重新由完整分页收据还原的名单，含前十既有块。"""
    tracked = raw.get(KEY) or {}
    candidates = []
    if tracked.get("schema") == SCHEMA and tracked.get("trade_date") == day:
        candidates.append((tracked.get("membership_source"), (tracked.get("snapshots") or {}).get(code)))
    launch = raw.get("theme_launch") or {}
    if launch.get("trade_date") == day:
        ev = launch.get("evidence") or {}
        candidates.append((ev.get("membership_source"), ev.get("membership")))
    for source, snap in candidates:
        if source == SOURCE:
            try:
                members = _members(snap, code, day)
                return {k: snap[k] for k in ("date", "fetched_at")} | {"themes": {code: snap["themes"][code]}}, members
            except (ValueError, TypeError, KeyError, AttributeError):
                continue
    return None, None


def _archive(daily_dir, day):
    envelope = _envelope(daily_dir, day, {})
    return envelope["raw_data"] if isinstance(envelope, dict) else {}


def _checksum(block):
    content = {k: v for k, v in block.items() if k != "sha256"}
    return sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _preservable(block, day, previous):
    if not isinstance(block, dict) or not block or block.get("trade_date") != day or block.get("schema") != SCHEMA:
        return False
    selection = block.get("selection") or {}
    if (block.get("sha256") != _checksum(block) or block.get("status") not in {"complete", "partial", "fallback_preserved"}
            or selection.get("date") != previous or block.get("previous_date") != previous):
        return False
    return any(_stored({KEY: block}, row["code"], day)[0] is not None
               for row in block.get("rows", []) if row.get("status") == "complete" and row.get("code"))


def collect(registry, day, raw, *, daily_dir, db_path=None, archive_root=DEFAULT_ARCHIVE, now=None):
    """registry=None 时只读取事实日已有证据，可独立复核；不会写归档或回填历史。"""
    result = dict(schema=SCHEMA, trade_date=day, previous_date=None, status="source_failed",
                  selection=None, membership_source=SOURCE, catalog_source=CATALOG,
                  catalog=None, snapshots={}, rows=[], gaps=[])
    saved_tracked = {}
    try:
        stamp = now or datetime.now(SHANGHAI)
        if stamp.tzinfo is None or not isinstance(raw, dict) or raw.get("date") != day:
            raise ValueError("目标日期或运行时区未核验")
        local = stamp.astimezone(SHANGHAI)
        target = date.fromisoformat(day)
        if target > local.date():
            raise ValueError("目标日在未来")
        with closing(get_readonly_connection(db_path)) as conn:
            flag = conn.execute("SELECT is_open FROM trade_calendar WHERE exchange='SSE' AND date=?", (day,)).fetchone()
            if flag and flag[0] == 0:
                result.update(status="skipped", gaps=["休市日"])
                return result
            prev, _ = _open_days(conn, day, 2)
        if target == local.date() and local.hour < 16:
            result.update(status="skipped", gaps=["尚未到当日盘后采集时间"])
            return result
        result["previous_date"] = prev
        saved = _archive(daily_dir, day)
        saved_tracked = saved.get(KEY) or {}
        entry, _, ignored = load_entry_before(Path(archive_root) / prev, datetime.fromisoformat(day + "T09:30:00+08:00"))
        requests, basis = _requests(entry)
        meta = entry["meta"]
        result["selection"] = {key: meta[key] for key in ("date", "recorded_at", "source_sha256", "supplement_sha256", "input_by")}
        result["selection"].update(revision=meta.get("revision", 0), basis=basis, ignored_late_revisions=ignored,
                                   requests=requests)
        if not requests:
            result["status"] = "empty"
            return result
        live = registry is not None and target == local.date()
        catalog_snapshot = saved_tracked.get("catalog") if saved_tracked.get("catalog_source") == CATALOG else None
        try:
            catalog = _catalog_rows(catalog_snapshot, day)
        except (ValueError, KeyError, TypeError, AttributeError):
            response = registry.call("get_concept_catalog_snapshot", day) if live else None
            if response is not None and response.success and response.source == CATALOG:
                catalog_snapshot = response.data
                catalog = _catalog_rows(catalog_snapshot, day)
            else:
                catalog_snapshot = None
                catalog = _daily_identities(raw, day)
                result["gaps"].append("事实日完整概念目录未归档或获取失败；不证明纯名称唯一")
        result["catalog"] = catalog_snapshot
        by_code = {}
        for item in requests:
            term = item["requested"]
            explicit_code = bool(re.search(r"[0-9]{6}\.TI`?$", term))
            code = _resolve(term, catalog) if catalog_snapshot is not None or explicit_code or term in KNOWN_CONCEPTS else None
            if not code:
                candidates = [key for key, name in catalog.items() if term == name]
                result["rows"].append(dict(name=term, status="identity_unverified", code=None, added=None,
                                           removed=None, candidate_codes=candidates,
                                           reason="名称未精确唯一匹配或代码与名称不一致"))
            else:
                by_code[code] = catalog[code]
                item.update(code=code, official_name=catalog[code],
                            identity_basis=("fixed_code_and_official_name" if term in KNOWN_CONCEPTS
                                            else "explicit_code" if explicit_code else "unique_catalog_name"),
                            identity_source=CATALOG if catalog_snapshot is not None else "tushare:moneyflow_cnt_ths")
        if catalog_snapshot is None:
            result["daily_identity_source"] = "tushare:moneyflow_cnt_ths"
            result["daily_identity_evidence"] = {code: name for code, name in catalog.items()
                if code in by_code or any(item["requested"] == name for item in requests)}
        try:
            top = {r["code"]: r["rank"] for r in rankings(raw.get("limit_cpt_list"), day)}
        except (ValueError, KeyError, TypeError, AttributeError):
            top = None
        prior_raw = _archive(daily_dir, prev) if by_code else {}
        missing = []
        for code in by_code:
            snap, _ = _stored(raw, code, day)
            if snap is None:
                snap, _ = _stored(saved, code, day)
            if snap is None:
                missing.append(code)
            else:
                result["snapshots"][code] = snap
        if live:
            for offset in range(0, len(missing), 10):
                batch = missing[offset:offset + 10]
                response = registry.call("get_theme_member_snapshot", day, batch)
                if response.success and response.source == SOURCE and isinstance(response.data, dict):
                    for code in batch:
                        snap = response.data
                        result["snapshots"][code] = dict(date=snap.get("date"), fetched_at=snap.get("fetched_at"),
                                                       themes={code: (snap.get("themes") or {}).get(code)})
        for code, name in by_code.items():
            row = dict(code=code, name=name, status="source_failed", count=None, added=None, removed=None,
                       top10_rank=top.get(code) if top is not None else None,
                       top10_known=top is not None and (code in top or len(top) == 10))
            result["rows"].append(row)
            try:
                current = _members(result["snapshots"].get(code), code, day)
            except (ValueError, KeyError, TypeError, AttributeError):
                row["reason"] = "事实日成员未取得或分页未闭合"
                continue
            row.update(status="complete", count=len(current))
            _, prior = _stored(prior_raw, code, prev)
            if prior is None:
                row["comparison_status"] = "previous_unavailable"
            else:
                row.update(comparison_status="complete", added=sorted(current.keys() - prior.keys()),
                           removed=sorted(prior.keys() - current.keys()))
        result["status"] = "complete" if all(r["status"] == "complete" for r in result["rows"]) else "partial"
        if top is None:
            result["gaps"].append("当日题材榜未核验，榜内外未知；成员独立采集")
    except (ValueError, FileNotFoundError) as exc:
        result["gaps"].append(str(exc) if isinstance(exc, ValueError) else "上一开放日手写复盘未归档")
    except Exception as exc:
        result["gaps"].append("跟踪题材采集失败：" + type(exc).__name__)
    try:
        preserve = result["status"] == "source_failed" and _preservable(saved_tracked, day, result["previous_date"])
    except (ValueError, TypeError, AttributeError, KeyError):
        preserve = False
    if preserve:
        selection = result["selection"]
        current_selection = (selection or {}).get("supplement_sha256")
        old_selection = saved_tracked["selection"]["supplement_sha256"]
        if current_selection is None or current_selection == old_selection:
            attempt = dict(status="source_failed", gaps=result["gaps"])
            result = dict(saved_tracked, status="fallback_preserved", latest_attempt=attempt)
    result["sha256"] = _checksum(result)
    return result
