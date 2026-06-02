# 交易认知沉淀定时汇总（cognition-digest）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建只读 `cognition_digest` 服务 + 顶层 CLI + 3 个 launchd 定时任务，把近3日/近1周/近1月「值得学习的交易认知」按热度+共识+新增汇总，gemini 合成体系/方向建议（红线护栏），推钉钉。

**Architecture:** 镜像 `research_digest` 分层（collector/scorer/narrator/renderer/service）。纯读 `data/trade.db` 认知三表，**不写库、不改 schema、不依赖任何行情 provider**。复用 `research_digest.narrator.build_gemini_runner`（gemini 调用）与 `recommend.formatter.REDLINE_KEYWORDS`（红线词单一真源）。三个窗口各一个 per-task launchd plist，共用参数化 runner（不碰 `main.py schedule` APScheduler）。

**Tech Stack:** Python 3.9 / argparse / sqlite3 / pytest（tmp_path 真库 + mock llm_runner）/ launchd plist。

**Spec:** `.cursor/plans/cognition-digest-design-2026-06-02.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `scripts/services/cognition_digest/__init__.py` | 导出 `run_window_digest` / `RenderedCognitionDigest` / `WINDOWS` | 新建 |
| `scripts/services/cognition_digest/windows.py` | 窗口定义 + 区间换算 | 新建 |
| `scripts/services/cognition_digest/collector.py` | 只读聚合查询 → `WindowData` | 新建 |
| `scripts/services/cognition_digest/scorer.py` | 热度+共识+新增打分排序 | 新建 |
| `scripts/services/cognition_digest/narrator.py` | gemini 建议合成 + 红线三级护栏 | 新建 |
| `scripts/services/cognition_digest/renderer.py` | 渲染 Markdown | 新建 |
| `scripts/services/cognition_digest/service.py` | `run_window_digest` 编排 | 新建 |
| `scripts/cli/cognition_digest.py` | 顶层 CLI subparser + 钉钉推送 | 新建 |
| `scripts/main.py` | 注册 subparser + dispatch | 修改 |
| `scripts/tests/test_cognition_digest_*.py` | 分层测试（5 文件） | 新建 |
| `scripts/tests/test_cli_smoke.py` | `ARCHITECTURE_COMMANDS` +5 | 修改 |
| `deploy/launchd/cognition-digest-runner.sh` | 参数化 runner | 新建 |
| `deploy/launchd/com.alyx.tradesystem.cognition-digest-{recent3d,weekly,monthly}.plist` | 3 个 plist | 新建 |
| `deploy/launchd/README.md` | 部署说明 | 修改 |
| `.agents/skills/INDEX.md` / `market-tasks/SKILL.md` / `cognition-evolution/SKILL.md` / `CLAUDE.md` / `AGENTS.md` | 文档同步 | 修改 |

> **阶段级 review（强制，对齐 `implementation-plan.md` 3.1 / `post-dev-codex-review.md`）**：每个 Task（G 阶段）pytest 全绿后，**立即**跑 subagent 审查 + `codex:codex-rescue` review 双门（前台），满足两规则结束条件才进下一 Task；不把 review 攒到最后。

---

## Task 1（G1）：窗口 + 采集 + 打分

**Files:**
- Create: `scripts/services/cognition_digest/__init__.py`, `windows.py`, `collector.py`, `scorer.py`
- Test: `scripts/tests/test_cognition_digest_collector.py`, `test_cognition_digest_scorer.py`

- [ ] **Step 1.1: 写 windows 失败测试**

`scripts/tests/test_cognition_digest_scorer.py`（先放 windows 用例）：

```python
from __future__ import annotations
import pytest
from services.cognition_digest.windows import WINDOWS, resolve_window, window_bounds


def test_windows_three_keys():
    assert set(WINDOWS) == {"recent3d", "weekly", "monthly"}
    assert WINDOWS["recent3d"].lookback_days == 3
    assert WINDOWS["weekly"].lookback_days == 7
    assert WINDOWS["monthly"].lookback_days == 30


def test_resolve_window_unknown_raises():
    with pytest.raises(ValueError):
        resolve_window("yearly")


def test_window_bounds_closed_interval():
    # recent3d, anchor=06-02 → [05-31, 06-02]（含 anchor，跨度=lookback）
    spec = resolve_window("recent3d")
    assert window_bounds(spec, "2026-06-02") == ("2026-05-31", "2026-06-02")
```

- [ ] **Step 1.2: 跑测试确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_scorer.py -v`
Expected: FAIL（`ModuleNotFoundError: services.cognition_digest`）

- [ ] **Step 1.3: 实现 windows.py + 空 __init__.py**

`scripts/services/cognition_digest/__init__.py`：

```python
"""交易认知沉淀定时汇总（只读）：近3日/近1周/近1月 → 钉钉。"""
from __future__ import annotations

from .service import RenderedCognitionDigest, run_window_digest
from .windows import WINDOWS

__all__ = ["run_window_digest", "RenderedCognitionDigest", "WINDOWS"]
```

> 注：`__init__` 先引用 `service`，Step 1 阶段 `service.py` 尚未建 → 本步**先写一个最小 `__init__.py` 只导出 WINDOWS**，待 Task 3 建 service 后再补全 import。Step 1.3 的 `__init__.py` 内容用下面这版：

```python
"""交易认知沉淀定时汇总（只读）。Task 3 后补 service 导出。"""
from __future__ import annotations
from .windows import WINDOWS
__all__ = ["WINDOWS"]
```

`scripts/services/cognition_digest/windows.py`：

```python
"""窗口定义与区间换算（日历日回溯，闭区间含 anchor）。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class WindowSpec:
    key: str
    lookback_days: int
    label: str
    top_n: int


WINDOWS: dict[str, WindowSpec] = {
    "recent3d": WindowSpec("recent3d", 3, "近3日", 5),
    "weekly": WindowSpec("weekly", 7, "近1周", 6),
    "monthly": WindowSpec("monthly", 30, "近1月", 8),
}


def resolve_window(key: str) -> WindowSpec:
    spec = WINDOWS.get(key)
    if spec is None:
        raise ValueError(f"未知窗口 {key!r}，需为 {sorted(WINDOWS)} 之一")
    return spec


def window_bounds(spec: WindowSpec, anchor_date: str) -> tuple[str, str]:
    """[start, anchor] 闭区间，start = anchor - (lookback_days - 1) 天。"""
    anchor = datetime.date.fromisoformat(anchor_date)
    start = anchor - datetime.timedelta(days=spec.lookback_days - 1)
    return start.isoformat(), anchor.isoformat()
```

- [ ] **Step 1.4: 跑 windows 测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_scorer.py -v`
Expected: PASS（3 个 windows 用例绿）

- [ ] **Step 1.5: 写 collector 失败测试**

`scripts/tests/test_cognition_digest_collector.py`：

```python
from __future__ import annotations
import pytest
from db.connection import get_connection, get_db
from db.migrate import migrate
from services.cognition_service import CognitionService
from services.cognition_digest import collector


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "cog_digest.db"
    conn = get_connection(p)
    migrate(conn)
    conn.close()
    return str(p)


def _add_cog(svc, title, *, category="signal", status="candidate"):
    return svc.add_cognition(
        category=category, title=title, description="desc " + title,
        status=status, input_by="manual",
    )["cognition_id"]


def _seed_teachers(db_path, teachers):
    """teacher_id 有 FK → teachers(id)，且 PRAGMA foreign_keys=1，写实例前必须先建老师行。"""
    with get_db(db_path) as conn:
        migrate(conn)
        for tid, name in teachers:
            conn.execute("INSERT OR IGNORE INTO teachers (id, name) VALUES (?, ?)",
                         (tid, name))


def _backdate_created(db_path, cid, date="2026-01-01 00:00:00"):
    """把 created_at 退到窗口外，隔离 collector「created_at∈窗口 纳新」分支对 wall-clock 的依赖。"""
    with get_db(db_path) as conn:
        conn.execute("UPDATE trading_cognitions SET created_at=? WHERE cognition_id=?",
                     (date, cid))


