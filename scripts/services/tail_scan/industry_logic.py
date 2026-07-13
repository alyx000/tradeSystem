"""尾盘扫描的个股主营、产业位置与近期催化证据聚合。

本模块只做确定性归一和只读检索：主营资料来自 provider，催化来自老师笔记、
慧博摘要和行业信息。行业位置是受控模板生成的 ``[判断]``，不会扩写输入中没有的
产业链环节、市场地位或价格判断。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
from typing import Optional, Union

from services.tail_scan import constants as C


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_HUIBO_DIR = REPO_ROOT / "data/reports/huibo/summaries"

STOP_TOKENS = {"科技", "行业", "概念", "设备", "材料"}
SUBSTRING_MATCH_MIN_NEEDLE_CHARS = 4
DIRECT_KINDS = {"teacher_stock", "huibo_stock", "huibo_relation"}
KIND_PRIORITY = {
    "teacher_stock": 0,
    "huibo_stock": 1,
    "huibo_relation": 2,
    "industry": 3,
}

_EVIDENCE_META = {
    "teacher_stock": "老师观点·个股",
    "huibo_stock": "研报观点·个股催化",
    "huibo_relation": "来源陈述·个股关联",
    "industry": "事实·行业催化",
    "industry_unlabeled": "来源陈述·行业催化",
}
_SEMANTIC_TAGS = (
    "[事实]",
    "[传闻]",
    "[判断]",
    "[待核验]",
    "[来源陈述]",
    "[观点]",
    "[老师观点]",
    "[边界]",
    "[风险]",
)
_SEMANTIC_TAG_PATTERN = re.compile(
    "(" + "|".join(re.escape(tag) for tag in _SEMANTIC_TAGS) + ")"
)


def _code(value: str) -> str:
    """把裸 A 股代码补成统一交易所后缀；已有后缀只做大小写归一。"""
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    if "." in raw:
        return raw
    if raw.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{raw}.BJ"
    if raw.startswith(("60", "68", "90")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _clip(value, limit: int = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS) -> str:
    """压缩全部空白并按字符数裁剪为单行文本。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


def _evidence(kind: str, label: str, date: str, source, text) -> Optional[dict]:
    clean_date = str(date or "").strip()
    clean_source = _clip(source, 60)
    clean_text = _clip(text, C.INDUSTRY_LOGIC_TEXT_MAX_CHARS)
    try:
        dt.date.fromisoformat(clean_date)
    except ValueError:
        return None
    if not clean_text:
        return None
    return {
        "kind": kind,
        "label": label,
        "date": clean_date,
        "source": clean_source or "来源未标注",
        "text": clean_text,
    }


