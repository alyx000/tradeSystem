# sector-crowding 每日板块拥挤度采集 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每交易日 21:30 采集申万 L1/L2 行业的交易拥挤度（成交额占全市场比）、斜率拥挤度（5/20/60 日涨幅）与资金流代理，落库 `sector_crowding_daily`，复盘时 `sector-crowding report` 现算历史分位与双高信号。

**Architecture:** 对齐 `sector_correlation` 四层模式（CLI → service 编排 → collector/analyzer/formatter/repo）。表内只存原始事实（close/amount/share_pct），分位与双高信号读取时从自身历史序列滚动计算。回填走「按码分片采集 → 内存按日期聚合 → 整日一次性 UPSERT」两阶段。

**Tech Stack:** Python 3 / SQLite / tushare 镜像（tushare.xyz）/ akshare 降级 / pytest / launchd。

**Spec:** `docs/superpowers/specs/2026-07-18-sector-crowding-design.md`（v2，codex 审查已处置）。

## Global Constraints

- 所有自然语言输出简体中文；不做买卖建议；`[事实]` 与 `[判断]` 区分（双高信号属 `[判断]`）。
- 派生指标（分位/双高）**不落库**，读取时计算；`sectors_json` 只存 `code/name/level/close/amount_billion/share_pct`。
- `market_total_billion` nullable；复用 volume_concentration 三段守卫常量；坏源日落 NULL 不落假值。
- L1 合成条件启用：`index_classify` `parent_code` 真机验证通过才允许 L2→L1 归并（Task 0 决定），否则 L1 标 missing 禁止合成。
- 资金流代理报告行强制「非公募持仓真值」标注，不参与双高评分。
- 回填分片单片返回 ≥2000 行视为疑似截断 → 报错不落库。
- 每个 commit 独立可过 pytest；`git add` 用具体路径，禁 `git add -A`。
- **阶段级 review 约束：每个大阶段结束 + 阶段测试通过后，立即跑门1（`/simplify` + `/code-review`）+ 门2（codex 原生 `adversarial-review --wait`），满足 code-review-gate 4 条 + post-dev-codex-review 6 条结束条件才进下一阶段；禁止攒到收尾一次性 review。**
  - 阶段 A = Task 1-3（schema/repo/analyzer）
  - 阶段 B = Task 4-7（provider 辅助/collector/formatter/service/backfill）
  - 阶段 C = Task 8-10（CLI/launchd/文档/真实库验证）

## 并行分组（7 字段）

| 字段 | G1 核心实现 | G2 部署 | G3 文档 |
|---|---|---|---|
| 角色 | 后端 + 测试（主：后端） | DevOps | 文档 |
| 执行 Agent | Claude Code 主 agent（主线 + TDD 紧密循环 + 高打断 → 按三轴与"测试降级条款 1"留主 agent） | Claude Code `generalPurpose` subagent（子任务/低打断/短） | Claude Code `generalPurpose` subagent（子任务/低打断/短；在 Task 8 CLI 定稿后启动） |
| 专项关注 | 数据正确性（守卫/截断/防双计） | 无横切 | 无横切 |
| 职责边界 | Task 0-8：schema/repo/analyzer/collector/formatter/service/backfill/CLI/smoke | Task 9：plist + runner.sh | Task 10 文档部分：INDEX.md / market-observability.md / launchd README |
| 文件范围 | `scripts/db/schema.py`、`scripts/services/sector_crowding/*`、`scripts/providers/tushare_provider.py`、`scripts/cli/sector_crowding.py`、`scripts/main.py`、`scripts/tests/test_sector_crowding.py`、`scripts/tests/test_cli_smoke.py` | `deploy/launchd/com.alyx.tradesystem.sector-crowding.plist`、`deploy/launchd/sector-crowding-runner.sh` | `.agents/skills/INDEX.md`、`.agents/skills/market-tasks/references/market-observability.md`、`deploy/launchd/README.md` |
| 禁区 | 允许改：上列文件范围；不得改：`web/`、既有 service 业务逻辑（volume_concentration 只 import 常量不改文件）、`deploy/launchd/` 既有文件；需先询问：`scripts/db/migrate.py`（本表 CREATE IF NOT EXISTS 无需迁移，若发现需要版本号则停下问） | 允许改：仅新增两个部署文件；不得改：任何 `scripts/` 代码、既有 plist；需先询问：调度时间变更 | 允许改：三个文档文件；不得改：代码、SKILL.md router 主体；需先询问：INDEX.md 既有行的语义改写 |
| 冲突标注 | `deploy/launchd/README.md` 由 G3 唯一归属（G2 不碰）；`scripts/main.py` 由 G1 唯一归属 | 同左 | 同左 |

Review 门执行体（角色型）：门1 `/simplify` + `/code-review` 本地；门2 codex 原生 `adversarial-review --wait --model gpt-5.5`（前台，禁 `--background`）。**三个阶段（含混含 G2/G3 产物的阶段 C）的 review 均由 G1 主 agent 统一执行。**

---

### Task 0: 真机验证 sw_daily L1 与 index_classify parent_code（gating spike，不提交代码）

**Files:**
- Create: `scratch/verify_sw_l1.py`（scratch/ 已 untracked，不提交）

**Interfaces:**
- Produces: 二值结论 `L1_AVAILABLE`（sw_daily 直接含 801xxx L1 行情）与 `PARENT_MAP_OK`（index_classify L2 行含可靠 parent_code → L1）。写入本计划执行记录，决定 Task 4 的 L1 路径分支。

- [ ] **Step 1: 写验证脚本**

```python
"""真机验证：镜像 sw_daily 是否含申万一级；index_classify parent_code 是否可靠。"""
import os, sys
sys.path.insert(0, "scripts")
from dotenv import load_dotenv
load_dotenv("scripts/.env")
import tushare as ts

ts.set_token(os.environ["TUSHARE_TOKEN"])
pro = ts.pro_api()

l1 = pro.index_classify(level="L1", src="SW2021")
l2 = pro.index_classify(level="L2", src="SW2021")
print(f"L1 codes: {len(l1)}")            # 期望 ~31
print(f"L2 codes: {len(l2)}")            # 期望 ~134
print("L2 列:", list(l2.columns))
has_parent = "parent_code" in l2.columns and l2["parent_code"].notna().all()
print(f"PARENT_MAP_OK={has_parent}")

d = "20260717"  # 最近交易日
daily = pro.sw_daily(trade_date=d)
l1_set = set(l1["index_code"])
hit = daily[daily["ts_code"].isin(l1_set)]
print(f"sw_daily 总行数 {len(daily)}, 其中 L1 行 {len(hit)}")
print(f"L1_AVAILABLE={len(hit) >= 28}")   # 31 个 L1 允许少量缺
print("样例:", hit.head(3).to_dict("records") if len(hit) else "无")
print("amount 单位抽检(万元→亿除1e4):", daily.iloc[0][["ts_code", "amount"]].to_dict())
```

- [ ] **Step 2: 运行**

Run: `cd /Users/alyx/tradeSystem && python3 scratch/verify_sw_l1.py`
Expected: 打印 `L1_AVAILABLE=True/False`、`PARENT_MAP_OK=True/False`。同时人工核对 amount 单位（sw_daily amount 官方口径为千元或万元，**以实测数量级校准 `AMOUNT_TO_BILLION` 除数**：对照当日全市场约 1.5-3 万亿，L1 行业总和应接近全市场）。

- [ ] **Step 3: 记录结论**

把两个布尔值与 amount 换算除数写进本文件「执行记录」小节（追加），Task 4 按此取分支：
- `L1_AVAILABLE=True` → collector 直取 L1 行，合成路径永不启用；
- `False` 且 `PARENT_MAP_OK=True` → 启用 L2→L1 归并（meta 标 `l1_synthesized`）；
- 双 False → L1 标 missing，报告只出 L2。

---

### Task 1: schema 新表 sector_crowding_daily

**Files:**
- Modify: `scripts/db/schema.py`（`_SQL_MARGIN_INDEX_CORRELATION_DAILY` 定义后、约 L536 处插入；表清单 ~L1542 区与 `EXPECTED_TABLES` ~L1604 区各加一行）
- Test: `scripts/tests/test_sector_crowding.py`（新建）

**Interfaces:**
- Produces: 表 `sector_crowding_daily(date PK, market_total_billion REAL NULL, sectors_json NOT NULL, proxy_json, meta_json, created_at, updated_at)`。

- [ ] **Step 1: 写失败测试**

```python
"""sector_crowding 全链路测试（schema/repo/analyzer/collector/service/formatter）。"""
import json
import sqlite3

import pytest

from db.schema import init_schema


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "t.db")
    c.row_factory = sqlite3.Row
    init_schema(c)
    yield c
    c.close()


class TestSchema:
    def test_sector_crowding_daily_table_exists(self, conn):
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sector_crowding_daily)")}
        assert {"date", "market_total_billion", "sectors_json", "proxy_json",
                "meta_json", "created_at", "updated_at"} <= cols

    def test_market_total_nullable(self, conn):
        conn.execute(
            "INSERT INTO sector_crowding_daily (date, sectors_json) VALUES ('2026-07-17', '[]')"
        )  # market_total_billion 缺省 NULL 不应报错
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: FAIL（`no such table: sector_crowding_daily`）

- [ ] **Step 3: 加表定义**

在 `scripts/db/schema.py` `_SQL_MARGIN_INDEX_CORRELATION_DAILY` 之后插入：

```python
# ──────────────────────────────────────────────────────────────
# 5e. 板块拥挤度（sector-crowding）：一天一行快照；只存原始事实
#     (close/amount/share_pct)，历史分位与双高信号读取时滚动计算、
#     不持久化——避免回填修正历史后落库派生值过期（spec v2 中5）。
#     market_total_billion nullable：守卫失败落 NULL 优于落假值。
# ──────────────────────────────────────────────────────────────
_SQL_SECTOR_CROWDING_DAILY = """
CREATE TABLE IF NOT EXISTS sector_crowding_daily (
    date TEXT PRIMARY KEY CHECK(date GLOB '????-??-??'),
    market_total_billion REAL,
    sectors_json TEXT NOT NULL,
    proxy_json TEXT,
    meta_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
"""
```

表清单（~L1542 `_SQL_SECTOR_CORRELATION_DAILY` 邻近）加 `_SQL_SECTOR_CROWDING_DAILY,`；`EXPECTED_TABLES` 中 `"sector_correlation_daily",` 同行区加 `"sector_crowding_daily",`。

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/db/schema.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): add sector_crowding_daily schema"
```

