# Trend Leader Mainline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `trend-leader` 默认主线门收紧为“申万二级近 3 个有效快照 Top-K 至少命中 2 次 + LLM 受控概念分支”，并修复定时环境找不到 `agy` 导致的伪 fallback。

**Architecture:** `scanner` 从现有 `daily_volume_concentration` 向前读取最近最多 3 条有效快照，按目标有效快照排名保留稳定申万二级并追加手工覆盖；`mainline_llm` 在合法输出时只接受输入概念子集，任何调用或解析失败时关闭概念分支；CLI 用带诊断的不可用 runner 区分启动失败和人工 `--no-llm`；renderer 展示窗口、门槛、来源状态与原因。数据库 schema、API、Web、既有池记录均不修改。

**Tech Stack:** Python 3.9、SQLite、pytest、Bash/launchd、现有 `ProviderRegistry` / Antigravity runner。

---

## 规格与硬约束

- 设计真源：`docs/superpowers/specs/2026-07-14-trend-leader-mainline-hardening-design.md`。
- “有效集中度快照”定义为：该记录的 Top-K 中至少有一个非空且非“未分类”的申万二级；空 `sector_summary` 或全“未分类”记录不计入窗口。
- 自动申万主线只允许来自目标有效快照的 Top-K；目标日记录无效时也按 degraded 路径回退最近有效快照；跨日窗口只决定是否保留，不得引入有效目标快照 Top-K 之外的板块。
- 最近 2～3 个有效快照要求命中至少 2 次；只有 1 条快照时要求 1 次；没有快照时自动主线为空。
- `--sectors` 是显式人工覆盖，去重后按输入顺序追加，不经过稳定门。
- 默认 `hybrid` 的 LLM 调用异常、空返回、非法/越界输出、红线命中一律 `fallback_l2`，最终 `main_concepts=[]`。
- 合法 `accepted_concepts=[]` 是正常裁决，状态仍为 `ok`，不得伪装成故障。
- `hybrid --no-llm` 与显式 `l2+concept` 继续保留机械概念；`l2` 不使用概念。
- 既有 `trend_leader_pool` 不自动删除、不回写；新主线只约束后续发现入池，维护链路不变。
- 测试不得连接真实 Tushare、Antigravity、钉钉或用户数据库，不发送老师观点或私有 payload。
- 当前主工作区有用户未提交改动；所有实现只在 `.worktrees/fix-trend-leader-mainline` 中进行，提交时用具体路径，禁止 `git add -A` / `git add .`。

## 独立改动项与耗时

共 5 项独立改动：

| # | 改动项 | 预估 | 主要文件 |
| --- | --- | ---: | --- |
| A | 申万三日稳定门、顺序与运行时元数据 | 中，20～30 分钟 | `scripts/services/trend_leader/{constants,scanner}.py`、`scripts/tests/test_trend_leader_concept_main.py` |
| B | LLM fail-closed、启动诊断与模式兼容 | 中，20～30 分钟 | `mainline_llm.py`、`scripts/cli/trend_leader.py`、概念/CLI 测试 |
| C | 报告状态文案和 launchd PATH | 中，15～25 分钟 | `renderer.py`、`trend-leader-runner.sh`、renderer/launchd 测试 |
| D | Agent、Skill 与索引文档同步 | 中，10～20 分钟 | `AGENTS.md`、`CLAUDE.md`、`.agents/skills/...`、`.agents/rules/skills-sync.md` |
| E | 定向/全后端验证、两道代码审查门和交付核对 | 中，20～40 分钟 | 测试命令、git diff、审查报告 |

`N=5`、总时长超过 20 分钟、跨后端/测试/DevOps/文档，不命中逃逸条款。A 与 B 共享 scanner 契约，由主 agent 串行完成；C 中 launchd 部分可与 A/B 并行；D 必须等行为语义稳定后再同步；E 由根 agent统一收口。

## 并行分组

