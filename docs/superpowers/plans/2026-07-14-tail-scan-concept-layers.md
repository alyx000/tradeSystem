# 尾盘实时筛选两层概念 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `tail-scan daily` 同时展示扫描时当前的完整窄概念归属与 T-1 资金流热概念命中，并修正容器概念占位和字段语义混淆。

**Architecture:** 新增 stock-centric provider capability，以一次 `ths_index(type="N")` 目录请求加 N 次 `ths_member(con_code=...)` 反查候选股票概念；新增 `concept_context` 统一完成成员数过滤、Top8 热概念选择和逐票状态。`scorer` 保留原热概念兼容字段并增加 `stock_concept_*` 字段，`industry_logic` 使用完整归属，`renderer` 分两行展示；完整归属及仅由其命中的 report-only 产业证据均不进入粗分或 PK。

**Tech Stack:** Python 3、pytest、Tushare `ths_index` / `ths_member` / `moneyflow_cnt_ths`、现有 `ProviderRegistry` / `DataResult`、Markdown / DingTalk。

---

## 规格与硬约束

- 设计真源：`docs/superpowers/specs/2026-07-14-tail-scan-concept-layers-design.md`。
- `归属概念` 是运行时当前快照，不得声称历史 as-of。
- `T-1热概念命中` 绑定严格上一交易日；先剔 `company_num>300`，再补足 Top8。
- 保留 `concept_names` / `concept_status` / `in_hot_concept` 原语义；只增加 `stock_concept_*` 字段。
- 完整归属概念只用于报告和行业证据匹配；仅由它命中的行业证据标记为 report-only，不进入 `_coarse_score()` 或 PK prompt。
- 不改 CLI 参数、筛选阈值、粗分权重、PK、DB、API、调度和计划层。
- 外网和密钥在单测中必须隔离；真实运行只用于最终抽检与已授权推送。

## 独立改动项与耗时

共 5 项独立改动：

| # | 改动项 | 预估 | 主要文件 |
|---|---|---:|---|
| A | 股票中心概念 capability 与 provider 测试 | 中，20–30 分钟 | `scripts/providers/base.py`、`scripts/providers/tushare_provider.py`、`scripts/tests/test_stock_concept_memberships.py`、`scripts/tests/test_tushare_p0_interfaces.py` |
| B | 两层概念上下文、scorer/产业逻辑接线与测试 | 长，30–60 分钟 | `scripts/services/tail_scan/concept_context.py`、`scorer.py`、`industry_logic.py`、`constants.py`、相关测试 |
| C | 两层概念 Markdown 展示与推送预算回归 | 中，15–30 分钟 | `scripts/services/tail_scan/renderer.py`、`scripts/tests/test_tail_scan_renderer.py` |
| D | Agent/Skill/索引文档同步 | 中，10–20 分钟 | `AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、`.agents/skills/INDEX.md`、`.agents/rules/skills-sync.md` |
| E | 全量验证、双门审查、真实 2026-07-13 覆盖推送 | 中，20–40 分钟 | 测试命令、git diff、运行产物 |

`N=5`、总时长超过 20 分钟、文件跨 provider/service/renderer/docs，不命中逃逸条款。A→B→C 有契约依赖，按 subagent-driven development 串行；D 可在 C 语义稳定后执行；E 由根 agent 统一完成。

## 执行分组

| 分组 | 角色 | 执行 Agent | 专项关注 | 职责边界 | 文件范围 | 禁区 | 冲突标注 |
|---|---|---|---|---|---|---|---|
| G1 Provider | 后端+测试 | Codex collaboration 写入子代理 | 性能、逐票可观测性 | 新 capability、Tushare 实现、provider 单测 | `base.py`、`tushare_provider.py`、两个 provider 测试 | 不得改 tail-scan service、renderer、docs；全局 registry 语义需先询问 | Provider 文件唯一归 G1 |
| G2 Context | 后端+测试 | Codex collaboration 写入子代理 | T-1、防看未来、事实边界 | `concept_context`、scorer、industry 接线和测试 | `tail_scan/{concept_context,scorer,industry_logic,constants}.py` 与对应测试 | 不得改 provider、renderer、PK、CLI、DB | Service 文件唯一归 G2 |
| G3 Renderer | 后端+测试 | Codex collaboration 写入子代理 | 标签准确、推送长度 | 两层报告行、状态文案、免责声明、测试 | `renderer.py`、`test_tail_scan_renderer.py` | 不得改 scorer/provider/权重/PK | Renderer 文件唯一归 G3 |
| G4 Docs | 文档 | Codex collaboration 写入子代理 | Skill/Agent 语义同步 | 同步 5 个真源文档 | 列出的 5 个文档 | 不得改代码、README、CLI 签名 | 文档唯一归 G4 |
| G5 Verify | 架构师+测试 | Codex 根 agent | 合规、可观测性 | 检查 diff、运行完整测试、规格审查、质量审查、真实推送 | 只读检查；审查修订回原实现代理 | 不得新增需求或带入主仓库脏改动 | 审查点唯一归 G5 |

每个 G1–G3 阶段完成且阶段测试通过后，立刻依次执行：规格合规审查 → 代码质量审查；发现问题必须回到同一实现代理修复并复审。阶段完成还需映射执行门1（简化检查 + 代码审查）和门2（独立对抗审查），不得全部攒到最后。

## Task 1：新增股票中心概念 capability

**Files:**

- Modify: `scripts/providers/base.py`
- Modify: `scripts/providers/tushare_provider.py`
- Create: `scripts/tests/test_stock_concept_memberships.py`
- Modify: `scripts/tests/test_tushare_p0_interfaces.py`

- [ ] **Step 1: 写失败测试**

新增以下行为测试：

```python
def test_stock_concept_memberships_queries_one_catalog_and_each_stock_once():
    provider = _provider_with_concept_reverse_stub()
    result = provider.get_stock_concept_memberships(
        ["605090", "600428.SH", "605090.SH"]
    )
    assert result.success
    assert provider.pro.ths_index_calls == [{"type": "N"}]
    assert provider.pro.ths_member_calls == [
        {"con_code": "605090.SH"},
        {"con_code": "600428.SH"},
    ]