---

### Task 2: repo 读写层

**Files:**
- Create: `scripts/services/sector_crowding/__init__.py`（空）
- Create: `scripts/services/sector_crowding/repo.py`
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Produces:
  - `save_snapshot(conn, record: dict) -> None`：record 键 `date/market_total_billion/sectors/proxy/meta`；UPSERT ON CONFLICT(date)，保留 created_at。
  - `get_snapshot(conn, date: str) -> dict | None`：JSON 已解码（`sectors` list / `proxy` dict|None / `meta` dict|None）。
  - `get_recent(conn, end_date: str, days: int) -> list[dict]`：`date <= end_date` 最近 days 行，**升序**返回。
  - `get_latest_market_total_before(conn, date: str) -> float | None`（骤降告警用）。

- [ ] **Step 1: 写失败测试（追加到 test_sector_crowding.py）**

```python
from services.sector_crowding import repo


def _rec(date, total=15000.0, sectors=None):
    return {
        "date": date,
        "market_total_billion": total,
        "sectors": sectors if sectors is not None else [
            {"code": "801080.SI", "name": "电子", "level": "L1",
             "close": 5000.0, "amount_billion": 3000.0, "share_pct": 20.0},
        ],
        "proxy": None,
        "meta": {"source": "tushare"},
    }


class TestRepo:
    def test_save_and_get_roundtrip(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["sectors"][0]["code"] == "801080.SI"
        assert got["market_total_billion"] == 15000.0

    def test_upsert_idempotent_keeps_created_at(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17"))
        created = repo.get_snapshot(conn, "2026-07-17")["created_at"]
        repo.save_snapshot(conn, _rec("2026-07-17", total=16000.0))
        got = repo.get_snapshot(conn, "2026-07-17")
        assert got["market_total_billion"] == 16000.0
        assert got["created_at"] == created

    def test_market_total_none_persists_null(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-17", total=None))
        assert repo.get_snapshot(conn, "2026-07-17")["market_total_billion"] is None

    def test_get_recent_ascending(self, conn):
        for d in ("2026-07-15", "2026-07-16", "2026-07-17"):
            repo.save_snapshot(conn, _rec(d))
        rows = repo.get_recent(conn, "2026-07-17", days=2)
        assert [r["date"] for r in rows] == ["2026-07-16", "2026-07-17"]

    def test_missing_required_raises(self, conn):
        with pytest.raises(ValueError):
            repo.save_snapshot(conn, {"date": "2026-07-17"})  # 缺 sectors
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestRepo -v`
Expected: FAIL（`No module named 'services.sector_crowding'`）

- [ ] **Step 3: 实现 repo.py**

```python
"""sector_crowding_daily 读写 — JSON 编解码封装在此，UPSERT 幂等、保留首次 created_at。"""
from __future__ import annotations

import json
import sqlite3


def save_snapshot(conn: sqlite3.Connection, record: dict) -> None:
    if record.get("date") is None or record.get("sectors") is None:
        raise ValueError("save_snapshot: 缺少必填字段 date/sectors")
    conn.execute(
        """
        INSERT INTO sector_crowding_daily (
            date, market_total_billion, sectors_json, proxy_json, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(date) DO UPDATE SET
            market_total_billion = excluded.market_total_billion,
            sectors_json = excluded.sectors_json,
            proxy_json = excluded.proxy_json,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            record["date"],
            record.get("market_total_billion"),
            json.dumps(record["sectors"], ensure_ascii=False),
            json.dumps(record["proxy"], ensure_ascii=False) if record.get("proxy") is not None else None,
            json.dumps(record["meta"], ensure_ascii=False) if record.get("meta") is not None else None,
        ),
    )
    conn.commit()


def _row_to_record(row: sqlite3.Row) -> dict:
    def _j(col):
        return json.loads(row[col]) if row[col] else None

    return {
        "date": row["date"],
        "market_total_billion": row["market_total_billion"],
        "sectors": _j("sectors_json") or [],
        "proxy": _j("proxy_json"),
        "meta": _j("meta_json"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_snapshot(conn: sqlite3.Connection, date: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM sector_crowding_daily WHERE date = ?", (date,)
    ).fetchone()
    return _row_to_record(row) if row else None


def get_recent(conn: sqlite3.Connection, end_date: str, days: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM sector_crowding_daily WHERE date <= ? ORDER BY date DESC LIMIT ?",
        (end_date, days),
    ).fetchall()
    return [_row_to_record(r) for r in reversed(rows)]


def get_latest_market_total_before(conn: sqlite3.Connection, date: str) -> float | None:
    row = conn.execute(
        """SELECT market_total_billion FROM sector_crowding_daily
           WHERE date < ? AND market_total_billion IS NOT NULL
           ORDER BY date DESC LIMIT 1""",
        (date,),
    ).fetchone()
    return row["market_total_billion"] if row else None
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/services/sector_crowding/__init__.py scripts/services/sector_crowding/repo.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): repo layer with idempotent UPSERT"
```

---

### Task 3: analyzer 纯函数（占比/涨幅/分位/双高）

**Files:**
- Create: `scripts/services/sector_crowding/analyzer.py`
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Produces（Task 5/6 消费）:
  - 常量 `SHARE_WARN_PCT=30.0`、`SHARE_EXTREME_PCT=40.0`、`GAIN_WINDOWS=(5,20,60)`、`HIGH_PCTILE=90.0`、`MIN_PCTILE_SAMPLES=60`。
  - `compute_share_pct(amount_billion, market_total_billion) -> float | None`（分母 None/≤0 → None）。
  - `interval_gain(bars: list[tuple[str, float]], n: int, end_date: str) -> float | None`：bars 升序 `(date, close)`；**末根日期 != end_date → None**；close 非有限数/基准≤0 → None。
  - `rolling_percentile(history: list[float], current: float) -> float | None`：history 不含 current；样本（含 current）< `MIN_PCTILE_SAMPLES` → None；返回 0-100。
  - `build_view(records: list[dict], date: str) -> dict | None`：records 为 `repo.get_recent` 升序输出且末行 date 必须等于目标日（否则 None）。输出 `{"date", "market_total_billion", "sectors": [附 share_pctile/gain_5d/gain_20d/gain_60d/gain_pctile_20d], "double_high": [...], "meta"}`；**分位与涨幅按 level 内同一 code 的历史序列计算，L1/L2 互不掺混**；`double_high` 仅由 share_pctile≥90 且 gain_pctile_20d≥90 的行业构成（任一分位为 None 不入选）。

- [ ] **Step 1: 写失败测试（追加）**

```python
from services.sector_crowding import analyzer


class TestAnalyzer:
    def test_share_pct_basic_and_none_denominator(self):
        assert analyzer.compute_share_pct(3000.0, 15000.0) == 20.0
        assert analyzer.compute_share_pct(3000.0, None) is None
        assert analyzer.compute_share_pct(3000.0, 0) is None

    def test_interval_gain_requires_last_bar_on_end_date(self):
        bars = [(f"2026-07-{d:02d}", 100.0 + i) for i, d in enumerate(range(1, 12))]
        assert analyzer.interval_gain(bars, 5, "2026-07-11") == pytest.approx(
            (110.0 / 105.0 - 1) * 100, abs=0.01)
        # 末根不是目标日（陈旧数据）→ None
        assert analyzer.interval_gain(bars, 5, "2026-07-12") is None

    def test_interval_gain_insufficient_history(self):
        bars = [("2026-07-10", 100.0), ("2026-07-11", 101.0)]
        assert analyzer.interval_gain(bars, 5, "2026-07-11") is None

    def test_rolling_percentile_threshold(self):
        hist = [float(i) for i in range(59)]          # 59 + current = 60 → 达标
        assert analyzer.rolling_percentile(hist, 100.0) == 100.0
        assert analyzer.rolling_percentile(hist[:10], 100.0) is None  # 样本不足

    def _records(self, n_days=100, share=5.0, spike_last=False):
        # n_days=100:20日涨幅历史序列=80个样本 ≥ MIN_PCTILE_SAMPLES=60,分位可算
        recs = []
        for i in range(n_days):
            d = f"2026-{4 + i // 30:02d}-{i % 30 + 1:02d}"
            s = share if not (spike_last and i == n_days - 1) else 45.0
            recs.append({
                "date": d, "market_total_billion": 15000.0,
                "sectors": [{"code": "801080.SI", "name": "电子", "level": "L1",
                             "close": 100.0 + i * (3.0 if spike_last else 0.01),
                             "amount_billion": 150.0 * s, "share_pct": s}],
                "proxy": None, "meta": None,
            })
        return recs

    def test_build_view_double_high(self):
        recs = self._records(spike_last=True)
        view = analyzer.build_view(recs, recs[-1]["date"])
        sec = view["sectors"][0]
        assert sec["share_pctile"] is not None and sec["share_pctile"] >= 90
        assert [s["code"] for s in view["double_high"]] == ["801080.SI"]

    def test_build_view_rejects_date_mismatch(self):
        recs = self._records()
        assert analyzer.build_view(recs, "2026-12-31") is None

    def test_build_view_no_l1_l2_crosstalk(self):
        # 同名不同 level 的 code 分开算分位：L2 历史不足时 L2 分位为 None，但 L1 正常
        recs = self._records()
        for r in recs[-5:]:
            r["sectors"].append({"code": "801081.SI", "name": "半导体", "level": "L2",
                                 "close": 50.0, "amount_billion": 300.0, "share_pct": 2.0})
        view = analyzer.build_view(recs, recs[-1]["date"])
        l2 = [s for s in view["sectors"] if s["level"] == "L2"][0]
        assert l2["share_pctile"] is None  # 样本 5 < 60
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestAnalyzer -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 analyzer.py**

```python
"""拥挤度纯函数：占比/区间涨幅/滚动分位/双高信号。全部无 IO，历史序列由调用方传入。"""
from __future__ import annotations

