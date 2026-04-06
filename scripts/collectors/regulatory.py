"""
A 股异动监管数据采集：已监管（停牌 suspend_d）、潜在监管（stk_shock + 偏离值 Level2/3）、
交易所重点提示证券（stk_alert，与券商 App「重点监控」列表同源类数据，需约 6000 积分）。
ST 股不参与潜在监管计算；stk_shock 不可用时降级为全市场粗筛 + 自算 3 日偏离。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable

from db.connection import get_db
from db.dual_write import _normalize_stock_code_for_match
from db.migrate import migrate
from db import queries as Q
from providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# SQLite TEXT 无硬上限；列表与交易所原文可能较长，适当放宽便于前端完整展示
_REGULATORY_REASON_MAX_LEN = 4000

_RE_TS_SHAPED_NAME = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$", re.I)
_RE_6DIGIT = re.compile(r"^\d{6}$")


def _name_looks_like_ts_code(ts_code: str, name: str | None) -> bool:
    """接口把简称填成代码、或仅 6 位数字时视为无效名称。"""
    if name is None:
        return True
    n = str(name).strip()
    if not n:
        return True
    c = ts_code.strip().upper()
    nu = n.upper()
    if nu == c:
        return True
    if _RE_TS_SHAPED_NAME.match(nu):
        return True
    prefix = c.split(".", 1)[0] if "." in c else c
    if _RE_6DIGIT.match(nu) and nu == prefix:
        return True
    return False


def ensure_names_for_ts_codes(
    registry: ProviderRegistry,
    name_map: dict[str, str],
    ts_codes: Iterable[str],
) -> None:
    """对映射缺失或名称像代码的 ts_code，用 stock_basic 按代码批量补全（原地写入 name_map）。"""
    want: list[str] = []
    seen: set[str] = set()
    for raw in ts_codes:
        c = str(raw or "").strip().upper()
        if not c or c in seen:
            continue
        seen.add(c)
        cur = (name_map.get(c) or "").strip()
        if not cur or _name_looks_like_ts_code(c, cur):
            want.append(c)
    if not want:
        return
    r = registry.call("get_stock_basic_batch", want)
    if not r.success or not isinstance(r.data, list):
        return
    for row in r.data:
        if not isinstance(row, dict):
            continue
        tc = str(row.get("ts_code") or "").strip().upper()
        nn = str(row.get("name") or "").strip()
        if tc and nn and not _name_looks_like_ts_code(tc, nn):
            name_map[tc] = nn


def _clean_reason_cell(val: Any) -> str:
    if val is None:
        return ""
    s = str(val).strip()
    if s in ("", "None", "nan"):
        return ""
    return s


def _is_placeholder_calendar_date(val: str) -> bool:
    """Tushare 常用占位复牌日（无实际含义）。"""
    v = val.replace("-", "").replace("/", "")[:8]
    if len(v) != 8 or not v.isdigit():
        return True
    return v in ("19000101", "00010101", "20991231", "99991231")


def _format_suspend_row_narrative(row: dict[str, Any]) -> str:
    """将 suspend 接口一行展开为可读说明（原因 + 时间/复牌等）。"""
    main = ""
    for key in ("change_reason", "suspend_reason", "reason"):
        main = _clean_reason_cell(row.get(key))
        if main:
            break
    bits: list[str] = []
    if main:
        bits.append(main)
    st = _clean_reason_cell(row.get("suspend_time"))
    if st and st not in main:
        bits.append(f"停复牌时间：{st}")
    rsd = _clean_reason_cell(row.get("resump_date") or row.get("resume_date"))
    if rsd and not _is_placeholder_calendar_date(rsd):
        bits.append(f"复牌日期：{rsd}")
    sdt = _clean_reason_cell(row.get("suspend_date"))
    if sdt and sdt not in main:
        bits.append(f"停牌日期：{sdt}")
    crt = row.get("change_reason_type")
    if crt is not None and str(crt).strip() not in ("", "None"):
        t = str(crt).strip()
        if t not in main:
            bits.append(f"原因类型代码：{t}")
    stype = row.get("suspend_type")
    if stype is not None and str(stype).strip() not in ("", "None"):
        t = str(stype).strip()
        if t not in main:
            bits.append(f"停复牌类型：{t}")
    return "；".join(bits) if bits else ""


def _suspend_api_snapshot_for_code(records: list[Any] | None, code: str) -> dict[str, Any]:
    """取与当日 suspend 匹配的第一行，保留可展示字段（写入 detail_json）。"""
    u = code.strip().upper()
    keys = (
        "change_reason",
        "suspend_reason",
        "reason",
        "suspend_time",
        "resump_date",
        "resume_date",
        "suspend_date",
        "suspend_type",
        "change_reason_type",
    )
    for row in records or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        if c != u:
            continue
        out: dict[str, Any] = {}
        for k in keys:
            v = row.get(k)
            if v is None:
                continue
            s = _clean_reason_cell(v)
            if not s:
                continue
            if k in ("resump_date", "resume_date") and _is_placeholder_calendar_date(s):
                continue
            out[k] = v
        return out
    return {}


def build_ts_code_name_map(registry: ProviderRegistry, target_date: str = "") -> dict[str, str]:
    """从 Tushare stock_basic 构建 ts_code -> 证券简称；失败返回空 dict。"""
    r = registry.call("get_stock_basic_list", target_date or "2000-01-01")
    if not r.success or not r.data:
        logger.warning("get_stock_basic_list 失败，名称映射为空: %s", r.error)
        return {}
    m: dict[str, str] = {}
    for row in r.data:
        if not isinstance(row, dict):
            continue
        c = str(row.get("ts_code") or "").strip().upper()
        n = str(row.get("name") or "").strip()
        if c and n:
            m[c] = n
    return m


def resolve_stock_display_name(
    ts_code: str,
    raw_name: str | None,
    name_map: dict[str, str],
) -> str:
    """接口 name 缺失或与代码相同时，用 stock_basic 映射补全。"""
    c = ts_code.strip().upper()
    n = (str(raw_name) if raw_name is not None else "").strip()
    if _name_looks_like_ts_code(c, n):
        n = ""
    mapped = (name_map.get(c) or "").strip()
    if _name_looks_like_ts_code(c, mapped):
        mapped = ""
    if mapped and not n:
        return mapped
    if mapped and n:
        return n
    if n:
        return n
    return mapped or c


def _suspend_change_reason_index(records: list[Any] | None) -> dict[str, str]:
    """Tushare suspend 多行合并为 ts_code -> 停牌原因文案（含时间、复牌日期等）。"""
    buckets: dict[str, list[str]] = {}
    for row in records or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        if not c:
            continue
        piece = _format_suspend_row_narrative(row)
        if not piece:
            continue
        if c not in buckets:
            buckets[c] = []
        if piece not in buckets[c]:
            buckets[c].append(piece)
    cap = _REGULATORY_REASON_MAX_LEN
    return {c: " ｜ ".join(parts)[:cap] for c, parts in buckets.items()}


def _type1_regulatory_reason(
    row: dict[str, Any],
    change_by_code: dict[str, str],
    code: str,
) -> str:
    """suspend_d 无文字原因时，用 suspend.change_reason；再补充日内停牌时段。"""
    cr = (change_by_code.get(code) or "").strip()
    timing_raw = row.get("suspend_timing")
    timing = ""
    if timing_raw is not None:
        timing = str(timing_raw).strip()
        if timing in ("", "None", "nan"):
            timing = ""
    parts: list[str] = []
    if cr:
        parts.append(cr)
    if timing and timing not in cr:
        parts.append(f"日内停牌时段：{timing}")
    if not parts:
        parts.append("停牌（公开接口未返回文字原因，请以交易所及公司公告为准）")
    return " | ".join(parts)[:_REGULATORY_REASON_MAX_LEN]


def _stk_shock_display_reason(row: dict[str, Any]) -> str:
    """合并异常说明、期间、市场等，避免仅有空泛默认句。"""
    raw = str(row.get("reason") or "").strip()
    period = str(row.get("period") or "").strip()
    market = _clean_reason_cell(row.get("trade_market"))
    base = raw
    if raw and period and period not in raw:
        base = f"{raw}（期间：{period}）"
    elif raw:
        base = raw
    elif period:
        base = f"交易所披露异常波动（期间：{period}）"
    else:
        base = "交易所认定异常波动"
    if market and market not in base:
        base = f"{base}（{market}）"
    return base[:_REGULATORY_REASON_MAX_LEN]


def _norm_yyyymmdd(raw: Any) -> str | None:
    s = str(raw or "").replace("-", "")[:8]
    if len(s) != 8 or not s.isdigit():
        return None
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _build_st_set(rows: list[dict] | None) -> set[str]:
    out: set[str] = set()
    if not rows:
        return out
    for r in rows:
        c = str(r.get("ts_code") or r.get("code") or "").strip().upper()
        if not c:
            continue
        out.add(c)
        n = _normalize_stock_code_for_match(c)
        if n:
            out.add(n)
    return out


def _is_st(st_set: set[str], ts_code: str) -> bool:
    u = ts_code.strip().upper()
    if u in st_set:
        return True
    n = _normalize_stock_code_for_match(u)
    return bool(n and n in st_set)


def _get_board_config(ts_code: str) -> dict[str, Any] | None:
    u = ts_code.strip().upper()
    if not u or "." not in u:
        return None
    code = u.split(".")[0]
    suf = u.split(".")[-1]
    if suf == "BJ":
        return None
    if suf == "SH":
        if code.startswith("688"):
            return {
                "board": "科创板",
                "index": "000688.SH",
                "l1_threshold": 30.0,
                "l2_count": 3,
                "l2_dev_up": 100.0,
                "l2_dev_down": -50.0,
                "l3_dev_up": 200.0,
                "l3_dev_down": -70.0,
            }
        return {
            "board": "沪市主板",
            "index": "000001.SH",
            "l1_threshold": 20.0,
            "l2_count": 4,
            "l2_dev_up": 100.0,
            "l2_dev_down": -50.0,
            "l3_dev_up": 200.0,
            "l3_dev_down": -70.0,
        }
    if suf == "SZ":
        if code.startswith("3"):
            return {
                "board": "创业板",
                "index": "399006.SZ",
                "l1_threshold": 30.0,
                "l2_count": 3,
                "l2_dev_up": 100.0,
                "l2_dev_down": -50.0,
                "l3_dev_up": 200.0,
                "l3_dev_down": -70.0,
            }
        return {
            "board": "深市主板",
            "index": "399107.SZ",
            "l1_threshold": 20.0,
            "l2_count": 4,
            "l2_dev_up": 100.0,
            "l2_dev_down": -50.0,
            "l3_dev_up": 200.0,
            "l3_dev_down": -70.0,
        }
    return None


def _trade_days_open(registry: ProviderRegistry, end_date: str, need: int) -> list[str]:
    years = {int(end_date[:4])}
    if int(end_date[5:7]) <= 3:
        years.add(int(end_date[:4]) - 1)
    days: list[str] = []
    for y in sorted(years):
        r = registry.call("get_trade_calendar", f"{y}-07-01")
        if not r.success or not r.data:
            continue
        for row in r.data:
            if int(row.get("is_open", 0) or 0) != 1:
                continue
            ymd = _norm_yyyymmdd(row.get("cal_date"))
            if ymd and ymd <= end_date:
                days.append(ymd)
    days = sorted(set(days))
    return days[-need:] if len(days) >= need else days


def _pct_map_from_range(result_rows: list[dict] | None) -> dict[str, float]:
    m: dict[str, float] = {}
    if not result_rows:
        return m
    for row in result_rows:
        d = row.get("trade_date")
        if not d:
            continue
        ds = str(d)[:10]
        pc = row.get("pct_chg")
        try:
            m[ds] = float(pc) if pc is not None else 0.0
        except (TypeError, ValueError):
            m[ds] = 0.0
    return m


def _deviation_sum(
    dates: list[str],
    stock_map: dict[str, float],
    index_map: dict[str, float],
) -> float:
    s = 0.0
    for d in dates:
        if d not in stock_map or d not in index_map:
            continue
        s += stock_map[d] - index_map[d]
    return s


def _sliding_l1_window_count(
    days_10: list[str],
    stock_map: dict[str, float],
    index_map: dict[str, float],
    threshold: float,
) -> int:
    if len(days_10) < 3:
        return 0
    cnt = 0
    for i in range(len(days_10) - 2):
        w = days_10[i : i + 3]
        dev = _deviation_sum(w, stock_map, index_map)
        if abs(dev) >= threshold:
            cnt += 1
    return cnt


def _shock_event_dates_from_raw(
    conn: Any,
    ts_code: str,
    d_start: str,
    d_end: str,
) -> set[str]:
    rows = Q.list_raw_interface_rows(
        conn,
        interface_name="stk_shock",
        biz_date_from=d_start,
        biz_date_to=d_end,
    )
    u = ts_code.strip().upper()
    seen: set[str] = set()
    for row in rows:
        c = str(row.get("ts_code") or row.get("code") or "").strip().upper()
        if c != u:
            continue
        td = _norm_yyyymmdd(row.get("trade_date"))
        if not td:
            td = str(row.get("trade_date_norm") or "")[:10]
        if td and len(td) == 10 and d_start <= td <= d_end:
            seen.add(td)
    return seen


class RegulatoryCollector:
    def __init__(self, registry: ProviderRegistry, db_path: str | None = None):
        self.registry = registry
        self.db_path = db_path

    def collect(self, target_date: str) -> dict[str, Any]:
        name_map = build_ts_code_name_map(self.registry, target_date)
        with get_db(self.db_path) as conn:
            migrate(conn)
            r1 = self.collect_regulated(conn, target_date, name_map)
            r2 = self.collect_potential(conn, target_date, name_map)
            r3 = self.collect_stk_alert(conn, target_date, name_map)
            conn.commit()
        return {
            "date": target_date,
            "regulated_count": len(r1),
            "potential_count": len(r2),
            "stk_alert_count": len(r3),
            "regulated": r1,
            "potential": r2,
            "stk_alert": r3,
        }

    def collect_regulated(
        self,
        conn: Any,
        target_date: str,
        name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        nm = name_map if name_map is not None else {}
        r = self.registry.call("get_suspend_list", target_date)
        if not r.success:
            logger.warning("get_suspend_list 失败: %s", r.error)
            return []
        rows = r.data if isinstance(r.data, list) else []
        codes = sorted(
            {
                str(row.get("ts_code") or row.get("code") or "").strip().upper()
                for row in rows
                if isinstance(row, dict)
            }
            - {""}
        )
        ensure_names_for_ts_codes(self.registry, nm, codes)
        scr = self.registry.call("get_suspend_change_reasons", target_date, codes)
        change_by_code: dict[str, str] = {}
        if scr.success and isinstance(scr.data, list):
            change_by_code = _suspend_change_reason_index(scr.data)
        elif not scr.success:
            logger.info("suspend(停牌文字原因)未获取，将仅用 suspend_d: %s", scr.error)
        scr_rows: list[Any] = scr.data if scr.success and isinstance(scr.data, list) else []
        written: list[dict[str, Any]] = []
        for row in rows:
            code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
            if not code:
                continue
            name = resolve_stock_display_name(code, row.get("name"), nm)
            reason = _type1_regulatory_reason(row, change_by_code, code)
            suspend_snap = _suspend_api_snapshot_for_code(scr_rows, code)
            rec = {
                "ts_code": code,
                "name": name,
                "regulatory_type": 1,
                "risk_level": 1,
                "reason": reason[:_REGULATORY_REASON_MAX_LEN],
                "publish_date": target_date,
                "source": r.source or "tushare:suspend_d",
                "risk_score": 1.0,
                "detail_json": {
                    "raw": {k: row.get(k) for k in list(row.keys())[:20]},
                    "suspend_change_reason": change_by_code.get(code),
                    "suspend_api": suspend_snap,
                },
            }
            Q.upsert_regulatory_monitor(conn, rec)
            written.append(rec)
        logger.info("监管停牌写入 %d 条 (Type1)", len(written))
        return written

    def collect_stk_alert(
        self,
        conn: Any,
        target_date: str,
        name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """交易所重点提示证券（stk_alert）：监控起止日期 + 提示类型。"""
        nm = name_map if name_map is not None else {}
        ar = self.registry.call("get_stk_alert", target_date)
        if not ar.success:
            logger.warning("get_stk_alert 失败: %s", ar.error)
            return []
        rows = ar.data if isinstance(ar.data, list) else []
        codes = sorted(
            {
                str(row.get("ts_code") or row.get("code") or "").strip().upper()
                for row in rows
                if isinstance(row, dict)
            }
            - {""}
        )
        ensure_names_for_ts_codes(self.registry, nm, codes)
        batch: list[dict[str, Any]] = []
        written: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
            if not code:
                continue
            ms = _norm_yyyymmdd(row.get("start_date"))
            me = _norm_yyyymmdd(row.get("end_date"))
            if not ms or not me:
                continue
            name = resolve_stock_display_name(code, row.get("name"), nm)
            alert_type = str(row.get("type") or "").strip()
            rec = {
                "ts_code": code,
                "name": name,
                "monitor_start": ms,
                "monitor_end": me,
                "alert_type": alert_type,
                "source": ar.source or "tushare:stk_alert",
                "detail_json": {"raw": {k: row.get(k) for k in list(row.keys())[:30]}},
            }
            batch.append(rec)
            written.append(rec)
        Q.replace_stk_alert_snapshot(conn, target_date, batch)
        logger.info("重点监控(stk_alert)写入 %d 条 (snapshot=%s)", len(batch), target_date)
        return written

    def collect_potential(
        self,
        conn: Any,
        target_date: str,
        name_map: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        nm = name_map if name_map is not None else {}
        st_r = self.registry.call("get_stock_st", target_date)
        st_rows = st_r.data if st_r.success and isinstance(st_r.data, list) else []
        st_set = _build_st_set(st_rows)

        days30 = _trade_days_open(self.registry, target_date, 32)
        if len(days30) < 3:
            logger.warning("交易日历不足，跳过潜在监管计算")
            return []
        last3 = days30[-3:]
        last10 = days30[-10:]
        d_start_10 = last10[0]

        level1: list[dict[str, Any]] = []
        shock_ok = False
        shock_r = self.registry.call("get_stk_shock", target_date)
        if shock_r.success and shock_r.data is not None:
            shock_ok = True
            for row in shock_r.data:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("ts_code") or row.get("code") or "").strip().upper()
                if not code or _is_st(st_set, code):
                    continue
                if _get_board_config(code) is None:
                    continue
                name = resolve_stock_display_name(code, row.get("name"), nm)
                reason = _stk_shock_display_reason(row)
                level1.append({
                    "ts_code": code,
                    "name": name,
                    "l1_reason": reason,
                    "l1_source": shock_r.source or "tushare:stk_shock",
                })
        else:
            logger.warning("stk_shock 不可用，降级自算 Level1: %s", shock_r.error or "")
            candidates: set[str] = set()
            daily_name_by_code: dict[str, str] = {}
            for td in last3:
                dr = self.registry.call("get_market_daily_changes", td)
                if not dr.success or not dr.data:
                    continue
                for item in dr.data:
                    if not isinstance(item, dict):
                        continue
                    code = str(item.get("ts_code") or item.get("code") or "").strip().upper()
                    if not code or _is_st(st_set, code):
                        continue
                    cfg = _get_board_config(code)
                    if cfg is None:
                        continue
                    try:
                        pct = float(item.get("pct_chg") or 0)
                    except (TypeError, ValueError):
                        pct = 0.0
                    if abs(pct) >= cfg["l1_threshold"] * 0.5:
                        candidates.add(code)
                        dn = str(item.get("name") or "").strip()
                        if dn and code not in daily_name_by_code:
                            daily_name_by_code[code] = dn
            for code in candidates:
                cfg = _get_board_config(code)
                if not cfg:
                    continue
                sr = self.registry.call("get_stock_daily_range", code, last3[0], target_date)
                ir = self.registry.call("get_index_daily_range", cfg["index"], last3[0], target_date)
                if not sr.success or not ir.success:
                    continue
                sm = _pct_map_from_range(sr.data if isinstance(sr.data, list) else [])
                im = _pct_map_from_range(ir.data if isinstance(ir.data, list) else [])
                dev3 = _deviation_sum(last3, sm, im)
                if abs(dev3) >= cfg["l1_threshold"]:
                    level1.append({
                        "ts_code": code,
                        "name": resolve_stock_display_name(
                            code, daily_name_by_code.get(code), nm
                        ),
                        "l1_reason": f"自算3日偏离值 {dev3:.2f}% (阈值±{cfg['l1_threshold']}%)",
                        "l1_source": "calculated:deviation_3d",
                    })

        by_code: dict[str, dict[str, Any]] = {}
        for it in level1:
            by_code[it["ts_code"]] = it
        level1 = list(by_code.values())
        ensure_names_for_ts_codes(self.registry, nm, [it["ts_code"] for it in level1])

        written: list[dict[str, Any]] = []
        for item in level1:
            code = item["ts_code"]
            cfg = _get_board_config(code)
            if not cfg:
                continue
            idx = cfg["index"]
            start_d = days30[0]
            sr = self.registry.call("get_stock_daily_range", code, start_d, target_date)
            ir = self.registry.call("get_index_daily_range", idx, start_d, target_date)
            sm: dict[str, float] = {}
            im: dict[str, float] = {}
            if sr.success and sr.data and ir.success and ir.data:
                sm = _pct_map_from_range(sr.data if isinstance(sr.data, list) else [])
                im = _pct_map_from_range(ir.data if isinstance(ir.data, list) else [])

            th = cfg["l1_threshold"]
            dev3 = _deviation_sum(last3, sm, im)
            dev10 = _deviation_sum(last10, sm, im)
            dev30 = _deviation_sum(days30, sm, im)

            from_api = shock_ok and str(item.get("l1_source", "")).startswith("tushare")

            risk_level = 1
            if sm and im:
                if dev30 >= cfg["l3_dev_up"] or dev30 <= cfg["l3_dev_down"]:
                    risk_level = 3
                elif dev10 >= cfg["l2_dev_up"] or dev10 <= cfg["l2_dev_down"]:
                    risk_level = 2
                else:
                    ev_dates = _shock_event_dates_from_raw(conn, code, d_start_10, target_date)
                    if from_api:
                        ev_dates.add(target_date)
                    shock_n = len(ev_dates)
                    if shock_n == 0:
                        shock_n = _sliding_l1_window_count(last10, sm, im, th)
                    if shock_n >= cfg["l2_count"]:
                        risk_level = 2

            if risk_level == 1 and abs(dev3) < th and not from_api:
                continue

            ratio = min(1.0, max(0.0, abs(dev3) / th - 1.0)) if th else 0.0
            if risk_level == 1:
                risk_score = round(0.4 + 0.2 * ratio, 4)
            elif risk_level == 2:
                risk_score = 0.75
            else:
                risk_score = 0.95

            reason_parts = [item["l1_reason"]]
            if sm and im:
                if risk_level >= 2:
                    reason_parts.append(
                        f"严重异动风险: 10日偏离{dev10:.2f}% 或近10日异常次数达到阈值"
                    )
                if risk_level >= 3:
                    reason_parts.append(f"极端异动: 30日偏离{dev30:.2f}%")
            elif from_api:
                reason_parts.append("（区间行情未取全，Level2/3 未精算）")
            reason = " | ".join(reason_parts)[:_REGULATORY_REASON_MAX_LEN]

            detail: dict[str, Any] = {
                "board": cfg["board"],
                "index": idx,
                "deviation_3d": round(dev3, 4),
                "deviation_10d": round(dev10, 4),
                "deviation_30d": round(dev30, 4),
                "l1_threshold": th,
                "l1_source": item["l1_source"],
                "risk_level": risk_level,
                "has_range": bool(sm and im),
            }
            rec = {
                "ts_code": code,
                "name": resolve_stock_display_name(code, item.get("name"), nm),
                "regulatory_type": 2,
                "risk_level": risk_level,
                "reason": reason,
                "publish_date": target_date,
                "source": "regulatory:potential",
                "risk_score": risk_score,
                "detail_json": detail,
            }
            Q.upsert_regulatory_monitor(conn, rec)
            written.append(rec)

        logger.info("潜在监管写入 %d 条 (Type2)", len(written))
        return written

    def format_report(self, result: dict[str, Any]) -> str:
        lines = [
            f"=== 异动监管 {result.get('date')} ===",
            f"已监管(Type1): {result.get('regulated_count', 0)}",
            f"潜在监管(Type2): {result.get('potential_count', 0)}",
            f"重点监控(stk_alert): {result.get('stk_alert_count', 0)}",
        ]
        for k in ("regulated", "potential", "stk_alert"):
            rows = result.get(k) or []
            if not rows:
                continue
            lines.append(f"--- {k} ---")
            for r in rows[:30]:
                if k == "stk_alert":
                    lines.append(
                        f"  {r.get('ts_code')} {r.get('name')} "
                        f"{r.get('monitor_start')}～{r.get('monitor_end')} "
                        f"{str(r.get('alert_type') or '')[:120]}"
                    )
                else:
                    lines.append(
                        f"  {r.get('ts_code')} {r.get('name')} L{r.get('risk_level')} "
                        f"{str(r.get('reason', '') or '')[:200]}"
                    )
            if len(rows) > 30:
                lines.append(f"  ... 共 {len(rows)} 条")
        return "\n".join(lines)