def test_stock_concept_memberships_filters_non_concept_rows_and_maps_count():
    result = _provider_with_mixed_types().get_stock_concept_memberships(["605090.SH"])
    assert result.data["stocks"]["605090.SH"] == {
        "status": "ok",
        "concepts": [{
            "concept_code": "885372.TI",
            "name": "页岩气",
            "member_count": 40,
        }],
    }


def test_stock_concept_memberships_isolates_single_stock_failure():
    result = _provider_with_one_member_failure().get_stock_concept_memberships(
        ["605090.SH", "600428.SH"]
    )
    assert result.success
    assert result.data["stocks"]["605090.SH"]["status"] == "ok"
    assert result.data["stocks"]["600428.SH"]["status"] == "source_failed"
    assert result.data["stocks"]["600428.SH"]["error"] == "member request failed"


def test_stock_concept_memberships_normalizes_invalid_catalog_counts():
    result = _provider_with_invalid_counts().get_stock_concept_memberships(
        ["605090.SH"]
    )
    assert result.success
    assert result.data["stocks"]["605090.SH"]["concepts"] == [
        {"concept_code": "885372.TI", "name": "页岩气", "member_count": 40}
    ]
```

同时保留并运行既有 `test_get_ths_member_uses_concept_index_scope` 和 `test_get_ths_member_filters_by_concept_names`。
再补充：未初始化 provider 顶层失败、清洗后全空目录顶层失败、`member_df is None` 逐票失败、
真空 DataFrame 逐票 `missing`、capability 只在 Tushare 注册，以及 Registry 无可用 provider 时返回明确顶层失败。

- [ ] **Step 2: 验证 RED**

Run：

```bash
python3 -m pytest scripts/tests/test_stock_concept_memberships.py -v
```

Expected：因 `get_stock_concept_memberships` 尚不存在而 FAIL；不得是 fixture、导入或拼写错误。

- [ ] **Step 3: 最小实现**

在 `DataProvider` 增加：

```python
def get_stock_concept_memberships(self, ts_codes: list[str]) -> DataResult:
    return DataResult(data=None, source=self.name, error="not implemented")
