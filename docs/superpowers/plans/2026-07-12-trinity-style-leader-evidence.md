# Trinity Style and Leader Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `style_regime` and `leader_signal` honest, independently grouped objective evidence so both can enter the dominant-factor competition without inflating evidence quality.

**Architecture:** Reuse the exact-date review prefill and split existing composite facts into compact evidence cards. Add an internal `quality_group` lineage key, exclude non-`ok` facts from quality and LLM reference whitelists, then replace exact-JSON leader T+1 comparison with structural continuity while retaining old-run fallbacks.

**Tech Stack:** Python 3.9+, SQLite, FastAPI/Pydantic, pytest, existing Antigravity/Gemini CLI runner, React/Vitest for regression verification.

---

## File map

- Modify `scripts/services/trinity_factor/evidence.py`: evidence-card builders, status cards, lineage-aware quality, compact LLM cards, ruleset/schema versions.
- Modify `scripts/services/trinity_factor/cycle.py`: new/legacy style signatures and structural leader T+1 comparison.
- Modify `scripts/tests/test_trinity_factor_orchestration.py`: evidence IDs, status semantics, quality grouping, compact-input and cache assertions.
- Modify `scripts/tests/test_trinity_factor_cycle.py`: new and legacy T+1 comparison coverage.
- Modify `scripts/tests/test_api.py`: DB → review prefill → score-run integration assertion using production-shaped fields.
- Modify `.agents/skills/daily-review/SKILL.md`: document the objective style/leader source families and judgement boundary.
- Modify `.agents/skills/INDEX.md`: keep review-factor dependency description synchronized.

## Task 0: Checkpoint the already-reviewed repair diff

**Files:** Existing modified files listed by `git status`; no new production behavior in this task.

- [ ] **Step 1: Run the existing targeted backend suite**

Run:

```bash
python3 -m pytest \
  scripts/tests/test_trinity_factor_service.py \
  scripts/tests/test_trinity_factor_llm.py \
  scripts/tests/test_trinity_factor_orchestration.py \
  scripts/tests/test_trinity_factor_cycle.py \
  scripts/tests/test_trinity_factor_runner.py \
  scripts/tests/test_trinity_factor_review_input.py \
  scripts/tests/test_api.py \
  scripts/tests/test_cli_smoke.py -q
```

Expected: exit 0 with no failed tests.

- [ ] **Step 2: Run the existing Web verification**

Run: `make check-web`

Expected: Vitest, TypeScript and Vite build all exit 0.

- [ ] **Step 3: Check whitespace and stage only the known repair files**

Run:

```bash
git diff --check
git add \
  .agents/skills/INDEX.md \
  .agents/skills/daily-review/SKILL.md \
  scripts/.env.example \
  scripts/api/routes/review.py \
  scripts/api/routes/review_factors.py \
  scripts/cli/review_factors.py \
  scripts/services/trinity_factor/cycle.py \
  scripts/services/trinity_factor/evidence.py \
  scripts/services/trinity_factor/review_input.py \
  scripts/services/trinity_factor/runner.py \
  scripts/services/trinity_factor/validation.py \
  scripts/tests/test_api.py \
  scripts/tests/test_review_factors_cli.py \
  scripts/tests/test_trinity_factor_cycle.py \
  scripts/tests/test_trinity_factor_orchestration.py \
  scripts/tests/test_trinity_factor_review_input.py \
  scripts/tests/test_trinity_factor_runner.py \
  web/src/lib/api.ts
git diff --cached --check
```

Expected: only the known repair diff is staged; design/plan commits remain separate.

- [ ] **Step 4: Commit the repair checkpoint**

Run:

```bash
git commit -m "fix(trinity-factor): close shadow scoring review gaps"
```

Expected: one commit; worktree becomes clean before the new TDD cycle.

## Task 1: Build lineage-aware style and leader evidence cards

**Files:**

- Modify: `scripts/tests/test_trinity_factor_orchestration.py`
- Modify: `scripts/services/trinity_factor/evidence.py`

- [ ] **Step 1: Write failing evidence-contract tests**

Add production-shaped fixtures containing `prev_market`, `continuous_board_counts`, `style_factors.promotion`, and consecutive-only popularity rows. Assert the exact semantic IDs and quality:

