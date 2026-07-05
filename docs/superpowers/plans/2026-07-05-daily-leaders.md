# Daily Leaders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a trading-day `daily-leaders` workflow that generates most-leader stock candidates at 22:30, pushes a DingTalk Markdown confirmation draft, accepts Codex natural-language confirmation converted to a payload, and writes confirmed leaders into review step 5 plus `leader_tracking`.

**Architecture:** Add a small backend workflow under `scripts/services/daily_leaders/` and a CLI under `scripts/cli/daily_leaders.py`. The workflow stays two-stage: `propose` is read-mostly and writes only local report artifacts; `confirm` requires `--input-by` and writes `daily_reviews.step5_leaders` through shared review leader-sync semantics.

**Tech Stack:** Python 3, argparse, sqlite3, pytest, existing review prefill logic, `DingTalkPusher`, `utils.llm_cli` for optional LLM narration, launchd.

**Spec Source:** `/Users/alyx/tradeSystem/docs/superpowers/specs/2026-07-05-daily-leaders-design.md`

---

## Scope

- v1 implements: DingTalk Markdown notification, Codex natural-language confirmation handled by Agent, CLI writeback.
- v1 does not implement: DingTalk button callback writeback, Web confirmation page, watchlist writes, trade plan writes.
- Scheduled trigger time is trading days `22:30`.
- All outputs are Simplified Chinese and separate `[事实]`, `[判断]`, and `[观点]`.
- `propose --no-llm` must work without Antigravity or network.

## Files

- Create `scripts/services/review_leaders.py`: reusable `sync_leader_tracking_from_step5` and `build_review_with_step5` helpers.
- Modify `scripts/api/routes/review.py`: replace private `_sync_leader_tracking` body with shared helper.
- Create `scripts/services/daily_leaders/__init__.py`
- Create `scripts/services/daily_leaders/models.py`: typed dict helpers and constants for candidate fields.
- Create `scripts/services/daily_leaders/candidates.py`: merge prefill, trend pool, history, teacher notes, and cognition evidence into ranked candidates.
- Create `scripts/services/daily_leaders/renderer.py`: render Markdown confirmation draft.
- Create `scripts/services/daily_leaders/store.py`: write/read `data/reports/daily-leaders/YYYY-MM-DD.{json,md}`.
- Create `scripts/services/daily_leaders/llm.py`: optional LLM reason enrichment with fail-closed fallback.
- Create `scripts/services/daily_leaders/service.py`: orchestrate `propose`, `show`, and `confirm`.
- Create `scripts/cli/daily_leaders.py`: argparse command registration and CLI handlers.
- Modify `scripts/main.py`: register and dispatch `daily-leaders`.
- Modify `scripts/tests/test_cli_smoke.py`: add `daily-leaders` parse smoke cases.
- Create `scripts/tests/test_review_leaders_service.py`
- Create `scripts/tests/test_daily_leaders_candidates.py`
- Create `scripts/tests/test_daily_leaders_renderer_store.py`
- Create `scripts/tests/test_daily_leaders_cli.py`
- Create `deploy/launchd/daily-leaders-runner.sh`
- Create `deploy/launchd/com.alyx.tradesystem.daily-leaders.plist`
- Modify `deploy/launchd/README.md`
- Modify `.agents/skills/INDEX.md`
- Modify `.agents/skills/daily-review/SKILL.md`
- Modify `.agents/skills/market-tasks/SKILL.md`
- Modify `AGENTS.md`

## Task 1: Reusable Review Leader Sync

**Files:**
- Create: `scripts/services/review_leaders.py`
- Modify: `scripts/api/routes/review.py`
- Test: `scripts/tests/test_review_leaders_service.py`

- [ ] **Step 1: Write failing tests**

