# Daily Leaders Role-Aware Candidate Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `daily-leaders propose` output at most 15 stock-unique candidates, with real sector assignment and at most one winner for each sector and semantic leader role, even when LLM review fails.

**Architecture:** Add a pure selection module that owns stock normalization, semantic roles, bounded review-pool construction, and final hard constraints. Candidate collection supplies factual sector, board, amount, and limit-height fields; the LLM only reviews a 30-item pool, while service orchestration always runs deterministic final selection and preserves actionable failure diagnostics.

**Tech Stack:** Python 3.9, argparse, sqlite3, pytest, existing provider registry, Antigravity CLI adapter, Markdown renderer.

**Spec Source:** `docs/superpowers/specs/2026-07-14-daily-leaders-role-cap-design.md`

---

## Scope and Independent Changes

共 4 项独立改动：

1. 角色选择核心（中，20–30 分钟）：`scripts/services/daily_leaders/selection.py`、`models.py`、`scripts/tests/test_daily_leaders_selection.py`。
2. 板块事实与 service 编排（中，20–30 分钟）：`candidates.py`、`service.py`、候选/service 测试。
3. LLM、渲染与 CLI 契约（中，20–30 分钟）：`llm.py`、`renderer.py`、`cli/daily_leaders.py`、对应测试。
4. 文档同步、真实回放与仓库验收（中，20–30 分钟）：`AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、索引规则及验证命令。

总预估超过 20 分钟，文件范围可拆且没有命中同文件/短任务逃逸条款，因此采用并行分组。

## Parallel Groups

### G1 — 角色选择核心

- **角色（role）：** 后端 + 测试（主角色：后端）
- **执行 Agent（executor）：** Claude Code subagent（在当前 Codex 环境等价使用协作 subagent）
- **职责边界（responsibility）：** 交付纯函数选择模块、固定角色枚举、股票规范键和硬约束测试。
- **文件范围（files）：** `scripts/services/daily_leaders/selection.py`、`scripts/services/daily_leaders/models.py`、`scripts/tests/test_daily_leaders_selection.py`。
- **禁区（off-limits）：** 允许改上述三文件；不得改 `service.py`、`llm.py`、renderer/CLI/文档；跨组接口变化需先通知主 agent。
- **冲突标注：** `models.py` 为 G1 唯一归属；其他组只读取并按本文契约引用。

### G2 — 板块事实与编排

- **角色（role）：** 后端 + 测试（主角色：后端）
- **执行 Agent（executor）：** Claude Code subagent（在当前 Codex 环境等价使用协作 subagent）
- **职责边界（responsibility）：** 申万二级映射、板型/连板事实进入候选，并接入 G1 选择函数。
- **文件范围（files）：** `scripts/services/daily_leaders/candidates.py`、`scripts/services/daily_leaders/service.py`、`scripts/tests/test_daily_leaders_candidates.py`、`scripts/tests/test_daily_leaders_service.py`。
- **禁区（off-limits）：** 允许改上述四文件；不得改 G1/G3 文件、CLI 和文档；不得调用真实外网写测试。
- **冲突标注：** `service.py` 为 G2 唯一归属；G3 不修改该文件。

### G3 — LLM、渲染与 CLI

- **角色（role）：** 后端 + 测试（主角色：后端）
- **执行 Agent（executor）：** Claude Code subagent（在当前 Codex 环境等价使用协作 subagent）
- **职责边界（responsibility）：** 更新 LLM 枚举/错误分类、报告文案和 CLI 15 条硬上限。
- **文件范围（files）：** `scripts/services/daily_leaders/llm.py`、`scripts/services/daily_leaders/renderer.py`、`scripts/cli/daily_leaders.py`、`scripts/tests/test_daily_leaders_renderer_store.py`、`scripts/tests/test_daily_leaders_cli.py`。
- **禁区（off-limits）：** 允许改上述五文件；不得改 selection/candidates/service/文档；不得改变 `confirm --input-by` 边界。
- **冲突标注：** `llm.py` 与 `renderer.py` 为 G3 唯一归属；G2 只调用既定接口。

### G4 — 集成、文档与审查

- **角色（role）：** 架构师 + 文档 + 测试（主角色：架构师）
- **执行 Agent（executor）：** Codex 主 agent
- **职责边界（responsibility）：** 合并三个实现组、修复接口偏差、同步文档索引、执行全量验证和两道审查门。
- **文件范围（files）：** `AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、`.agents/skills/INDEX.md`、`.agents/rules/skills-sync.md`、`scripts/tests/test_cli_smoke.py`，以及必要的集成修正。
- **禁区（off-limits）：** 允许改文档/索引与集成测试；不得覆盖子 agent 已完成的正确实现；涉及数据库写入、钉钉推送或 `confirm` 必须先询问用户。
- **冲突标注：** 文档与索引文件仅 G4 修改；若集成必须改 G1–G3 文件，由 G4 在对应 agent 完成后串行修改。