```

在 `TushareProvider.get_capabilities()` 注册，并实现以下固定流程：

```python
def get_stock_concept_memberships(self, ts_codes: list[str]) -> DataResult:
    normalized = []
    for raw in ts_codes or []:
        code = self._normalize_stock_code(raw)
        if code and code not in normalized:
            normalized.append(code)
    if not normalized:
        return DataResult(data={"stocks": {}}, source="tushare:ths_member:by_stock")

    guard = self._ensure_pro("get_stock_concept_memberships")
    if guard is not None:
        return guard
    catalog_df = self.pro.ths_index(type="N")
    if catalog_df is None or catalog_df.empty:
        return DataResult(data=None, source=self.name, error="同花顺概念目录为空")
    catalog = {
        clean_code(row.get("ts_code")): {
            "name": clean_text(row.get("name")),
            "member_count": normalize_positive_count(row.get("count")),
        }
        for row in self._df_to_records(catalog_df)
        if clean_code(row.get("ts_code")) and clean_text(row.get("name"))
    }
    if not catalog:
        return DataResult(data=None, source=self.name, error="同花顺概念目录清洗后为空")

    stocks = {}
    for code in normalized:
        try:
            member_df = self.pro.ths_member(con_code=code)
            if member_df is None:
                raise RuntimeError("member response is None")
        except Exception as exc:
            stocks[code] = {
                "status": "source_failed", "concepts": [], "error": clean_text(exc)
            }
            continue
        concepts = []
        for item in self._df_to_records(member_df):
            meta = catalog.get(str(item.get("ts_code") or ""))
            if meta:
                concepts.append({
                    "concept_code": str(item["ts_code"]),
                    "name": meta["name"],
                    "member_count": meta["member_count"],
                })
        concepts = list({row["concept_code"]: row for row in concepts}.values())
        stocks[code] = {"status": "ok" if concepts else "missing", "concepts": concepts}
    return DataResult(data={"stocks": stocks}, source="tushare:ths_member:by_stock")
```

实际实现需用仓库现有日志和异常风格；不得修改旧 `get_ths_member`。
`normalize_positive_count` 必须防御 `None` / 空串 / `NaN` / 无穷大 / 负数，非法值归一为 `0`；
后续上下文按 `0<member_count<=300` fail-closed 过滤，不得因 `int(float("nan"))` 中断整批。
`clean_code/clean_text` 必须复用现有 scalar 清洗语义，不得生成字符串 `"nan"`。

- [ ] **Step 4: 验证 GREEN 与回归**

```bash
python3 -m pytest scripts/tests/test_stock_concept_memberships.py scripts/tests/test_tushare_p0_interfaces.py -v
```

Expected：全部 PASS；断言旧接口仍按 `ts_code` 正向查询，新接口仅按 `con_code` 反查。

- [ ] **Step 5: 阶段审查与提交**

规格审查必须确认返回结构、逐票失败和旧接口兼容；质量审查必须检查代码规范化、去重、空目录、异常隔离和无全概念循环。修订后重跑 Step 4。

提交：`feat(providers): add stock concept membership lookup`，实现与测试同 commit，body 记录实际 TDD 微循环数。

## Task 2：构建两层概念上下文并接入 scorer

**Files:**

- Create: `scripts/services/tail_scan/concept_context.py`
- Modify: `scripts/services/tail_scan/constants.py`
- Modify: `scripts/services/tail_scan/scorer.py`
- Modify: `scripts/services/tail_scan/industry_logic.py`（仅必要的参数语义/调用说明）
- Create: `scripts/tests/test_tail_scan_concept_context.py`
- Modify: `scripts/tests/test_tail_scan_scorer.py`
- Modify: `scripts/tests/test_tail_scan_industry_logic.py`（仅完整概念证据匹配回归）
- Modify: `scripts/tests/test_tail_scan_pk.py`（仅内部 membership 不泄漏回归）

- [ ] **Step 1: 写失败测试**

至少覆盖：

```python
def test_hot_concepts_filter_containers_before_filling_top8():
    rows = [
        {"name": "AI智能体", "net_amount_yi": 100, "company_num": 449},
        {"name": "百度概念", "net_amount_yi": 99, "company_num": 234},
        {"name": "DeepSeek概念", "net_amount_yi": 98, "company_num": 773},
        *narrow_rows(7),
    ]
    names, status = select_hot_concepts(rows, top_m=8)
    assert names == ["百度概念", *narrow_names(7)]
    assert status == "ok"