Create `scripts/tests/test_review_leaders_service.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from db.migrate import migrate
from db import queries as Q
from services.review_leaders import build_review_with_step5, sync_leader_tracking_from_step5


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "trade.db"
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    migrate(c)
    yield c
    c.close()


def test_sync_leader_tracking_accepts_step5_dict(conn):
    step5 = {
        "top_leaders": [
            {
                "stock": "688041 海光信息",
                "sector": "半导体",
                "attribute_type": "走势引领",
                "position": "主升初期",
            }
        ]
    }

    count = sync_leader_tracking_from_step5(conn, "2026-07-03", step5)

    rows = Q.get_active_leaders(conn)
    assert count == 1
    assert rows[0]["stock_code"] == "688041 海光信息"
    assert rows[0]["stock_name"] == "688041 海光信息"
    assert rows[0]["sector"] == "半导体"
    assert rows[0]["attribute_type"] == "走势引领"
    assert rows[0]["current_phase"] == "主升初期"


def test_sync_leader_tracking_ignores_invalid_payload(conn):
    assert sync_leader_tracking_from_step5(conn, "2026-07-03", None) == 0
    assert sync_leader_tracking_from_step5(conn, "2026-07-03", {"top_leaders": "bad"}) == 0
    assert Q.get_active_leaders(conn) == []


def test_build_review_with_step5_preserves_existing_sections():
    existing = {
        "step1_market": {"notes": "大盘复盘"},
        "step5_leaders": {"top_leaders": [{"stock": "旧票", "sector": "旧板块"}]},
    }
    confirmed = {
        "top_leaders": [
            {"stock": "新票", "sector": "半导体", "attribute_type": "容量最大"}
        ],
        "notes": "系统候选，经用户确认",
    }

    merged = build_review_with_step5(existing, confirmed)

    assert merged["step1_market"] == {"notes": "大盘复盘"}
    assert merged["step5_leaders"] == confirmed
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_review_leaders_service.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'services.review_leaders'`.

- [ ] **Step 3: Implement shared service**

Create `scripts/services/review_leaders.py`:

```python
"""Shared helpers for review step5 leader writeback."""
from __future__ import annotations

import copy
import json
import sqlite3
from typing import Any

from db import queries as Q


def _coerce_step5(step5: Any) -> dict[str, Any] | None:
    if not step5:
        return None
    if isinstance(step5, str):
        try:
            step5 = json.loads(step5)
        except (json.JSONDecodeError, TypeError):
            return None
    return step5 if isinstance(step5, dict) else None


def sync_leader_tracking_from_step5(
    conn: sqlite3.Connection,
    review_date: str,
    step5: Any,
) -> int:
    """Sync confirmed review step5 leaders into leader_tracking."""
    payload = _coerce_step5(step5)
    if not payload:
        return 0
    leaders = payload.get("top_leaders")
    if not isinstance(leaders, list):
        return 0
    synced = 0
    for item in leaders:
        if not isinstance(item, dict):
            continue
        stock = str(item.get("stock") or "").strip()
        sector = str(item.get("sector") or "").strip()
        if not stock or not sector:
            continue
        Q.upsert_leader_tracking(
            conn,
            stock_code=stock,
            stock_name=stock,
            sector=sector,
            attribute_type=item.get("attribute_type") or item.get("attribute") or "",
            seen_date=review_date,
            current_phase=item.get("position") or None,
        )
        synced += 1
    return synced


def build_review_with_step5(existing: dict[str, Any] | None, step5: dict[str, Any]) -> dict[str, Any]:
    """Return a review payload preserving existing sections while replacing step5."""
    merged = copy.deepcopy(existing or {})
    merged["step5_leaders"] = step5
    return merged
```

Modify `scripts/api/routes/review.py`:

```python
from services.review_leaders import sync_leader_tracking_from_step5
```

Replace the call in `save_review`:

```python
    sync_leader_tracking_from_step5(conn, date, body.get("step5_leaders"))
```

Replace `_sync_leader_tracking` body with a thin compatibility wrapper:

```python
def _sync_leader_tracking(conn: sqlite3.Connection, review_date: str, step5: Any) -> None:
    """Compatibility wrapper for tests/imports; real logic lives in services.review_leaders."""
    sync_leader_tracking_from_step5(conn, review_date, step5)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest scripts/tests/test_review_leaders_service.py scripts/tests/test_leader_tracking.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add scripts/services/review_leaders.py scripts/api/routes/review.py scripts/tests/test_review_leaders_service.py
git commit -m "feat(daily-leaders): share review leader sync"
```

## Task 2: Candidate Evidence and Ranking

**Files:**
- Create: `scripts/services/daily_leaders/__init__.py`
- Create: `scripts/services/daily_leaders/models.py`
- Create: `scripts/services/daily_leaders/candidates.py`
- Test: `scripts/tests/test_daily_leaders_candidates.py`

- [ ] **Step 1: Write failing tests**

Create `scripts/tests/test_daily_leaders_candidates.py`:

```python
from __future__ import annotations

from services.daily_leaders.candidates import build_candidates, teacher_alignment


def test_build_candidates_merges_prefill_and_marks_new_leader():
    prefill = {
        "step5_leaders": {
            "top_leaders": [
                {
                    "stock": "海光信息",
                    "sector": "半导体",
                    "attribute_type": "走势引领",
                    "is_prefilled": True,
                }
            ]
        },
        "teacher_notes": [],
        "cognitions_by_step": {"step5_leaders": []},
    }
    history = [{"stock_name": "工业富联", "sector": "算力", "attribute_type": "容量最大"}]
    trend_pool = []

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=history)

    assert result["date"] == ""
    assert result["top_leaders"][0]["stock"] == "海光信息"
    assert result["top_leaders"][0]["sector"] == "半导体"
    assert result["top_leaders"][0]["is_new"] is True
    assert result["top_leaders"][0]["teacher_alignment"] == "未提及"
    assert any(e["label"] == "[判断]" for e in result["top_leaders"][0]["evidence"])


def test_trend_pool_candidate_added_when_not_in_prefill():
    prefill = {"step5_leaders": None, "teacher_notes": [], "cognitions_by_step": {}}
    trend_pool = [
        {
            "code": "688041",
            "name": "海光信息",
            "sw_l2": "半导体",
            "entered_date": "2026-07-03",
            "last_signal": {"entry_trigger": "涨停"},
        }
    ]

    result = build_candidates(prefill=prefill, trend_pool=trend_pool, history=[])

    item = result["top_leaders"][0]
    assert item["stock"] == "688041 海光信息"
    assert item["sector"] == "半导体"
    assert item["attribute_type"] == "走势引领"
    assert item["clarity"] == "中"


def test_teacher_alignment_support_conflict_and_unmentioned():
    notes = [
        {"teacher_name": "鞠磊", "sectors": '["半导体"]', "core_view": "半导体主线继续观察海光信息"},
        {"teacher_name": "小鲍", "sectors": '["机器人"]', "core_view": "机器人退潮，龙头承接走弱"},
    ]

    assert teacher_alignment("海光信息", "半导体", notes)["status"] == "支持"
    assert teacher_alignment("机器人A", "机器人", notes)["status"] == "冲突"
    assert teacher_alignment("其它股", "券商", notes)["status"] == "未提及"


def test_teacher_alignment_conflict_words_must_be_near_matched_term():
    notes = [
        {
            "teacher_name": "鞠磊",
            "sectors": '["半导体", "机器人"]',
            "core_view": "半导体继续观察海光信息，机器人退潮",
        }
    ]

    assert teacher_alignment("海光信息", "半导体", notes)["status"] == "支持"
    assert teacher_alignment("机器人A", "机器人", notes)["status"] == "冲突"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_candidates.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'services.daily_leaders'`.

- [ ] **Step 3: Implement models and candidate builder**

Create `scripts/services/daily_leaders/__init__.py`:

```python
"""Daily most-leader candidate confirmation workflow."""
```

Create `scripts/services/daily_leaders/models.py`:

```python
from __future__ import annotations

LEADER_FIELDS = ("stock", "sector", "attribute_type", "attribute", "clarity", "position", "is_new")
CLARITY_HIGH = "高"
CLARITY_MEDIUM = "中"
CLARITY_LOW = "低"
TEACHER_SUPPORT = "支持"
TEACHER_CONFLICT = "冲突"
TEACHER_UNMENTIONED = "未提及"
```

Create `scripts/services/daily_leaders/candidates.py`:

```python
from __future__ import annotations

import json
from typing import Any

from . import models as M


def _loads_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _history_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("stock_name") or row.get("stock") or "").strip(), str(row.get("sector") or "").strip())


def teacher_alignment(stock: str, sector: str, notes: list[dict[str, Any]]) -> dict[str, Any]:
    stock_text = str(stock or "")
    sector_text = str(sector or "")
    conflict_words = ("退潮", "走弱", "回避")
    for note in notes:
        haystack = " ".join(
            str(note.get(k) or "") for k in ("title", "core_view", "key_points", "raw_content")
        )
        sectors = [str(x) for x in _loads_list(note.get("sectors"))]
        sector_hit = sector_text and (sector_text in haystack or sector_text in sectors)
        stock_hit = stock_text and stock_text in haystack
        matched_terms = [term for term in (stock_text, sector_text) if term and term in haystack]
        if sector_hit and _has_nearby_conflict(haystack, matched_terms, conflict_words):
            return {"status": M.TEACHER_CONFLICT, "note": note}
        if sector_hit or stock_hit:
            return {"status": M.TEACHER_SUPPORT, "note": note}
    return {"status": M.TEACHER_UNMENTIONED, "note": None}


def _has_nearby_conflict(haystack: str, terms: list[str], conflict_words: tuple[str, ...]) -> bool:
    window = 4
    for term in terms:
        start = haystack.find(term)
        while start != -1:
            end = start + len(term)
            nearby = haystack[max(0, start - window): min(len(haystack), end + window)]
            if any(word in nearby for word in conflict_words):
                return True
            start = haystack.find(term, start + 1)
    return False


def _candidate_from_prefill(item: dict[str, Any], notes: list[dict[str, Any]], history_keys: set[tuple[str, str]]) -> dict[str, Any]:
    stock = str(item.get("stock") or "").strip()
    sector = str(item.get("sector") or "").strip()
    align = teacher_alignment(stock, sector, notes)
    return {
        "stock": stock,
        "code": item.get("code"),
        "sector": sector,
        "attribute_type": item.get("attribute_type") or item.get("attribute") or "走势引领",
        "attribute": item.get("attribute") or "",
        "clarity": item.get("clarity") or M.CLARITY_MEDIUM,
        "position": item.get("position") or "",
        "is_new": (stock, sector) not in history_keys,
        "teacher_alignment": align["status"],
        "evidence": [{"label": "[判断]", "text": "来自复盘预填候选，需用户确认"}],
        "score": 80,
    }


def _candidate_from_trend_pool(row: dict[str, Any], notes: list[dict[str, Any]], history_keys: set[tuple[str, str]]) -> dict[str, Any]:
    code = str(row.get("code") or "").strip()
    name = str(row.get("name") or "").strip()
    stock = f"{code} {name}".strip()
    sector = str(row.get("sw_l2") or "").strip() or "未分类"
    align = teacher_alignment(stock, sector, notes)
    trigger = (row.get("last_signal") or {}).get("entry_trigger") if isinstance(row.get("last_signal"), dict) else ""
    return {
        "stock": stock,
        "code": code or None,
        "sector": sector,
        "attribute_type": "走势引领",
        "attribute": f"趋势主升池触发：{trigger or '趋势信号'}",
        "clarity": M.CLARITY_MEDIUM,
        "position": "",
        "is_new": (stock, sector) not in history_keys,
        "teacher_alignment": align["status"],
        "evidence": [{"label": "[判断]", "text": "来自趋势主升观察池，需用户确认"}],
        "score": 70,
    }


def build_candidates(
    *,
    prefill: dict[str, Any],
    trend_pool: list[dict[str, Any]],
    history: list[dict[str, Any]],
    date: str = "",
) -> dict[str, Any]:
    notes = [n for n in prefill.get("teacher_notes") or [] if isinstance(n, dict)]
    history_keys = {_history_key(row) for row in history if isinstance(row, dict)}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    step5 = prefill.get("step5_leaders") or {}
    for item in step5.get("top_leaders") or []:
        if not isinstance(item, dict):
            continue
        cand = _candidate_from_prefill(item, notes, history_keys)
        key = (cand["stock"], cand["sector"])
        if cand["stock"] and cand["sector"] and key not in seen:
            seen.add(key)
            out.append(cand)

    for row in trend_pool:
        cand = _candidate_from_trend_pool(row, notes, history_keys)
        key = (cand["stock"], cand["sector"])
        if cand["stock"] and cand["sector"] and key not in seen:
            seen.add(key)
            out.append(cand)

    out.sort(key=lambda item: (-int(item.get("score") or 0), item.get("sector", ""), item.get("stock", "")))
    return {"date": date, "top_leaders": out}
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_candidates.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add scripts/services/daily_leaders scripts/tests/test_daily_leaders_candidates.py
git commit -m "feat(daily-leaders): build leader candidates"
```

## Task 3: Renderer, Store, and LLM Fallback

**Files:**
- Create: `scripts/services/daily_leaders/renderer.py`
- Create: `scripts/services/daily_leaders/store.py`
- Create: `scripts/services/daily_leaders/llm.py`
- Test: `scripts/tests/test_daily_leaders_renderer_store.py`

- [ ] **Step 1: Write failing tests**

Create `scripts/tests/test_daily_leaders_renderer_store.py`:

```python
from __future__ import annotations

from services.daily_leaders.llm import enrich_with_llm_reason
from services.daily_leaders.renderer import render_markdown
from services.daily_leaders.store import read_proposal, write_proposal


def _proposal():
    return {
        "date": "2026-07-03",
        "top_leaders": [
            {
                "stock": "688041 海光信息",
                "sector": "半导体",
                "attribute_type": "走势引领",
                "attribute": "启动日主动引领",
                "clarity": "高",
                "position": "主升初期",
                "is_new": True,
                "teacher_alignment": "支持",
                "evidence": [{"label": "[事实]", "text": "半导体候选板块"}, {"label": "[判断]", "text": "走势引领候选"}],
                "llm_reason": "",
            }
        ],
    }


def test_render_markdown_contains_labels_and_confirmation_instruction():
    md = render_markdown(_proposal())
    assert "每日最票候选确认稿 · 2026-07-03" in md
    assert "[事实]" in md
    assert "[判断]" in md
    assert "老师观点对照：支持" in md
    assert "可回复：确认，全部录入" in md


def test_store_round_trip(tmp_path):
    paths = write_proposal(_proposal(), root=tmp_path)
    loaded = read_proposal("2026-07-03", root=tmp_path)
    assert paths["json"].name == "2026-07-03.json"
    assert paths["markdown"].name == "2026-07-03.md"
    assert loaded["top_leaders"][0]["stock"] == "688041 海光信息"


def test_llm_fallback_returns_original_when_disabled():
    proposal = _proposal()
    out = enrich_with_llm_reason(proposal, enabled=False)
    assert out == proposal


def test_llm_enrichment_uses_runner_mapping():
    proposal = _proposal()
    out = enrich_with_llm_reason(
        proposal,
        enabled=True,
        runner=lambda prompt: {"688041 海光信息|半导体": "走势引领清晰，老师观点支持，仍需人工确认。"},
    )
    assert out["top_leaders"][0]["llm_reason"] == "走势引领清晰，老师观点支持，仍需人工确认。"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_renderer_store.py -v
```