## Shared Contracts

`models.py` 固定导出：

```python
LEADER_ROLES = {"趋势中军", "连板核心", "前排活跃", "弹性前排"}
STATUS_ROLES = {"备选", "剔除"}
BOARD_TYPES = {"10cm", "20cm", "30cm", "非涨停"}
MAX_CONFIRMATION_CANDIDATES = 15
MAX_LLM_REVIEW_CANDIDATES = 30
```

`selection.py` 固定导出：

```python
def normalize_stock_display(value: object) -> str: ...
def stock_identity_key(item: dict[str, object]) -> str: ...
def assign_fallback_roles(items: list[dict[str, object]]) -> list[dict[str, object]]: ...
def prepare_llm_review_pool(items: list[dict[str, object]], limit: int = 30) -> list[dict[str, object]]: ...
def select_confirmation_candidates(
    items: list[dict[str, object]],
    *,
    max_candidates: int = 15,
    llm_ok: bool = False,
) -> tuple[list[dict[str, object]], dict[str, int]]: ...
```

最终候选必须具有 `leader_role`、`attribute_type`、`board_type`、`selection_basis`；`attribute_type == leader_role`。

## Test Pyramid

| Layer | Tests | Isolation | Completion standard |
| --- | --- | --- | --- |
| Pure selection | normalization, fallback role, group uniqueness, stock uniqueness, 15 cap | dictionaries only | all boundary tests pass |
| Candidate/service | SW map, limit-step, orchestration order, LLM fallback | fake registry + monkeypatch | no network/real DB dependency |
| Adapter/renderer/CLI | prompt enum, timeout status, Markdown, CLI bounds | injected runner / mocked subprocess | exact contract assertions pass |
| Repository | CLI smoke and `make check-scripts` | local environment | zero failures; existing LibreSSL warning tolerated |

## Task 1: Pure Role-Aware Selection Core (G1)

**Files:**
- Create: `scripts/services/daily_leaders/selection.py`
- Modify: `scripts/services/daily_leaders/models.py`
- Create: `scripts/tests/test_daily_leaders_selection.py`

- [ ] **Step 1: Write failing normalization and cap tests**