def test_context_separates_full_memberships_from_hot_hits():
    result = build_concept_context(
        registry_with_memberships({"688106.SH": [
            concept("存储芯片", 180), concept("第三代半导体", 160), concept("融资融券", 3800)
        ]}),
        ["688106.SH"],
        concept_date="2026-07-10",
        top_m=8,
    )
    row = result["688106.SH"]
    assert row["stock_concept_names"] == ["第三代半导体", "存储芯片"]
    assert row["stock_concept_total"] == 2
    assert row["concept_names"] == []
    assert row["concept_status"] == "ok"


def test_complete_memberships_do_not_change_coarse_score():
    base = _card(in_hot_concept=False, stock_concept_names=[])
    enriched = {**base, "stock_concept_names": ["存储芯片", "第三代半导体"]}
    assert scorer._coarse_score(base) == scorer._coarse_score(enriched)
```

并补：成员数 300 保留/301 剔除、资金流空、`company_num` 为 `None` / 空串 / `NaN` /
无穷大 / 负数、脏宽度位于 Top8 填满路径/已填满后的尾部、脏净流入、单票 membership 失败、
热概念顺序、完整概念传入 `industry_logic`。
具名新增 `test_prev_date_failure_still_fetches_current_memberships`，断言当前归属调用一次、
不调 T-1 资金流、归属状态仍为 `ok`、只有热概念为 `source_failed`，且完整归属仍传入产业逻辑。

- [ ] **Step 2: 验证 RED**

```bash
python3 -m pytest scripts/tests/test_tail_scan_concept_context.py scripts/tests/test_tail_scan_scorer.py -v
```

Expected：新模块/字段不存在或旧 `_hot_concepts` 先截 Top8 导致具名断言 FAIL。

- [ ] **Step 3: 实现 concept context**

新增展示常量，容器上限复用共享真源：

```python
from services.concept_tags import CONTAINER_MAX_MEMBERS