import math

SHARE_WARN_PCT = 30.0      # 交易拥挤提示线
SHARE_EXTREME_PCT = 40.0   # 历史极值区（2020-21 白酒 ~42 / 本轮电子 47）
GAIN_WINDOWS = (5, 20, 60)
HIGH_PCTILE = 90.0
MIN_PCTILE_SAMPLES = 60    # 历史样本(含当日)不足 60 个交易日不出分位


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def compute_share_pct(amount_billion, market_total_billion) -> float | None:
    if not (_finite(amount_billion) and _finite(market_total_billion)) or market_total_billion <= 0:
        return None
    return round(amount_billion / market_total_billion * 100, 2)


def interval_gain(bars: list, n: int, end_date: str) -> float | None:
    """bars 升序 (date, close)。末根日期必须等于 end_date（防节假日/陈旧数据冒充当日）。

    窗口按 bar 索引回数（非交易日历距离）：假设行业指数 close 无缺失日；若历史存在
    缺 close 被跳过的日子，窗口会向前偏移（指数极少缺 close，接受该假设）。"""
    if len(bars) < n + 1 or bars[-1][0] != end_date:
        return None
    base, last = bars[-1 - n][1], bars[-1][1]
    if not (_finite(base) and _finite(last)) or base <= 0:
        return None
    gain = round((last / base - 1) * 100, 2)
    return gain if math.isfinite(gain) else None


def rolling_percentile(history: list, current) -> float | None:
    """current 在 history+current 中的分位(0-100,最大值=100)。样本不足 MIN_PCTILE_SAMPLES → None。"""
    samples = [v for v in history if _finite(v)]
    if not _finite(current) or len(samples) + 1 < MIN_PCTILE_SAMPLES:
        return None
    below = sum(1 for v in samples if v <= current) + 1  # +1 计入 current 自身
    return round(below / (len(samples) + 1) * 100, 1)


def _series_by_code(records: list[dict]) -> dict:
    """{(level, code): {"bars": [(date, close)], "shares": [float], "name": str}}。
    按 (level, code) 键隔离，L1/L2 永不掺混（spec 事故级用例）。"""
    out: dict = {}
    for rec in records:
        for s in rec.get("sectors") or []:
            key = (s.get("level"), s.get("code"))
            ent = out.setdefault(key, {"bars": [], "shares": [], "name": s.get("name")})
            if _finite(s.get("close")):
                ent["bars"].append((rec["date"], s["close"]))
            if _finite(s.get("share_pct")):
                ent["shares"].append(s["share_pct"])
    return out


def build_view(records: list[dict], date: str) -> dict | None:
    """从升序历史快照现算当日视图（分位/涨幅/双高）。末行必须是目标日。"""
    if not records or records[-1]["date"] != date:
        return None
    today = records[-1]
    series = _series_by_code(records)
    sectors, double_high = [], []
    for s in today.get("sectors") or []:
        key = (s.get("level"), s.get("code"))
        ent = series.get(key, {"bars": [], "shares": []})
        row = dict(s)
        # 分位的 history 剔除当日值（shares 末元素即当日，若当日有值）
        hist_shares = ent["shares"][:-1] if _finite(s.get("share_pct")) else ent["shares"]
        row["share_pctile"] = rolling_percentile(hist_shares, s.get("share_pct"))
        for n in GAIN_WINDOWS:
            row[f"gain_{n}d"] = interval_gain(ent["bars"], n, date)
        gain_hist = _gain_history(ent["bars"], 20)
        row["gain_pctile_20d"] = rolling_percentile(gain_hist[:-1] if gain_hist else [],
                                                    row["gain_20d"])
        sectors.append(row)
        if (row["share_pctile"] is not None and row["share_pctile"] >= HIGH_PCTILE
                and row["gain_pctile_20d"] is not None and row["gain_pctile_20d"] >= HIGH_PCTILE):
            double_high.append(row)
    return {
        "date": date,
        "market_total_billion": today.get("market_total_billion"),
        "sectors": sectors,
        "double_high": double_high,
        "meta": today.get("meta"),
    }


def _gain_history(bars: list, n: int) -> list:
    """整段历史上每个可计算日的 n 日涨幅序列（含末日），供涨幅分位。"""
    out = []
    for i in range(n, len(bars)):
        base, last = bars[i - n][1], bars[i][1]
        if _finite(base) and _finite(last) and base > 0:
            g = (last / base - 1) * 100
            if math.isfinite(g):
                out.append(round(g, 2))
    return out
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/services/sector_crowding/analyzer.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): analyzer pure functions (share/gain/percentile/double-high)"
```

> **阶段 A 完成门：** `python3 -m pytest scripts/tests/test_sector_crowding.py scripts/tests/test_cli_smoke.py -v` 全绿后，立即跑门1（`/simplify` → 重跑 pytest → `/code-review`）+ 门2（codex `adversarial-review --wait`），两门结束条件满足才进 Task 4。

---

### Task 4: provider L1 辅助 + collector 采集层

**Files:**
- Modify: `scripts/providers/tushare_provider.py`（`_ensure_sw_l2_codes` ~L920 附近追加两个私有辅助）
- Create: `scripts/services/sector_crowding/collector.py`
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Consumes: `provider.pro.sw_daily(trade_date=YYYYMMDD)`、`provider._ensure_sw_l1_codes()`（新增）、`provider._ensure_sw_l1_parent_map()`（新增）、`registry.call("get_market_volume"/"get_sector_moneyflow_ths"/"get_sector_moneyflow_dc"/"get_etf_flow"/"get_margin_data")`、`volume_concentration.collector` 的三个守卫常量、`repo.get_latest_market_total_before`。
- Produces:
  - provider: `_ensure_sw_l1_codes() -> set`（index_classify level=L1 惰性缓存，模式同 `_ensure_sw_l2_codes`）；`_ensure_sw_l1_parent_map() -> dict`（L2 code → parent L1 code；parent_code 列缺失/含空 → 返回 `{}`）。
  - collector:
    - `AMOUNT_TO_BILLION`（Task 0 实测校准的除数，模块常量，注释写明实测依据）。
    - `fetch_sector_daily(provider, date: str) -> dict | None`：返回 `{"sectors": [{code,name,level,close,amount_billion}], "meta": {"l1_status": "native"|"synthesized"|"missing", "source": "tushare:sw_daily"}}`；空数据/异常 → None。**L1 行按 `_ensure_sw_l1_codes` 识别；无 L1 行时仅当 parent_map 非空才归并合成（close 不可合成，置 None），否则 l1_status=missing 只出 L2。**
    - `fetch_market_total(conn, registry, date) -> tuple[float | None, str | None]`：复用 volume_concentration 三段守卫常量（import 常量，不改其文件），prev 值读**本任务自己的表**。
    - `fetch_proxy(registry, date) -> dict | None`：`{"moneyflow": [{name, net_amount_yi}], "moneyflow_source": str, "etf": [...], "margin": {...}, "errors": [...]}`；三路各自独立失败不拖垮整体，全失败返回含 errors 的 dict。moneyflow 按 ths→dc 顺序（registry.call 自带 provider 降级），normalizer 统一 `net_amount_yi` 字段（dc/akshare 源的 `net_inflow_billion` 换算）。

- [ ] **Step 1: 写失败测试（追加；mock provider/registry，不碰外网）**

```python
from unittest.mock import MagicMock

from services.sector_crowding import collector


def _sw_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def _mk_provider(daily_rows, l1_codes=frozenset({"801080.SI"}), parent_map=None):
    p = MagicMock()
    p.pro.sw_daily.return_value = _sw_df(daily_rows)
    p._ensure_sw_l1_codes.return_value = set(l1_codes)
    p._ensure_sw_l2_codes.return_value = {"801081.SI"}
    p._ensure_sw_l1_parent_map.return_value = parent_map or {}
    return p


ROWS = [
    {"ts_code": "801080.SI", "name": "电子", "close": 5000.0,
     "amount": 3000.0 * collector.AMOUNT_TO_BILLION, "trade_date": "20260717"},
    {"ts_code": "801081.SI", "name": "半导体", "close": 8000.0,
     "amount": 1500.0 * collector.AMOUNT_TO_BILLION, "trade_date": "20260717"},
]