```python
from services.daily_leaders.selection import (
    assign_fallback_roles,
    normalize_stock_display,
    prepare_llm_review_pool,
    select_confirmation_candidates,
)


def test_normalize_stock_display_collapses_repeated_tokens():
    assert normalize_stock_display("协创数据 协创数据 协创数据 协创数据") == "协创数据"
    assert normalize_stock_display("601138 工业富联") == "601138 工业富联"


def test_select_confirmation_candidates_is_unique_by_sector_role_and_stock():
    items = [
        {"stock": "甲", "sector": "元件", "fallback_role": "前排活跃", "llm_role": "前排活跃", "llm_rank": 2, "_selection_score": 90},
        {"stock": "乙", "sector": "元件", "fallback_role": "前排活跃", "llm_role": "前排活跃", "llm_rank": 1, "_selection_score": 80},
        {"stock": "乙", "sector": "PCB", "fallback_role": "弹性前排", "llm_role": "弹性前排", "llm_rank": 3, "_selection_score": 70},
    ]

    selected, stats = select_confirmation_candidates(items, llm_ok=True)

    assert [(row["stock"], row["sector"], row["leader_role"]) for row in selected] == [
        ("乙", "元件", "前排活跃")
    ]
    assert stats["sector_role_trimmed_count"] == 1
    assert stats["stock_duplicate_trimmed_count"] == 1


def test_select_confirmation_candidates_never_exceeds_fifteen():
    items = [
        {"stock": f"股票{i}", "sector": f"板块{i}", "fallback_role": "前排活跃", "_selection_score": 100 - i}
        for i in range(20)
    ]
    selected, _ = select_confirmation_candidates(items, max_candidates=15, llm_ok=False)
    assert len(selected) == 15
    assert all(row["selection_basis"] == "deterministic_fallback" for row in selected)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_daily_leaders_selection.py -v
```

Expected: collection fails because `services.daily_leaders.selection` does not exist.

- [ ] **Step 3: Implement constants and normalization**

Implement the shared constants exactly as listed under “Shared Contracts”. `normalize_stock_display` must split whitespace, collapse only the case where every token is identical, and preserve `code name` displays. `stock_identity_key` must prefer normalized `stock_code`/`code`, then normalized stock display.

- [ ] **Step 4: Implement fallback roles and bounded review pool**

```python
def _fallback_role(item):
    if int(item.get("limit_height") or 0) >= 2:
        return "连板核心"
    if item.get("is_sector_amount_leader"):
        return "趋势中军"
    if item.get("board_type") in {"20cm", "30cm"}:
        return "弹性前排"
    return "前排活跃"
```

`assign_fallback_roles` first computes the highest valid `amount_yi` stock per sector, then applies the priority above. `prepare_llm_review_pool` keeps at most two rows per `(sector, fallback_role)` and at most 30 globally, ordered by `_selection_score` descending with stable input-order tie breaking.

- [ ] **Step 5: Implement final selection**

Validate `1 <= max_candidates <= 15`. Use legal LLM role only when `llm_ok`; treat `剔除` as excluded and `备选` as the fallback role with lower priority. Sort by LLM rank then deterministic score when LLM is valid, otherwise deterministic score only. Apply `(sector, final_role)` uniqueness, then global stock uniqueness, then final cap. Return trim statistics.

- [ ] **Step 6: Verify GREEN and commit**

```bash
python3 -m pytest scripts/tests/test_daily_leaders_selection.py -v
git add scripts/services/daily_leaders/models.py scripts/services/daily_leaders/selection.py scripts/tests/test_daily_leaders_selection.py
git commit -m "feat(daily-leaders): enforce sector-role candidate selection"
```

Expected: all selection tests pass.

## Task 2: Sector Facts and Service Orchestration (G2)

**Files:**
- Modify: `scripts/services/daily_leaders/candidates.py`
- Modify: `scripts/services/daily_leaders/service.py`
- Modify: `scripts/tests/test_daily_leaders_candidates.py`
- Modify: `scripts/tests/test_daily_leaders_service.py`

- [ ] **Step 1: Write failing sector and orchestration tests**

Add assertions equivalent to:

```python
def test_quote_strength_uses_sw_l2_sector_and_separate_board_type():
    prefill = {"market": {"stock_quotes": {"data": [
        {"code": "688041.SH", "name": "海光信息", "pct_chg": 20.0, "amount_yi": 88.0, "sw_l2": "半导体"}
    ]}}}
    result = build_candidates(prefill=prefill, trend_pool=[], history=[])
    item = result["top_leaders"][0]
    assert item["sector"] == "半导体"
    assert item["board_type"] == "20cm"
    assert item["source_sector"] == "日内强势"


def test_propose_llm_failure_still_caps_and_groups_candidates(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "build_candidates", lambda **kwargs: {"date": "2026-07-14", "top_leaders": MANY_DUPLICATE_GROUP_ROWS})
    monkeypatch.setattr(service, "enrich_with_llm_reason", lambda proposal, **kwargs: {**proposal, "llm_status": {"ok": False, "reason": "timeout"}})
    result = service.propose(conn, "2026-07-14", PREFILL, output_root=tmp_path, registry=FAKE_REGISTRY)
    assert len(result["top_leaders"]) <= 15
    assert len({(x["sector"], x["leader_role"]) for x in result["top_leaders"]}) == len(result["top_leaders"])
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m pytest scripts/tests/test_daily_leaders_candidates.py scripts/tests/test_daily_leaders_service.py -v
```

Expected: new sector/board/selection assertions fail against current behavior.

- [ ] **Step 3: Enrich market quote facts**

In `attach_market_quotes`, call `get_stock_sw_industry_map` once. Build full-code and bare-code indexes. Add `sw_l2` and `sector_source` to stock quote rows. Normalize `limit_step` into maps by code/name and add `limit_height` to matching quote rows. Provider failures remain best-effort and are recorded under `market.stock_industry_map` / existing market metadata without aborting proposal generation.

- [ ] **Step 4: Emit factual candidate fields**

For quote candidates, set:

```python
{
    "sector": row.get("sw_l2") or "未分类",
    "source_sector": "日内强势",
    "board_type": _board_attribute(code, pct_chg),
    "amount_yi": amount_yi,
    "pct_chg": pct_chg,
    "limit_height": int(row.get("limit_height") or 0),
}
```

For market-flow candidates, preserve the original concept/industry in `source_sector`, but replace `sector` with matched quote `sw_l2` when available. Keep enough numeric facts to compute `_selection_score` before removing internal keys.

- [ ] **Step 5: Orchestrate selection around LLM**

`propose` order becomes:

```python
proposal = build_candidates(...)
proposal["top_leaders"] = assign_fallback_roles(proposal["top_leaders"])
proposal["top_leaders"] = prepare_llm_review_pool(proposal["top_leaders"])
proposal = enrich_with_llm_reason(proposal, enabled=not no_llm)
proposal["top_leaders"], selection_stats = select_confirmation_candidates(
    proposal.get("top_leaders") or [],
    max_candidates=max_candidates,
    llm_ok=bool((proposal.get("llm_status") or {}).get("ok")),
)
proposal["candidate_limit"] = {**pool_stats, **selection_stats, "max_candidates": max_candidates}
```

When `no_llm=True`, set `llm_status={"ok": False, "reason": "disabled"}` before deterministic selection so the renderer can accurately describe fallback.

- [ ] **Step 6: Keep confirm compatible**

`_confirmed_step5_leaders` prefers a legal `leader_role`, then a legal new `llm_role`, then existing `attribute_type`. It must not accept `备选/剔除` as persisted leader attributes.

- [ ] **Step 7: Verify GREEN and commit**

```bash
python3 -m pytest scripts/tests/test_daily_leaders_candidates.py scripts/tests/test_daily_leaders_service.py -v
git add scripts/services/daily_leaders/candidates.py scripts/services/daily_leaders/service.py scripts/tests/test_daily_leaders_candidates.py scripts/tests/test_daily_leaders_service.py
git commit -m "feat(daily-leaders): enrich sectors and orchestrate bounded review"
```

Expected: all candidate/service tests pass without network calls.

## Task 3: LLM Diagnostics, Renderer, and CLI Contract (G3)

**Files:**
- Modify: `scripts/services/daily_leaders/llm.py`
- Modify: `scripts/services/daily_leaders/renderer.py`
- Modify: `scripts/cli/daily_leaders.py`
- Modify: `scripts/tests/test_daily_leaders_renderer_store.py`
- Modify: `scripts/tests/test_daily_leaders_cli.py`

- [ ] **Step 1: Write failing prompt, timeout, renderer and CLI tests**