| 分组 | 角色 | 执行 Agent | 专项关注 | 职责边界 | 文件范围 | 禁区 | 冲突标注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| G1 Core | 后端（主）+ 测试（辅） | Codex 根 agent | 正确性、失败收口、兼容性 | 完成 A/B 及 renderer 业务文案，按 TDD 维护核心契约 | `scripts/services/trend_leader/{constants,scanner,mainline_llm,renderer}.py`、`scripts/cli/trend_leader.py`、`scripts/tests/test_trend_leader_{concept_main,renderer,cli}.py` | 允许改列出文件；不得改 launchd、文档、DB/API/Web；若需改公共 volume repo 先询问 | 核心代码和三份既有测试唯一归 G1；根 agent 独占所有 Git 操作 |
| G2 Runtime | DevOps（主）+ 测试（辅） | Codex collaboration 写入 subagent | 可观测性、最小环境 | 只编辑 runner PATH 与静态测试并运行定向 pytest，不执行 Git 操作 | `deploy/launchd/trend-leader-runner.sh`、新建 `scripts/tests/test_trend_leader_launchd.py` | 允许改两文件；不得改 Python 业务逻辑、其他 launchd、文档；不得 `git add/commit`；runner 入口命令变化需先询问 | launchd 脚本与新测试唯一归 G2；不得碰 G1 测试 |
| G3 Docs | 文档 | Codex collaboration 写入 subagent | 事实边界、Skill 同步 | 行为稳定后只编辑五份真源并做文本核对，不执行 Git 操作 | `AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、`.agents/skills/INDEX.md`、`.agents/rules/skills-sync.md` | 允许改列出文档；不得改代码、CLI 签名、README；不得 `git add/commit`；若与主工作区用户改动冲突先停下报告 | 五份文档唯一归 G3；不与 G1/G2共享文件 |
| G4 Review | 架构师（主）+ 测试（辅） | Codex 原生 `adversarial-review --wait` | 合规、边界、回归 | 每个大阶段审当前 diff，给出分级 findings；修订仍回原归属组 | 只读当前阶段 git diff 与测试结果 | 不直接改文件、不新增需求、不运行真实外部服务；修复指回 G1/G2/G3 | 代码审查唯一归 G4，具体指派点见阶段完成标准 |

执行顺序：先并行启动 G1 Task 1 与 G2 Task 3 的 launchd 子任务；G1 再串行完成 Task 2 和 renderer；行为测试转绿后启动 G3。所有 subagent 只编辑归属文件并运行定向测试，Codex 根 agent 串行独占 `git add`、`git commit`、review 基线与最终验证，避免共享 worktree 的 index/HEAD 竞态。

开始业务实现前，根 agent 在方案/设计提交后记录 `IMPLEMENTATION_BASE="$(git rev-parse HEAD)"`；阶段级 `--base "$IMPLEMENTATION_BASE"` 仅用于已提交的阶段 diff，未提交 working tree 一律使用 working-tree review，禁止混用导致漏审。

> **阶段审查硬门：每个大阶段结束且阶段测试通过后，立即跑一次门1（简化检查 + `/code-review`）和门2 Codex 原生 `adversarial-review --wait`；不得把多阶段审查全部攒到收尾。** 当前环境若不能直接调用 slash 命令，按仓库规则执行等价的人工简化复查与原生 reviewer 命令，并完整记录处置。

## Task 1：实现申万二级三日稳定门

**Files:**

- Modify: `scripts/services/trend_leader/constants.py:29-45`
- Modify: `scripts/services/trend_leader/scanner.py:38-49,100-105,168-213`
- Modify: `scripts/tests/test_trend_leader_concept_main.py:78-85` and new stable-mainline tests

- [ ] **Step 1: 先写稳定门失败测试**

在 `_seed_concentration` 后新增：

```python
def test_main_sectors_requires_two_hits_and_preserves_target_order(conn):
    _seed_concentration(conn, "2026-07-10", ["半导体", "通信设备", "元件", "光学光电子", "电池"])
    _seed_concentration(conn, "2026-07-13", ["通信设备", "半导体", "光学光电子", "元件", "消费电子"])
    _seed_concentration(conn, "2026-07-14", ["半导体", "通信设备", "元件", "光学光电子", "IT服务Ⅱ"])

    sectors, meta = scanner._main_sectors(conn, "2026-07-14", top_k=5, sectors=None)

    assert sectors == ["半导体", "通信设备", "元件", "光学光电子"]
    assert meta == {
        "status": "exact",
        "source_date": "2026-07-14",
        "snapshot_count": 3,
        "required_hits": 2,
    }


def test_main_sectors_single_snapshot_and_manual_override(conn):
    _seed_concentration(conn, "2026-07-14", ["半导体", "IT服务Ⅱ"])

    sectors, meta = scanner._main_sectors(
        conn, "2026-07-14", top_k=2, sectors=["IT服务Ⅱ", "软件开发"])

    assert sectors == ["半导体", "IT服务Ⅱ", "软件开发"]
    assert meta["snapshot_count"] == 1
    assert meta["required_hits"] == 1