class TestCollector:
    def test_fetch_sector_daily_native_l1(self):
        out = collector.fetch_sector_daily(_mk_provider(ROWS), "2026-07-17")
        levels = {s["code"]: s["level"] for s in out["sectors"]}
        assert levels == {"801080.SI": "L1", "801081.SI": "L2"}
        assert out["meta"]["l1_status"] == "native"
        amounts = {s["code"]: s["amount_billion"] for s in out["sectors"]}
        assert amounts["801080.SI"] == pytest.approx(3000.0)

    def test_fetch_sector_daily_l1_missing_no_synthesis(self):
        # sw_daily 只有 L2 行、parent_map 为空 → 禁止合成，l1_status=missing
        out = collector.fetch_sector_daily(
            _mk_provider([ROWS[1]], parent_map={}), "2026-07-17")
        assert out["meta"]["l1_status"] == "missing"
        assert all(s["level"] == "L2" for s in out["sectors"])

    def test_fetch_sector_daily_l1_synthesized_when_map_ok(self):
        out = collector.fetch_sector_daily(
            _mk_provider([ROWS[1]], parent_map={"801081.SI": "801080.SI"}), "2026-07-17")
        assert out["meta"]["l1_status"] == "synthesized"
        l1 = [s for s in out["sectors"] if s["level"] == "L1"]
        assert l1 and l1[0]["code"] == "801080.SI" and l1[0]["close"] is None

    def test_fetch_market_total_guard_floor(self, conn):
        registry = MagicMock()
        registry.call.return_value = MagicMock(
            success=True, data={"total_billion": 1000.0,
                                "shanghai_billion": 500.0, "shenzhen_billion": 500.0},
            source="tushare")
        total, src = collector.fetch_market_total(conn, registry, "2026-07-17")
        assert total is None  # 低于绝对地板 3000 亿

    def test_fetch_proxy_partial_failure(self):
        registry = MagicMock()

        def _call(cap, *a, **kw):
            if cap == "get_sector_moneyflow_ths":
                return MagicMock(success=True, source="tushare:moneyflow_ind_ths",
                                 data=[{"name": "电子", "net_amount_yi": 55.0}])
            return MagicMock(success=False, data=None, error="down", source="x")

        registry.call.side_effect = _call
        out = collector.fetch_proxy(registry, "2026-07-17")
        assert out["moneyflow"][0]["net_amount_yi"] == 55.0
        assert out["etf"] is None and out["margin"] is None
        assert out["errors"]  # 失败路有记录

    def test_normalize_moneyflow_akshare_field_shape(self):
        # akshare get_sector_fund_flow 源字段为 net_inflow_billion → 归一为 net_amount_yi
        out = collector._normalize_moneyflow(
            [{"name": "电子", "net_inflow_billion": 30.5},
             {"name": "脏值", "net_inflow_billion": "bad"}])
        assert out == [{"name": "电子", "net_amount_yi": 30.5}]
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestCollector -v`
Expected: FAIL

- [ ] **Step 3: 实现 provider 辅助 + collector**

`tushare_provider.py` 追加（`_ensure_sw_l2_codes` 之后）：

```python
    def _ensure_sw_l1_codes(self) -> set:
        """惰性加载申万一级行业代码表（~31个，拥挤度分蛋糕口径主体）"""
        if self._sw_l1_codes is None:
            try:
                ic = self.pro.index_classify(level="L1", src="SW2021")
                self._sw_l1_codes = set(ic["index_code"].tolist()) if ic is not None and not ic.empty else set()
            except Exception as e:
                logger.warning(f"获取申万一级分类失败: {e}")
                self._sw_l1_codes = set()
        return self._sw_l1_codes

    def _ensure_sw_l1_parent_map(self) -> dict:
        """惰性加载 L2 code → 所属 L1 code 映射（index_classify parent_code）。

        parent_code 列缺失或含空值 → 返回 {}（调用方按"映射不可靠禁止合成 L1"处理，
        spec v2 严重1：合成路径条件启用）。"""
        if self._sw_l1_parent_map is None:
            try:
                ic = self.pro.index_classify(level="L2", src="SW2021")
                if ic is None or ic.empty or "parent_code" not in ic.columns or ic["parent_code"].isna().any():
                    self._sw_l1_parent_map = {}
                else:
                    self._sw_l1_parent_map = dict(zip(ic["index_code"], ic["parent_code"]))
            except Exception as e:
                logger.warning(f"获取申万 L2→L1 映射失败: {e}")
                self._sw_l1_parent_map = {}
        return self._sw_l1_parent_map
```

（`__init__` 中与 `self._sw_l2_codes = None` 同处初始化 `self._sw_l1_codes = None`、`self._sw_l1_parent_map = None`。）

`collector.py`：

```python
"""sector_crowding 采集层：sw_daily 行业行情 + 两市总额守卫 + 资金流代理三路。"""
from __future__ import annotations

import logging

# 复用 volume-watch 已实战校准的三段守卫常量（只 import 不改其文件）
from services.volume_concentration.collector import (
    MARKET_SZ_SH_RATIO_FLOOR,
    MARKET_TOTAL_DROP_WARN_RATIO,
    MARKET_TOTAL_FLOOR_BILLION,
)

from . import repo

logger = logging.getLogger(__name__)

# sw_daily amount 单位换算除数 → 亿元。Task 0 真机实测校准后回填此值
# （现有 get_sector_rankings 用 amount/10000，与之对齐；若实测不符以实测为准）。
AMOUNT_TO_BILLION = 10000.0


def fetch_sector_daily(provider, date: str) -> dict | None:
    """当日申万 L1+L2 快照。L1 缺失且 parent_map 可靠才合成（meta 标 synthesized）。"""
    d = date.replace("-", "")
    try:
        df = provider.pro.sw_daily(trade_date=d)
    except Exception as e:
        logger.warning("[sector-crowding] sw_daily 失败: %s", e)
        return None
    if df is None or df.empty:
        return None
    l1_codes = provider._ensure_sw_l1_codes() or set()
    l2_codes = provider._ensure_sw_l2_codes() or set()
    sectors, l1_rows = [], []
    for row in df.to_dict("records"):
        code = row.get("ts_code")
        level = "L1" if code in l1_codes else ("L2" if code in l2_codes else None)
        if level is None:
            continue  # L3/非申万行过滤，防混级双计
        amount = row.get("amount")
        rec = {
            "code": code, "name": row.get("name"), "level": level,
            "close": row.get("close"),
            "amount_billion": round(amount / AMOUNT_TO_BILLION, 2) if amount is not None else None,
        }
        sectors.append(rec)
        if level == "L1":
            l1_rows.append(rec)
    if not sectors:
        return None
    if l1_rows:
        l1_status = "native"
    else:
        parent_map = provider._ensure_sw_l1_parent_map() or {}
        if parent_map:
            l1_status = "synthesized"
            sectors.extend(_synthesize_l1(sectors, parent_map))
        else:
            l1_status = "missing"  # 映射不可靠禁止合成（spec v2 严重1）
    return {"sectors": sectors, "meta": {"l1_status": l1_status, "source": "tushare:sw_daily"}}


def _synthesize_l1(l2_sectors: list[dict], parent_map: dict) -> list[dict]:
    """L2 成交额按 parent_code 归并成 L1。close 不可加总 → None（斜率维度缺席）。"""
    agg: dict = {}
    for s in l2_sectors:
        if s["level"] != "L2" or s.get("amount_billion") is None:
            continue
        parent = parent_map.get(s["code"])
        if not parent:
            continue
        ent = agg.setdefault(parent, {"code": parent, "name": parent, "level": "L1",
                                      "close": None, "amount_billion": 0.0})
        ent["amount_billion"] = round(ent["amount_billion"] + s["amount_billion"], 2)
    return list(agg.values())


def fetch_market_total(conn, registry, date: str):
    """两市总额（get_market_volume）+ 三段守卫；prev 读本任务自己的表。"""
    result = registry.call("get_market_volume", date)
    if not (result.success and result.data):
        return None, None
    data = result.data
    total = data.get("total_billion")
    if total is None or total < MARKET_TOTAL_FLOOR_BILLION:
        if total is not None:
            logger.warning("[sector-crowding] %s 两市总额 %.0f 亿低于地板,落 NULL(source=%s)",
                           date, total, result.source)
        return None, None
    sh, sz = data.get("shanghai_billion"), data.get("shenzhen_billion")
    if sh is not None and sz is not None and sh > 0 and sz < sh * MARKET_SZ_SH_RATIO_FLOOR:
        logger.warning("[sector-crowding] %s 深/沪腿比疑口径退化,落 NULL(source=%s)", date, result.source)
        return None, None
    prev = repo.get_latest_market_total_before(conn, date)
    if prev and total < prev * (1 - MARKET_TOTAL_DROP_WARN_RATIO):
        logger.warning("[sector-crowding] %s 两市总额较前值骤降逾 %.0f%%,仅告警照常落库",
                       date, MARKET_TOTAL_DROP_WARN_RATIO * 100)
    return total, result.source


def fetch_proxy(registry, date: str) -> dict:
    """资金流代理三路，各自独立失败不拖垮整体。moneyflow ths→dc 顺序 + normalizer。"""
    errors = []
    moneyflow, mf_source = None, None
    # 三级顺序贴 spec #7:ths→dc→akshare fund_flow。前两个 capability akshare 也声明了 dc,
    # registry 会自动跨 provider 降级;第三级覆盖"仅 akshare fund_flow 可用"的残余场景。
    for cap in ("get_sector_moneyflow_ths", "get_sector_moneyflow_dc", "get_sector_fund_flow"):
        r = registry.call(cap, date)
        if r.success and r.data:
            moneyflow = _normalize_moneyflow(r.data)
            mf_source = r.source
            break
        errors.append(f"{cap}: {getattr(r, 'error', None) or 'no data'}")
    etf = _safe_call(registry, "get_etf_flow", date, errors)
    margin = _safe_call(registry, "get_margin_data", date, errors)
    return {"moneyflow": moneyflow, "moneyflow_source": mf_source,
            "etf": etf, "margin": margin, "errors": errors}


def _safe_call(registry, cap: str, date: str, errors: list):
    r = registry.call(cap, date)
    if r.success and r.data:
        return r.data
    errors.append(f"{cap}: {getattr(r, 'error', None) or 'no data'}")
    return None


def _normalize_moneyflow(records: list) -> list[dict]:
    """归一不同源字段形态：统一输出 {name, net_amount_yi}，脏值剔除。"""
    out = []
    for row in records:
        name = row.get("name") or row.get("industry") or ""
        val = row.get("net_amount_yi")
        if val is None and row.get("net_inflow_billion") is not None:
            val = row["net_inflow_billion"]
        try:
            val = round(float(val), 2)
        except (TypeError, ValueError):
            continue
        if name:
            out.append({"name": name, "net_amount_yi": val})
    return out
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: 跑 provider 既有回归**

Run: `python3 -m pytest scripts/tests/test_post_market_enhancements.py -q`
Expected: 无新增失败（只加了私有辅助，未动既有方法）

- [ ] **Step 6: Commit**

```bash
git add scripts/providers/tushare_provider.py scripts/services/sector_crowding/collector.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): collector with L1 gating, market guard reuse, proxy normalizer"
```

---

### Task 5: formatter 报告渲染