```python
def test_style_regime_uses_three_independent_quality_groups() -> None:
    snapshot = build_evidence_snapshot("2026-07-10", _prefill(), {})
    style = next(
        row for row in snapshot["factor_candidates"]
        if row["factor_code"] == "style_regime"
    )
    assert {
        item["evidence_id"] for item in style["evidence_items"]
        if item.get("kind") == "fact"
    } == {
        "2026-07-10:style_regime:cap_relative_strength",
        "2026-07-10:style_regime:board_preference",
        "2026-07-10:style_regime:premium_regime",
    }
    assert style["objective_source_count"] == 3
    assert style["evidence_quality"] == 4


def test_leader_cards_share_two_lineage_groups() -> None:
    snapshot = build_evidence_snapshot("2026-07-10", _prefill(), {})
    leader = next(
        row for row in snapshot["factor_candidates"]
        if row["factor_code"] == "leader_signal"
    )
    assert [
        item["source"] for item in leader["evidence_items"]
        if item.get("kind") == "fact"
    ] == ["ladder_structure", "promotion_realization", "prior_core_feedback"]
    assert leader["objective_source_count"] == 2
    assert leader["evidence_quality"] == 3
```

Add separate tests proving:

- two cards with the same `quality_group` count once;
- `missing/source_failed/source_ok_empty/rule_filtered_empty` do not count;
- a promotion card with `trade_date != target date` is rejected as stale;
- `failed_names`, full popularity rows and `quality_group` are absent from `build_factor_llm_input`;
- `leader_detection` remains non-objective.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_trinity_factor_orchestration.py \
  -k "style_regime_uses_three or leader_cards_share or quality_group or stale or compact" -q
```

Expected: failures show the old single `style_factors` card, old single ladder source, and source-string quality counting.

- [ ] **Step 3: Implement compact source builders**

In `evidence.py`, add `_style_objective_items(trade_date, market)` and
`_leader_objective_items(trade_date, prefill, ladder_rows)` as focused helpers.
Both return `list[dict[str, Any]]`; the first emits only the three style cards,
and the second emits the ladder, promotion and prior-core cards. Extend
`_fact_item` with the exact implementation below:

```python
_STYLE_PREMIUM_KEYS = (
    "first_board", "first_board_10cm", "first_board_20cm",
    "first_board_30cm", "second_board", "third_board_plus",
    "capacity_top10",
)
def _fact_item(
    evidence_id: str,
    source: str,
    layer: str,
    content: Any,
    *,
    quality_group: str | None = None,
    source_status: str = "ok",
    polarity: str = "support",
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source": source,
        "quality_group": quality_group or source,
        "source_status": source_status,
        "layer": layer,
        "kind": "fact",
        "polarity": polarity,
        "content": _json_safe_value(content),
    }
```

Required card contents:

```python
cap_content = {
    "csi300_chg": cap["csi300_chg"],
    "csi1000_chg": cap["csi1000_chg"],
    "spread": cap["spread"],
    "relative": cap["relative"],
}

board_content = {
    "dominant_type": board["dominant_type"],
    "pct_10cm": board.get("pct_10cm"),
    "pct_20cm": board.get("pct_20cm"),
    "pct_30cm": board.get("pct_30cm"),
}

premium_content = {
    "groups": {
        key: {
            "count": value.get("count"),
            "premium_median": value.get("premium_median"),
            "open_up_rate": value.get("open_up_rate"),
        }
        for key, value in snapshot.items()
        if key in _STYLE_PREMIUM_KEYS and isinstance(value, Mapping)
    },
    "trend_direction": trend.get("direction"),
    "capacity_proxy": True,
}
```

Parse `continuous_board_counts` from JSON/dict, keep only tiers `>=2`, and produce stable sorted `tier_counts`, `top_tier_names`, `highest_board` and `consecutive_count`. Aggregate prior feedback with `statistics.median`; only rows whose source includes `consecutive` are eligible, and previous highest-tier names are preferred when available.

Promotion output must contain only `base/promoted/rate` and bounded `promoted_names`; never pass `failed_names`.

- [ ] **Step 4: Implement lineage-aware quality and controlled LLM projection**

Use only valid facts:

```python
def _valid_fact_items(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        item for item in items
        if item.get("kind") == "fact" and item.get("source_status") == "ok"
    ]


def _quality_group(item: Mapping[str, Any]) -> str:
    return str(item.get("quality_group") or item.get("source") or "")
```

Calculate `critical_missing`, `objective_source_count`, quality and allowed fact IDs from `_valid_fact_items`. Keep review judgement IDs allowed, but exclude non-`ok` objective cards.

Before returning `_factor_llm_card`, copy each visible item and remove `quality_group`. Do not expose internal status-only cards, caps or quality metadata.

Bump:

```python
RULESET_VERSION = "trinity_ruleset_v2"
SERVICE_SCHEMA_VERSION = "trinity_dual_score_run_v2"
```

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python3 -m pytest scripts/tests/test_trinity_factor_orchestration.py -q`

Expected: all orchestration tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add scripts/services/trinity_factor/evidence.py \
  scripts/tests/test_trinity_factor_orchestration.py