def _select_evidence(items: list[dict]) -> list[dict]:
    """按来源级别、同级日期倒序稳定去重，最多保留两条。"""
    seen = set()
    deduped = []
    for item in items:
        key = (
            item.get("kind"),
            item.get("date"),
            item.get("source"),
            item.get("text"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return sorted(
        deduped,
        key=lambda item: (
            KIND_PRIORITY.get(item.get("kind"), 99),
            -int(str(item.get("date") or "0").replace("-", "")),
            str(item.get("source") or ""),
            str(item.get("text") or ""),
        ),
    )[: C.INDUSTRY_LOGIC_MAX_CATALYSTS]


def _empty_profile(code: str, status: str) -> dict:
    return {
        "ts_code": code,
        "profile_status": status,
        "introduction": "",
        "main_business": "",
        "business_scope": "",
        "product_types": [],
        "product_names": [],
        "source": "",
        "error": "",
    }


def _load_profiles(registry, ts_codes: list[str]) -> dict[str, dict]:
    """批量读取主营；Tushare 只对非 ok 股票做 AkShare 补齐。"""
    normalized_codes = {_code(code) for code in ts_codes}
    codes = sorted(code for code in normalized_codes if code)
    if not codes:
        return {}
    try:
        result = registry.call("get_stock_business_profiles", codes)
    except Exception:
        return {code: _empty_profile(code, "source_failed") for code in codes}
    if not getattr(result, "success", False) or not isinstance(result.data, dict):
        return {code: _empty_profile(code, "source_failed") for code in codes}

    profiles = {
        code: result.data.get(code, _empty_profile(code, "missing"))
        for code in codes
    }
    source = str(getattr(result, "source", "") or "").lower()
    if not source.startswith("tushare"):
        return profiles

    fallback_codes = sorted(
        code
        for code, row in profiles.items()
        if not isinstance(row, dict) or row.get("profile_status") != "ok"
    )
    if not fallback_codes:
        return profiles

    try:
        fallback = registry.call_specific(
            "akshare", "get_stock_business_profiles", fallback_codes
        )
    except Exception:
        fallback = None

    fallback_ok = (
        getattr(fallback, "success", False)
        and isinstance(getattr(fallback, "data", None), dict)
    )
    for code in fallback_codes:
        primary = profiles.get(code)
        if not isinstance(primary, dict):
            primary = _empty_profile(code, "missing")
        if fallback_ok:
            secondary = fallback.data.get(code, _empty_profile(code, "missing"))
            if not isinstance(secondary, dict):
                secondary = _empty_profile(code, "missing")
            if secondary.get("profile_status") == "ok":
                profiles[code] = secondary
                continue
            statuses = {
                primary.get("profile_status"),
                secondary.get("profile_status"),
            }
            merged_status = "missing" if "missing" in statuses else "source_failed"
            profiles[code] = _empty_profile(code, merged_status)
        else:
            primary_status = primary.get("profile_status")
            merged_status = "missing" if primary_status == "missing" else "source_failed"
            profiles[code] = _empty_profile(code, merged_status)
    return profiles


def _industry_position(sw_l2: str, business: str, products: list[str]) -> str:
    """只复用传入行业、主营、产品的受控产业位置模板。"""
    sw_l2 = _clip(sw_l2)
    business = _clip(business)
    cleaned_products = (
        _clip(item, 40) for item in products[: C.INDUSTRY_LOGIC_MAX_PRODUCTS]
    )
    product_text = "、".join(item for item in cleaned_products if item)
    if sw_l2 and product_text:
        return _clip(f"{sw_l2}产业链企业，核心产品包括{product_text}")
    if sw_l2 and business:
        return _clip(f"{sw_l2}领域企业，主营{business}")
    if sw_l2:
        return _clip(f"{sw_l2}相关企业")
    if business:
        return _clip(f"主营{business}")
    return ""


def _read_teacher_evidence(
    conn,
    start_date: str,
    end_date: str,
    code_to_name: dict[str, str],
) -> tuple[dict[str, list[dict]], bool]:
    """按股票标准代码精确读取老师笔记中的直接提及。"""
    try:
        rows = conn.execute(
            "SELECT id,date,title,mentioned_stocks FROM teacher_notes "
            "WHERE date BETWEEN ? AND ? ORDER BY date DESC, id DESC",
            (start_date, end_date),
        ).fetchall()
        output: dict[str, list[dict]] = {}
        for _note_id, date, title, raw_stocks in rows:
            try:
                stocks = json.loads(raw_stocks or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(stocks, list):
                continue
            for stock in stocks:
                if not isinstance(stock, dict):
                    continue
                code = _code(stock.get("code"))
                if code not in code_to_name:
                    continue
                text = stock.get("reason") or stock.get("viewpoint")
                if not _clip(text):
                    text = f"老师笔记直接提及{code_to_name.get(code) or code}"
                item = _evidence(
                    "teacher_stock",
                    _EVIDENCE_META["teacher_stock"],
                    str(date or ""),
                    title or "老师笔记",
                    text,
                )
                if item is not None:
                    output.setdefault(code, []).append(item)
        return output, True
    except Exception:
        return {}, False


def _parse_date(value) -> str:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    if not match:
        return ""
    candidate = match.group(0)
    try:
        dt.date.fromisoformat(candidate)
    except ValueError:
        return ""
    return candidate


def _huibo_row_date(reader: dict, row: dict, path: pathlib.Path) -> str:
    for value in (
        reader.get("pdf_report_date"),
        row.get("huibo_list_time"),
        path.stem,
    ):
        parsed = _parse_date(value)
        if parsed:
            return parsed
    return ""


def _huibo_availability_date(row: dict, path: pathlib.Path) -> str:
    """研报进入本地摘要源的可用日；独立于 PDF 内容日期。"""
    list_time = row.get("huibo_list_time")
    if list_time is not None and str(list_time).strip():
        return _parse_date(list_time)
    return _parse_date(path.stem)


def _read_huibo_evidence(
    huibo_dir: Union[pathlib.Path, str],
    start_date: str,
    end_date: str,
    code_to_name: dict[str, str],
) -> tuple[dict[str, list[dict]], bool]:
    """按股票全名精确读取慧博摘要；单文件损坏不影响其它文件。"""
    try:
        directory = pathlib.Path(huibo_dir)
        if not directory.is_dir():
            return {}, False
        files = sorted(directory.glob("*.json"))
        if not files:
            return {}, True

        names_to_codes: dict[str, list[str]] = {}
        for code, name in code_to_name.items():
            if name:
                names_to_codes.setdefault(str(name), []).append(code)

        output: dict[str, list[dict]] = {}
        valid_files = 0
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            reader_results = payload.get("reader_results")
            if not isinstance(reader_results, list):
                continue
            valid_files += 1
            for row in reader_results:
                if not isinstance(row, dict):
                    continue
                reader = row.get("reader")
                if not isinstance(reader, dict):
                    continue
                availability_date = _huibo_availability_date(row, path)
                if not availability_date or availability_date > end_date:
                    continue
                evidence_date = _huibo_row_date(reader, row, path)
                if not evidence_date or not (start_date <= evidence_date <= end_date):
                    continue
                stocks = reader.get("mentioned_stocks")
                if not isinstance(stocks, list):
                    continue
                for stock in stocks:
                    if not isinstance(stock, dict):
                        continue
                    matched_codes = names_to_codes.get(str(stock.get("name") or ""), [])
                    if not matched_codes:
                        continue
                    viewpoint = _clip(stock.get("viewpoint"))
                    relation = _clip(stock.get("source"))
                    if viewpoint:
                        kind, text = "huibo_stock", viewpoint
                    elif relation:
                        kind, text = "huibo_relation", relation
                    else:
                        continue
                    source = row.get("title") or "慧博研报"
                    item = _evidence(
                        kind,
                        _EVIDENCE_META[kind],
                        evidence_date,
                        source,
                        text,
                    )
                    if item is not None:
                        for code in matched_codes:
                            output.setdefault(code, []).append(item)
        return (output, True) if valid_files else ({}, False)
    except Exception:
        return {}, False


def _sector_tokens(sector_name: str) -> list[str]:
    raw_tokens = re.split(r"[/、,，]+", str(sector_name or ""))
    tokens = []
    for raw in raw_tokens:
        token = re.sub(r"\s+", "", raw)
        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", token))
        if chinese_count >= 2 and token not in STOP_TOKENS:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


def _token_matches(token: str, haystacks: list[str]) -> bool:
    token = re.sub(r"\s+", "", str(token or ""))
    usable = []
    for text in haystacks:
        cleaned = re.sub(r"\s+", "", str(text or ""))
        if len(cleaned) >= 2 and cleaned not in STOP_TOKENS:
            usable.append(cleaned)
    return any(
        token == text
        or (
            len(token) >= SUBSTRING_MATCH_MIN_NEEDLE_CHARS
            and token in text
        )
        or (
            len(text) >= SUBSTRING_MATCH_MIN_NEEDLE_CHARS
            and text in token
        )
        for text in usable
    )


def _tokens_match(tokens: list[str], haystacks: list[str]) -> bool:
    return any(_token_matches(token, haystacks) for token in tokens)


def _fact_only_industry_text(content: str) -> str:
    """存在显式标签时仅保留事实段；无标签的历史内容保持兼容。"""
    text = str(content or "").strip()
    parts = _SEMANTIC_TAG_PATTERN.split(text)
    if len(parts) == 1:
        return _clip(text)
    current = ""
    facts = []
    for part in parts:
        if part in _SEMANTIC_TAGS:
            current = part
        elif current == "[事实]" and part.strip():
            facts.append(part.strip())
    return _clip(" ".join(facts))


def _industry_evidence_label(content: str) -> str:
    if _SEMANTIC_TAG_PATTERN.search(str(content or "")):
        return _EVIDENCE_META["industry"]
    return _EVIDENCE_META["industry_unlabeled"]


def _read_industry_evidence(
    conn,
    start_date: str,
    end_date: str,
    profiles: dict[str, dict],
) -> tuple[dict[str, list[dict]], bool]:
    """把行业信息仅匹配到已有行业、主营、产品或概念标签。"""
    try:
        rows = conn.execute(
            "SELECT date,sector_name,content,source FROM industry_info "
            "WHERE date BETWEEN ? AND ? ORDER BY date DESC",
            (start_date, end_date),
        ).fetchall()
        output: dict[str, list[dict]] = {}
        for date, sector_name, content, source in rows:
            tokens = _sector_tokens(sector_name)
            if not tokens:
                continue
            fact_text = _fact_only_industry_text(content)
            if not fact_text:
                continue
            for code, profile in profiles.items():
                haystacks = [
                    profile.get("sw_l2", ""),
                    profile.get("business_summary", ""),
                    *(profile.get("product_names") or []),
                    *(profile.get("concept_names") or []),
                ]
                if not _tokens_match(tokens, haystacks):
                    continue
                item = _evidence(
                    "industry",
                    _industry_evidence_label(content),
                    str(date or ""),
                    source or "行业信息",
                    fact_text,
                )
                if item is not None:
                    output.setdefault(code, []).append(item)
        return output, True
    except Exception:
        return {}, False


def build_industry_logic_map(
    conn,
    registry,
    candidates: list[dict],
    *,
    scan_date: str,
    industry_map: dict,
    concept_map: dict,
    lookback_days: int = C.INDUSTRY_LOGIC_LOOKBACK_DAYS,
    huibo_dir: Optional[Union[pathlib.Path, str]] = None,
) -> dict[str, dict]:
    """构建每只尾盘候选的主营、产业位置和近期证据。"""
    if not candidates:
        return {}

    end = dt.datetime.strptime(scan_date, "%Y-%m-%d").date()
    start = end - dt.timedelta(days=lookback_days)
    start_s, end_s = start.isoformat(), end.isoformat()
    code_to_name = {}
    for row in candidates:
        code = _code(row.get("code"))
        if code:
            code_to_name[code] = str(row.get("name") or "")
    # TODO(owner=tail-scan, trigger=加入 historical replay/backfill 或允许生成历史报告,
    # deadline=该能力上线前): 当前 provider 无 as-of 口径，`--date` 不代表可审计的历史回放；
    # 此处主营是 fetch 时的当前公开资料。触发后必须持久化 profile snapshot_date，并强制
    # snapshot_date <= scan_date，历史扫描才能使用对应快照。
    profiles = _load_profiles(registry, list(code_to_name))

    base: dict[str, dict] = {}
    for code in code_to_name:
        profile = profiles.get(code, _empty_profile(code, "source_failed"))
        if not isinstance(profile, dict):
            profile = _empty_profile(code, "source_failed")
        status = str(profile.get("profile_status") or "source_failed")
        if status not in {"ok", "missing", "source_failed"}:
            status = "source_failed"
        business = _clip(profile.get("main_business") or profile.get("introduction"))
        products = []
        for product in profile.get("product_names") or []:
            cleaned = _clip(product, 40)
            if cleaned and cleaned not in products:
                products.append(cleaned)
        products = products[: C.INDUSTRY_LOGIC_MAX_PRODUCTS]
        sw_l2 = _clip((industry_map.get(code) or {}).get("sw_l2"))
        visible_business = business if status == "ok" else ""
        visible_products = products if status == "ok" else []
        base[code] = {
            "sw_l2": sw_l2,
            "business_summary": visible_business,
            "product_names": visible_products,
            "business_source": _clip(profile.get("source"), 60) if status == "ok" else "",
            "business_status": status,
            "industry_position": _industry_position(
                sw_l2, visible_business, visible_products
            ),
            "concept_names": list(concept_map.get(code.split(".")[0], []) or []),
        }

    teacher, teacher_ok = _read_teacher_evidence(
        conn, start_s, end_s, code_to_name
    )
    huibo, huibo_ok = _read_huibo_evidence(
        pathlib.Path(huibo_dir) if huibo_dir is not None else DEFAULT_HUIBO_DIR,
        start_s,
        end_s,
        code_to_name,
    )
    industry, industry_ok = _read_industry_evidence(
        conn, start_s, end_s, base
    )
    all_sources_failed = not any((teacher_ok, huibo_ok, industry_ok))

    output: dict[str, dict] = {}
    for code, row in base.items():
        combined = (
            teacher.get(code, [])
            + huibo.get(code, [])
            + industry.get(code, [])
        )
        ordered = _select_evidence(combined)
        if any(item.get("kind") in DIRECT_KINDS for item in ordered):
            catalyst_status = "exact"
        elif ordered:
            catalyst_status = "sector"
        else:
            catalyst_status = "source_failed" if all_sources_failed else "none"
        public_row = {key: value for key, value in row.items() if key != "concept_names"}
        public_row["catalyst_evidence"] = ordered
        public_row["catalyst_status"] = catalyst_status
        output[code] = public_row
    return output