Expected: fails because renderer/store/llm modules do not exist.

- [ ] **Step 3: Implement renderer, store, and LLM fallback**

Create `scripts/services/daily_leaders/renderer.py`:

```python
from __future__ import annotations

from typing import Any


def render_markdown(proposal: dict[str, Any]) -> str:
    date = proposal.get("date") or "-"
    lines = [
        f"# 每日最票候选确认稿 · {date}",
        "",
        "> 本稿为系统候选 [判断]，不构成买卖建议；请确认后再写入复盘第 5 步。",
        "",
    ]
    leaders = proposal.get("top_leaders") or []
    if not leaders:
        lines += ["今日无可用最票候选。", ""]
    for idx, item in enumerate(leaders, start=1):
        lines += [
            f"## {idx}. {item.get('sector') or '-'} · {item.get('stock') or '-'}",
            f"- 最的属性：{item.get('attribute_type') or '-'}",
            f"- 清晰度：{item.get('clarity') or '-'}",
            f"- 是否新最：{'是' if item.get('is_new') else '否'}",
            f"- 老师观点对照：{item.get('teacher_alignment') or '未提及'}",
        ]
        if item.get("attribute"):
            lines.append(f"- 补充说明：{item.get('attribute')}")
        if item.get("llm_reason"):
            lines.append(f"- LLM 复核：{item.get('llm_reason')}")
        for ev in item.get("evidence") or []:
            if isinstance(ev, dict):
                lines.append(f"- {ev.get('label') or '[判断]'} {ev.get('text') or ''}".rstrip())
        lines.append("")
    lines += [
        "## 确认方式",
        "- 可回复：确认，全部录入",
        "- 可回复：确认录入半导体和算力，剔除机器人",
        "- 可回复：半导体最票改成 A，容量中军保留 B",
    ]
    return "\n".join(lines).strip() + "\n"
```

Create `scripts/services/daily_leaders/store.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .renderer import render_markdown

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ROOT = REPO_ROOT / "data" / "reports" / "daily-leaders"


def _root(root: str | Path | None = None) -> Path:
    return Path(root) if root is not None else DEFAULT_ROOT


def write_proposal(proposal: dict[str, Any], *, root: str | Path | None = None) -> dict[str, Path]:
    date = str(proposal.get("date") or "").strip()
    if not date:
        raise ValueError("proposal.date is required")
    base = _root(root)
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / f"{date}.json"
    md_path = base / f"{date}.md"
    json_path.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(proposal), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}


def read_proposal(date: str, *, root: str | Path | None = None) -> dict[str, Any]:
    path = _root(root) / f"{date}.json"
    return json.loads(path.read_text(encoding="utf-8"))
```

Create `scripts/services/daily_leaders/llm.py`:

```python
from __future__ import annotations

import copy
import json
import subprocess
from typing import Any

from utils import llm_cli


def _build_prompt(proposal: dict[str, Any]) -> str:
    compact = {
        "date": proposal.get("date"),
        "top_leaders": [
            {
                "key": f"{item.get('stock')}|{item.get('sector')}",
                "stock": item.get("stock"),
                "sector": item.get("sector"),
                "attribute_type": item.get("attribute_type"),
                "clarity": item.get("clarity"),
                "teacher_alignment": item.get("teacher_alignment"),
                "evidence": item.get("evidence") or [],
            }
            for item in proposal.get("top_leaders") or []
        ],
    }
    return (
        "你是 A 股复盘助手。请只基于输入证据，为每个最票候选输出一句中文复核理由。"
        "必须标注这是[判断]，不得给买卖建议、价格目标或仓位建议。"
        "返回 JSON object，key 为输入 key，value 为不超过 60 字的理由。\n\n"
        + json.dumps(compact, ensure_ascii=False)
    )


def _run_llm(prompt: str) -> dict[str, str]:
    config = llm_cli.resolve_config(default_timeout=180)
    cmd = llm_cli.build_prompt_command(config, prompt)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=config.timeout_seconds + 5)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "llm command failed")
    parsed = json.loads(proc.stdout.strip())
    return parsed if isinstance(parsed, dict) else {}


def enrich_with_llm_reason(
    proposal: dict[str, Any],
    *,
    enabled: bool = True,
    runner=None,
) -> dict[str, Any]:
    """Add short LLM review reasons; on any failure, return the original proposal unchanged."""
    if not enabled:
        return proposal
    try:
        reason_map = (runner or _run_llm)(_build_prompt(proposal))
    except Exception:
        return proposal
    enriched = copy.deepcopy(proposal)
    for item in enriched.get("top_leaders") or []:
        key = f"{item.get('stock')}|{item.get('sector')}"
        reason = reason_map.get(key)
        if isinstance(reason, str) and reason.strip():
            item["llm_reason"] = reason.strip()
    return enriched
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_renderer_store.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/services/daily_leaders/renderer.py scripts/services/daily_leaders/store.py scripts/services/daily_leaders/llm.py scripts/tests/test_daily_leaders_renderer_store.py
git commit -m "feat(daily-leaders): render and store proposals"
```

## Task 4: Service and CLI

**Files:**
- Create: `scripts/services/daily_leaders/service.py`
- Create: `scripts/cli/daily_leaders.py`
- Modify: `scripts/main.py`
- Modify: `scripts/tests/test_cli_smoke.py`
- Test: `scripts/tests/test_daily_leaders_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `scripts/tests/test_daily_leaders_cli.py`:

```python
from __future__ import annotations

import argparse

from cli.daily_leaders import register_subparser


def _parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    register_subparser(sub)
    return parser


def test_parse_propose():
    args = _parser().parse_args(["daily-leaders", "propose", "--date", "2026-07-03", "--push", "--no-llm"])
    assert args.command == "daily-leaders"
    assert args.daily_leaders_command == "propose"
    assert args.date == "2026-07-03"
    assert args.push is True
    assert args.no_llm is True