def test_collect_groups_instances_in_window(db_path):
    svc = CognitionService(db_path)
    _seed_teachers(db_path, [(1, "沈纯"), (2, "李四")])  # 满足 teacher_id FK
    cid = _add_cog(svc, "认知A")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-01",
                     source_type="teacher_note", teacher_id=1,
                     teacher_name_snapshot="沈纯", input_by="manual")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="daily_review", teacher_id=2,
                     teacher_name_snapshot="李四", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.total_instances == 2
    assert len(data.activities) == 1
    act = data.activities[0]
    assert act.cognition_id == cid
    assert len(act.instances) == 2
    assert set(data.teacher_names) == {"沈纯", "李四"}


def test_collect_excludes_out_of_window(db_path):
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知B")
    _backdate_created(db_path, cid)  # created_at 退出窗口，确保仅靠"窗口外实例"判定排除
    svc.add_instance(cognition_id=cid, observed_date="2026-05-20",
                     source_type="teacher_note", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.activities == []
    assert data.total_instances == 0


def test_collect_excludes_deprecated(db_path):
    # 弃用认知排除出 activities，其实例也不计入 total_instances（口径与"活跃认知"一致）
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知C")
    svc.add_instance(cognition_id=cid, observed_date="2026-06-01",
                     source_type="teacher_note", input_by="manual")
    svc.deprecate_cognition(cognition_id=cid, reason="过期", input_by="manual")
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert all(a.cognition_id != cid for a in data.activities)
    assert data.total_instances == 0


def test_collect_window_endpoints_inclusive(db_path):
    # observed_date 恰好等于 start / end 都应纳入（闭区间端点）
    svc = CognitionService(db_path)
    cid = _add_cog(svc, "认知D")
    svc.add_instance(cognition_id=cid, observed_date="2026-05-31",
                     source_type="teacher_note", input_by="manual")  # == start
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="daily_review", input_by="manual")  # == end
    data = collector.collect(db_path, "2026-05-31", "2026-06-02")
    assert data.total_instances == 2


def test_collect_is_readonly(db_path):
    # 严重 1 回归：collect 不得写真实库（不 migrate、不 commit、不动 user_version）
    import sqlite3
    before = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    collector.collect(db_path, "2026-05-31", "2026-06-02")
    after = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    assert before == after