CONCEPT_CONTAINER_MAX_MEMBERS = CONTAINER_MAX_MEMBERS
CONCEPT_AFFILIATION_DISPLAY_MAX = 5
CONCEPT_HOT_DISPLAY_MAX = 2
```

加相等性测试，禁止在 tail-scan 维护第二个独立 `300` 真源。

`concept_context.py` 公开接口固定为
`build_concept_context(registry, ts_codes: list[str], *, concept_date: str | None, top_m: int) -> dict[str, dict]`
和 `rank_stock_concepts(memberships: list[dict], hot_names: list[str], semantic_texts: list[str]) -> list[str]`。

实现必须：

- 调 `get_stock_concept_memberships(ts_codes)` 一次；保留 `source/fetched_at`。
- 当 `concept_date is None` 时仍必须调用当前 `get_stock_concept_memberships`；只跳过 T-1 资金流并将热概念状态标为 `source_failed`。
- `concept_date` 有效时调 T-1 `get_concept_moneyflow_ths` 一次；空列表视为 `source_failed`。
- `select_hot_concepts(rows, top_m) -> tuple[list[str], str]` 先把净流入解析为有限浮点数（负数合法，
  `NaN` / 无穷大 / 非法字符串跳过）并稳定降序；再按顺序扫描、剔容器并填满 `top_m`。
  只在 `top_m` 尚未填满时遇到非法 `company_num` 才 fail-closed 为空名单 + `coverage_failed`；
  已填满后的脏尾部不得击穿结果。原始空列表是 `source_failed`，非空源扫描结束仍不足 `top_m` 是 `coverage_failed`。
- 每票热命中严格按热榜顺序。
- 归属概念只保留 `0<member_count<=C.CONCEPT_CONTAINER_MAX_MEMBERS`，其值是共享真源的显式别名。
- 每票上下文内部保留 `stock_concept_memberships: list[dict]`，供主营返回后按相关性重排；
  该内部字段必须在组装事实卡时丢弃，不得进入最终 card 或 PK prompt。
- `rank_stock_concepts` 用“热命中优先 → 与主营/产品/行业文本最长公共中文/字母数字片段 → 成员数更少 → 名称”稳定排序；不得新增概念或改名。
  `semantic_texts` 只允许 `sw_l2/business_summary/product_names/industry_position`；有效匹配片段至少 2 个中文字符或 3 个字母数字，
  并过滤“概念/科技/行业/产业/业务”等停用词，避免单字或宽泛词制造虚假相关排序。
- 所有异常收敛为设计规格中的逐票状态，不中断事实卡。
- 状态真值表固定并逐行测试：
  - 全局 `source_failed` + 任意逐票状态 → `concept_status=source_failed`。
  - 全局 `coverage_failed` + 任意逐票状态 → `concept_status=coverage_failed`。
  - 全局 `ok` + membership `source_failed` → `concept_status=member_failed`。
  - 全局 `ok` + membership `missing` → `concept_status=ok`、无热命中。
  - membership 原始 `ok` 但全因 `member_count>300` 或非法宽度被过滤 → `stock_concept_status=missing`。
  - 全局 `ok` + membership `ok` 但无交集 →确定的 `concept_status=ok`。

- [ ] **Step 4: 接入 scorer 与行业证据**

`build_fact_cards()` 顺序调整为：

1. 解析 `prev_date`、主线和大势。
2. 无论 `prev_date` 是否成功，都对候选代码批量构建当前归属上下文；`prev_date` 只控制 T-1 热概念分支。
3. 从内部 `stock_concept_memberships` 取完整名称映射，传给
   `industry_logic.build_industry_logic_map(..., concept_map=full_concept_map)`。
4. 主营/产品/行业逻辑返回后，以原始 membership 元数据、热命中和语义文本调用 `rank_stock_concepts`。
5. 写入兼容热字段与新增 `stock_concept_*` 字段，显式不写入内部 `stock_concept_memberships`。

`_coarse_score()` 保持原样，只读取 `in_hot_concept`。

- [ ] **Step 5: 验证 GREEN 与回归**

```bash
python3 -m pytest scripts/tests/test_tail_scan_concept_context.py scripts/tests/test_tail_scan_scorer.py scripts/tests/test_tail_scan_industry_logic.py -v
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
```

Expected：全部 PASS；原 T-1、防看未来、历史行情、产业逻辑和粗分测试无回归。
新增一个记录调用的 fake registry 集成测试，断言 `build_fact_cards()` 只调用一次
`get_stock_concept_memberships`、不再调用旧 `get_ths_member`；同时更新旧 scorer 测试中的所有方法分派 fake，
未知 capability 必须抛 `AssertionError`，不得默认返回空结果。
再增一个端到端元数据桥测试：断言 `member_count/concept_code` 在 industry logic 返回后仍可用于重排，
`stock_concept_source/snapshot_at` 正确传播，完整概念映射传入 industry logic，最终 card 不含
`stock_concept_memberships`，且 `pk._payload()` 不含该内部字段。

- [ ] **Step 6: 阶段审查与提交**

规格审查逐项核对两层字段、状态和 non-scoring；质量审查重点检查重复 provider 调用、排序稳定性、None/脏数据、错误状态和循环复杂度。修订后重跑 Step 5。

提交：`feat(tail-scan): separate stock and hot concepts`，实现与测试同 commit。

## Task 3：渲染两层概念

**Files:**

- Modify: `scripts/services/tail_scan/renderer.py`
- Modify: `scripts/tests/test_tail_scan_renderer.py`

- [ ] **Step 1: 写失败测试**

```python
def test_render_shows_memberships_and_t1_hot_hits_separately():
    card = {
        **_scored()[0],
        "stock_concept_names": ["天然气", "航运概念", "煤化工概念", "页岩气", "智能物流", "新疆振兴"],
        "stock_concept_total": 6,
        "stock_concept_status": "ok",
        "stock_concept_snapshot_at": "2026-07-14T00:00:00",
        "concept_names": ["航运概念"],
        "concept_status": "ok",
    }
    md = renderer.render_daily(_scan(), [card], None)
    assert "[事实·归属概念]" in md
    assert "当前快照，共6个" in md
    assert "[事实·T-1热概念命中] 航运概念" in md
    assert "概念:" not in md


def test_render_distinguishes_no_hot_hit_from_failed_lookup():
    no_hit = {
        "stock_concept_names": ["天然气"],
        "stock_concept_total": 1,
        "stock_concept_status": "ok",
        "concept_names": [],
        "concept_status": "ok",
    }
    failed = {
        "stock_concept_names": [],
        "stock_concept_total": 0,
        "stock_concept_status": "source_failed",
        "concept_names": [],
        "concept_status": "source_failed",
    }
    no_hit_md = "".join(renderer._render_concept_context(no_hit))
    failed_md = "".join(renderer._render_concept_context(failed))
    assert "未命中上一交易日资金流前8窄概念" in no_hit_md
    assert "无法判断" not in no_hit_md
    assert "概念归属源失败" in failed_md
    assert "无法判断" in failed_md