```

再补四类边界：

- 恰好 2 条有效快照时仍要求命中 2 次。
- 空 `sector_summary` 与全“未分类”快照夹在有效快照之间时不计入 `snapshot_count`，窗口继续向前找满最多 3 条有效记录。
- 目标日记录为空/全未分类时回退最近有效日，`status=fallback` 且 `source_date` 为实际来源日。
- 完全无有效快照时只返回手工板块，`status=missing`、`source_date=None`、`snapshot_count=0`；目标日不稳定板块经 `--sectors` 可显式覆盖。

- [ ] **Step 2: 验证 RED**

Run:

```bash
python3 -m pytest scripts/tests/test_trend_leader_concept_main.py -k 'main_sectors' -v
```

Expected: 旧 `_main_sectors` 的第二返回值仍是布尔值且单日 Top-K 全放行，新测试因 metadata/稳定门行为断言而 FAIL；不得是 fixture/导入错误。

- [ ] **Step 3: 增加稳定门常量与最小实现**

在 `constants.py` 增加：

```python
MAIN_SECTOR_LOOKBACK_RECORDS = 3
MAIN_SECTOR_MIN_HITS = 2
```

在 `scanner.py` 用现有 repo 接口逐条向前找有效记录；无效记录不占 3 条窗口：

```python
def _ranked_sectors(record: dict | None, top_k: int) -> list[str]:
    rows = (record or {}).get("sector_summary") or []
    return [
        row["industry"] for row in rows
        if row.get("industry") and row.get("industry") != UNCLASSIFIED
    ][:top_k]


