"""资料层服务：knowledge_assets 与 draft 生成入口。"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from uuid import uuid4

from db.connection import get_db
from db.migrate import migrate
from services.planning_service import PlanningService


THEME_KEYWORDS = ["AI", "AI算力", "机器人", "锂电", "储能", "金融", "半导体"]


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
                    asset_type,
                    title,
                    content,
                    source,
                    json.dumps(tags or [], ensure_ascii=False),
                    summary,
                    json.dumps(trade_clues, ensure_ascii=False),
                ),
            )
        return self.get_asset(asset_id)

    def list_assets(self, limit: int = 20) -> list[dict[str, Any]]:
        with get_db(self.db_path) as conn:
            migrate(conn)
            rows = conn.execute(
                """
                SELECT * FROM knowledge_assets
                ORDER BY created_at DESC, asset_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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
        clues = json.loads(asset["trade_clues"] or "{}")
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
