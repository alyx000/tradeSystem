"""资料层服务：knowledge_assets 与 draft 生成入口。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
import json
import re
from typing import Any
from uuid import uuid4

from db.connection import get_db
from db.migrate import migrate
from services.planning_service import PlanningService


THEME_KEYWORDS = ["AI", "AI算力", "机器人", "锂电", "储能", "金融", "半导体"]

# 与 SQLite CHECK 一致的可新建/可筛选类型（不含 teacher_note / course_note）
_ALLOWED_KNOWLEDGE_ASSET_TYPES = frozenset({"news_note", "manual_note"})

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date_param(label: str, value: str) -> str:
    s = value.strip()
    if not _ISO_DATE_RE.match(s):
        raise ValueError(f"{label} 须为 YYYY-MM-DD，收到: {value!r}")
    try:
        date_type.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"{label} 不是合法日历日期: {value!r}") from exc
    return s


def _sql_like_pattern_literal(keyword: str) -> str:
    """将用户关键词作字面量匹配：对 LIKE 中的 % _ ! 转义，配合 ESCAPE '!'。"""
    return keyword.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def infer_ts_code(code: str) -> str:
    """6 位 A 股代码推断交易所后缀，供 stock_facts 与正则一致。"""
    c = code.strip().upper()
    if "." in c:
        return c
    if len(c) != 6 or not c.isdigit():
        return c
    if c.startswith(("5", "6", "9")):
        return f"{c}.SH"
    if c.startswith(("0", "1", "2", "3")):
        return f"{c}.SZ"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def build_text_for_trade_clues(note_row: dict[str, Any]) -> str:
    """从 teacher_notes 行拼出用于规则抽取的正文。"""
    parts: list[str] = []
    for key in ("title", "core_view", "raw_content", "position_advice"):
        v = note_row.get(key)
        if v:
            parts.append(str(v))
    for key in ("key_points", "sectors", "tags"):
        v = note_row.get(key)
        if not v:
            continue
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                parts.append(json.dumps(parsed, ensure_ascii=False) if isinstance(parsed, (list, dict)) else str(parsed))
            except json.JSONDecodeError:
                parts.append(v)
        else:
            parts.append(str(v))
    return "\n".join(parts)


def merge_mentioned_stocks_into_clues(clues: dict[str, Any], note_row: dict[str, Any]) -> dict[str, Any]:
    """把 mentioned_stocks JSON 合并进 trade_clues.stocks（去重 subject_code）。"""
    raw = note_row.get("mentioned_stocks")
    if not raw:
        return clues
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return clues
    if not isinstance(items, list):
        return clues
    stocks: list[dict[str, Any]] = list(clues.get("stocks") or [])
    seen = {s.get("subject_code") for s in stocks if isinstance(s, dict)}
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip()
        if not code:
            continue
        ts = infer_ts_code(code)
        if ts in seen:
            continue
        seen.add(ts)
        name = str(it.get("name") or "").strip()
        stocks.append(
            {
                "subject_type": "stock",
                "subject_code": ts,
                "subject_name": name,
                "reason": "来自老师笔记 mentioned_stocks",
            }
        )
    out = dict(clues)
    out["stocks"] = stocks
    return out


@dataclass
class KnowledgeService:
    db_path: str | None = None

    def add_asset(
        self,
        *,
        asset_type: str,
        title: str,
        content: str,
        source: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        at = (asset_type or "").strip()
        if at == "teacher_note":
            raise ValueError(
                "老师观点须写入 teacher_notes，请使用 db add-note 或 POST /api/teacher-notes；"
                "勿向 knowledge_assets 写入 asset_type=teacher_note"
            )
        if at == "course_note":
            raise ValueError(
                "已不再通过 knowledge_assets 新建课程笔记类型，请使用 news_note / manual_note，"
                "或写入 teacher_notes（老师观点）"
            )
        if at not in _ALLOWED_KNOWLEDGE_ASSET_TYPES:
            raise ValueError(
                "asset_type 仅支持 news_note、manual_note"
                + (f"；收到: {asset_type!r}" if (asset_type or "").strip() != "" else "（当前为空）")
            )
        asset_id = f"asset_{uuid4().hex[:12]}"
        summary = self._build_summary(content)
        trade_clues = self._extract_trade_clues(content, tags or [])
        with get_db(self.db_path) as conn:
            migrate(conn)
            conn.execute(
                """
                INSERT INTO knowledge_assets
                (asset_id, asset_type, title, content, source, tags, summary, trade_clues)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    at,
                    title,
                    content,
                    source,
                    json.dumps(tags or [], ensure_ascii=False),
                    summary,
                    json.dumps(trade_clues, ensure_ascii=False),
                ),
            )
        return self.get_asset(asset_id)

    def list_assets(
        self,
        limit: int = 20,
        offset: int = 0,
        asset_type: str | None = None,
        keyword: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出资料资产；排除 teacher_note。asset_type 筛选仅允许 news_note / manual_note。"""
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        where = ["(asset_type IS NULL OR asset_type != 'teacher_note')"]
        params: list[Any] = []
        at = (asset_type or "").strip()
        if at:
            if at not in _ALLOWED_KNOWLEDGE_ASSET_TYPES:
                raise ValueError(
                    "asset_type 筛选仅支持 news_note、manual_note；"
                    "不支持 course_note、teacher_note（历史 course_note 仍会在未指定类型时出现在列表中）"
                )
            where.append("asset_type = ?")
            params.append(at)
        kw = (keyword or "").strip()
        if kw:
            lit = _sql_like_pattern_literal(kw)
            like_pat = f"%{lit}%"
            where.append(
                "(title LIKE ? ESCAPE '!' OR content LIKE ? ESCAPE '!' "
                "OR IFNULL(source,'') LIKE ? ESCAPE '!')"
            )
            params.extend([like_pat, like_pat, like_pat])
        cf = (created_from or "").strip()
        if cf:
            where.append("date(created_at) >= date(?)")
            params.append(_validate_iso_date_param("created_from", cf))
        ct = (created_to or "").strip()
        if ct:
            where.append("date(created_at) <= date(?)")
            params.append(_validate_iso_date_param("created_to", ct))
        sql = f"""
            SELECT * FROM knowledge_assets
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, asset_id DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def delete_asset(self, asset_id: str) -> int:
        with get_db(self.db_path) as conn:
            migrate(conn)
            cur = conn.execute("DELETE FROM knowledge_assets WHERE asset_id = ?", (asset_id,))
            conn.commit()
            return cur.rowcount

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                "SELECT * FROM knowledge_assets WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"asset not found: {asset_id}")
        return dict(row)

    def draft_from_asset(self, *, asset_id: str, trade_date: str, input_by: str | None = None) -> dict[str, Any]:
        asset = self.get_asset(asset_id)
        if (asset.get("asset_type") or "").strip() == "teacher_note":
            raise ValueError(
                "knowledge_assets 中 asset_type=teacher_note 为历史数据，请改用 teacher_notes 对应笔记，"
                "并调用 POST /api/knowledge/teacher-notes/{note_id}/draft 生成草稿"
            )
        raw_clues = asset["trade_clues"] or "{}"
        try:
            clues = json.loads(raw_clues)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"knowledge_assets.trade_clues 不是合法 JSON（asset_id={asset_id}）: {exc}"
            ) from exc
        planning = PlanningService(self.db_path)
        observation = planning.create_observation(
            trade_date=trade_date,
            source_type="knowledge_asset",
            title=asset["title"],
            sector_facts={"main_themes": clues.get("themes", [])},
            stock_facts=clues.get("stocks", []),
            judgements=clues.get("judgements", []),
            source_refs=[{"asset_id": asset_id, "asset_type": asset["asset_type"]}],
            input_by=input_by,
        )
        draft = planning.create_draft(
            trade_date=trade_date,
            source_observation_ids=[observation["observation_id"]],
            title=f"{trade_date} 资料草稿",
            summary=asset.get("summary") or asset["title"],
            input_by=input_by,
        )
        return {
            "asset": asset,
            "observation": observation,
            "draft": draft,
        }

    def get_teacher_note_row(self, note_id: int) -> dict[str, Any]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            row = conn.execute(
                """
                SELECT n.*, t.name AS teacher_name
                FROM teacher_notes n
                JOIN teachers t ON n.teacher_id = t.id
                WHERE n.id = ?
                """,
                (note_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"teacher note not found: {note_id}")
        return dict(row)

    def draft_from_teacher_note(
        self, *, note_id: int, trade_date: str, input_by: str | None = None
    ) -> dict[str, Any]:
        """从 teacher_notes 生成 Observation + TradeDraft（不写入 knowledge_assets）。"""
        note = self.get_teacher_note_row(note_id)
        text = build_text_for_trade_clues(note)
        tags: list[str] = []
        raw_tags = note.get("tags")
        if raw_tags:
            try:
                parsed = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
                if isinstance(parsed, list):
                    tags = [str(x) for x in parsed]
            except json.JSONDecodeError:
                pass
        clues = self._extract_trade_clues(text, tags)
        clues = merge_mentioned_stocks_into_clues(clues, note)
        planning = PlanningService(self.db_path)
        title = note.get("title") or f"老师笔记 #{note_id}"
        observation = planning.create_observation(
            trade_date=trade_date,
            source_type="teacher_note",
            title=title,
            sector_facts={"main_themes": clues.get("themes", [])},
            stock_facts=clues.get("stocks", []),
            judgements=clues.get("judgements", []),
            source_refs=[
                {
                    "teacher_note_id": note_id,
                    "teacher_name": note.get("teacher_name"),
                    "note_date": note.get("date"),
                }
            ],
            input_by=input_by,
        )
        summary = self._build_summary(text) or title
        draft = planning.create_draft(
            trade_date=trade_date,
            source_observation_ids=[observation["observation_id"]],
            title=f"{trade_date} 老师笔记草稿",
            summary=summary,
            input_by=input_by,
        )
        return {
            "teacher_note": note,
            "observation": observation,
            "draft": draft,
        }

    def _build_summary(self, content: str) -> str:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        return lines[0][:120] if lines else content[:120]

    def _extract_trade_clues(self, content: str, tags: list[str]) -> dict[str, Any]:
        themes: list[str] = []
        for keyword in THEME_KEYWORDS + tags:
            if keyword and keyword in content and keyword not in themes:
                themes.append(keyword)

        stocks: list[dict[str, Any]] = []
        for match in re.findall(r"\b\d{6}\.(?:SZ|SH|BJ)\b", content.upper()):
            stocks.append(
                {
                    "subject_type": "stock",
                    "subject_code": match,
                    "subject_name": "",
                    "reason": "来自资料提炼",
                }
            )

        judgements: list[dict[str, Any]] = []
        for token in ["主线", "分歧", "退潮", "回流", "反包"]:
            if token in content:
                judgements.append({"text": token, "ambiguous": True})

        risk_words = [word for word in ["风险", "谨慎", "注意", "监管"] if word in content]

        return {
            "themes": themes,
            "stocks": stocks,
            "judgements": judgements,
            "risk_words": risk_words,
        }