```python
def test_llm_prompt_uses_semantic_roles_not_board_types():
    def runner(prompt):
        assert "趋势中军|连板核心|前排活跃|弹性前排|备选|剔除" in prompt
        assert '"role": "趋势中军|小票弹性（连板）|20cm' not in prompt
        return {}
    enrich_with_llm_reason(_proposal(), runner=runner)


def test_llm_timeout_is_classified(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("agy", 180)))
    out = enrich_with_llm_reason(_proposal())
    assert out["llm_status"]["reason"] == "timeout"


def test_renderer_describes_deterministic_fallback_and_role_board_split():
    proposal = _proposal_with_timeout_and_selected_candidate()
    md = render_markdown(proposal)
    assert "已按确定性板块/属性规则兜底收敛" in md
    assert "最票属性：趋势中军" in md
    assert "板型：20cm" in md


def test_cli_default_and_upper_bound():
    args = _parser().parse_args(["daily-leaders", "propose"])
    assert args.max_candidates == 15
    with pytest.raises(SystemExit):
        _parser().parse_args(["daily-leaders", "propose", "--max-candidates", "16"])
```

- [ ] **Step 2: Verify RED**

```bash
python3 -m pytest scripts/tests/test_daily_leaders_renderer_store.py scripts/tests/test_daily_leaders_cli.py -v
```

Expected: prompt/timeout/renderer/default assertions fail.

- [ ] **Step 3: Update LLM contract and diagnostics**

Use `LEADER_ROLES` from `models.py`; retain `备选/剔除` only as status roles. Catch `subprocess.TimeoutExpired` in `_run_llm` and return `_llm_error("timeout", f"timeout_seconds={...}; log_file={log_file}")`. Catch `json.JSONDecodeError` around response parsing and return `invalid_json`. Preserve a short `detail` in `llm_status`, including the log path, while renderer shows only the reason.

- [ ] **Step 4: Update renderer**

Render separate lines:

```text
- 最票属性：趋势中军
- 板型：20cm
- 入选方式：[判断] LLM复核 / 确定性兜底
```

The candidate summary includes original count, stock-deduped count, sector-role trimmed count, final count, and hard cap 15. For `disabled`, say LLM was intentionally skipped; for other failures, say LLM review did not complete and deterministic constraints were applied.

- [ ] **Step 5: Enforce CLI range**

Add an argparse type function:

```python
def _candidate_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 15:
        raise argparse.ArgumentTypeError("--max-candidates 必须在 1 到 15 之间")
    return parsed
```

Set `default=15` and update help text.

- [ ] **Step 6: Verify GREEN and commit**

```bash
python3 -m pytest scripts/tests/test_daily_leaders_renderer_store.py scripts/tests/test_daily_leaders_cli.py -v
git add scripts/services/daily_leaders/llm.py scripts/services/daily_leaders/renderer.py scripts/cli/daily_leaders.py scripts/tests/test_daily_leaders_renderer_store.py scripts/tests/test_daily_leaders_cli.py
git commit -m "fix(daily-leaders): bound LLM review and expose fallback status"
```

Expected: adapter/renderer/CLI tests pass.

## Task 4: Integration, Documentation, and Verification (G4)

**Files:**
- Modify: `scripts/tests/test_cli_smoke.py`
- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `.agents/skills/market-tasks/SKILL.md`
- Modify: `.agents/skills/INDEX.md`
- Modify: `.agents/rules/skills-sync.md`

- [ ] **Step 1: Integrate and run focused tests**

```bash
python3 -m pytest \
  scripts/tests/test_daily_leaders_selection.py \
  scripts/tests/test_daily_leaders_candidates.py \
  scripts/tests/test_daily_leaders_service.py \
  scripts/tests/test_daily_leaders_renderer_store.py \
  scripts/tests/test_daily_leaders_cli.py -v
```

Expected: all focused tests pass; fix interface discrepancies without weakening assertions.