def test_render_limits_memberships_to_five_but_preserves_total():
    card = {
        "stock_concept_names": ["天然气", "航运概念", "煤化工概念", "页岩气", "智能物流", "新疆振兴"],
        "stock_concept_total": 6,
        "stock_concept_status": "ok",
        "concept_names": [],
        "concept_status": "ok",
    }
    md = "".join(renderer._render_concept_context(card))
    for name in card["stock_concept_names"][:5]:
        assert name in md
    assert "新疆振兴" not in md
    assert "当前快照，共6个" in md
```

- [ ] **Step 2: 验证 RED**

```bash
python3 -m pytest scripts/tests/test_tail_scan_renderer.py -k "concept or push_summary" -v
```

Expected：旧 renderer 只有头行 `概念:`，新两层断言 FAIL。

- [ ] **Step 3: 最小实现**

新增 `_render_concept_context(card)`：

```python
def _render_concept_context(card: dict) -> list[str]:
    if card.get("stock_concept_status") == "source_failed":
        membership = "  - [来源状态·归属概念] 概念归属源失败，本次未取得。\n"
    elif card.get("stock_concept_status") == "missing":
        membership = "  - [来源状态·归属概念] 当前快照暂无可用窄概念。\n"
    else:
        names = [_plain(v) for v in (card.get("stock_concept_names") or [])[:C.CONCEPT_AFFILIATION_DISPLAY_MAX]]
        total = int(card.get("stock_concept_total") or len(names))
        membership = f"  - [事实·归属概念] {' / '.join(names)}（当前快照，共{total}个）\n"

    if card.get("concept_status") != "ok":
        hot = "  - [来源状态·T-1热概念命中] T-1概念资金流或归属数据失败，本次无法判断。\n"
    else:
        hot_names = [_plain(v) for v in (card.get("concept_names") or [])[:C.CONCEPT_HOT_DISPLAY_MAX]]
        hot = (
            f"  - [事实·T-1热概念命中] {' / '.join(hot_names)}\n"
            if hot_names
            else "  - [事实·T-1热概念命中] 未命中上一交易日资金流前8窄概念。\n"
        )
    return [membership, hot]
```

实际实现需防御非 list、非字符串、负数总数和 Markdown 注入。`_candidate_block()` 在产业逻辑前追加两层行，并删除头行 `概念:`。免责声明改为“归属概念=current snapshot；热概念=T-1”。`_degradation_note()` 分别统计归属和热概念失败。

- [ ] **Step 4: 验证 GREEN 与预算回归**

```bash
python3 -m pytest scripts/tests/test_tail_scan_renderer.py -v
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
```

Expected：全部 PASS；50 候选推送仍 `<=18000 bytes`，每个已展示候选块包含两层概念和产业逻辑完整行。

- [ ] **Step 5: 阶段审查与提交**

规格审查确认标签、上限、总数、失败文案和免责声明；质量审查检查转义、非法类型、重复信息和推送截断。修订后重跑 Step 4。

提交：`feat(tail-scan): render layered concept context`。

## Task 4：同步 Agent / Skill / 索引文档

**Files:**

- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `.agents/skills/market-tasks/SKILL.md`
- Modify: `.agents/skills/INDEX.md`
- Modify: `.agents/rules/skills-sync.md`

- [ ] 在 `AGENTS.md` 和 `CLAUDE.md` 的 `tail-scan` 行追加：归属概念为当前快照、T-1 热概念先过滤容器再 Top8、两层同时展示、完整归属不进粗分/PK。
- [ ] 在 `market-tasks/SKILL.md` 的 tail-scan “四维事实卡/输出”说明加入相同语义和状态边界，不新增 CLI 示例参数。
- [ ] 在 `INDEX.md` 的 tail-scan 行加入 `get_stock_concept_memberships` capability 与两层字段用途。
- [ ] 在 `skills-sync.md` 的 tail-scan 检查行加入 `concept_context.py` 和新 capability 文档同步要求。
- [ ] 检查 `README.md`：本次没有新命令、安装或公开 API，记录无需更新，不修改文件。
- [ ] 运行：

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py scripts/tests/test_agent_symlinks.py -v
git diff --check
```