def test_ro_connect_rejects_writes(db_path):
    # 强化只读门禁：直接证明 _ro_connect 的连接拒绝任何写（比 user_version 比对更强，codex 中项）
    import sqlite3
    conn = collector._ro_connect(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE trading_cognitions SET title = 'x'")
    finally:
        conn.close()
```

- [ ] **Step 1.6: 跑 collector 测试确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_collector.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'collect'`）

> 若 `deprecate_cognition` 参数名与上不符，先 `grep -n "def deprecate_cognition" scripts/services/cognition_service.py` 核对真实签名再调整测试调用（**以真实签名为准**）。

- [ ] **Step 1.7: 实现 collector.py**

`scripts/services/cognition_digest/collector.py`：

```python
"""只读聚合：窗口内认知活跃度 + 概览统计。

只读连接（SQLite mode=ro，URI）：**不调 migrate、不 commit**，保证生产路径对真实
data/trade.db 严格只读（codex 严重 1 修订）。db_path=None → 默认库。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from db.connection import _DEFAULT_DB_PATH


@dataclass
class CognitionActivity:
    cognition_id: str
    title: str
    category: str
    sub_category: str | None
    pattern: str | None
    confidence: float
    status: str
    created_at: str
    instances: list[dict] = field(default_factory=list)  # {observed_date, teacher_id, teacher_name}


@dataclass
class WindowData:
    activities: list[CognitionActivity]
    total_instances: int       # 只数 activities（非弃用认知）的窗口实例，与"活跃认知"口径一致
    teacher_names: list[str]


def _ro_connect(db_path: str | None) -> sqlite3.Connection:
    """只读连接（mode=ro）。生产路径绝不写库、不 migrate；DB 必须已存在。"""
    path = str(db_path or _DEFAULT_DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def collect(db_path: str | None, start: str, end: str) -> WindowData:
    """聚合 [start, end] 闭区间内的认知活跃度（只读）。"""
    conn = _ro_connect(db_path)
    try:
        inst_rows = conn.execute(
            """
            SELECT cognition_id, observed_date, teacher_id, teacher_name_snapshot
            FROM cognition_instances
            WHERE observed_date >= ? AND observed_date <= ?
            """,
            (start, end),
        ).fetchall()
        new_rows = conn.execute(
            """
            SELECT cognition_id FROM trading_cognitions
            WHERE date(created_at) >= ? AND date(created_at) <= ?
              AND status != 'deprecated'
            """,
            (start, end),
        ).fetchall()

        by_cog: dict[str, list[dict]] = {}
        for r in inst_rows:
            by_cog.setdefault(r["cognition_id"], []).append(
                {
                    "observed_date": r["observed_date"],
                    "teacher_id": r["teacher_id"],
                    "teacher_name": r["teacher_name_snapshot"],
                }
            )

        cand_ids = set(by_cog) | {r["cognition_id"] for r in new_rows}
        if not cand_ids:
            return WindowData([], 0, [])

        placeholders = ",".join("?" * len(cand_ids))
        meta_rows = conn.execute(
            f"""
            SELECT cognition_id, title, category, sub_category, pattern,
                   confidence, status, created_at
            FROM trading_cognitions
            WHERE cognition_id IN ({placeholders})
              AND status != 'deprecated'
            """,
            tuple(cand_ids),
        ).fetchall()
    finally:
        conn.close()

    activities = [
        CognitionActivity(
            cognition_id=m["cognition_id"],
            title=m["title"],
            category=m["category"],
            sub_category=m["sub_category"],
            pattern=m["pattern"],
            confidence=float(m["confidence"]),
            status=m["status"],
            created_at=m["created_at"],
            instances=by_cog.get(m["cognition_id"], []),
        )
        for m in meta_rows
    ]
    # 概览口径：只统计非弃用（activities）认知的窗口实例 + 这些实例覆盖的老师（与 active 口径一致）
    total_instances = sum(len(a.instances) for a in activities)
    teacher_names = sorted(
        {it["teacher_name"] for a in activities for it in a.instances if it["teacher_name"]}
    )
    return WindowData(activities, total_instances, teacher_names)
```

- [ ] **Step 1.8: 跑 collector 测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_collector.py -v`
Expected: PASS（3 个 collector 用例绿）

- [ ] **Step 1.9: 写 scorer 失败测试**（追加到 `test_cognition_digest_scorer.py`）

```python
from services.cognition_digest.collector import CognitionActivity
from services.cognition_digest import scorer


def _act(cid, *, instances, confidence=0.5, created_at="2026-01-01 00:00:00", category="signal"):
    return CognitionActivity(
        cognition_id=cid, title=f"T{cid}", category=category, sub_category=None,
        pattern="p", confidence=confidence, status="candidate",
        created_at=created_at, instances=instances,
    )


def _inst(date, tid):
    return {"observed_date": date, "teacher_id": tid, "teacher_name": None}


def test_score_heat_and_consensus():
    a = _act("c1", instances=[_inst("2026-06-01", 1), _inst("2026-06-02", 2)])
    out = scorer.score_activities([a], anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert out[0].heat == 2
    assert out[0].consensus == 2  # 两个不同 teacher_id


def test_score_is_new_bonus_ranks_higher():
    old = _act("old", instances=[_inst("2026-06-01", 1)], created_at="2026-01-01 00:00:00")
    new = _act("new", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 09:00:00")
    out = scorer.score_activities([old, new], anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert out[0].cognition_id == "new"  # is_new 加分使其靠前
    assert out[0].is_new is True


def test_score_top_n_truncates():
    acts = [_act(f"c{i}", instances=[_inst("2026-06-01", 1)]) for i in range(8)]
    out = scorer.score_activities(acts, anchor="2026-06-02", start="2026-05-31",
                                  lookback_days=3, top_n=5)
    assert len(out) == 5


def test_score_consensus_by_name_fallback():
    # teacher_id 为 None 时用 teacher_name 去重（轻微项回归）
    insts = [
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "沈纯"},
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "李四"},
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "沈纯"},
    ]
    out = scorer.score_activities([_act("c1", instances=insts)], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    assert out[0].consensus == 2  # 沈纯 / 李四 去重


def test_score_consensus_mixed_id_and_name_no_double_count():
    # 同一老师既有 id 实例又有仅 name 实例 → 按 name 归并，不重复计数（codex 中项回归）
    insts = [
        {"observed_date": "2026-06-01", "teacher_id": 3, "teacher_name": "沈纯"},
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "沈纯"},  # 同一老师
        {"observed_date": "2026-06-01", "teacher_id": None, "teacher_name": "李四"},  # 另一老师
    ]
    out = scorer.score_activities([_act("c1", instances=insts)], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    assert out[0].consensus == 2  # 沈纯(id=3) + 李四(name) = 2，不是 3


def test_score_is_new_boundary_equal_start_and_anchor():
    # created_at 日期恰好 == start 或 == anchor 都算 is_new（闭区间端点）
    at_start = _act("s", instances=[_inst("2026-06-01", 1)], created_at="2026-05-31 00:00:00")
    at_anchor = _act("a", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 23:59:00")
    out = scorer.score_activities([at_start, at_anchor], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    assert all(s.is_new for s in out)


def test_score_tiebreak_by_created_at():
    # 同 score/heat/consensus 时，created_at 更新者稳定靠前（codex 中项回归）
    older = _act("old", instances=[_inst("2026-06-01", 1)], created_at="2026-06-01 09:00:00")
    newer = _act("new", instances=[_inst("2026-06-01", 1)], created_at="2026-06-02 09:00:00")
    out = scorer.score_activities([older, newer], anchor="2026-06-02",
                                  start="2026-05-31", lookback_days=3, top_n=5)
    # 两者 heat/consensus/is_new/recency 全同 → 仅 created_at 决定顺序
    assert [s.cognition_id for s in out] == ["new", "old"]
```

- [ ] **Step 1.10: 跑 scorer 测试确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_scorer.py -k score -v`
Expected: FAIL（`module ... has no attribute 'score_activities'`）

- [ ] **Step 1.11: 实现 scorer.py**

`scripts/services/cognition_digest/scorer.py`：

```python
"""热度+共识+新增 打分排序（无验证信号场景）。常量化魔法数，单测锁定。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

HEAT_W = 1.0       # 实例条数权重（被反复印证强度）
CONSENSUS_W = 0.8  # distinct 老师数权重（多老师共识）
CONF_W = 0.5       # confidence 权重
NEW_BONUS = 0.6    # 本期新捕获加分
RECENCY_W = 0.4    # 时近衰减权重
DECAY_FLOOR = 0.2  # recency 衰减地板


@dataclass
class ScoredCognition:
    cognition_id: str
    title: str
    category: str
    sub_category: str | None
    pattern: str | None
    confidence: float
    heat: int
    consensus: int
    is_new: bool
    score: float
    created_at: str = ""  # 仅用于排序并列兜底，默认空（renderer 不展示）


def _distinct_teachers(instances: list[dict]) -> int:
    # 同一老师可能既有 teacher_id 实例、又有仅 teacher_name 的历史实例 → 必须按 name 归并，
    # 避免重复计数抬高 consensus（codex 中项：成功但产出错数据）。
    id_names: dict = {}     # teacher_id -> 该 id 实例携带的 name（可能为 None）
    name_only: set = set()  # 仅有 name、无 id 的实例
    for it in instances:
        tid = it.get("teacher_id")
        name = it.get("teacher_name")
        if tid is not None:
            id_names[tid] = name
        elif name:
            name_only.add(name)
    # 排除已被某个 id 实例同名覆盖的 name-only 老师
    covered = {n for n in id_names.values() if n}
    return len(id_names) + len(name_only - covered)


def _recency_decay(instances: list[dict], anchor: str, lookback_days: int) -> float:
    if not instances:
        return 0.0
    anchor_d = datetime.date.fromisoformat(anchor)
    latest = max(datetime.date.fromisoformat(it["observed_date"]) for it in instances)
    days_since = (anchor_d - latest).days
    return max(DECAY_FLOOR, 1.0 - days_since / lookback_days)


def score_activities(activities, *, anchor: str, start: str,
                     lookback_days: int, top_n: int) -> list[ScoredCognition]:
    scored: list[ScoredCognition] = []
    for a in activities:
        heat = len(a.instances)
        consensus = _distinct_teachers(a.instances)
        is_new = start <= a.created_at[:10] <= anchor
        recency = _recency_decay(a.instances, anchor, lookback_days)
        score = (
            HEAT_W * heat
            + CONSENSUS_W * consensus
            + CONF_W * a.confidence
            + NEW_BONUS * (1 if is_new else 0)
            + RECENCY_W * recency
        )
        scored.append(
            ScoredCognition(
                cognition_id=a.cognition_id, title=a.title, category=a.category,
                sub_category=a.sub_category, pattern=a.pattern, confidence=a.confidence,
                heat=heat, consensus=consensus, is_new=is_new, score=round(score, 4),
                created_at=a.created_at,
            )
        )
    # 并列兜底：score → heat → consensus → created_at（更新者靠前），全 reverse
    scored.sort(key=lambda s: (s.score, s.heat, s.consensus, s.created_at), reverse=True)
    return scored[:top_n]
```

- [ ] **Step 1.12: 跑 G1 全部测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_collector.py tests/test_cognition_digest_scorer.py -v`
Expected: PASS（windows 3 + collector 6 + scorer 7 = 16 绿）

- [ ] **Step 1.13: 提交 G1**

```bash
cd /Users/alyx/tradeSystem
git add scripts/services/cognition_digest/__init__.py \
        scripts/services/cognition_digest/windows.py \
        scripts/services/cognition_digest/collector.py \
        scripts/services/cognition_digest/scorer.py \
        scripts/tests/test_cognition_digest_collector.py \
        scripts/tests/test_cognition_digest_scorer.py
git commit -m "feat(cognition-digest): G1 窗口+采集+打分

windows 三窗口闭区间换算；collector 只读连接（mode=ro，不 migrate/不 commit）聚合
（窗口实例分组 + deprecated 排除 + created_at∈窗口 纳新，total/老师口径只数 activities）；
scorer 热度+共识+新增 5 因子常量化打分排序（created_at 第四兜底键，混合 id/name 共识归并）。
16 个 TDD 用例（windows 3 / collector 6 / scorer 7），tmp_path 真库 + CognitionService seed。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 1.14: 阶段 review 双门**

跑 subagent 审查（Explore，readonly）+ `codex:codex-rescue`（前台）审 G1 三模块 diff。满足 `subagent-code-review.md` 4 条 + `post-dev-codex-review.md` 6 条结束条件后再进 Task 2。

---

## Task 2（G2）：叙事（红线护栏）+ 渲染

**Files:**
- Create: `scripts/services/cognition_digest/narrator.py`, `renderer.py`
- Test: `scripts/tests/test_cognition_digest_narrator.py`, `test_cognition_digest_renderer.py`

- [ ] **Step 2.1: 写 narrator 失败测试**

`scripts/tests/test_cognition_digest_narrator.py`：

```python
from __future__ import annotations
from services.cognition_digest import narrator
from services.cognition_digest.scorer import ScoredCognition


def _sc(cid="c1", *, category="signal", heat=3, consensus=2, is_new=True):
    return ScoredCognition(
        cognition_id=cid, title=f"认知{cid}", category=category, sub_category=None,
        pattern="范式", confidence=0.6, heat=heat, consensus=consensus,
        is_new=is_new, score=1.0,
    )


def test_no_llm_falls_back_to_template():
    out = narrator.generate_suggestions([_sc()], no_llm=True, llm_runner=None)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板非空
    assert out["direction_suggestions"]


def test_llm_clean_output_accepted():
    def runner(prompt, payload):
        return {"system_suggestions": ["加强认知验证机制"],
                "direction_suggestions": ["聚焦高共识方向"]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is True
    assert "加强认知验证机制" in out["system_suggestions"]


def test_llm_redline_bullet_dropped():
    def runner(prompt, payload):
        return {"system_suggestions": ["建议买入算力龙头", "完善复盘节奏"],
                "direction_suggestions": ["设置止损位 12 元"]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    # "买入" / "止损位" 命中红线被丢；system 保留 1 条，direction 全空 → 模板兜底
    assert "建议买入算力龙头" not in out["system_suggestions"]
    assert "完善复盘节奏" in out["system_suggestions"]
    assert all("止损位" not in b for b in out["direction_suggestions"])
    assert out["direction_suggestions"]  # 空段走模板兜底


def test_llm_exception_falls_back():
    def runner(prompt, payload):
        raise RuntimeError("boom")
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板兜底


def test_llm_bad_type_falls_back():
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=lambda p, d: None)
    assert out["_llm_used"] is False


def test_llm_missing_key_falls_back():
    # 缺键 / 非 list → L1 结构不符，整段模板兜底（不部分采纳，codex 中项回归）
    def runner(prompt, payload):
        return {"system_suggestions": "不是列表"}  # 缺 direction_suggestions 且类型错
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["_llm_used"] is False
    assert out["system_suggestions"]  # 模板兜底非空


def test_llm_non_string_bullets_dropped():
    # list 内含非字符串元素 → 逐条丢弃，不渲染 "None"/dict 串（codex 中项回归）
    def runner(prompt, payload):
        return {"system_suggestions": [None, "完善复盘节奏", {"x": 1}],
                "direction_suggestions": [123]}
    out = narrator.generate_suggestions([_sc()], no_llm=False, llm_runner=runner)
    assert out["system_suggestions"] == ["完善复盘节奏"]  # 非字符串全丢
    assert "None" not in "".join(out["system_suggestions"])
    assert out["direction_suggestions"]  # direction 全非字符串 → 清空 → 模板兜底
```

- [ ] **Step 2.2: 跑确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_narrator.py -v`
Expected: FAIL（`module ... has no attribute 'generate_suggestions'`）

- [ ] **Step 2.3: 实现 narrator.py**

`scripts/services/cognition_digest/narrator.py`：

```python
"""gemini 建议合成 + 红线三级护栏。复用 build_gemini_runner 与 REDLINE_KEYWORDS。

红线只扫 LLM 生成内容，不扫认知事实（对齐红线约束『生成』非『事实』）。
L1 调用/解析失败 → 模板兜底；L2 逐条 bullet 命中红线丢弃、空段兜底；L3 no_llm 纯结构化。
"""
from __future__ import annotations

import logging
from collections import Counter

from services.recommend.formatter import REDLINE_KEYWORDS  # 红线词单一真源

logger = logging.getLogger(__name__)


def _scan_redline(text: str) -> str | None:
    for kw in REDLINE_KEYWORDS:
        if kw in (text or ""):
            return kw
    return None


def _build_prompt() -> str:
    return (
        "你是交易认知复盘助理。下面 JSON 数组每条是一条**真实**沉淀的交易认知"
        "（title 标题 / category 分类 / pattern 复用范式 / heat 近期印证次数 / "
        "consensus 老师共识数 / is_new 是否本期新捕获）。"
        "请基于这些认知，输出两类**体系级**建议，返回严格 JSON："
        '{"system_suggestions": ["...","..."], "direction_suggestions": ["...","..."]}。'
        "system_suggestions：对交易体系/框架的建议（认知结构、验证机制、复盘节奏等），2-3 条，每条≤40 字；"
        "direction_suggestions：下一步研究/跟踪方向（聚焦哪类认知、补哪类验证），2-3 条，每条≤40 字。"
        "**严禁出现具体标的买卖点、价格目标、仓位操作词（买入/卖出/加仓/满仓/建仓/止损位/空仓/必涨）；"
        "只到体系与方向层，不做个股操作建议。严禁臆造输入中未出现的认知或事实。**"
    )


def _payload(scored) -> list[dict]:
    return [
        {"title": s.title, "category": s.category, "pattern": s.pattern or "",
         "heat": s.heat, "consensus": s.consensus, "is_new": s.is_new}
        for s in scored
    ]


def _clean_bullets(raw) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        # 只接受字符串；LLM 可能返 [None] / [{...}] 等，str(item) 会渲染出 "None"/dict 串
        # 且绕过红线护栏（codex 中项）→ 非字符串直接丢弃
        if not isinstance(item, str):
            logger.warning("[cognition-digest] 建议条目非字符串(%s)，丢弃", type(item).__name__)
            continue
        text = item.strip()
        if not text:
            continue
        hit = _scan_redline(text)
        if hit:
            logger.warning("[cognition-digest] 建议命中红线 '%s'，丢弃该条", hit)
            continue
        out.append(text)
    return out


def _template_suggestions(scored) -> dict:
    if not scored:
        return {"system_suggestions": [], "direction_suggestions": [], "_llm_used": False}
    cat_heat: Counter = Counter()
    for s in scored:
        cat_heat[s.category] += s.heat
    top_cat = cat_heat.most_common(1)[0][0] if cat_heat else "—"
    new_cnt = sum(1 for s in scored if s.is_new)
    return {
        "system_suggestions": [
            f"本期热度最高方向为「{top_cat}」，建议在该方向加强认知验证与复盘沉淀",
            f"本期新捕获 {new_cnt} 条候选认知，建议安排盘后验证补足事实源",
        ],
        "direction_suggestions": [
            f"优先跟踪「{top_cat}」相关认知的后续印证与失效边界",
            "对仅单老师提出（共识=1）的认知保持观察，等待跨老师共识再升级",
        ],
        "_llm_used": False,
    }


def generate_suggestions(scored, *, no_llm: bool = False, llm_runner=None) -> dict:
    """返回 {system_suggestions, direction_suggestions, _llm_used}。任何降级走模板兜底。"""
    if no_llm or llm_runner is None or not scored:
        return _template_suggestions(scored)
    try:
        result = llm_runner(_build_prompt(), _payload(scored))
    except Exception as e:  # noqa: BLE001  与 research_digest narrator 一致：任何异常全量降级
        logger.warning("[cognition-digest] narrator LLM 异常，模板兜底: %s", e)
        return _template_suggestions(scored)
    # L1 结构严格校验：必须 dict 且两键都在且都是 list；任一不符 → 整段模板兜底（不部分采纳）
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("system_suggestions"), list)
        or not isinstance(result.get("direction_suggestions"), list)
    ):
        logger.info("[cognition-digest] narrator 结构不符(L1：缺键/非列表)，模板兜底")
        return _template_suggestions(scored)

    # L2 逐条红线清洗；某段全被清空 → 该段模板兜底
    system = _clean_bullets(result["system_suggestions"])
    direction = _clean_bullets(result["direction_suggestions"])
    tmpl = _template_suggestions(scored)
    # _llm_used 为整体单标记：任一段保留了 LLM bullet 即 True。混合段（一段 LLM + 一段红线清空后
    # 走模板）不细分 per-section source —— 有意取舍：per-section 审计收益边际，徒增 renderer/契约复杂度
    # （YAGNI；codex 轻微项，defer）。如未来需要精确审计，再加 system_llm_used/direction_llm_used。
    return {
        "system_suggestions": system or tmpl["system_suggestions"],
        "direction_suggestions": direction or tmpl["direction_suggestions"],
        "_llm_used": bool(system or direction),
    }
```

- [ ] **Step 2.4: 跑 narrator 测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_narrator.py -v`
Expected: PASS（7 绿）。若 `REDLINE_KEYWORDS` import 路径报错，`grep -n "REDLINE_KEYWORDS" scripts/services/recommend/formatter.py` 核对后修正 import。

- [ ] **Step 2.5: 写 renderer 失败测试**

`scripts/tests/test_cognition_digest_renderer.py`：

```python
from __future__ import annotations
from services.cognition_digest import renderer
from services.cognition_digest.windows import resolve_window
from services.cognition_digest.scorer import ScoredCognition


def _sc(cid="c1", *, is_new=True):
    return ScoredCognition(
        cognition_id=cid, title=f"认知{cid}", category="signal", sub_category="rotation",
        pattern="高位退潮回流低位", confidence=0.6, heat=3, consensus=2,
        is_new=is_new, score=1.0,
    )


def _stats(**kw):
    base = {"active": 1, "new": 1, "instances": 3, "teachers": 2}
    base.update(kw)
    return base


def test_render_has_sections():
    spec = resolve_window("weekly")
    sug = {"system_suggestions": ["加强验证"], "direction_suggestions": ["聚焦算力"]}
    title, md = renderer.render_md(spec, "2026-05-27", "2026-06-02",
                                   [_sc()], _stats(), sug, llm_used=True)
    assert "近1周" in title
    assert "概览" in md and "活跃认知 1 条" in md
    assert "🏆 值得沉淀 Top1" in md
    assert "加强验证" in md and "聚焦算力" in md
    assert "LLM" in md


def test_render_empty_window_graceful():
    spec = resolve_window("recent3d")
    sug = {"system_suggestions": [], "direction_suggestions": []}
    title, md = renderer.render_md(spec, "2026-05-31", "2026-06-02",
                                   [], _stats(active=0, new=0, instances=0, teachers=0),
                                   sug, llm_used=False)
    assert "无新增认知沉淀" in md
    assert "🏆" not in md


def test_render_no_llm_marker():
    spec = resolve_window("monthly")
    sug = {"system_suggestions": ["x"], "direction_suggestions": []}
    _, md = renderer.render_md(spec, "2026-05-04", "2026-06-02",
                               [_sc()], _stats(), sug, llm_used=False)
    assert "纯结构化" in md


def test_render_missing_stats_key_no_crash():
    # stats 缺键不应 KeyError（codex 中项回归）；缺键按 0 渲染
    spec = resolve_window("weekly")
    sug = {"system_suggestions": [], "direction_suggestions": []}
    title, md = renderer.render_md(spec, "2026-05-27", "2026-06-02",
                                   [], {}, sug, llm_used=False)  # 空 stats
    assert "活跃认知 0 条" in md
```

- [ ] **Step 2.6: 跑确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_renderer.py -v`
Expected: FAIL（`module ... has no attribute 'render_md'`）

- [ ] **Step 2.7: 实现 renderer.py**

`scripts/services/cognition_digest/renderer.py`：

```python
"""渲染钉钉 Markdown：概览 + Top-N + 体系/方向建议 + 页脚。"""
from __future__ import annotations

from .windows import WindowSpec


def render_md(spec: WindowSpec, start: str, end: str, scored, stats: dict,
              suggestions: dict, *, llm_used: bool) -> tuple[str, str]:
    title = f"📚 交易认知沉淀·{spec.label}（{start}~{end}）"
    lines = [f"## {title}", ""]
    # stats 用 .get 兜底：上游漏键不应让整条日报渲染崩（codex 中项：测试永远绿但生产静默炸）
    lines.append(
        f"**概览**：活跃认知 {stats.get('active', 0)} 条｜新增 {stats.get('new', 0)}｜"
        f"实例 {stats.get('instances', 0)}｜覆盖老师 {stats.get('teachers', 0)} 位"
    )
    lines.append("")

    if scored:
        lines.append(f"### 🏆 值得沉淀 Top{len(scored)}")
        for i, s in enumerate(scored, 1):
            badge = "🆕" if s.is_new else f"置信{s.confidence:.2f}"
            cat = s.category + (f"·{s.sub_category}" if s.sub_category else "")
            lines.append(f"**{i}. {s.title}**")
            lines.append(f"`{cat}` 🔥{s.heat} 🤝{s.consensus}位 {badge}")
            if s.pattern:
                lines.append(f"> {s.pattern}")
            lines.append("")
    else:
        lines.append("_本窗口无新增认知沉淀。_")
        lines.append("")

    sys_s = suggestions.get("system_suggestions") or []
    dir_s = suggestions.get("direction_suggestions") or []
    if sys_s or dir_s:
        tag = "LLM" if llm_used else "纯结构化"
        lines.append(f"### 🤖 体系与方向建议（{tag}）")
        if sys_s:
            lines.append("**交易体系建议**")
            lines += [f"- {b}" for b in sys_s]
        if dir_s:
            lines.append("**下一步方向建议**")
            lines += [f"- {b}" for b in dir_s]
        lines.append("")

    footer = f"———\n数据源 trade.db ｜ {end} 生成"
    if not llm_used:
        footer += " ｜ 纯结构化"
    lines.append(footer)
    return title, "\n".join(lines)
```

- [ ] **Step 2.8: 跑 renderer 测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_renderer.py -v`
Expected: PASS（4 绿）

- [ ] **Step 2.9: 提交 G2**

```bash
cd /Users/alyx/tradeSystem
git add scripts/services/cognition_digest/narrator.py \
        scripts/services/cognition_digest/renderer.py \
        scripts/tests/test_cognition_digest_narrator.py \
        scripts/tests/test_cognition_digest_renderer.py
git commit -m "feat(cognition-digest): G2 叙事红线护栏 + 渲染

narrator 复用 build_gemini_runner + REDLINE_KEYWORDS：L1 调用异常/结构不符(缺键/非list)整段
模板兜底、L2 逐条 bullet 红线丢弃+空段兜底、L3 no_llm 纯结构化；renderer 出概览/Top-N/建议/页脚 MD，
空窗口优雅降级 + no-llm 标注。11 个 TDD 用例（narrator 7 / renderer 4）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 2.10: 阶段 review 双门**（同 Task 1.14）

---

## Task 3（G3）：编排 service + CLI + main.py + smoke

**Files:**
- Create: `scripts/services/cognition_digest/service.py`, `scripts/cli/cognition_digest.py`
- Modify: `scripts/services/cognition_digest/__init__.py`, `scripts/main.py`, `scripts/tests/test_cli_smoke.py`
- Test: `scripts/tests/test_cognition_digest_service.py`

- [ ] **Step 3.1: 写 service 失败测试**

`scripts/tests/test_cognition_digest_service.py`：

```python
from __future__ import annotations
import pytest
from db.connection import get_connection, get_db
from db.migrate import migrate
from services.cognition_service import CognitionService
from services.cognition_digest import run_window_digest, RenderedCognitionDigest


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "svc.db"
    conn = get_connection(p)
    migrate(conn)
    conn.close()
    return str(p)


def _seed_teachers(db_path, teachers):
    """teacher_id 有 FK → teachers(id)，写带 teacher_id 的实例前须先建老师行。"""
    with get_db(db_path) as conn:
        migrate(conn)
        for tid, name in teachers:
            conn.execute("INSERT OR IGNORE INTO teachers (id, name) VALUES (?, ?)",
                         (tid, name))


def test_run_window_digest_end_to_end_no_llm(db_path):
    svc = CognitionService(db_path)
    _seed_teachers(db_path, [(1, "沈纯")])  # 满足 teacher_id FK
    cid = svc.add_cognition(category="signal", title="认知A",
                            description="d", status="candidate", input_by="manual")["cognition_id"]
    svc.add_instance(cognition_id=cid, observed_date="2026-06-02",
                     source_type="teacher_note", teacher_id=1,
                     teacher_name_snapshot="沈纯", input_by="manual")
    digest = run_window_digest(db_path, "recent3d", "2026-06-02", no_llm=True)
    assert isinstance(digest, RenderedCognitionDigest)
    assert "认知A" in digest.markdown
    assert digest.stats["active"] == 1
    assert digest.is_empty is False


def test_run_window_digest_empty(db_path):
    digest = run_window_digest(db_path, "weekly", "2026-06-02", no_llm=True)
    assert digest.is_empty is True
    assert "无新增认知沉淀" in digest.markdown
```

- [ ] **Step 3.2: 跑确认失败**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_service.py -v`
Expected: FAIL（`ImportError: cannot import name 'run_window_digest'`）

- [ ] **Step 3.3: 实现 service.py + 补全 __init__.py**

`scripts/services/cognition_digest/service.py`：

```python
"""编排：窗口换算 → 采集 → 打分 → 建议 → 渲染。纯读，不写库、不依赖 provider。"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import collector, narrator, renderer, scorer
from .windows import resolve_window, window_bounds


@dataclass
class RenderedCognitionDigest:
    title: str
    markdown: str
    ranked: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    suggestions: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        # (self.stats or {}) 兜底：stats 显式传 None 时不抛 AttributeError（codex 轻微项）
        return not self.ranked and (self.stats or {}).get("instances", 0) == 0


def run_window_digest(db_path, window: str, anchor_date: str, *,
                      no_llm: bool = False, llm_runner=None) -> RenderedCognitionDigest:
    spec = resolve_window(window)
    start, end = window_bounds(spec, anchor_date)
    data = collector.collect(db_path, start, end)
    scored = scorer.score_activities(
        data.activities, anchor=end, start=start,
        lookback_days=spec.lookback_days, top_n=spec.top_n,
    )
    stats = {
        "active": len(data.activities),
        "new": sum(1 for a in data.activities if start <= a.created_at[:10] <= end),
        "instances": data.total_instances,
        "teachers": len(data.teacher_names),
    }
    suggestions = narrator.generate_suggestions(scored, no_llm=no_llm, llm_runner=llm_runner)
    title, markdown = renderer.render_md(
        spec, start, end, scored, stats, suggestions,
        llm_used=suggestions.get("_llm_used", False),
    )
    return RenderedCognitionDigest(title, markdown, scored, stats, suggestions)
```

补全 `scripts/services/cognition_digest/__init__.py`（替换 Task 1 的最小版）：

```python
"""交易认知沉淀定时汇总（只读）：近3日/近1周/近1月 → 钉钉。"""
from __future__ import annotations

from .service import RenderedCognitionDigest, run_window_digest
from .windows import WINDOWS

__all__ = ["run_window_digest", "RenderedCognitionDigest", "WINDOWS"]
```

- [ ] **Step 3.4: 跑 service 测试确认通过**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_service.py -v`
Expected: PASS（2 绿）

- [ ] **Step 3.5: 实现 CLI**

`scripts/cli/cognition_digest.py`：

```python
"""CLI: 交易认知沉淀定时汇总（近3日/近1周/近1月 → 钉钉）。

  cognition-digest recent3d|weekly|monthly [--date YYYY-MM-DD] [--dry-run] [--no-llm]

- 默认推送钉钉（对齐 research-digest 语义，无 --push）。
- --dry-run：仅打印 markdown，不调 gemini、不推送。
- --no-llm：关闭 LLM 叙事，纯结构化建议。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

logger = logging.getLogger(__name__)

WINDOW_CHOICES = ("recent3d", "weekly", "monthly")


def _iso_date(s: str) -> str:
    """argparse type 校验：--date 必须 YYYY-MM-DD，否则给 argparse 风格报错 + exit 2（codex 中项）。"""
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--date 需为 YYYY-MM-DD 格式，收到: {s!r}")
    return s


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("cognition-digest", help="交易认知沉淀定时汇总（近3日/周/月 → 钉钉）")
    sub = p.add_subparsers(dest="cognition_digest_window")
    for win in WINDOW_CHOICES:
        sp = sub.add_parser(win, help=f"{win} 窗口认知沉淀汇总")
        sp.add_argument("--date", default=None, type=_iso_date, help="anchor 日期 YYYY-MM-DD（默认今天）")
        sp.add_argument("--dry-run", action="store_true", help="仅打印 markdown，不推送")
        sp.add_argument("--no-llm", action="store_true", help="关闭 LLM，纯结构化建议")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    win = getattr(args, "cognition_digest_window", None)
    if win not in WINDOW_CHOICES:
        print("用法：python main.py cognition-digest recent3d|weekly|monthly "
              "[--date YYYY-MM-DD] [--dry-run] [--no-llm]", file=sys.stderr)
        sys.exit(2)
    _run(config, args, win)


def _run(config: dict, args: argparse.Namespace, window: str) -> None:
    from services.cognition_digest import run_window_digest
    from services.research_digest.narrator import build_gemini_runner

    anchor = args.date or datetime.date.today().isoformat()
    no_llm = bool(args.no_llm or args.dry_run)
    llm_runner = None if no_llm else build_gemini_runner()

    digest = run_window_digest(None, window, anchor, no_llm=no_llm, llm_runner=llm_runner)

    if args.dry_run:
        print(digest.markdown)
        logger.info("[cognition-digest] dry-run 完成（未推送）")
        return
    # 空窗口不推送：避免安静日（尤其每日 recent3d）发"无新增认知"噪音通知（钉钉减负；消费 is_empty）
    if digest.is_empty:
        logger.info("[cognition-digest] %s 本窗口无新增认知沉淀，跳过推送", window)
        return
    _push_to_dingtalk(digest.title, digest.markdown)


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[cognition-digest] DingTalk pusher 未启用（缺 env DINGTALK_*），跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[cognition-digest] 推送 %s", "成功" if ok else "失败")
```

- [ ] **Step 3.6: 注册到 main.py**

在 `scripts/main.py` `build_parser()` 里 research-digest 注册块**之后**加（参考 `main.py:1741-1743`）：

```python
    # cognition-digest (交易认知沉淀定时汇总:近3日/周/月 → 钉钉)
    from cli.cognition_digest import register_subparser as register_cognition_digest_subparser
    register_cognition_digest_subparser(subparsers)
```

在 `main()` dispatch 里 research-digest 分支**之后**加（参考 `main.py:1797-1799`）：

```python
    elif args.command == "cognition-digest":
        from cli import cognition_digest as cognition_digest_module
        cognition_digest_module.handle_command(config, args)
```

- [ ] **Step 3.7: 加 ARCHITECTURE_COMMANDS smoke 用例**

在 `scripts/tests/test_cli_smoke.py` 的 `ARCHITECTURE_COMMANDS`（research-digest 块后，约 286 行附近）加：

```python
    # cognition-digest (交易认知沉淀定时汇总:近3日/周/月 → 钉钉)
    ["cognition-digest", "recent3d"],
    ["cognition-digest", "weekly"],
    ["cognition-digest", "monthly"],
    ["cognition-digest", "weekly", "--date", "2026-05-31"],
    ["cognition-digest", "monthly", "--dry-run", "--no-llm"],
```

- [ ] **Step 3.7b: 写 CLI 行为测试（--date 校验 + 空窗口跳推）**

`scripts/tests/test_cognition_digest_cli.py`：

```python
from __future__ import annotations
import argparse
import pytest
import services.cognition_digest as cd_pkg
from services.cognition_digest import RenderedCognitionDigest
from cli import cognition_digest


def test_date_validator_rejects_bad_format():
    with pytest.raises(argparse.ArgumentTypeError):
        cognition_digest._iso_date("2026/06/02")
    assert cognition_digest._iso_date("2026-06-02") == "2026-06-02"


def _args(**kw):
    base = dict(cognition_digest_window="weekly", date=None, dry_run=False, no_llm=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_empty_window_skips_push(monkeypatch):
    empty = RenderedCognitionDigest("t", "m", [], {"instances": 0}, {})
    monkeypatch.setattr(cd_pkg, "run_window_digest", lambda *a, **k: empty)
    pushed = []
    monkeypatch.setattr(cognition_digest, "_push_to_dingtalk", lambda *a, **k: pushed.append(1))
    cognition_digest.handle_command({}, _args())
    assert pushed == []  # 空窗口 → 不推送


def test_nonempty_window_pushes(monkeypatch):
    full = RenderedCognitionDigest("t", "m", [object()], {"instances": 3}, {})
    monkeypatch.setattr(cd_pkg, "run_window_digest", lambda *a, **k: full)
    pushed = []
    monkeypatch.setattr(cognition_digest, "_push_to_dingtalk", lambda *a, **k: pushed.append(1))
    cognition_digest.handle_command({}, _args())
    assert pushed == [1]
```

> 说明：CLI `_run` 内 `from services.cognition_digest import run_window_digest` 是**调用期惰性导入**，故 monkeypatch `cd_pkg.run_window_digest`（包属性）能被拾取；`_push_to_dingtalk` 是模块级名，直接 patch。

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_cli.py -v`
Expected: PASS（3 绿）

- [ ] **Step 3.8: 跑 smoke + dispatch 验证**

Run: `cd scripts && python3 -m pytest tests/test_cli_smoke.py -k cognition-digest -v`
Expected: PASS（5 条 parse 绿）

Run: `cd /Users/alyx/tradeSystem && python3 scripts/main.py cognition-digest recent3d --dry-run`
Expected: 真实库打印 markdown（概览 + Top 段），无推送、无报错。

- [ ] **Step 3.9: 跑模块全套 + check-scripts**

Run: `cd scripts && python3 -m pytest tests/test_cognition_digest_*.py tests/test_cli_smoke.py -v`
Expected: PASS（模块 32 + smoke 全绿）
Run: `cd /Users/alyx/tradeSystem && make check-scripts`
Expected: 全绿无新增 Warning

- [ ] **Step 3.10: 提交 G3**

```bash
cd /Users/alyx/tradeSystem
git add scripts/services/cognition_digest/service.py \
        scripts/services/cognition_digest/__init__.py \
        scripts/cli/cognition_digest.py \
        scripts/main.py \
        scripts/tests/test_cognition_digest_service.py \
        scripts/tests/test_cognition_digest_cli.py \
        scripts/tests/test_cli_smoke.py
git commit -m "feat(cognition-digest): G3 编排 service + CLI + 注册

service.run_window_digest 串起 采集→打分→建议→渲染，RenderedCognitionDigest.is_empty；
顶层 CLI cognition-digest recent3d/weekly/monthly（--date 校验/--dry-run/--no-llm，dry-run 不调 gemini，
空窗口跳推送）；main.py 注册 subparser + dispatch；ARCHITECTURE_COMMANDS +5 smoke。
2 service + 3 CLI 行为用例 + 5 条 smoke；真实库 dry-run 自检通过。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 3.11: 阶段 review 双门**（同 Task 1.14；CLI/main.py 跨层改动属"实质性"，必审）

---

## Task 4（G4）：launchd 部署（3 plist + 参数化 runner）

**Files:**
- Create: `deploy/launchd/cognition-digest-runner.sh`, 3 个 plist
- Modify: `deploy/launchd/README.md`

- [ ] **Step 4.1: 写参数化 runner**

`deploy/launchd/cognition-digest-runner.sh`（严格按 `launchd-deploy.md` 5 段；window 作为 `$1` 透传）：

```bash
#!/bin/bash
# 交易认知沉淀定时汇总入口（launchd 调用）。window 作为 $1 传入（recent3d|weekly|monthly）。
# 由 com.alyx.tradesystem.cognition-digest-{recent3d,weekly,monthly}.plist 触发。
# 跑 main.py cognition-digest <window>：只读认知三表 → 热度+共识+新增 → gemini 建议 → 推钉钉。
set -e

# 1. PATH（launchd 默认不含 /opt/homebrew/bin，python/gemini 找不到）
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根（python import 解析）
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env：scripts/.env + ~/.config/tradeSystem.env（DingTalk 凭据 / GEMINI 配置）
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳前缀方便排障
echo "===== $(date '+%Y-%m-%d %H:%M:%S') cognition-digest ${1} start ====="

# 5. 凭据存在性诊断（${VAR:+set} 只判存在不打值，规避 /tmp/*.log 泄漏）
echo "[env] DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set} DINGTALK_WEBHOOK_SECRET=${DINGTALK_WEBHOOK_SECRET:+set} GEMINI_BIN=${GEMINI_BIN:+set} LLM_TIMEOUT_SECONDS=${LLM_TIMEOUT_SECONDS:+set}"

exec /usr/bin/python3 scripts/main.py cognition-digest "$@"
```

`chmod +x deploy/launchd/cognition-digest-runner.sh`

- [ ] **Step 4.2: 写 recent3d plist（每交易日 18:30）**

`deploy/launchd/com.alyx.tradesystem.cognition-digest-recent3d.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- Sleep policy: 认知沉淀错过可接受（非交易决策任务）；macOS 休眠不触发可接受，不配 pmset。 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.alyx.tradesystem.cognition-digest-recent3d</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/alyx/tradeSystem/deploy/launchd/cognition-digest-runner.sh</string>
        <string>recent3d</string>
    </array>
    <!-- 每交易日 18:30（盘后）。Weekday 1-5 各一个 dict（launchd 不支持范围语法）。 -->
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>18</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/tmp/tradesystem-cognition-digest.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/tradesystem-cognition-digest.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 4.3: 写 weekly plist（周日 20:00）**

`deploy/launchd/com.alyx.tradesystem.cognition-digest-weekly.plist`（同结构，改 Label→`...-weekly`、ProgramArguments 第二参→`weekly`、StartCalendarInterval 单 dict）：

```xml
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>0</integer>
        <key>Hour</key><integer>20</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
```
（其余 `<key>` 与 recent3d 一致：同一 `StandardOutPath`/`StandardErrorPath` 日志文件、`RunAtLoad=false`。Weekday 0=周日。）

- [ ] **Step 4.4: 写 monthly plist（每月1号 09:00）**

`deploy/launchd/com.alyx.tradesystem.cognition-digest-monthly.plist`（Label→`...-monthly`、第二参→`monthly`、StartCalendarInterval 用 Day=1）：

```xml
    <key>StartCalendarInterval</key>
    <dict>
        <key>Day</key><integer>1</integer>
        <key>Hour</key><integer>9</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
```

- [ ] **Step 4.5: plist 语法校验**

Run:
```bash
plutil -lint deploy/launchd/com.alyx.tradesystem.cognition-digest-recent3d.plist \
             deploy/launchd/com.alyx.tradesystem.cognition-digest-weekly.plist \
             deploy/launchd/com.alyx.tradesystem.cognition-digest-monthly.plist
```
Expected: 三个均 `OK`

- [ ] **Step 4.6: README 补 3 个新任务说明**

在 `deploy/launchd/README.md` 任务列表补 3 行（cognition-digest recent3d/weekly/monthly 的触发时间 + runner + 日志路径 `/tmp/tradesystem-cognition-digest.log`）。

- [ ] **Step 4.7: 提交 G4**

```bash
cd /Users/alyx/tradeSystem
chmod +x deploy/launchd/cognition-digest-runner.sh
git add deploy/launchd/cognition-digest-runner.sh \
        deploy/launchd/com.alyx.tradesystem.cognition-digest-recent3d.plist \
        deploy/launchd/com.alyx.tradesystem.cognition-digest-weekly.plist \
        deploy/launchd/com.alyx.tradesystem.cognition-digest-monthly.plist \
        deploy/launchd/README.md
git commit -m "feat(cognition-digest): G4 launchd 部署（3 plist + 参数化 runner）

参数化 runner（window 透传 \$@，5 段：PATH/cd/source env/时间戳/凭据存在性诊断）；
recent3d 每交易日 18:30 / weekly 周日 20:00 / monthly 每月1号 09:00；
合并日志 /tmp/tradesystem-cognition-digest.log，RunAtLoad=false，Sleep policy 错过可接受。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 4.8: 安装 + 真触发验证（部署动作，需用户在本机执行/授权）**

```bash
cp deploy/launchd/com.alyx.tradesystem.cognition-digest-*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-recent3d.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-weekly.plist
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-monthly.plist
rm -f /tmp/tradesystem-cognition-digest.log
launchctl start com.alyx.tradesystem.cognition-digest-weekly
sleep 5 && cat /tmp/tradesystem-cognition-digest.log
```
Expected: log 含 `[env] DINGTALK_WEBHOOK_TOKEN=set ...` + 推送成功/失败行。`set` 缺失 → 回看 `~/.config/tradeSystem.env`。

> **注**：Step 4.8 是 launchd 真机部署，可能触发真实钉钉推送 + 需写 `~/Library/LaunchAgents/`。**执行前向用户确认**是否现在部署，或先只做 `plutil -lint` + 手动 `--dry-run` 验证、把 install/start 留给用户。
>
> **回滚**（部署后如需撤下）：
> ```bash
> launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-recent3d.plist
> launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-weekly.plist
> launchctl unload ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-monthly.plist
> rm -f ~/Library/LaunchAgents/com.alyx.tradesystem.cognition-digest-*.plist
> ```
> 仓库内模块/CLI/plist 全删即彻底回滚，无残留数据（只读、未写库）。

---

## Task 5（G5）：文档同步（skills-sync 触发）

**Files:** `.agents/skills/INDEX.md`, `market-tasks/SKILL.md`, `cognition-evolution/SKILL.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 5.1: INDEX.md 加依赖行**

在 `.agents/skills/INDEX.md` 加 `cognition-digest recent3d/weekly/monthly` 行，标注：只读认知三表（`trading_cognitions`/`cognition_instances`）+ 钉钉推送，复用 gemini + REDLINE_KEYWORDS。

- [ ] **Step 5.2: market-tasks/SKILL.md 补入口**

在盘后/定时任务段补"交易认知沉淀定时推送（cognition-digest）"小节，给出 `python3 main.py cognition-digest recent3d|weekly|monthly --dry-run` 示例。

- [ ] **Step 5.3: cognition-evolution/SKILL.md 补关联**

在"切换条件"或"Web 查看入口"附近补一句：定时**只读**汇总走 `cognition-digest`（与本 skill 的手动写入闭环区分，不写库）。

- [ ] **Step 5.4: CLAUDE.md + AGENTS.md 标准命令组加行**

在两文件"标准写入语义"命令组列表加：

```
- `python3 main.py cognition-digest recent3d|weekly|monthly ...`（交易认知沉淀只读汇总：热度+共识+新增 Top-N + gemini 体系/方向建议[红线护栏] → 钉钉；3 个 per-task launchd，不写库不进 schedule）
```

- [ ] **Step 5.5: 跑文档相关测试**

Run: `cd scripts && python3 -m pytest tests/test_cli_smoke.py tests/test_agent_symlinks.py -v`
Expected: PASS（无新增 rule 文件 → symlink 测试不受影响；smoke 仍绿）

- [ ] **Step 5.6: 全套回归**

Run: `cd /Users/alyx/tradeSystem && make check-scripts`
Expected: 全绿

- [ ] **Step 5.7: 提交 G5**

```bash
cd /Users/alyx/tradeSystem
git add .agents/skills/INDEX.md \
        .agents/skills/market-tasks/SKILL.md \
        .agents/skills/cognition-evolution/SKILL.md \
        CLAUDE.md AGENTS.md
git commit -m "docs(cognition-digest): G5 文档同步（INDEX/SKILL/CLAUDE/AGENTS）

INDEX 加依赖行；market-tasks 补定时推送入口；cognition-evolution 补只读汇总关联；
CLAUDE/AGENTS 标准命令组加 cognition-digest 行。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 5.8: G5 收尾 review + 全 PR 范围整合 review**

跑 subagent + codex:codex-rescue 对整个 `feat/cognition-digest` 分支范围做最终独立审查（对齐 `post-dev-codex-review.md` 收尾整合）。

---

## 方案审查修订（双门 review 后，2026-06-02）

explore subagent + codex:codex-rescue 双门方案审查结论已并入本计划，处置如下：

| 级别 | 来源 | 问题 | 处置 |
|---|---|---|---|
| 严重 | codex | collector 调 `migrate()` + `get_db` 会 commit/ALTER/写 user_version，违反"只读" | **已修**：改 `_ro_connect`（SQLite `mode=ro` URI），不 migrate/不 commit；补 `test_collect_is_readonly` 回归 |
| 中 | codex | `deprecated_count` 用 `updated_at` 不可靠（插实例/验证都 bump，老弃用被误算"本期弃用"） | **已修**：删除"本期弃用"计数（无 `deprecated_at` 又不改 schema，删最干净）；概览不再含弃用行 |
| 中 | codex+explore | `total_instances` 含弃用认知实例，口径与"活跃认知"不一致 | **已修**：`total_instances` / `teacher_names` 改为只数 activities（非弃用）的窗口实例 |
| 中 | codex | 排序缺 `created_at` 第四兜底键（design 写了 plan 漏） | **已修**：`ScoredCognition` 加 `created_at`，排序键补 `created_at`；补 `test_score_tiebreak_by_created_at` |
| 中 | codex | narrator L1 不严：缺键/非 list 被部分采纳 | **已修**：加结构严格校验（两键都在且都是 list），不符即整段模板兜底；补 `test_llm_missing_key_falls_back` |
| 轻微 | codex+explore | 边界测试缺口 | **已修+补测**：consensus by name fallback / 窗口端点等值 / is_new 等值 / tiebreak 共 4 个用例 |
| 低 | explore | Task 4 缺回滚步骤 | **已修**：Step 4.8 补 `launchctl unload` 回滚清单 |
| — | explore | "Task 5 缺 `.agents/skills/cognition-digest/` 目录" | **反驳**：cognition-digest 是 CLI 命令，经 `market-tasks/SKILL.md` + INDEX.md 行透出即可；`test_agent_symlinks` 只校验已有 symlink 完整性，不要求每命令一个 skill 目录，无需新建 |

> 修订后用例数：windows 3 / collector 6 / scorer 7 / narrator 7 / renderer 4 / service 2 / cli 3 + smoke 5 = **37**。
>
> **G3 代码 review 修订（codex 中等 1 + 轻微 2）**：① `--date` 加 `type=_iso_date` 校验，格式错给 argparse 风格
> 报错 + exit 2（非 traceback）；② 空窗口跳过推送（消费 `is_empty`，对齐用户「钉钉减负」偏好，dry-run 仍打印）；
> ③ `is_empty` 用 `(self.stats or {})` 兜底防 None。新增 `test_cognition_digest_cli.py` 3 用例。
>
> **G2 代码 review 修订（codex 中等 2 + 轻微 1）**：① renderer `stats` 改 `.get(k,0)` 兜底防上游漏键
> KeyError 静默炸推送，补 `test_render_missing_stats_key_no_crash`；② `_clean_bullets` 只收 `isinstance str`，
> 非字符串元素（`[None]`/`[{..}]`）丢弃防绕过红线渲染脏串，补 `test_llm_non_string_bullets_dropped`；
> ③ 轻微 `_llm_used` 单标记不区分混合段 —— defer 并落 narrator.py 注释（per-section 审计 YAGNI）。
>
> **G1 代码 review 修订（codex 中等 2 条）**：① `_distinct_teachers` 按 name 归并，避免同一老师 id 实例 +
> name-only 实例被重复计数抬高 consensus，补 `test_score_consensus_mixed_id_and_name_no_double_count`；
> ② 加 `test_ro_connect_rejects_writes` 直接证明只读连接拒写（比 user_version 比对更强的只读门禁）。

> **实施期补丁（G1 Task 1 执行时由 implementer 发现，两审查均漏，因其只静态读 plan 未真跑）**：
> collector 测试 fixture 漏两处真实库约束 —— ① `cognition_instances.teacher_id` 有 FK→`teachers(id)` 且
> `PRAGMA foreign_keys=1`，写带 `teacher_id` 的实例前必须 `_seed_teachers`；② `add_cognition` 的
> `created_at` 默认 `datetime('now')`，当 today 落在测试窗口内会污染 collector「created_at∈窗口 纳新」分支，
> `test_collect_excludes_out_of_window` 须 `_backdate_created` 把创建时间退出窗口。两处均测试 fixture 修复，未动已审源码。

## 自检（writing-plans Self-Review）

- **Spec 覆盖**：① 只读模块镜像 research_digest → Task 1-3；② 热度+共识+新增打分 → Task 1（scorer）；③ LLM 红线三级护栏 → Task 2（narrator）；④ 三独立 launchd → Task 4；⑤ 不写库/不改 schema → 全程无 migrate 写、无 schema 改；⑥ 文档同步 → Task 5。无遗漏。
- **占位符**：无 TBD/TODO；每个 code step 含完整可运行代码。
- **类型一致**：`CognitionActivity`（collector）→ `ScoredCognition`（scorer）→ `render_md`/`generate_suggestions` 入参字段全程一致；`run_window_digest(db_path, window, anchor_date, *, no_llm, llm_runner)` 签名在 service / CLI / 测试三处一致；`WindowSpec.{key,lookback_days,label,top_n}` 一致。
- **已知降级**：① 窗口用日历日回溯（非交易日，已与用户确认）；② 概览不含"本期弃用"统计（无可信 `deprecated_at` 事件字段，审查后移除）。

---

## 执行选项

**1. Subagent-Driven（推荐）**：每个 Task 派新 subagent，Task 间两段式 review。
**2. Inline Execution**：本会话内按 Task 批量执行，阶段 checkpoint review。