git commit -m "feat(trinity-factor): add independent style and leader evidence"
```

## Task 2: Replace exact-JSON T+1 comparison and preserve legacy runs

**Files:**

- Modify: `scripts/tests/test_trinity_factor_cycle.py`
- Modify: `scripts/services/trinity_factor/cycle.py`

- [ ] **Step 1: Write failing style and leader T+1 tests**

Add complete tests using the existing `_insert_run`, `Q.upsert_daily_review`,
`_style_prefill`, `_leader_prefill` and `suggest_t1_evaluation` helpers. The
style hit test body is:

```python
def test_t1_new_style_cards_match_all_dimensions(conn) -> None:
    source_prefill = _style_prefill(
        date="2026-07-10", cap="偏小盘", board="20cm"
    )
    actual_prefill = _style_prefill(
        date="2026-07-13", cap="偏小盘", board="20cm"
    )
    source_prefill["market"]["style_factors"]["premium_trend"] = {
        "direction": "走强"
    }
    actual_prefill["market"]["style_factors"]["premium_trend"] = {
        "direction": "走强"
    }
    _insert_run(
        conn,
        primary="style_regime",
        evidence_snapshot=build_evidence_snapshot(
            "2026-07-10", source_prefill, {}
        ),
    )
    Q.upsert_daily_review(
        conn, "2026-07-13", {"step1_market": {"notes": "已复盘"}}
    )
    conn.commit()
    suggestion = suggest_t1_evaluation(
        conn,
        evaluation_trade_date="2026-07-13",
        source_review_date="2026-07-10",
        score_run_id="run-1",
        prefill=actual_prefill,
    )
    assert suggestion["system_outcome"] == "hit"
    assert suggestion["actual_evidence_json"]["comparison"]["matched_dimensions"] == 3
```

For leader continuity, extend `_leader_prefill` so source day has highest tier
`{"3": ["龙头A"]}` and the actual day has `{"4": ["龙头A"]}` plus a
`prior_core_feedback` row with positive close change. Insert the source run,
create the target review, call `suggest_t1_evaluation`, and assert `hit` with
`positive_dimensions >= 2`. Repeat with only identity positive for `partial`,
no positive dimensions for `miss`, and absent source structure for
`missing_data`. Finally create hand-built old snapshots containing
`source=style_factors` and `source=limit_ladder` to prove legacy evaluation
still works.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest scripts/tests/test_trinity_factor_cycle.py \
  -k "new_style or leader_continuity or legacy_style or legacy_leader" -q
```

Expected: new style cards produce no common signature and leader comparisons still depend on whole-JSON equality.

- [ ] **Step 3: Implement new/legacy style signatures**

`_style_signature` must first read:

```python
cap = _fact_content(factor, "cap_relative_strength")
board = _fact_content(factor, "board_preference")
premium = _fact_content(factor, "premium_regime")
```

Extract `cap_preference`, `board_preference`, and `premium_trend`. If all are absent, fall back to the old composite `source=style_factors` behavior.

- [ ] **Step 4: Implement leader structural comparison**

Create a signature containing `highest_board`, `top_tier_names` and actual-day prior-core feedback. Compare source and actual as follows:

```python
height_positive = actual_highest >= source_highest
identity_positive = bool(source_names & actual_names)
feedback_positive = (
    actual_feedback.get("limit_up_count", 0) > 0
    or (actual_feedback.get("median_close_change_pct") or 0) >= 0
)
```

Count only dimensions with real values. Return:

- `hit` when at least two dimensions are positive;
- `partial` when exactly one is positive;
- `miss` when comparable dimensions exist and none is positive;
- `missing_data` when source or actual structure is unavailable.

For old `limit_ladder` content, normalize `name/nums` rows into the same height/name signature before comparing.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `python3 -m pytest scripts/tests/test_trinity_factor_cycle.py -q`

Expected: all cycle tests pass, including old-run fixtures.

- [ ] **Step 6: Commit Task 2**

```bash
git add scripts/services/trinity_factor/cycle.py \
  scripts/tests/test_trinity_factor_cycle.py
git commit -m "fix(trinity-factor): evaluate style and leader continuity structurally"
```

## Task 3: Verify DB-to-score integration and synchronize docs

**Files:**

- Modify: `scripts/tests/test_api.py`
- Modify: `.agents/skills/daily-review/SKILL.md`
- Modify: `.agents/skills/INDEX.md`

- [ ] **Step 1: Write the failing integration assertion**

Extend the production-field score test so its inserted `daily_market.raw_data` contains `style_factors`, while the row contains `highest_board` and `continuous_board_counts`. Invoke `POST /api/review-factors/{date}/score` with `no_llm=true`, read the stored score run, and assert:

```python
factors = {
    row["factor_code"]: row
    for row in stored["evidence_snapshot_json"]["factor_candidates"]
}
assert factors["style_regime"]["evidence_quality"] == 4
assert factors["leader_signal"]["evidence_quality"] >= 3
assert stored["ruleset_version"] == "trinity_ruleset_v2"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `python3 -m pytest scripts/tests/test_api.py -k "review_factor_score_uses_production" -q`

Expected: old evidence cards produce quality 2 and ruleset v1.

- [ ] **Step 3: Complete integration fixture and documentation**

Document in `daily-review/SKILL.md`:

- style facts are index-relative strength, board mix and realized premium;
- leader facts are non-ST ladder and strict T-1→T outcome;
- Step 5 and automatic leader names remain judgement context;
- all scores remain relative importance, not probability or advice.

Update the corresponding review-factor description in `.agents/skills/INDEX.md` without changing CLI/API commands.

- [ ] **Step 4: Run integration and smoke tests**

```bash
python3 -m pytest scripts/tests/test_api.py -k "review_factor" -q
python3 -m pytest scripts/tests/test_cli_smoke.py -q
```

Expected: exit 0 for both commands.

- [ ] **Step 5: Commit Task 3**

```bash
git add scripts/tests/test_api.py \
  .agents/skills/daily-review/SKILL.md \
  .agents/skills/INDEX.md
git commit -m "docs(review): describe style and leader evidence lineage"
```

## Task 4: Full verification, independent review, and real 20-day replay

**Files:** No production edits unless a failing regression receives its own RED test first.

- [ ] **Step 1: Run focused and full backend verification**

```bash
python3 -m pytest \
  scripts/tests/test_trinity_factor_service.py \
  scripts/tests/test_trinity_factor_llm.py \
  scripts/tests/test_trinity_factor_orchestration.py \
  scripts/tests/test_trinity_factor_cycle.py \
  scripts/tests/test_trinity_factor_runner.py \
  scripts/tests/test_trinity_factor_review_input.py \
  scripts/tests/test_api.py \
  scripts/tests/test_cli_smoke.py -q
python3 -m pytest scripts/tests/ -q
```

Expected: zero failures. Record the exact pass count and any pre-existing warnings.

- [ ] **Step 2: Run Web and repository checks**

```bash
make check-web
git diff --check
```

Expected: Web tests/typecheck/build and diff check exit 0. Run `make check-scripts`; if its known cwd-only launchd failure remains, verify the isolated test from repository root and report the distinction rather than claiming the Make target passed.

- [ ] **Step 3: Run two-stage independent review**

Dispatch one reviewer for spec compliance and a different reviewer for code quality/security. Any actionable finding must be fixed through a failing regression test and re-reviewed.

- [ ] **Step 4: Create a read-only production copy and run real scoring**

```bash
TMP_DB="/tmp/trinity-factor-style-leader-$(date +%Y%m%d%H%M%S).db"
sqlite3 /Users/alyx/tradeSystem/data/trade.db ".backup '$TMP_DB'"
for D in 2026-06-12 2026-06-15 2026-06-16 2026-06-17 2026-06-18 \
         2026-06-22 2026-06-23 2026-06-24 2026-06-25 2026-06-26 \
         2026-06-29 2026-06-30 2026-07-01 2026-07-02 2026-07-03 \
         2026-07-06 2026-07-07 2026-07-08 2026-07-09 2026-07-10; do
  TRADE_DB_PATH="$TMP_DB" python3 scripts/main.py review factor-score \
    --date "$D" --input-by codex-shadow-style-leader
done
```

Expected: 20 completed score runs in the temporary database; production DB schema/version and row counts remain unchanged.

- [ ] **Step 5: Query and report the replay**

Report:

- model, prompt/ruleset/schema versions and total duration;
- call success, invalid-output and fallback rates;
- per-factor evidence-quality coverage;
- primary/supporting distribution and `undetermined` reasons;
- second-layer priority/watch/deprioritized totals;
- daily primary result and priority sectors;
- before/after comparison against the prior 20-day run;
- detailed 2026-07-02 scores and evidence references;
- data-quality limitations and the fact that no human-confirmed T+1 sample means this is not win-rate validation.

- [ ] **Step 6: Verify production DB was not modified**

Check production `PRAGMA user_version`, absence/count of factor tables, file modification time and score-run counts before/after. Report exact evidence.

- [ ] **Step 7: Final diff and branch handoff**

Run:

```bash
git status --short --branch
git log --oneline -6
git diff HEAD^ --check
```

Expected: only intentional commits, no generated DB/report artifacts tracked, and no push performed without explicit user instruction.