def test_parse_confirm_requires_input_by():
    parser = _parser()
    try:
        parser.parse_args(["daily-leaders", "confirm", "--date", "2026-07-03"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("confirm without --input-by should fail")


def test_parse_show_json():
    args = _parser().parse_args(["daily-leaders", "show", "--date", "2026-07-03", "--json"])
    assert args.daily_leaders_command == "show"
    assert args.json is True
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_cli.py -v
```

Expected: fails with `ModuleNotFoundError: No module named 'cli.daily_leaders'`.

- [ ] **Step 3: Implement service**

Create `scripts/services/daily_leaders/service.py`:

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from db import queries as Q
from services.review_leaders import build_review_with_step5, sync_leader_tracking_from_step5

from .candidates import build_candidates
from .llm import enrich_with_llm_reason
from .store import read_proposal, write_proposal


def _active_history(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        return Q.get_active_leaders(conn)
    except Exception:
        return []


def _trend_pool(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    try:
        from services.trend_leader import pool
        return pool.list_pool(conn, status="active")
    except Exception:
        return []


def propose(
    *,
    conn: sqlite3.Connection,
    date: str,
    prefill: dict[str, Any],
    no_llm: bool = False,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    proposal = build_candidates(
        prefill=prefill,
        trend_pool=_trend_pool(conn),
        history=_active_history(conn),
        date=date,
    )
    proposal = enrich_with_llm_reason(proposal, enabled=not no_llm)
    paths = write_proposal(proposal, root=output_root)
    proposal["paths"] = {k: str(v) for k, v in paths.items()}
    return proposal


def show(date: str, *, output_root: str | Path | None = None) -> dict[str, Any]:
    return read_proposal(date, root=output_root)


def confirm(
    *,
    conn: sqlite3.Connection,
    date: str,
    input_by: str,
    leaders_file: str | Path | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    if not input_by:
        raise ValueError("--input-by is required")
    source = json.loads(Path(leaders_file).read_text(encoding="utf-8")) if leaders_file else read_proposal(date, root=output_root)
    step5 = {"top_leaders": []}
    for item in source.get("top_leaders") or []:
        step5["top_leaders"].append({
            "stock": item.get("stock") or "",
            "sector": item.get("sector") or "",
            "attribute_type": item.get("attribute_type") or "",
            "attribute": item.get("attribute") or "",
            "clarity": item.get("clarity") or "",
            "position": item.get("position") or "",
            "is_new": bool(item.get("is_new")),
        })
    step5["notes"] = f"daily-leaders confirmed by {input_by}"
    existing = Q.get_daily_review(conn, date) or {}
    payload = build_review_with_step5(existing, step5)
    Q.upsert_daily_review(conn, date, payload)
    synced = sync_leader_tracking_from_step5(conn, date, step5)
    conn.commit()
    return {"ok": True, "date": date, "synced_leader_tracking": synced, "step5_leaders": step5}
```

- [ ] **Step 4: Implement CLI**

Create `scripts/cli/daily_leaders.py`:

```python
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from db.connection import get_connection
from services.daily_leaders import renderer, service


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("daily-leaders", help="每日最票候选确认流水线")
    sub = parser.add_subparsers(dest="daily_leaders_command")

    p = sub.add_parser("propose", help="生成最票候选稿")
    p.add_argument("--date", default=None, help="交易日 YYYY-MM-DD，默认今天")
    p.add_argument("--push", action="store_true", help="推送钉钉 Markdown")
    p.add_argument("--no-llm", action="store_true", help="不调用 LLM，只生成规则候选")

    c = sub.add_parser("confirm", help="确认写入复盘第 5 步")
    c.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    c.add_argument("--input-by", required=True, help="写入来源")
    c.add_argument("--leaders-file", default=None, help="确认后的候选 JSON 文件")

    s = sub.add_parser("show", help="查看候选稿")
    s.add_argument("--date", required=True, help="交易日 YYYY-MM-DD")
    s.add_argument("--json", action="store_true", help="输出 JSON")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    command = getattr(args, "daily_leaders_command", None)
    if command == "propose":
        _handle_propose(args)
    elif command == "confirm":
        _handle_confirm(args)
    elif command == "show":
        _handle_show(args)
    else:
        print("用法：python main.py daily-leaders propose|confirm|show [...]", file=sys.stderr)
        sys.exit(2)


def _date(args: argparse.Namespace) -> str:
    return args.date or dt.date.today().isoformat()


def _handle_propose(args: argparse.Namespace) -> None:
    from api.routes.review import get_prefill
    conn = get_connection()
    try:
        prefill = get_prefill(_date(args), conn=conn)
        proposal = service.propose(conn=conn, date=_date(args), prefill=prefill, no_llm=args.no_llm)
    finally:
        conn.close()
    md = renderer.render_markdown(proposal)
    print(md)
    if args.push:
        _push_to_dingtalk(f"每日最票候选确认稿 · {_date(args)}", md)


def _handle_confirm(args: argparse.Namespace) -> None:
    conn = get_connection()
    try:
        result = service.confirm(conn=conn, date=args.date, input_by=args.input_by, leaders_file=args.leaders_file)
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _handle_show(args: argparse.Namespace) -> None:
    proposal = service.show(args.date)
    if args.json:
        print(json.dumps(proposal, ensure_ascii=False, indent=2))
    else:
        print(renderer.render_markdown(proposal))


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher
    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        print("[daily-leaders] DingTalk 未配置，已保留本地候选稿", file=sys.stderr)
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    print("[daily-leaders] DingTalk 推送成功" if ok else "[daily-leaders] DingTalk 推送失败", file=sys.stderr)
```

Modify `scripts/main.py` where other command parsers are registered:

```python
    from cli.daily_leaders import register_subparser as register_daily_leaders_subparser
    register_daily_leaders_subparser(subparsers)
```

Modify dispatch section:

```python
    elif args.command == "daily-leaders":
        from cli import daily_leaders as daily_leaders_module
        daily_leaders_module.handle_command(config, args)
```

- [ ] **Step 5: Add smoke cases**

Modify `scripts/tests/test_cli_smoke.py` to include main parser cases near other top-level command tests:

```python
DAILY_LEADERS_COMMANDS = [
    ["daily-leaders", "propose", "--date", "2026-07-03", "--no-llm"],
    ["daily-leaders", "propose", "--date", "2026-07-03", "--push"],
    ["daily-leaders", "show", "--date", "2026-07-03", "--json"],
    ["daily-leaders", "confirm", "--date", "2026-07-03", "--input-by", "codex"],
]
```

Add these commands to the existing main parser smoke parametrization. If the file only parametrizes DB commands today, add a new test:

```python
@pytest.mark.parametrize("argv", DAILY_LEADERS_COMMANDS)
def test_daily_leaders_commands_parse(argv):
    parser = _build_main_parser()
    args = parser.parse_args(argv)
    assert args.command == "daily-leaders"
```

- [ ] **Step 6: Run tests and verify GREEN**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_cli.py scripts/tests/test_cli_smoke.py -k "daily_leaders or daily-leaders" -v
```

Expected: daily-leaders parse tests pass.

- [ ] **Step 7: Commit Task 4**

```bash
git add scripts/services/daily_leaders/service.py scripts/cli/daily_leaders.py scripts/main.py scripts/tests/test_daily_leaders_cli.py scripts/tests/test_cli_smoke.py
git commit -m "feat(daily-leaders): add CLI workflow"
```

## Task 5: Scheduling, Docs, and Verification

**Files:**
- Create: `deploy/launchd/daily-leaders-runner.sh`
- Create: `deploy/launchd/com.alyx.tradesystem.daily-leaders.plist`
- Modify: `deploy/launchd/README.md`
- Modify: `.agents/skills/INDEX.md`
- Modify: `.agents/skills/daily-review/SKILL.md`
- Modify: `.agents/skills/market-tasks/SKILL.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Add launchd runner**

Create `deploy/launchd/daily-leaders-runner.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ -f "$HOME/.config/tradeSystem.env" ]; then
  set -a
  source "$HOME/.config/tradeSystem.env"
  set +a
fi

cd /Users/alyx/tradeSystem/scripts
python3 main.py daily-leaders propose --push
```

Create `deploy/launchd/com.alyx.tradesystem.daily-leaders.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.alyx.tradesystem.daily-leaders</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/alyx/tradeSystem/deploy/launchd/daily-leaders-runner.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>22</integer><key>Minute</key><integer>30</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>/tmp/tradesystem-daily-leaders.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/tradesystem-daily-leaders.err.log</string>
</dict>
</plist>
```

Run:

```bash
chmod +x deploy/launchd/daily-leaders-runner.sh
```

- [ ] **Step 2: Update docs and skills**

Add concise entries:

- `.agents/skills/INDEX.md`: add `python main.py daily-leaders propose|show|confirm ...` row under `daily-review` or `market-tasks`.
- `.agents/skills/daily-review/SKILL.md`: mention v1 confirmation flow and `22:30` proposal.
- `.agents/skills/market-tasks/SKILL.md`: mention scheduled `daily-leaders propose --push` after post-market derived tasks.
- `AGENTS.md`: add standard command bullet for `python3 main.py daily-leaders ...`.
- `deploy/launchd/README.md`: add install/start instructions for `com.alyx.tradesystem.daily-leaders`.

- [ ] **Step 3: Run full focused verification**

Run:

```bash
python3 -m pytest scripts/tests/test_review_leaders_service.py scripts/tests/test_daily_leaders_candidates.py scripts/tests/test_daily_leaders_renderer_store.py scripts/tests/test_daily_leaders_cli.py -v
python3 -m pytest scripts/tests/test_cli_smoke.py -v
make commands-doc
make commands-check
```

Expected: all pass. If `make commands-doc` updates `docs/commands.*`, inspect and commit those generated changes only if they are deterministic and relevant.

- [ ] **Step 4: Manual dry-run verification**

Run for a recent trading date with existing data:

```bash
cd /Users/alyx/tradeSystem/scripts
python3 main.py daily-leaders propose --date 2026-07-03 --no-llm
python3 main.py daily-leaders show --date 2026-07-03
```

Expected:

- Markdown prints with title `每日最票候选确认稿 · 2026-07-03`.
- JSON exists at `/Users/alyx/tradeSystem/data/reports/daily-leaders/2026-07-03.json`.

- [ ] **Step 5: Commit Task 5**

```bash
git add deploy/launchd/daily-leaders-runner.sh deploy/launchd/com.alyx.tradesystem.daily-leaders.plist deploy/launchd/README.md .agents/skills/INDEX.md .agents/skills/daily-review/SKILL.md .agents/skills/market-tasks/SKILL.md AGENTS.md docs/commands.json docs/commands.md
git commit -m "chore(daily-leaders): schedule and document workflow"
```

## Final Verification

- [ ] Run all focused tests:

```bash
python3 -m pytest scripts/tests/test_review_leaders_service.py scripts/tests/test_daily_leaders_candidates.py scripts/tests/test_daily_leaders_renderer_store.py scripts/tests/test_daily_leaders_cli.py scripts/tests/test_cli_smoke.py -v
```

- [ ] Run a no-LLM proposal:

```bash
cd /Users/alyx/tradeSystem/scripts
python3 main.py daily-leaders propose --date 2026-07-03 --no-llm
```

- [ ] Run a confirmation against a copied proposal in a test DB or disposable branch before touching production `data/trade.db`.

## Self-Review

- Spec coverage: v1 proposal, DingTalk Markdown, natural-language-to-payload handled by Agent, CLI confirmation, `leader_tracking` sync, 22:30 launchd, and v2 button deferral are covered.
- Placeholder scan: no unresolved markers or undefined implementation steps remain.
- Type consistency: proposal uses `top_leaders` throughout; writeback maps only page-compatible fields into `daily_reviews.step5_leaders`.