Expected：全部 PASS，无 symlink 漂移、CLI 签名漂移或 Markdown 空白错误。

- [ ] 提交：`docs(tail-scan): document layered concept context`。

纯文档阶段不触发代码双门，但根 agent 必须逐项对照实际代码，不得把当前快照写成历史 as-of。

## Task 5：完整功能域验证、最终审查与真实覆盖推送

### 5.1 分层验证

```bash
python3 -m pytest scripts/tests/test_stock_concept_memberships.py scripts/tests/test_tushare_p0_interfaces.py -v
python3 -m pytest scripts/tests/test_tail_scan_concept_context.py -v
python3 -m pytest scripts/tests/test_tail_scan_scorer.py scripts/tests/test_tail_scan_industry_logic.py scripts/tests/test_tail_scan_renderer.py -v
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
python3 -m pytest scripts/tests/test_cli_smoke.py scripts/tests/test_agent_symlinks.py -v
make check-scripts
git diff --check main...HEAD
```

完成标准：全部退出码 0；不接受 test failure、collection error、traceback、CLI parse error、线程/进程泄漏或新增 warning。现有 urllib3 LibreSSL warning 可如实记录，不得掩盖失败。

### 5.2 最终质量门

1. 新鲜子代理做整段规格合规审查。
2. 规格通过后，新鲜子代理做整段代码质量审查。
3. 按仓库门1映射执行简化/复用检查和代码审查；所有高优先级修复，中优先级显式处置，低优先级记录。
4. 门1修订后重跑 `make check-scripts`。
5. 前台执行 Codex 原生 adversarial review；严重问题全部修复，中等问题显式处置，轻微问题记录，最多 3 轮。

### 5.3 真实 dry-run

当前时点为 2026-07-14 凌晨，Sina 尚应返回最近完成交易日 2026-07-13 快照。执行前后都验证 quote date，禁止把 2026-07-14 数据冒充 2026-07-13：

```bash
cd scripts
set -a
source /Users/alyx/tradeSystem/scripts/.env
set +a
: "${TUSHARE_TOKEN:?missing TUSHARE_TOKEN}"
: "${DINGTALK_WEBHOOK_TOKEN:?missing DINGTALK_WEBHOOK_TOKEN}"
: "${DINGTALK_WEBHOOK_SECRET:?missing DINGTALK_WEBHOOK_SECRET}"
export TRADE_DB_PATH=/Users/alyx/tradeSystem/data/trade.db
python3 main.py tail-scan daily --date 2026-07-13 --dry-run --no-llm
```

上述前置检查只验证变量非空，不打印值。执行前还必须验证
`data/reports/huibo/summaries` 存在且真实指向 `/Users/alyx/tradeSystem/data/reports/huibo/summaries`；
不得依赖上次运行遗留的无法解析 symlink。
功能工作树中 `data/reports/tail-scan` 若不存在，在真实落盘前创建一个仅用于运行产物的 symlink，
指向 `/Users/alyx/tradeSystem/data/reports/tail-scan`；若该路径已存在，必须验证真实指向相同，
不同则停止，禁止删除或强制覆盖。`--dry-run` 前可以只做路径检查，因为它不落盘。

Expected：

- 命令退出码 0、`quote_date=2026-07-13`；若不等则停止外发。
- 六只候选（若实时源仍返回同一快照）分别出现 `归属概念` 与 `T-1热概念命中`。
- `DeepSeek概念` / `AI智能体` 因成员数超过 300 不进入两层展示或热概念 Top8。
- 九丰能源、中远海特、金宏气体、恒玄科技、合肥城建不再因未命中热 Top8 而显示“无归属概念”。
- `--dry-run` 不落盘、不推送。

### 5.4 已授权真实覆盖推送

用户已在同一任务中明确“确认外发并覆盖”。只有 5.1–5.3 全部通过且 quote date 确认为 2026-07-13 才执行：