- [ ] **Step 2: Update CLI smoke**

Add valid smoke coverage for `--max-candidates 15` and parser-level rejection in the CLI test for `16`. Run:

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

Expected: all smoke cases pass.

- [ ] **Step 3: Synchronize external behavior docs**

Update daily-leaders descriptions to state:

- final hard cap 15;
- actual SW L2 sector where available;
- semantic roles `趋势中军/连板核心/前排活跃/弹性前排`;
- board type shown separately;
- same sector + same role keeps one winner;
- LLM reviews a bounded pool and deterministic fallback keeps the same hard constraints.

Because `.agents/skills/market-tasks/SKILL.md` changes, update `.agents/skills/INDEX.md` and `.agents/rules/skills-sync.md` in the same commit.

- [ ] **Step 4: Replay 2026-07-14 locally without external writes**

First run deterministic mode:

```bash
python3 scripts/main.py daily-leaders propose --date 2026-07-14 --no-llm
python3 scripts/main.py daily-leaders show --date 2026-07-14 --json
```

Verify with a read-only assertion command that `count <= 15`, stock keys are unique, `(sector, leader_role)` keys are unique, and `selection_basis=deterministic_fallback`. Do not pass `--push` and do not run `confirm`.

Then run the normal local proposal once to verify the reduced prompt can complete. If the external LLM still fails, accept a correctly classified `timeout/nonzero_exit/...` only if the final deterministic constraints remain valid; do not treat external availability as a unit-test failure.

- [ ] **Step 5: Run repository verification**

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
make check-scripts
git diff --check
```

Expected: zero failures. The existing `urllib3 NotOpenSSLWarning` is tolerated; new warnings, tracebacks, malformed Markdown, or hidden network dependency in tests are not.

- [ ] **Step 6: Commit docs and integration**

```bash
git add scripts/tests/test_cli_smoke.py AGENTS.md CLAUDE.md .agents/skills/market-tasks/SKILL.md .agents/skills/INDEX.md .agents/rules/skills-sync.md
git commit -m "docs(daily-leaders): document role-aware 15-stock confirmation"
```

- [ ] **Step 7: Run simplify and review gates**

Follow `.agents/rules/code-review-gate.md`: simplify changed code, run the code-review gate until no high/medium findings remain or the two-round soft cap is reached. Then run the native adversarial review from `.agents/rules/post-dev-codex-review.md`, addressing valid findings for at most three rounds.

- [ ] **Step 8: Final verification before completion**

Re-run focused tests and `make check-scripts` after the final review fix. Report exact pass counts, real replay candidate count, LLM status, group uniqueness, branch, and unpushed status.

## Plan Self-Review

- Spec coverage: all five confirmed decisions map to Tasks 1–4.
- Placeholder scan: no placeholder markers or deferred implementation steps remain.
- Type consistency: roles, board types, limits, selection exports, and service orchestration signatures match the shared contracts.
- 范围：不含 DB migration、API route、推送或实际 `confirm` 写回。门2审查后仅补充 `confirm` 事务前校验与 Web 可选 `stock_code` 类型契约；未执行生产写入。

## 门2审查追加修订（2026-07-15）

- [x] RED：`leaders-file` 超过 15 条、规范股票身份重复、同板块同属性重复均会在旧实现中被放行。
- [x] GREEN：`_confirmed_step5_leaders` 在事务前拒绝上述输入，失败后 `daily_reviews` 与 `leader_tracking` 保持零写入。
- [x] RED/GREEN：确认转换保留规范裸 6 位 `stock_code`，tracking 优先按代码 upsert；旧 payload 无代码继续回退 `stock`。
- [x] 契约同步：复盘 Web 类型增加可选 `stock_code`，skills / INDEX / AGENTS / CLAUDE 同步确认护栏语义。
- [x] 门2第二轮：统一提案/确认/跟踪的 Unicode 空白压缩板块键；旧名称型 tracking 身份在确认事务内迁移，旧键与代码键并存时合并为单行。