**Files:**
- Create: `scripts/services/sector_crowding/formatter.py`
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Consumes: `analyzer.build_view` 输出的 view dict；`analyzer.SHARE_WARN_PCT/SHARE_EXTREME_PCT`。
- Produces:
  - `format_report(view: dict) -> str`：Markdown。结构：标题「全行业交易拥挤度」（与 volume-watch「Top20 主线集中度」命名区分）→ 双高清单（无则一行"无双高拥挤板块"）→ L1 全量表（占比降序：占比/分位/⚠标注）→ L2 TOP10 → 资金流代理段（**每行含「非公募持仓真值」**；proxy 为 None 显示"代理数据缺失"）→ meta 段（l1_status/missing_data 标注）。占比 ≥40 标 `🔴极值区`、≥30 标 `⚠高`；市场总额 None 时占比列显示 `-` 并注明"两市总额缺失,当日占比不可算"。
  - `format_trend(rows: list[dict], sector: str) -> str`：单板块时间序列表（date/share_pct/close），rows 为 repo.get_recent 输出。

- [ ] **Step 1: 写失败测试（追加）**

```python
from services.sector_crowding import formatter


def _view(double_high=False, l1_status="native"):
    sec = {"code": "801080.SI", "name": "电子", "level": "L1", "close": 5000.0,
           "amount_billion": 7050.0, "share_pct": 47.0, "share_pctile": 99.0,
           "gain_5d": 8.0, "gain_20d": 70.0, "gain_60d": 120.0, "gain_pctile_20d": 99.0}
    return {"date": "2026-07-17", "market_total_billion": 15000.0,
            "sectors": [sec], "double_high": [sec] if double_high else [],
            "meta": {"l1_status": l1_status}}


class TestFormatter:
    def test_report_contains_extreme_marker_and_naming(self):
        md = formatter.format_report(_view())
        assert "全行业交易拥挤度" in md
        assert "🔴" in md            # 47% ≥ 40 极值区
        assert "电子" in md

    def test_report_double_high_section(self):
        md = formatter.format_report(_view(double_high=True))
        assert "双高拥挤" in md and "801080.SI" not in md.split("双高拥挤")[0]

    def test_report_proxy_disclaimer_always_present(self):
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": [{"name": "电子", "net_amount_yi": 55.0}],
            "moneyflow_source": "tushare:moneyflow_ind_ths",
            "etf": None, "margin": None, "errors": []}})
        assert "非公募持仓真值" in md

    def test_report_l1_missing_no_fake_rows(self):
        v = _view(l1_status="missing")
        v["sectors"][0]["level"] = "L2"
        md = formatter.format_report(v)
        assert "L1 数据缺失" in md

    def test_report_etf_share_jump_flagged(self):
        # 单次份额变动超存量 30% → 疑拆分,必须标「勿直读」(spec 事故级用例 6)
        md = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None,
            "etf": [{"code": "512480", "name": "半导体ETF",
                     "total_shares_billion": 100.0, "shares_change_billion": 40.0}],
            "margin": None, "errors": []}})
        assert "勿直读" in md
        # 正常变动不误标
        md2 = formatter.format_report({**_view(), "proxy": {
            "moneyflow": None, "moneyflow_source": None,
            "etf": [{"code": "512480", "name": "半导体ETF",
                     "total_shares_billion": 100.0, "shares_change_billion": 2.0}],
            "margin": None, "errors": []}})
        assert "勿直读" not in md2
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestFormatter -v`
Expected: FAIL

- [ ] **Step 3: 实现 formatter.py**

```python
"""拥挤度 Markdown 渲染。命名与 volume-watch 严格区分：本报告=「全行业交易拥挤度」
（行业成交额÷全市场），volume-watch=「Top20 主线集中度」（Top20 内部占比）。"""
from __future__ import annotations

from .analyzer import SHARE_EXTREME_PCT, SHARE_WARN_PCT

PROXY_DISCLAIMER = "（资金流代理，非公募持仓真值）"


def _share_cell(s: dict) -> str:
    v = s.get("share_pct")
    if v is None:
        return "-"
    mark = " 🔴极值区" if v >= SHARE_EXTREME_PCT else (" ⚠高" if v >= SHARE_WARN_PCT else "")
    return f"{v:.1f}%{mark}"


def _pct(v) -> str:
    return f"{v:.0f}" if v is not None else "-"


def _sector_line(s: dict) -> str:
    gains = "/".join(_pct(s.get(f"gain_{n}d")) for n in (5, 20, 60))
    return (f"| {s.get('name')} | {_share_cell(s)} | {_pct(s.get('share_pctile'))} "
            f"| {gains} | {_pct(s.get('gain_pctile_20d'))} |")


def format_report(view: dict) -> str:
    lines = [f"## 全行业交易拥挤度 · {view['date']}", ""]
    total = view.get("market_total_billion")
    lines.append(f"两市总成交额:{total:.0f} 亿" if total is not None
                 else "两市总成交额缺失,当日占比不可算")
    lines.append("")
    dh = view.get("double_high") or []
    lines.append("### 双高拥挤(交易分位≥90 且 20日斜率分位≥90)[判断]")
    if dh:
        lines += [f"- {s['name']}({s['code']}) 占比 {_share_cell(s)} / 20日涨幅 "
                  f"{_pct(s.get('gain_20d'))}%" for s in dh]
    else:
        lines.append("- 无双高拥挤板块")
    header = ["", "| 板块 | 占比 | 占比分位 | 涨幅5/20/60日% | 20日斜率分位 |",
              "|---|---|---|---|---|"]
    l1 = sorted([s for s in view["sectors"] if s["level"] == "L1"],
                key=lambda s: -(s.get("share_pct") or 0))
    l2 = sorted([s for s in view["sectors"] if s["level"] == "L2"],
                key=lambda s: -(s.get("share_pct") or 0))[:10]
    meta = view.get("meta") or {}
    lines.append("")
    lines.append("### 申万一级(全量)")
    if l1:
        lines += header + [_sector_line(s) for s in l1]
        if meta.get("l1_status") == "synthesized":
            lines.append("> L1 由 L2 成交额归并合成(close 缺席,斜率维度不可用)")
    else:
        lines.append("L1 数据缺失(映射不可靠,禁止合成)")
    lines.append("")
    lines.append("### 申万二级 TOP10")
    lines += header + [_sector_line(s) for s in l2]
    lines.append("")
    lines.append(f"### 资金流代理{PROXY_DISCLAIMER}")
    proxy = view.get("proxy")
    if not proxy:
        lines.append("- 代理数据缺失")
    else:
        mf = proxy.get("moneyflow") or []
        top = sorted(mf, key=lambda r: -r["net_amount_yi"])[:5]
        lines += [f"- {r['name']} 主力净流入 {r['net_amount_yi']:+.1f} 亿{PROXY_DISCLAIMER}"
                  for r in top] or ["- 行业资金流缺失"]
        # ETF:get_etf_flow 现有 watchlist 直出;shares_change=最近两条披露记录之差(日度语义)
        for e in proxy.get("etf") or []:
            total, chg = e.get("total_shares_billion"), e.get("shares_change_billion")
            anomaly = (" ⚠份额跳变(疑拆分/异常,勿直读)" if total and chg
                       and abs(chg) > abs(total) * 0.3 else "")
            lines.append(f"- ETF {e.get('name')}({e.get('code')}) 份额变动 "
                         f"{chg:+.2f} 亿份{anomaly}{PROXY_DISCLAIMER}")
        m = proxy.get("margin")
        if m:
            stale = f"(数据日 {m['trade_date']})" if m.get("trade_date") != view["date"] else ""
            lines.append(f"- 全市场两融余额 {m['total_rzrqye_yi']:.0f} 亿{stale}{PROXY_DISCLAIMER}")
        if proxy.get("errors"):
            lines.append(f"- 代理缺口: {'; '.join(proxy['errors'])}")
    return "\n".join(lines)


def format_trend(rows: list[dict], sector: str) -> str:
    lines = [f"## 拥挤度趋势 · {sector}", "", "| 日期 | 占比 | 收盘 |", "|---|---|---|"]
    for rec in rows:
        for s in rec.get("sectors") or []:
            if s.get("code") == sector or s.get("name") == sector:
                share = f"{s['share_pct']:.1f}%" if s.get("share_pct") is not None else "-"
                close = f"{s['close']:.1f}" if s.get("close") is not None else "-"
                lines.append(f"| {rec['date']} | {share} | {close} |")
    return "\n".join(lines) if len(lines) > 4 else f"{sector} 无历史拥挤度数据。"
```

（注：`format_report` 消费的 view 需含 `proxy` 键——Task 6 的 service 在 build_view 输出上补 `view["proxy"] = snapshot["proxy"]`。）

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/services/sector_crowding/formatter.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): markdown formatter with proxy disclaimer and naming boundary"
```

---

### Task 6: service 编排（daily / report / trend）

**Files:**
- Create: `scripts/services/sector_crowding/service.py`
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Consumes: Task 2-5 全部接口。
- Produces（CLI 消费）:
  - `HISTORY_DAYS = 1900`（分位回看窗口 ≈ 2019 起全量交易日）。
  - `run_daily(conn, registry, provider, date, *, persist=True) -> str | None`：采集（sectors 必需，proxy 尽力）→ share_pct 计算 → 落库（persist）→ 读回历史现算 view → 渲染。sectors 采不到返 None。
  - `run_report(conn, date) -> str`：只读；当日无快照返回提示串。
  - `run_trend(conn, date, sector, days=60) -> str`：只读。

- [ ] **Step 1: 写失败测试（追加）**

```python
from services.sector_crowding import service


def _mk_registry_ok():
    registry = MagicMock()

    def _call(cap, *a, **kw):
        if cap == "get_market_volume":
            return MagicMock(success=True, source="tushare",
                             data={"total_billion": 15000.0, "shanghai_billion": 7000.0,
                                   "shenzhen_billion": 8000.0})
        if cap == "get_sector_moneyflow_ths":
            return MagicMock(success=True, source="tushare:moneyflow_ind_ths",
                             data=[{"name": "电子", "net_amount_yi": 55.0}])
        return MagicMock(success=False, data=None, error="down", source="x")

    registry.call.side_effect = _call
    return registry


