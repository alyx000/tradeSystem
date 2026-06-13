"""采集读回层：从落库 payload 读同批数据 + 水位线过滤 + 持仓/关注命中。

单次权威取数原则：本模块**不调外网、不拼 SQL**——数据库访问统一走 `db.queries`
（由 service 先触发 `IngestService.execute_interface` 写入）——推送内容与存档
严格同源可复盘。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from db import queries as Q

from .normalize import _digits

logger = logging.getLogger(__name__)

# 水位线 seen_keys 的前序快照回扫天数：≥ 采集回看窗口（默认 3）+ 余量。
# 必须跨多份快照聚合（codex review：镜像非累积返回时，最新一份快照可能缺失
# 同公告日里更早快照已含的行——只看最新一份会把已推行误判为新行重推）
_SEEN_KEYS_SCAN_DAYS = 7


def _business_key(row: dict) -> tuple:
    """公告行业务键：跨快照去重与水位线"已见"判定共用同一口径。"""
    return (
        row.get("ts_code"),
        _digits(row.get("end_date")),
        _digits(row.get("ann_date")),
        str(row.get("update_flag") or "0"),
    )


def read_payload_rows_between(
    conn: sqlite3.Connection,
    interface_name: str,
    start_biz_date: str,
    end_biz_date: str,
) -> list[dict]:
    """读区间内全部按日 payload 行并按业务键去重。

    与 `Q.get_latest_raw_interface_rows`（只取最近一份）不同：缺口验证的候选名单
    跨多个按日窗口（长假场景跨 N 天），必须 union 区间内所有 payload 快照——
    各按日快照的回看窗口重叠，同一公告会出现在多份快照里，故按业务键去重。
    """
    raw_rows = Q.list_raw_interface_rows(
        conn,
        interface_name=interface_name,
        biz_date_from=start_biz_date,
        biz_date_to=end_biz_date,
    )
    merged = {_business_key(item): item for item in raw_rows}
    return list(merged.values())


def get_push_watermark(
    conn: sqlite3.Connection,
    interface_name: str,
    target_date: str,
) -> dict | None:
    """推送水位线：本次之前最近一份**非空** payload 的最大 `ann_date` + 该日已见业务键。

    返回 ``{"max_ann_date": "YYYYMMDD", "seen_keys": set}`` 或 None（首跑推整窗）。
    按「已存档内容」而非 run 日期推进（codex review 多轮收敛后的口径）：
    - run 日期粒度有凌晨/补跑陷阱——当日早间 run 的 biz_date=T 先于 T 晚间公告存在，
      按 run 推水位线会把 T 当晚公告永久吞掉；按内容最大公告日推进则天然免疫。
    - **键感知截断**：标量「严格大于」会吞掉与水位线同公告日的迟到行（镜像分批吐出
      同一天的公告时），故记录最大公告日上的已见业务键，同日未见键照常放行。
    - empty/failed 天然不推进（empty payload 被 status 过滤跳过、failed 不写 payload）。
    - 排除本次（biz_date < target_date），防当日刚落库的 payload 把水位线推到当前。
    - seen_keys 跨近 _SEEN_KEYS_SCAN_DAYS 天**全部**前序快照聚合（非只看最新一份），
      防镜像非累积快照导致已推行从 seen 集合消失而被重推。
    超出 LOOKBACK 回看窗口的极端迟到（>3 天）由采集窗口边界兜底，属已知限制。
    """
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    scan_start = (target_dt - timedelta(days=_SEEN_KEYS_SCAN_DAYS)).strftime("%Y-%m-%d")
    prev_day = (target_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = read_payload_rows_between(conn, interface_name, scan_start, prev_day)
    dated = [(d, row) for row in rows if (d := _digits(row.get("ann_date")))]
    if not dated:
        return None
    max_ann = max(d for d, _ in dated)
    return {
        "max_ann_date": max_ann,
        "seen_keys": {_business_key(row) for d, row in dated if d == max_ann},
    }


def filter_new_since_watermark(rows: list[dict], watermark: dict | None) -> list[dict]:
    """留下水位线之后的新行：公告日更晚，或同最大公告日但业务键未见过
    （周日晚推过的周末公告周一不重复推；同日迟到的新公告/修正照常放行）。"""
    if watermark is None:
        return list(rows)
    wm = watermark["max_ann_date"]
    seen = watermark["seen_keys"]
    return [
        row for row in rows
        if (ann := _digits(row.get("ann_date"))) > wm
        or (ann == wm and _business_key(row) not in seen)
    ]


def load_position_codes(conn: sqlite3.Connection) -> dict[str, set[str]]:
    """持仓/关注池股票代码集合（6 位裸码，匹配 ts_code 前缀）。"""
    def _bare(code: Any) -> str:
        # split(".")[0] 与 db.dual_write._normalize_stock_code_for_match 在现实输入
        # （6位裸码 / XXXXXX.SZ|SH|BJ）上行为等价；不导入他模块私有函数，保持本地实现。
        text = str(code or "").strip().upper()
        return text.split(".")[0] if text else ""

    holdings = {_bare(row.get("stock_code")) for row in Q.get_holdings(conn)}
    watchlist = {_bare(row.get("stock_code")) for row in Q.get_watchlist(conn)}
    holdings.discard("")
    watchlist.discard("")
    return {"holdings": holdings, "watchlist": watchlist}


def position_hit(ts_code: str, codes: dict[str, set[str]]) -> str | None:
    """命中标签：持仓 > 关注（同时命中报持仓）。"""
    bare = str(ts_code or "").split(".")[0]
    if bare in codes.get("holdings", set()):
        return "持仓"
    if bare in codes.get("watchlist", set()):
        return "关注"
    return None


# ---- 已推送标记（同日重跑幂等兜底，无 schema） ----
# 水位线设计上排除 target_date（防本次刚落库的 payload 自抑制），因此本日内没有
# 「已推 business_key」状态——稳态每日 1 次不触发，但手动同日重跑 / launchd 双触发 /
# 钉钉失败重试会把同一批内容再次当新增重推（codex review 2026-06-12）。
# 兜底：成功推送后把本次内容的业务键落 .pushed-{date}.json，重跑按它过滤。
# 按日独立文件、内容键级去重；推送失败不落标记（保证重试仍能补推）。


def announcement_marker_key(interface_name: str, row: dict) -> str:
    """公告行已推键：interface + 业务键（与水位线同口径，含 update_flag 区分修正）。"""
    ts_code, end, ann, flag = _business_key(row)
    return f"{interface_name}|{ts_code}|{end}|{ann}|{flag}"


def gap_marker_key(item: dict) -> str:
    """缺口命中已推键：同一公告的缺口只报一次（同日 quotes 固定，重跑结果不变）。

    不含 update_flag（修正前后缺口同一事件，只报一次）；不含 interface 前缀——缺口候选
    当前仅来自 forecast（service.candidate_rows），无跨源；若将来纳入 express 缺口需加源前缀。
    """
    return f"{item.get('ts_code')}|{_digits(item.get('end_date'))}|{_digits(item.get('ann_date'))}"


def filter_unpushed(rows: list[dict], interface_name: str, pushed_keys: set[str]) -> list[dict]:
    """剔除已推送过的公告行（同日重跑只保留增量）。"""
    if not pushed_keys:
        return list(rows)
    return [r for r in rows if announcement_marker_key(interface_name, r) not in pushed_keys]


def filter_unpushed_gaps(gap_hits: list[dict], pushed_gap_keys: set[str]) -> list[dict]:
    """剔除已推送过的缺口命中。"""
    if not pushed_gap_keys:
        return list(gap_hits)
    return [h for h in gap_hits if gap_marker_key(h) not in pushed_gap_keys]


def _marker_path(target_date: str, report_dir: str | Path) -> Path:
    return Path(report_dir) / f".pushed-{target_date}.json"


def _empty_pushed() -> dict[str, set[str]]:
    return {"announcements": set(), "gaps": set(), "warnings": set()}


def load_pushed(target_date: str, report_dir: str | Path) -> dict[str, set[str]]:
    """读本日已推标记 → {"announcements", "gaps", "warnings"} 三集；缺失/损坏按未推处理。

    warnings 收纳「会单独触发推送的告警」（如 gap_error）——这类告警不在公告/缺口键
    空间内，但 render 会把它当内容推送，故必须同样去重防同日重跑重推（codex review）。
    """
    path = _marker_path(target_date, report_dir)
    if not path.exists():
        return _empty_pushed()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("[earnings-digest] 推送标记 %s 解析失败，按未推处理（最坏退化为重推一次）", path)
        return _empty_pushed()
    return {
        "announcements": set(data.get("announcements") or []),
        "gaps": set(data.get("gaps") or []),
        "warnings": set(data.get("warnings") or []),
    }


def gap_error_marker_key(target_date: str) -> str:
    """故障告警已推键：每 target_date 一次缺口验证故障告警（变体不重复刷屏）。"""
    return f"gaperr|{target_date}"


def record_pushed(
    target_date: str,
    announcement_keys: set[str],
    gap_keys: set[str],
    report_dir: str | Path,
    warning_keys: set[str] | None = None,
) -> Path:
    """成功推送后并入已推标记（与既有标记取并集，幂等）。"""
    existing = load_pushed(target_date, report_dir)
    merged = {
        "announcements": sorted(existing["announcements"] | set(announcement_keys)),
        "gaps": sorted(existing["gaps"] | set(gap_keys)),
        "warnings": sorted(existing["warnings"] | set(warning_keys or ())),
    }
    path = _marker_path(target_date, report_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    return path