```bash
cd scripts
set -a
source /Users/alyx/tradeSystem/scripts/.env
set +a
: "${TUSHARE_TOKEN:?missing TUSHARE_TOKEN}"
: "${DINGTALK_WEBHOOK_TOKEN:?missing DINGTALK_WEBHOOK_TOKEN}"
: "${DINGTALK_WEBHOOK_SECRET:?missing DINGTALK_WEBHOOK_SECRET}"
export TRADE_DB_PATH=/Users/alyx/tradeSystem/data/trade.db
python3 main.py tail-scan daily --date 2026-07-13 --no-llm
```

完成后机械验证：

- `/Users/alyx/tradeSystem/data/reports/tail-scan/2026-07-13.md` 已更新且含两层概念行。
- 命令输出明确推送成功；若推送失败，只报告失败，不重复无界重试。
- 不修改 SQLite、计划层或关注池。

## Commit 边界

1. `docs(tail-scan): specify layered concept context` —— 已确认设计规格。
2. `docs(tail-scan): plan layered concept context` —— 本实施计划。
3. `feat(providers): add stock concept membership lookup` —— capability + provider 测试。
4. `feat(tail-scan): separate stock and hot concepts` —— context/scorer/industry + 测试。
5. `feat(tail-scan): render layered concept context` —— renderer + 测试。
6. `docs(tail-scan): document layered concept context` —— 5 个真源文档。

每个功能 commit 的实现与测试必须同 commit；使用具体路径 `git add`，禁止 `git add -A` / `git add .`。未经新的明确指令不 push Git 远端；本计划中的“推送”仅指用户已授权的钉钉报告外发。

## 风险与回滚

| 风险 | 防护 | 回滚 |
|---|---|---|
| 逐票反查慢 | 复杂度为 1+N，不扫描全概念；真实运行记录耗时 | 关闭完整归属调用，保留原热概念 |
| 当前标签被误作历史标签 | 字段、报告和 disclaimer 均标当前快照 | 隐藏归属行，不影响筛选 |
| 宽度字段缺失 | `coverage_failed` fail-closed | 不输出未过滤 Top8 |
| 完整归属改变排序 | `_coarse_score` 对照测试 | 移除 `stock_concept_*` 消费 |
| 报告过长 | 归属展示 5 个、热命中 2 个、候选块完整截断 | 进一步减少展示数，不改事实卡 |

本次没有 schema、API、CLI、调度或数据迁移，回滚不需要 DB 操作。

## 方案审查结论

只读方案审查结论为 **修订后可执行**。发现与处置如下：

| 级别 | 审查问题 | 本计划修订 |
|---|---|---|
| 高 | 真实运行错 source 了不存在的根目录 `.env` | 改为 `scripts/.env`，增加三个必需变量的非空检查和慧博目录真实指向检查 |
| 高 | 工作树默认落盘目录不是主仓现有报告目录 | 真实落盘前只在路径不存在时创建指向主仓的产物 symlink，已存在但指向不符则停止 |
| 中 | provider 未初始化、全脏目录、`None` 响应和逐票错误原因未定义 | 增 `_ensure_pro`、清洗后空目录批次失败、`None` 逐票失败、错误字段及具名测试 |
| 中 | 任意尾部脏宽度都击穿整批，且净流入脏值无语义 | 改为按有限净流入扫描，只在填满路径内的非法宽度 fail-closed，增前段/尾部测试 |
| 中 | `prev_date=None` 独立归属取数缺具名回归 | 新增 `test_prev_date_failure_still_fetches_current_memberships` |
| 中 | membership 元数据桥可能丢失或泄漏进 card/PK | 新增跨 industry 重排、源时间传播、card/PK 不泄漏端到端测试 |
| 中 | 状态组合、语义匹配及旧 fake 仍可漏接线 | 增六行真值表、匹配最小长度/停用词、未知 capability 必须抛错的 fake |
| 中 | 重复定义容器上限 `300` | 改为复用 `services.concept_tags.CONTAINER_MAX_MEMBERS` 并加相等性测试 |
| 低 | capability 注册与 registry 失败路径缺断言 | 补 Tushare/AkShare/Registry 边界测试 |
| 低 | “完整验证”标题可能夸大为全仓覆盖 | 更名为“完整功能域验证”，保留全部受影响测试和 `make check-scripts` |

修订后的角色/文件边界仍可执行：G1→G2→G3 串行，G4 只在代码语义稳定后同步，
G5 仅做验证、审查与已授权的真实覆盖外发。