class TestService:
    def test_run_daily_persists_and_renders(self, conn):
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-17")
        assert md is not None and "全行业交易拥挤度" in md
        snap = repo.get_snapshot(conn, "2026-07-17")
        assert snap is not None
        sec = {s["code"]: s for s in snap["sectors"]}
        assert sec["801080.SI"]["share_pct"] == pytest.approx(20.0)
        # 派生指标不落库
        assert "share_pctile" not in sec["801080.SI"]

    def test_run_daily_dry_run_no_persist(self, conn):
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS),
                               "2026-07-17", persist=False)
        assert md is not None
        assert repo.get_snapshot(conn, "2026-07-17") is None

    def test_run_daily_no_sectors_returns_none(self, conn):
        p = _mk_provider([])
        p.pro.sw_daily.return_value = _sw_df([])
        assert service.run_daily(conn, _mk_registry_ok(), p, "2026-07-17") is None

    def test_run_report_missing_date(self, conn):
        assert "无拥挤度快照" in service.run_report(conn, "2026-01-01")

    def test_run_daily_non_trading_day_guard(self, conn, monkeypatch):
        monkeypatch.setattr(service, "is_non_trading_day", lambda *a: True)
        md = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS), "2026-07-19")
        assert md is None
        assert repo.get_snapshot(conn, "2026-07-19") is None
        # dry-run 豁免守卫（与 sector_correlation 同语义）
        md2 = service.run_daily(conn, _mk_registry_ok(), _mk_provider(ROWS),
                                "2026-07-19", persist=False)
        assert md2 is not None
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestService -v`
Expected: FAIL

- [ ] **Step 3: 实现 service.py**

```python
"""sector_crowding 编排层（只编排，不实现）。

run_daily：采集→share_pct→落库→读回历史现算分位→渲染（None=无数据，调用方不推送）。
run_report/run_trend：只读。分位/双高永不落库（spec v2 关键设计 1）。
"""
from __future__ import annotations

import logging
import sqlite3

from utils.trade_date import is_non_trading_day

from . import analyzer, collector, formatter, repo

logger = logging.getLogger(__name__)

HISTORY_DAYS = 1900  # 分位回看窗口（≈2019 起全量交易日数）


def run_daily(conn: sqlite3.Connection, registry, provider, date: str, *,
              persist: bool = True) -> str | None:
    # 非交易日守卫下沉到 service(而非 CLI,Explore review 中3:CLI 层守卫无法单测)。
    # 与 sector_correlation 同语义:仅 persist 时守卫,dry-run 豁免(免守卫日历预取写真实库)。
    if persist and is_non_trading_day(conn, registry, date):
        logger.warning("⚠️ %s 为非交易日,跳过拥挤度采集(不落库、不推送)", date)
        return None
    fetched = collector.fetch_sector_daily(provider, date)
    if fetched is None:
        return None
    market_total, mt_source = collector.fetch_market_total(conn, registry, date)
    for s in fetched["sectors"]:
        s["share_pct"] = analyzer.compute_share_pct(s.get("amount_billion"), market_total)
    proxy = collector.fetch_proxy(registry, date)
    meta = dict(fetched["meta"])
    meta["market_total_source"] = mt_source
    if market_total is None:
        meta["missing_data"] = "market_total"
    record = {"date": date, "market_total_billion": market_total,
              "sectors": fetched["sectors"], "proxy": proxy, "meta": meta}
    if persist:
        repo.save_snapshot(conn, record)
        return run_report(conn, date)
    # dry-run：不落库。当日行一律用刚采集的 fresh record 顶替(库里可能有更早跑过的
    # 陈旧行;get_recent 是精简列,历史行无 proxy/meta——见 repo.get_recent docstring)
    history = [h for h in repo.get_recent(conn, date, HISTORY_DAYS) if h["date"] != date]
    history.append(record)
    view = analyzer.build_view(history, date)
    if view is None:
        return None
    view["proxy"] = record["proxy"]
    return formatter.format_report(view)


def run_report(conn: sqlite3.Connection, date: str) -> str:
    # 契约:get_recent 为精简列(历史行 proxy/meta 恒 None),当日全量必须 get_snapshot
    # 单行覆盖,否则报告的代理段/meta 标注段静默消失(门1 review 高优先级)
    history = repo.get_recent(conn, date, HISTORY_DAYS)
    snap = repo.get_snapshot(conn, date)
    if history and snap and history[-1]["date"] == date:
        history[-1] = snap
    view = analyzer.build_view(history, date) if history else None
    if view is None:
        return f"{date} 无拥挤度快照(先跑 sector-crowding daily)。"
    view["proxy"] = snap.get("proxy") if snap else None
    return formatter.format_report(view)