def _recent_valid_sector_snapshots(conn, end_date, top_k, limit) -> list[dict]:
    cursor = end_date
    valid = []
    seen_dates = set()
    while len(valid) < limit:
        recent = vc_repo.get_recent_concentration(conn, cursor, 1)
        if not recent:
            break
        record = recent[-1]
        record_date = record.get("date")
        if not record_date or record_date in seen_dates:
            break
        seen_dates.add(record_date)
        if _ranked_sectors(record, top_k):
            valid.append(record)
        cursor = (datetime.strptime(record_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return valid  # 最新有效记录在前


def _main_sectors(conn, date, top_k, sectors) -> tuple[list[str], dict]:
    history = _recent_valid_sector_snapshots(
        conn, date, top_k, C.MAIN_SECTOR_LOOKBACK_RECORDS)
    source = history[0] if history else None
    current = _ranked_sectors(source, top_k)
    required_hits = C.MAIN_SECTOR_MIN_HITS if len(history) >= 2 else 1
    hit_counts = {
        name: sum(name in _ranked_sectors(item, top_k) for item in history)
        for name in current
    }
    result = [name for name in current if hit_counts[name] >= required_hits]
    for name in sectors or []:
        if name and name not in result:
            result.append(name)
    source_date = source.get("date") if source else None
    status = "exact" if source_date == date else "fallback" if source else "missing"
    return result, {
        "status": status,
        "source_date": source_date,
        "snapshot_count": len(history),
        "required_hits": required_hits,
    }
```

实际实现可用 `Counter` 简化，但必须保持：只统计有效快照、每个快照同一板块最多命中一次、输出有效目标快照顺序、手工顺序、历史不足门槛；循环必须保证 cursor 单调向前并有重复日期防护。

- [ ] **Step 4: 接入 scanner 摘要且 membership 用集合**

`run_daily` 改为：

```python
main_sector_list, main_sector_meta = _main_sectors(conn, date, top_k, sectors)
main_sectors = set(main_sector_list)
```

传给 LLM 的 `main_sectors` 使用有序 `main_sector_list`，候选判断使用 `main_sectors` 集合。摘要改为：

```python
"main_sectors": main_sector_list,
"main_sector_window": C.MAIN_SECTOR_LOOKBACK_RECORDS,
"main_sector_required_hits": main_sector_meta["required_hits"],
"main_sector_snapshot_count": main_sector_meta["snapshot_count"],
"main_sector_source_date": main_sector_meta["source_date"],
"main_sector_status": main_sector_meta["status"],
"degraded_main": main_sector_meta["status"] != "exact",
```

不得再 `sorted(main_sectors)`，否则报告顺序会退回字典序。

- [ ] **Step 5: 验证 GREEN 与 7 月 14 日 fixture**

```bash
python3 -m pytest scripts/tests/test_trend_leader_concept_main.py -v
```

Expected: 全部 PASS；新用例明确断言 `IT服务Ⅱ` 仅命中 1 次被排除，四个稳定板块按目标日顺序保留。

- [ ] **Step 6: 提交稳定门逻辑**

仅暂存 Task 1 文件，提交：

```bash
git add scripts/services/trend_leader/constants.py scripts/services/trend_leader/scanner.py scripts/tests/test_trend_leader_concept_main.py
git commit -m "fix(trend-leader): require persistent SW mainline sectors"
```

commit body 说明 What/Why、历史不足降级和实际 TDD 微循环数；测试与实现同 commit。

## Task 2：让默认 hybrid 的 LLM 失败路径 fail-closed

**Files:**

- Modify: `scripts/services/trend_leader/mainline_llm.py:21-71`
- Modify: `scripts/services/trend_leader/scanner.py:168-213`
- Modify: `scripts/cli/trend_leader.py:153-162,190-198`
- Modify: `scripts/tests/test_trend_leader_concept_main.py:209-269`
- Modify: `scripts/tests/test_trend_leader_cli.py` after current line 176

- [ ] **Step 1: 把旧 mechanical fallback 测试改成 fail-closed RED**

将旧 `test_hybrid_llm_bad_output_falls_back_to_mechanical_concepts` 改为：

```python
def test_hybrid_llm_bad_output_closes_concept_branch(conn):
    _seed_concentration(conn, "2026-06-09", ["半导体"])
    reg = _branch_reg("其他电子Ⅱ")

    def bad_runner(prompt, payload):
        return {"accepted_concepts": ["输入外概念"]}

    summary = scanner.run_daily(
        conn, reg, "2026-06-09", main_line="hybrid", top_concepts=5,
        mainline_llm_runner=bad_runner,
    )

    assert "301628" not in summary["entered"]
    assert summary["main_concepts"] == []
    assert summary["mainline_llm"]["status"] == "fallback_l2"
    assert summary["mainline_llm"]["reason"] == "invalid_output"
    assert "mainline_llm" in summary["source_errors"]
```

再新增：

- runner 抛异常；runner 返回 `None` 且 `last_diagnostics.reason="timeout"`。
- 输出命中 `REDLINE_KEYWORDS` 时 fail-closed。
- 合法空集合保持 `status=ok`。
- `hybrid` 无 runner 保留机械概念且 `status=disabled`。
- `l2+concept` 与 `l2` 都为 `not_applicable`。
- `ok` / `disabled` / `not_applicable` 均不得产生虚假的 `mainline_llm` source error。
- 在成功 runner 内断言 `payload["main_sectors"] == ["半导体", "通信设备", "元件", "光学光电子"]`，锁定目标有效快照排名顺序。

- [ ] **Step 2: 写 CLI 启动失败诊断 RED**

```python
def test_mainline_runner_startup_failure_returns_diagnostic_runner(monkeypatch):
    from services.research_digest import narrator

    def fail():
        raise RuntimeError("agy missing")

    monkeypatch.setattr(narrator, "build_antigravity_runner", fail)
    runner = tl._mainline_llm_runner(_daily_args(main_line="hybrid", no_llm=False))

    assert callable(runner)
    assert runner("prompt", {}) is None
    assert runner.last_diagnostics == {
        "reason": "startup_failed",
        "message": "agy missing",
    }
```

并断言 `no_llm=True` 与非 `hybrid` 仍返回 `None`。

- [ ] **Step 3: 验证 RED**

```bash
python3 -m pytest \
  scripts/tests/test_trend_leader_concept_main.py \
  scripts/tests/test_trend_leader_cli.py \
  -k 'llm or mainline_runner' -v
```

Expected: 旧逻辑继续让概念票入池、状态仍为 `fallback`，CLI 启动失败返回 `None`，因此行为断言 FAIL。

- [ ] **Step 4: 最小实现 LLM fail-closed 和诊断原因**

`mainline_llm.py` 复用现有诊断工具并把有序板块作为 `Sequence[str]` 接口：

```python
from collections.abc import Sequence
from utils.antigravity_diagnostics import diag_reason

# filter_concepts(..., main_sectors: Sequence[str], ...)
payload = {
    "date": date,
    "main_sectors": list(dict.fromkeys(main_sectors or [])),
    "main_concepts": sorted(concepts),
    "candidates": candidates,
}
```

调用异常或 `_parse_result` 返回 `None` 时统一：

```python
meta.update({
    "status": "fallback_l2",
    "reason": diag_reason(runner) or "runner_exception",  # parse 分支默认 invalid_output
    "accepted_concepts": [],
    "rejected": [],
})
return set(), meta
```

异常分支默认 `runner_exception`，解析分支默认 `invalid_output`；两者都优先使用 `diag_reason(runner)`。合法空集合走现有成功分支，保持 `ok`。无 runner 仍返回机械集合和 `disabled`。更新 docstring，明确“无 runner=人工机械模式；runner 失败=概念关闭”。

- [ ] **Step 5: 修正 scanner 状态初始化与 source error**

非 `hybrid` 初始元数据使用：

```python
{
    "enabled": False,
    "status": "not_applicable",
    "accepted_concepts": sorted(main_concepts),
    "rejected": [],
}
```

仅当 `status == "fallback_l2"` 时追加 `source_errors += ["mainline_llm"]`。`hybrid --no-llm` 由 `filter_concepts(... runner=None)` 产出 `disabled` 且不报故障。

- [ ] **Step 6: 合并 CLI 重复 helper 并返回诊断 runner**

删除文件末尾重复的 `_mainline_llm_runner`。唯一实现的异常分支：

```python
except Exception as exc:  # noqa: BLE001
    message = str(exc)
    logger.warning("[trend-leader] mainline LLM runner 初始化失败: %s", message)

    def unavailable(_prompt, _payload):
        return None

    unavailable.last_diagnostics = {
        "reason": "startup_failed",
        "message": message,
    }
    return unavailable
```

必须先把 `str(exc)` 保存到局部变量，避免 Python 在 `except` 后清理异常变量导致 closure 失效。

- [ ] **Step 7: 验证 GREEN 与显式机械模式回归**

```bash
python3 -m pytest scripts/tests/test_trend_leader_concept_main.py scripts/tests/test_trend_leader_cli.py -v
```

Expected: 全部 PASS；`hybrid` 故障不放行概念，`hybrid --no-llm` / `l2+concept` 仍机械放行，申万稳定主线不受 LLM 否决。

- [ ] **Step 8: G1 第一大阶段门1、提交与门2**

先运行等价 `/simplify` 的人工复查（复用、重复 helper、状态分支、类型一致性），不得在 simplify 与门1之间 commit；重跑 Step 7，并对 Task 2 working tree 执行门1代码审查。门1四条结束条件满足后，根 agent 先提交 Task 2：

```bash
git add scripts/services/trend_leader/mainline_llm.py scripts/services/trend_leader/scanner.py \
  scripts/cli/trend_leader.py scripts/tests/test_trend_leader_concept_main.py \
  scripts/tests/test_trend_leader_cli.py
git commit -m "fix(trend-leader): close concepts when LLM filtering fails"
```

随后 G4 门2用阶段基线审已提交的 Task 1+2，确保不会漏掉刚才的 working tree：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait --base "$IMPLEMENTATION_BASE" \
  "重点:主线稳定门/LLM fail-closed/模式兼容/边界/测试缺口/隐私"
```

严重/高必须修；中等必须修、defer 或代码注释反驳；轻微至少记录。修订后重跑定向测试，以具体路径创建 follow-up commit，再用同一 `--base` 复审。

> 完成标准：两份定向 pytest 全绿 + 门1四条结束条件 + 门2六条结束条件全部满足，才能进入下一大阶段。

## Task 3：修复报告可观测性与 launchd PATH

**Files:**

- Modify (G1): `scripts/services/trend_leader/renderer.py:38-59`
- Modify (G1): `scripts/tests/test_trend_leader_renderer.py:37-47` and new status tests
- Modify (G2): `deploy/launchd/trend-leader-runner.sh:13-15`
- Create (G2): `scripts/tests/test_trend_leader_launchd.py`

- [ ] **Step 1: G1 写 renderer RED**

扩充 `_summary` 默认字段，并新增：

```python
def test_render_daily_shows_stable_window_and_preserves_sector_order(conn):
    md = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        main_sectors=["半导体", "通信设备", "元件", "光学光电子"],
        main_sector_window=3,
        main_sector_required_hits=2,
        main_sector_snapshot_count=3,
        main_sector_source_date="2026-07-14",
        main_sector_status="exact",
    ))
    assert "申万二级近3个有效快照 Top-K 至少2次∪手工" in md
    assert "半导体、通信设备、元件、光学光电子" in md


def test_render_daily_llm_failure_explains_closed_branch(conn):
    md = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={
            "enabled": True,
            "status": "fallback_l2",
            "reason": "startup_failed",
            "accepted_concepts": [],
        },
    ))
    assert "LLM调用失败，概念分支已关闭（原因：startup_failed）" in md


def test_render_daily_distinguishes_disabled_and_valid_empty(conn):
    disabled = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={"enabled": False, "status": "disabled", "accepted_concepts": ["PCB概念"]},
    ))
    valid_empty = renderer.render_daily(conn, _summary(
        main_line="hybrid",
        mainline_llm={"enabled": True, "status": "ok", "accepted_concepts": []},
    ))
    assert "人工禁用 LLM，使用机械概念分支" in disabled
    assert "LLM未确认概念分支" in valid_empty
```

再新增来源状态文案：`fallback + source_date=2026-07-13` 必须显示实际回退日；`missing + source_date=None` 必须显示“无可用集中度快照，仅保留手工板块”，不得声称“已回退最近一日”；`snapshot_count=1` 必须提示历史仅 1 条有效快照。

- [ ] **Step 2: G2 写 launchd RED**

新建纯静态测试，不 source/执行真实 runner：

```python
from pathlib import Path


def test_trend_leader_runner_path_includes_user_local_bin_first():
    repo = Path(__file__).resolve().parents[2]
    script = (repo / "deploy/launchd/trend-leader-runner.sh").read_text()
    assert 'export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"' in script
```

- [ ] **Step 3: 分别验证 RED**

```bash
python3 -m pytest scripts/tests/test_trend_leader_renderer.py -k 'stable_window or llm_failure or distinguishes' -v
python3 -m pytest scripts/tests/test_trend_leader_launchd.py -v
```

Expected: renderer 文案仍显示旧 Top-K/fallback，runner PATH 缺 `$HOME/.local/bin`，两组均 FAIL。

- [ ] **Step 4: G1 最小实现 renderer 状态标签**

新增小 helper 将状态映射为中文：

```python
def _llm_status_text(meta: dict) -> str | None:
    status = meta.get("status")
    if status == "fallback_l2":
        return f"LLM调用失败，概念分支已关闭（原因：{meta.get('reason') or 'unknown'}）"
    if status == "disabled":
        return "人工禁用 LLM，使用机械概念分支"
    if status == "ok" and not (meta.get("accepted_concepts") or []):
        return "LLM未确认概念分支"
    if status == "ok":
        return "LLM概念过滤：成功"
    if status == "skipped_empty_concepts":
        return "无可供 LLM 过滤的机械概念"
    return None
```

主线板块标签从 summary 读取窗口、实际有效快照数、门槛、来源日与状态：

- `exact`：目标日可用；若实际数小于窗口，追加“历史仅 N 条有效快照”。
- `fallback`：显示“目标日不可用，使用 YYYY-MM-DD 快照”；同样显示历史不足。
- `missing`：显示“无可用集中度快照，仅保留手工板块”，绝不使用旧的“已回退最近一日”。

只在 `main_line == "hybrid"` 且 helper 有文案时显示 LLM 行。

- [ ] **Step 5: G2 最小修复 PATH**

```bash
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
```

只改 PATH，不改仓库根、env 加载、push 或执行命令。

- [ ] **Step 6: 验证 GREEN 和最小环境解析**

```bash
python3 -m pytest scripts/tests/test_trend_leader_renderer.py scripts/tests/test_trend_leader_launchd.py -v
env -i HOME="$HOME" PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
  /bin/bash -c 'command -v agy'
```

Expected: pytest 全部 PASS；最后一条只输出 `/Users/alyx/.local/bin/agy`。不得实际调用 `agy`。

- [ ] **Step 7: Task 3 阶段审查**

Task 3 属独立实质性阶段。G1/G2 定向测试转绿后，根 agent 先做简化复查并重跑 Step 6，再执行门1和 G4 门2（working tree 模式）：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait \
  "重点:报告状态准确性/完全缺失不误报/launchd PATH/测试隔离/安全"
```

所有 findings 按门禁处置并复测，才能进入文档阶段。

- [ ] **Step 8: 根 agent 串行提交展示和运行环境**

G1/G2 subagent 均不得操作 Git；由根 agent 在审查通过后按文件范围串行创建两个提交：

```bash
git add scripts/services/trend_leader/renderer.py scripts/tests/test_trend_leader_renderer.py
git commit -m "fix(trend-leader): explain stable mainline and LLM status"

git add deploy/launchd/trend-leader-runner.sh scripts/tests/test_trend_leader_launchd.py
git commit -m "fix(launchd): expose local agy to trend leader"
```

## Task 4：同步 Agent、Skill 与索引文档

**Files:**

- Modify: `AGENTS.md` trend-leader entry
- Modify: `CLAUDE.md` trend-leader entry
- Modify: `.agents/skills/market-tasks/SKILL.md:198-225`
- Modify: `.agents/skills/INDEX.md:87`
- Modify: `.agents/rules/skills-sync.md:108`

- [ ] **Step 1: 按最终行为同步五份真源**

文档必须一致表达：

- 自动申万二级为最近最多 3 个有效集中度快照 Top-K 至少 2 次；仅 1 条历史时降为 1 次。
- `--sectors` 绕过稳定门并按输入顺序追加。
- 默认 `hybrid` 的 LLM 失败关闭概念分支；不是机械 fallback。
- `hybrid --no-llm` 和 `l2+concept` 才保留机械概念分支。
- `fallback_l2` 会记录 `source_errors=mainline_llm` 并在报告显示原因。
- 不自动清理既有池、不改交易计划/关注池。

`.agents/rules/skills-sync.md` 的 trend-leader 映射行补充必须核对“三日稳定门、失败关闭概念、显式机械模式、既有池不追溯”四项。

- [ ] **Step 2: 检查 Skill agent metadata**

```bash
find .agents/skills/market-tasks -path '*/agents/openai.yaml' -print
```

若存在，只检查 display/description/default prompt 是否仍准确；本次 skill 目标未变，除非语义明显过期，否则不改。

- [ ] **Step 3: 文档一致性与占位符扫描**

```bash
rg -n "机械概念分支|确定性 l2\+concept|异常或红线|三日|fallback_l2" \
  AGENTS.md CLAUDE.md .agents/skills/market-tasks/SKILL.md \
  .agents/skills/INDEX.md .agents/rules/skills-sync.md
rg -n "TODO|TBD|待补|placeholder" \
  AGENTS.md CLAUDE.md .agents/skills/market-tasks/SKILL.md \
  .agents/skills/INDEX.md .agents/rules/skills-sync.md
```

Expected: 不再把默认 LLM 失败描述为机械 fallback；没有本计划新增的占位符。

- [ ] **Step 4: 根 agent 提交文档同步**

G3 subagent 不操作 Git；根 agent 检查 diff 后执行：

```bash
git add AGENTS.md CLAUDE.md .agents/skills/market-tasks/SKILL.md \
  .agents/skills/INDEX.md .agents/rules/skills-sync.md
git commit -m "docs(trend-leader): document stable mainline fallback"
```

提交前确认没有带入主工作区已有的 string-yang、慧博或其他无关改动。

## Task 5：整体验证、阶段审查与交付核对

**Files:** Read-only verification across the branch; fixes return to the owning group.

- [ ] **Step 1: 运行定向测试**

```bash
python3 -m pytest \
  scripts/tests/test_trend_leader_concept_main.py \
  scripts/tests/test_trend_leader_renderer.py \
  scripts/tests/test_trend_leader_cli.py \
  scripts/tests/test_trend_leader_launchd.py \
  scripts/tests/test_cli_smoke.py -v
```

Expected: 全部 PASS；不得新增 warning。现有 `urllib3` LibreSSL 环境 warning 可记录为既有，不得归因于本改动。

- [ ] **Step 2: 运行仓库后端统一检查**

```bash
make check-scripts
```

Expected: 退出码 0。任何失败先定位是否基线已有；本 worktree 基线的 287 个定向用例已于实施前通过。

- [ ] **Step 3: 验证 7 月 14 日固定 fixture 和模式矩阵**

用内存库/测试 fixture 验证：

| 场景 | 稳定申万 | 概念 | 状态 |
| --- | --- | --- | --- |
| 3 快照下 7/14 Top5 | 半导体、通信设备、元件、光学光电子 | 不适用 | `IT服务Ⅱ` 排除 |
| hybrid + LLM 合法子集 | 稳定门结果 | 接受子集 | `ok` |
| hybrid + LLM 合法空 | 稳定门结果 | 空 | `ok` |
| hybrid + LLM 故障 | 稳定门结果 | 空 | `fallback_l2` + source error |
| hybrid --no-llm | 稳定门结果 | 机械 Top-M | `disabled` |
| l2+concept | 稳定门结果 | 机械 Top-M | `not_applicable` |
| l2 | 稳定门结果 | 空 | `not_applicable` |

同时断言只有 `fallback_l2` 追加 `source_errors=mainline_llm`；其余五种状态均不产生假 LLM source error。再覆盖恰好 2 条有效快照、空/全未分类记录不占窗口、目标日无效回退、完全缺失、手工覆盖不稳定板块。

- [ ] **Step 4: 第二大阶段门1和门2审查**

先做简化/复用复查并重跑 Step 1，再运行门1和：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait --base "$IMPLEMENTATION_BASE" \
  "重点:主线判定正确性/LLM失败收口/launchd环境/报告误导/测试缺口/文档一致性/安全"
```

按仓库门禁处置所有 findings；任何代码修订后重跑 Step 1～2。审查最多 3 轮，第三轮仍有严重问题则停下报告用户。

- [ ] **Step 5: 检查分支范围与用户改动隔离**

```bash
git status --short --branch
git diff --check 20b30d9a..HEAD
git diff --stat 20b30d9a..HEAD
git diff --name-only 20b30d9a..HEAD
```

Expected: 仅包含本计划声明的代码、测试、launchd 和文档文件；无 SQLite/YAML/运行报告、密钥、缓存或主工作区无关改动。

- [ ] **Step 6: 最终完成标准**

- 7 月 14 日 fixture 中 `IT服务Ⅱ` 被排除，目标日稳定板块顺序保留。
- LLM 故障时 `main_concepts=[]`，报告明确“概念分支已关闭”并显示原因。
- 显式机械模式保持兼容，既有池不追溯修改。
- 最小 launchd PATH 下 `command -v agy` 成功，且没有执行外部 LLM。
- 定向测试、CLI smoke、`make check-scripts` 全绿。
- Skills 同步检查：INDEX、market-tasks、AGENTS/CLAUDE、skills-sync 均已同步。
- 门1四条结束条件、门2六条结束条件均满足；审查结论按“已修/反驳/defer/接受为已知”汇总。

## 测试验证方案

### 分层与隔离

| 层级 | 用例重心 | 隔离方式 | 完成标准 |
| --- | --- | --- | --- |
| 纯业务/数据读取层 | 三日命中、历史不足、目标日回退、手工覆盖、顺序 | `sqlite3 :memory:` + 直接 seed `daily_volume_concentration` | 所有窗口与边界场景通过 |
| LLM/编排层 | 合法子集、合法空、异常/None/越界/红线、模式矩阵 | callable fake runner + FakeRegistry | 故障必收口，显式机械模式不回归 |
| CLI/报告层 | 启动失败诊断、报告状态/原因、命令签名 | monkeypatch builder/pusher/connection | 无外网、无真实 DB、文案不误导 |
| 运行环境层 | runner PATH 包含 `$HOME/.local/bin` | 静态文件断言 + `env -i command -v` | 能定位 `agy`，不实际调用 |
| 仓库回归层 | CLI smoke、后端全套检查、Skill 同步 | 统一 Makefile 入口 | `make check-scripts` 退出码 0 |

测试金字塔以纯业务和内存 DB 用例为主，CLI/运行环境只保留少量契约测试；不增加依赖真实服务的 E2E。

### 验收命令

```bash
python3 -m pytest scripts/tests/test_trend_leader_concept_main.py -v
python3 -m pytest scripts/tests/test_trend_leader_renderer.py scripts/tests/test_trend_leader_cli.py -v
python3 -m pytest scripts/tests/test_trend_leader_launchd.py scripts/tests/test_cli_smoke.py -v
make check-scripts
```

### Warning / Error 标准

- 不接受新增 pytest warning、未处理异常、网络访问、真实推送、真实 DB 写入或敏感信息输出。
- 可记录但不归因于本改动：基线已存在的 `urllib3` / LibreSSL warning。
- `mainline_llm` 故障必须转为可见的 `fallback_l2`，不得静默伪装成 `disabled` 或“今日无概念”。

## 回滚与集成边界

- 回滚仅需回退本分支相关代码/文档/runner PATH；无 schema 或数据回滚。
- 本计划不授权自动清理 `trend_leader_pool` 历史记录，也不授权真实推送。
- 实现完成后先保留在 `codex/fix-trend-leader-mainline-impl`。`trend-leader-runner.sh` 硬编码 `/Users/alyx/tradeSystem`，所以分支验证通过不等于生产定时路径已生效；未合并/集成到该主目录前，最终报告只能写“分支已修复、生产未激活”。是否合并到 `main`、如何处理主工作区同文件的用户改动，按用户后续指令执行。

## 方案审查结论

只读方案审查初审为 `修订后推进`；两轮复核指出的问题均已按下列条目修订，当前 `verdict=可推进`。审查过程未改文件、未调用外部服务。

- [高·已修订] “有效快照”原算法按 DB 行数计门槛：现已定义有效性、跳过空/全未分类记录、目标日无效回退，并补对应测试。
- [高·已修订] 多 Agent 共享 worktree 各自提交存在 index/HEAD 竞态：现已规定 subagent 只编辑和定向测试，根 agent 独占全部 Git 操作。
- [中·已修订] Task 3 审查被推迟：现已在 renderer/launchd 阶段后立即增加门1和门2。
- [中·已修订] LLM payload 仍会排序：现已要求 `Sequence[str]` 原序传递并用 runner 断言锁定。
- [中·已修订] 完全缺失会误报回退：现已增加 `status/source_date/snapshot_count` 并区分 exact/fallback/missing 文案。
- [中·已修订] 测试缺口与重复诊断 helper：现已补恰好两条、红线、模式/source-error 矩阵、手工覆盖，并复用 `diag_reason`。
- [中·已修订] 执行体与生产激活边界不清：现已改为实际 Codex root/collaboration/reviewer，并明确分支完成不代表硬编码主目录已上线。
- [中·已修订] 未提交 Task 2 使用 `--base` 会漏审：现改为门1审 working tree，根 agent 提交后门2按 `IMPLEMENTATION_BASE` 审完整阶段 diff。
- [低·接受为已知] 极端长期全无效历史会逐条查询，最坏 O(N) 次 SQLite；日级记录规模且只在异常数据时触发，本次不增加分页 API。
- [低·接受为已知] `date` 列若破坏 ISO 数据契约会在 cursor 解析时报错；该表主键由标准采集写入，本次不扩展到损坏数据库修复。

六项并行反模式均未命中；修订后可进入实现。