def run_trend(conn: sqlite3.Connection, date: str, sector: str, days: int = 60) -> str:
    return formatter.format_trend(repo.get_recent(conn, date, days), sector)
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/services/sector_crowding/service.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): service orchestration, derived metrics computed at read time"
```

---

### Task 7: backfill 两阶段回填

**Files:**
- Modify: `scripts/services/sector_crowding/collector.py`（追加回填采集函数）
- Modify: `scripts/services/sector_crowding/service.py`（追加 `run_backfill`）
- Test: `scripts/tests/test_sector_crowding.py`（追加）

**Interfaces:**
- Produces:
  - `collector.BackfillTruncationError(Exception)`。
  - `collector.CHUNK_YEARS = 4`（分片窗口；7.5 年≈1820 行/码贴近 2000 上限）。
  - `collector.fetch_code_history(provider, code: str, start: str, end: str) -> list[dict]`：按 ≤4 年窗口分片调 `pro.sw_daily(ts_code, start_date, end_date)`；**单片返回行数 ≥2000 抛 BackfillTruncationError**；返回 `[{date, close, amount_billion}]` 升序。
  - `service.run_backfill(conn, registry, provider, start: str, end: str) -> dict`：两阶段——阶段1 逐码采集（L1+L2 全码，来自 `_ensure_sw_l1_codes` ∪ `_ensure_sw_l2_codes`）在内存按日期聚合；阶段2 逐日期 `get_market_volume` 守卫 + share_pct + **整日一次性 save_snapshot**（禁止按码逐次 UPSERT）。已有当日行（daily 采的）跳过不覆盖。返回 `{"dates_written", "dates_skipped", "codes_failed"}`。

- [ ] **Step 1: 写失败测试（追加）**

```python
class TestBackfill:
    def test_truncation_raises(self):
        p = MagicMock()
        import pandas as pd
        p.pro.sw_daily.return_value = pd.DataFrame(
            [{"trade_date": "20200101", "close": 1.0, "amount": 1.0}] * 2000)
        with pytest.raises(collector.BackfillTruncationError):
            collector.fetch_code_history(p, "801080.SI", "2019-01-01", "2026-07-17")

    def test_backfill_writes_whole_day_once(self, conn):
        p = _mk_provider(ROWS)
        import pandas as pd

        def _sw(ts_code=None, start_date=None, end_date=None, trade_date=None):
            rows = [r for r in ROWS if r["ts_code"] == ts_code]
            return pd.DataFrame([{**r, "trade_date": "20260716"} for r in rows])

        p.pro.sw_daily.side_effect = _sw
        stats = service.run_backfill(conn, _mk_registry_ok(), p,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_written"] == 1
        snap = repo.get_snapshot(conn, "2026-07-16")
        # 两个码都在同一行里 → 没有互相覆盖
        assert {s["code"] for s in snap["sectors"]} == {"801080.SI", "801081.SI"}

    def test_backfill_skips_existing_daily_rows(self, conn):
        repo.save_snapshot(conn, _rec("2026-07-16"))
        p = _mk_provider(ROWS)
        import pandas as pd
        p.pro.sw_daily.side_effect = lambda **kw: pd.DataFrame(
            [{**ROWS[0], "trade_date": "20260716"}])
        stats = service.run_backfill(conn, _mk_registry_ok(), p,
                                     "2026-07-16", "2026-07-16")
        assert stats["dates_skipped"] == 1
        # 原行未被覆盖
        assert repo.get_snapshot(conn, "2026-07-16")["market_total_billion"] == 15000.0
```

- [ ] **Step 2: 跑测试确认 RED**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py::TestBackfill -v`
Expected: FAIL

- [ ] **Step 3: 实现（collector 追加 + service 追加）**

collector.py 追加：

```python
CHUNK_YEARS = 4  # 分片窗口:7.5年≈1820行/码贴近镜像2000行静默截断上限,必须分片


class BackfillTruncationError(Exception):
    """单片返回 ≥2000 行=疑似静默截断,拒绝落库(memory: index_member_all 同坑)。"""


def fetch_code_history(provider, code: str, start: str, end: str) -> list[dict]:
    """按 ≤CHUNK_YEARS 年窗口分片拉单码区间日线，升序返回 {date, close, amount_billion}。"""
    out = []
    chunk_start = start
    while chunk_start <= end:
        cy = int(chunk_start[:4])
        chunk_end = min(f"{cy + CHUNK_YEARS - 1}-12-31", end)
        df = provider.pro.sw_daily(
            ts_code=code,
            start_date=chunk_start.replace("-", ""),
            end_date=chunk_end.replace("-", ""),
        )
        if df is not None and len(df) >= 2000:
            raise BackfillTruncationError(
                f"{code} {chunk_start}~{chunk_end} 返回 {len(df)} 行,疑似截断")
        if df is not None and not df.empty:
            for row in df.to_dict("records"):
                td = str(row.get("trade_date"))
                amount = row.get("amount")
                out.append({
                    "date": f"{td[:4]}-{td[4:6]}-{td[6:]}",
                    "close": row.get("close"),
                    "amount_billion": round(amount / AMOUNT_TO_BILLION, 2)
                    if amount is not None else None,
                })
        chunk_start = f"{cy + CHUNK_YEARS}-01-01"
    out.sort(key=lambda r: r["date"])
    return out
```

service.py 追加：

```python
def run_backfill(conn: sqlite3.Connection, registry, provider, start: str, end: str) -> dict:
    """两阶段回填:①逐码分片采集,内存按日期聚合;②逐日守卫总额+share_pct,整日一次写。

    已有快照的日期跳过(daily 采的行含 proxy,回填行不含,不可覆盖)。"""
    l1 = provider._ensure_sw_l1_codes() or set()
    l2 = provider._ensure_sw_l2_codes() or set()
    code_meta = [(c, "L1") for c in sorted(l1)] + [(c, "L2") for c in sorted(l2)]
    by_date: dict = {}
    codes_failed = []
    for code, level in code_meta:
        try:
            bars = collector.fetch_code_history(provider, code, start, end)
        except collector.BackfillTruncationError:
            raise
        except Exception as e:  # 单码失败记账继续,不拖垮全量
            logger.warning("[sector-crowding backfill] %s 失败: %s", code, e)
            codes_failed.append(code)
            continue
        for bar in bars:
            by_date.setdefault(bar["date"], []).append(
                {"code": code, "name": code, "level": level,
                 "close": bar["close"], "amount_billion": bar["amount_billion"]})
    # L1 合成与 daily 同一分支逻辑(Explore review 中1):回填若无 L1 行而 parent_map 可靠,
    # 逐日合成 L1,否则合成 L1 永无历史序列 → 分位/双高对 L1 长期失效,白酒极值覆盖目标落空
    parent_map = {} if l1 else (provider._ensure_sw_l1_parent_map() or {})
    written = skipped = 0
    for d in sorted(by_date):
        if repo.get_snapshot(conn, d) is not None:
            skipped += 1
            continue
        total, _src = collector.fetch_market_total(conn, registry, d)
        sectors = by_date[d]
        has_l1 = any(s["level"] == "L1" for s in sectors)
        if not has_l1 and parent_map:
            sectors = sectors + collector._synthesize_l1(sectors, parent_map)
            l1_status = "synthesized"
        else:
            l1_status = "native" if has_l1 else "missing"
        for s in sectors:
            s["share_pct"] = analyzer.compute_share_pct(s.get("amount_billion"), total)
        repo.save_snapshot(conn, {
            "date": d, "market_total_billion": total, "sectors": sectors,
            "proxy": None, "meta": {"backfilled": True, "l1_status": l1_status}})
        written += 1
    return {"dates_written": written, "dates_skipped": skipped, "codes_failed": codes_failed}
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_sector_crowding.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/services/sector_crowding/collector.py scripts/services/sector_crowding/service.py scripts/tests/test_sector_crowding.py
git commit -m "feat(sector-crowding): two-phase backfill with truncation guard"
```

> **阶段 B 完成门：** 同阶段 A——pytest 全绿 → 门1（`/simplify` + `/code-review`）→ 门2（codex adversarial-review）→ 结束条件满足才进 Task 8。
> 备注：回填行 `name` 暂存 code（sw_daily 区间接口可能不带 name），report 渲染时以当日 daily 行 name 为准；若门1/门2 认为需改，此处补 name 映射。

---

### Task 8: CLI + main.py 挂载 + smoke（先 RED 后注册）

**Files:**
- Modify: `scripts/tests/test_cli_smoke.py`（`ARCHITECTURE_COMMANDS` 追加 6 条）
- Create: `scripts/cli/sector_crowding.py`
- Modify: `scripts/main.py`（`register_sector_correlation_subparser` 挂载点 ~L1779 与分发点 ~L1887 各加对应两行）

**Interfaces:**
- Consumes: `service.run_daily/run_report/run_trend/run_backfill`；`cli/sector_correlation.py` 的 `_setup_tushare` 模式与非交易日守卫模式。
- Produces: 顶层命令 `sector-crowding daily|report|trend|backfill`。

- [ ] **Step 1: smoke 先行（skills-sync 2.1 规定顺序：先加用例跑 RED）**

`ARCHITECTURE_COMMANDS` 追加：

```python
    # sector-crowding（板块拥挤度:交易/斜率/资金流代理）
    ["sector-crowding", "daily"],
    ["sector-crowding", "daily", "--date", "2026-07-17", "--dry-run"],
    ["sector-crowding", "daily", "--date", "2026-07-17", "--push"],
    ["sector-crowding", "report", "--date", "2026-07-17"],
    ["sector-crowding", "trend", "--sector", "801080.SI", "--days", "60"],
    ["sector-crowding", "backfill", "--start", "2019-01-01", "--end", "2026-07-17"],
```

Run: `python3 -m pytest scripts/tests/test_cli_smoke.py -k "sector-crowding" -v`
Expected: FAIL（`invalid choice: 'sector-crowding'`）

- [ ] **Step 2: 实现 CLI**

```python
"""CLI: 板块拥挤度（交易拥挤度/斜率拥挤度/资金流代理）。

  sector-crowding daily    [--date] [--dry-run] [--push]
  sector-crowding report   [--date]
  sector-crowding trend    --sector CODE [--date] [--days 60]
  sector-crowding backfill --start 2019-01-01 [--end]

daily: 采集+落库,默认不推送(--push 才推钉钉;--dry-run 仅打印不落库)。
report/trend: 只读现算分位。backfill: 一次性历史回填(两阶段,防同日覆盖)。
"""
from __future__ import annotations

import argparse
import datetime
import logging
import sys

from db.connection import get_connection
from services.sector_crowding import service

logger = logging.getLogger(__name__)


def register_subparser(subparsers: argparse._SubParsersAction) -> None:
    sc = subparsers.add_parser("sector-crowding", help="板块拥挤度(交易/斜率/资金流代理)")
    sub = sc.add_subparsers(dest="sector_crowding_command")

    daily = sub.add_parser("daily", help="采集+落库(默认不推送)")
    daily.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")
    daily.add_argument("--dry-run", action="store_true", help="仅打印,不落库不推送")
    daily.add_argument("--push", action="store_true", help="落库后推钉钉(默认不推)")

    report = sub.add_parser("report", help="只读:当日三部分全景+分位+双高")
    report.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认今天)")

    trend = sub.add_parser("trend", help="只读:单板块拥挤度时间序列")
    trend.add_argument("--sector", required=True, help="申万行业代码或名称(如 801080.SI/电子)")
    trend.add_argument("--date", default=None, help="截止交易日(默认今天)")
    trend.add_argument("--days", type=int, default=60, help="窗口天数(默认 60)")

    backfill = sub.add_parser("backfill", help="一次性历史回填(两阶段)")
    backfill.add_argument("--start", default=service.DEFAULT_BACKFILL_START,
                          help=f"起始日(默认 {service.DEFAULT_BACKFILL_START})")
    backfill.add_argument("--end", default=None, help="截止日(默认今天)")


def handle_command(config: dict, args: argparse.Namespace) -> None:
    sub = getattr(args, "sector_crowding_command", None)
    if sub == "daily":
        _run_daily(config, args)
    elif sub == "report":
        _run_readonly(lambda conn: service.run_report(conn, args.date or _today()))
    elif sub == "trend":
        _run_readonly(lambda conn: service.run_trend(
            conn, args.date or _today(), args.sector, days=args.days))
    elif sub == "backfill":
        _run_backfill(config, args)
    else:
        print("用法:python main.py sector-crowding daily|report|trend|backfill [...]",
              file=sys.stderr)
        sys.exit(2)


def _today() -> str:
    return datetime.date.today().isoformat()


def _run_readonly(fn) -> None:
    conn = get_connection()
    try:
        print(fn(conn))
    finally:
        conn.close()


def _setup(config):
    """复刻 sector_correlation._setup_tushare:setup_providers+initialize_all 取 tushare。"""
    from main import setup_providers

    registry = setup_providers(config)
    registry.initialize_all()
    provider = registry.get_provider("tushare")
    if provider is None or not getattr(provider, "_initialized", False):
        logger.error("[sector-crowding] Tushare provider 未初始化(检查 TUSHARE_TOKEN)")
        return None, None
    return registry, provider


def _run_daily(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    date = args.date or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry, provider = _setup(config)
            if provider is None:
                return
            # 非交易日守卫在 service.run_daily 内(persist 时生效,dry-run 豁免)
            md = service.run_daily(conn, registry, provider, date,
                                   persist=not args.dry_run)
    finally:
        conn.close()
    if md is None:
        print(f"{date} 无拥挤度数据,跳过。")
        return
    print(md)
    if args.push and not args.dry_run:
        _push_to_dingtalk(f"板块拥挤度 · {date}", md)


def _run_backfill(config: dict, args: argparse.Namespace) -> None:
    from utils.network_env import without_standard_http_proxy

    end = args.end or _today()
    conn = get_connection()
    try:
        with without_standard_http_proxy():
            registry, provider = _setup(config)
            if provider is None:
                return
            stats = service.run_backfill(conn, registry, provider, args.start, end)
    finally:
        conn.close()
    print(f"回填完成: 写入 {stats['dates_written']} 日 / 跳过 {stats['dates_skipped']} 日"
          f" / 失败码 {len(stats['codes_failed'])} 个"
          + (f": {','.join(stats['codes_failed'][:10])}" if stats["codes_failed"] else ""))


def _push_to_dingtalk(title: str, markdown: str) -> None:
    from pushers.dingtalk_pusher import DingTalkPusher

    pusher = DingTalkPusher(config={})
    if not pusher.initialize():
        logger.error("[sector-crowding] DingTalk pusher 未启用,跳过推送")
        return
    ok = pusher.send_markdown(title=title, content=markdown)
    logger.info("[sector-crowding] 推送 %s", "成功" if ok else "失败")
```

service.py 顶部常量区追加 `DEFAULT_BACKFILL_START = "2019-01-01"`。

- [ ] **Step 3: main.py 挂载（对照 sector-correlation 的两处）**

注册区（~L1779）：

```python
    from cli.sector_crowding import register_subparser as register_sector_crowding_subparser
    register_sector_crowding_subparser(subparsers)
```

分发区（~L1887）：

```python
    elif args.command == "sector-crowding":
        from cli.sector_crowding import handle_command as handle_sector_crowding
        handle_sector_crowding(config, args)
```

（具体插入位置以文件内 `sector-correlation` 两处为锚，紧随其后，保持既有 import 风格——若该文件用顶部集中 import 则跟随。）

- [ ] **Step 4: 跑 smoke 确认 GREEN**

Run: `python3 -m pytest scripts/tests/test_cli_smoke.py -v`
Expected: 全部 passed（含新 6 条）

- [ ] **Step 5: 全量后端验证**

Run: `make check-scripts`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add scripts/cli/sector_crowding.py scripts/main.py scripts/tests/test_cli_smoke.py scripts/services/sector_crowding/service.py
git commit -m "feat(sector-crowding): CLI daily/report/trend/backfill with smoke coverage"
```

---

### Task 9: launchd 部署（G2 可并行）

**Files:**
- Create: `deploy/launchd/com.alyx.tradesystem.sector-crowding.plist`
- Create: `deploy/launchd/sector-crowding-runner.sh`

- [ ] **Step 1: plist（对照 sector-correlation 模板，21:30，五个 Weekday dict）**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!-- Sleep policy: 拥挤度为复盘辅助数据,错过可接受;macOS 休眠期间不触发,可接受 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.alyx.tradesystem.sector-crowding</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/alyx/tradeSystem/deploy/launchd/sector-crowding-runner.sh</string>
    </array>
    <!-- 工作日 21:30(错开 volume-watch 21:00 / sector-correlation 21:15,降低镜像并发压力) -->
    <key>StartCalendarInterval</key>
    <array>
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
    </array>
    <key>StandardOutPath</key>
    <string>/tmp/tradesystem-sector-crowding.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/tradesystem-sector-crowding.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 2: runner.sh（五段规范；默认不推送故不带 --push）**

```bash
#!/bin/bash
# 板块拥挤度定时入口(launchd 调用)。交易日 21:30 采集 L1/L2 拥挤度快照落库,默认不推送
# (复盘时 sector-crowding report 查看;要推送手动跑 --push)。非交易日任务内守卫跳过。
set -e

# 1. PATH
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

# 2. 仓库根
REPO_ROOT="/Users/alyx/tradeSystem"
cd "$REPO_ROOT"

# 3. env
if [ -f "$REPO_ROOT/scripts/.env" ]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/scripts/.env"
fi
if [ -f "$HOME/.config/tradeSystem.env" ]; then
    # shellcheck disable=SC1091
    source "$HOME/.config/tradeSystem.env"
fi

# 4. 时间戳
echo "===== $(date '+%Y-%m-%d %H:%M:%S') sector-crowding daily start ====="

# 5. 凭据诊断(${VAR:+set} 只判存在不打值;默认不推送,钉钉凭据仅 --push 场景需要)
echo "[env] TUSHARE_TOKEN=${TUSHARE_TOKEN:+set} DINGTALK_WEBHOOK_TOKEN=${DINGTALK_WEBHOOK_TOKEN:+set}"

exec /usr/bin/python3 scripts/main.py sector-crowding daily
```

- [ ] **Step 3: 校验与安装（安装步骤实施时执行）**

```bash
chmod +x deploy/launchd/sector-crowding-runner.sh
plutil -lint deploy/launchd/com.alyx.tradesystem.sector-crowding.plist
# 安装(实施阶段 C 收尾时):
cp deploy/launchd/com.alyx.tradesystem.sector-crowding.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alyx.tradesystem.sector-crowding.plist
rm -f /tmp/tradesystem-sector-crowding.log
launchctl start com.alyx.tradesystem.sector-crowding
tail -20 /tmp/tradesystem-sector-crowding.log   # 必须真触发验证,不能只看 list
```

- [ ] **Step 4: Commit**

```bash
git add deploy/launchd/com.alyx.tradesystem.sector-crowding.plist deploy/launchd/sector-crowding-runner.sh
git commit -m "chore(sector-crowding): launchd weekday 21:30 schedule"
```

---

### Task 10: 文档同步 + 真实库验证（G3 文档部分可并行）

**Files:**
- Modify: `.agents/skills/INDEX.md`（依赖表加 `sector-crowding` 4 条命令行）
- Modify: `.agents/skills/market-tasks/references/market-observability.md`（新增 sector-crowding 小节：三部分口径、与 volume-watch 边界表、默认不推送、backfill 一次性说明）
- Modify: `deploy/launchd/README.md`（任务清单加一行 21:30）

- [ ] **Step 1: INDEX.md 依赖表追加**

```markdown
| `sector-crowding daily [--date] [--dry-run] [--push]` | 申万 L1/L2 拥挤度采集落库(默认不推送,--push 才推钉钉) | [stock-scanners 之外:market-observability](market-tasks/references/market-observability.md) |
| `sector-crowding report [--date]` | 只读:交易/斜率拥挤度分位+双高清单(读取时现算) | 同上 |
| `sector-crowding trend --sector CODE [--days]` | 只读:单板块拥挤度时间序列 | 同上 |
| `sector-crowding backfill --start [--end]` | 一次性历史回填(两阶段防同日覆盖,2000行截断报错) | 同上 |
```

（表格式对照 INDEX.md 现有行微调。）

- [ ] **Step 2: market-observability.md 新增小节**

内容必含：① 三部分口径表（交易拥挤度=占全市场、斜率=5/20/60 日涨幅+自身分位、资金流代理=非公募持仓真值）；② 与 volume-watch 的边界表（spec「口径边界」章节照搬）；③ 派生指标不落库、读取时现算；④ 调度 21:30 launchd、默认不推送；⑤ 绝对参考线 30%/40% 语义。

- [ ] **Step 3: launchd README 加一行**

任务清单表加 `sector-crowding | 21:30 工作日 | 板块拥挤度采集(默认不推送)`。

- [ ] **Step 4: 真实库 dry-run 验证（内存库 ≠ 真实库）**

```bash
python3 scripts/main.py sector-crowding daily --date <最近交易日> --dry-run
```

Expected: 打印真实 Markdown 报告；肉眼核对——L1 占比总和应≈100%（合成/缺失时按 meta 标注）、电子等热门行业占比数量级与近期盘面相符、无 NaN/None 渲染残留。

- [ ] **Step 5: skills-sync 验证报告 + Commit**

Run: `make check-scripts`
Expected: 全绿。输出 skills-sync 四行检查结果（INDEX/smoke/SKILL/openai.yaml——market-tasks 无 openai.yaml 变更需求则标"无需"）。

```bash
git add .agents/skills/INDEX.md .agents/skills/market-tasks/references/market-observability.md deploy/launchd/README.md
git commit -m "docs(sector-crowding): sync INDEX, market-observability reference, launchd README"
```

> **阶段 C 完成门：** make check-scripts 全绿 + 真实库 dry-run 通过后，跑门1 + 门2（收尾整合审查,对整个分支 diff `--base main`）；6 条结束条件满足后向用户汇报,并提示可手动 `/code-review ultra`。
> **backfill 实跑（写真实库）不在本计划自动执行**——涉及 ~165 码 × 分片 + 逐日 market_volume 调用（预估 10-30 分钟数据源压力），完成 Task 10 后向用户确认再跑。

---

## 测试验证方案（implementation-plan 步骤 3）

- **分层**：数据层（repo/tmp_path SQLite）→ 纯函数层（analyzer）→ 采集层（collector/mock provider+registry）→ 编排层（service）→ 渲染层（formatter）→ CLI（smoke 参数化）。全程 mock 外网，无密钥依赖。
- **验收命令**：`python3 -m pytest scripts/tests/test_sector_crowding.py -v`（预计 ~25 用例）+ `python3 -m pytest scripts/tests/test_cli_smoke.py -v` + `make check-scripts`。
- **完成标准**：上述全绿；真实库 dry-run 报告肉眼核对通过；launchd 手动 start 一次日志正常；任何"占比>100%/L1 合成未标注/代理无免责标"为不可接受缺陷。

## 方案审查结论（Explore subagent，2026-07-18）

总体结论：**修订后可推进**（已全部修订如下）。可行性五项全部核实通过：守卫常量可 import、provider 初始化位对齐、**计划所用 5 个 capability 均已在 get_capabilities() 声明（无 registry 静默跳过风险）**、import 路径正确、mock 与真实返回形态一致；并行分组 7 字段齐全，反模式 6 项零命中。

| 级别 | 意见 | 处置 |
|---|---|---|
| 中 | 回填缺 L2→L1 合成分支，合成 L1 将永无历史分位 | 已修：`run_backfill` 复用 `_synthesize_l1` 同款逐日合成 |
| 中 | ETF 份额跳变标注无测试（事故级用例 6 缺席） | 已修：Task 5 补 `test_report_etf_share_jump_flagged` 正反两例 |
| 中 | 非交易日守卫在 CLI 层无法单测 | 已修：守卫下沉 `service.run_daily`（persist 时生效、dry-run 豁免），补 monkeypatch 测试，CLI 层删守卫 |
| 低 | moneyflow 降级顺序偏离 spec 字面（缺 akshare fund_flow 第三级，normalizer 兜底成死支） | 已修：循环补 `get_sector_fund_flow` + `test_normalize_moneyflow_akshare_field_shape` |
| 低 | schema `updated_at` 无 DDL 默认值（与 spec 数据模型表不符） | 已修：加 `DEFAULT (datetime('now'))` |
| 低 | 涨幅窗口按 bar 索引的假设未注释 | 已修：`interval_gain` docstring 写明假设与接受理由 |
| 低 | schema 插入锚点行号轻微漂移 | 接受为已知：计划已注明"以名字锚点为准，行号仅供参考" |
| 低 | 阶段 C review 执行方为隐含约定 | 已修：并行分组章节显式声明三阶段 review 均由 G1 统一执行 |

## 执行记录（实施时追加）

- Task 0 结论（2026-07-18 真机实测，trade_date=20260717）：`L1_AVAILABLE=True`（sw_daily 439 行含全部 31 个 L1）；`PARENT_MAP_OK=True`（parent_code 列全非空，降级映射双保险）；`AMOUNT_TO_BILLION=10000`（万元→亿元：L1 总和 265,411,140 万元 ≈ 2.65 万亿，量级吻合）。合成路径预期永不启用，但代码分支保留（数据源行为可能漂移）。注意：sw_daily 含 L3 与"申万50"等特殊指数，L1/L2 码表过滤必须保留。
