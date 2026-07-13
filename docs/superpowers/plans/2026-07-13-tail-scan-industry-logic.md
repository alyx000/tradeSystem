# 尾盘实时筛选个股产业逻辑增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `tail-scan daily` 的每只候选同时展示可溯源的主营与产品、受控归纳的产业链位置、最近 30 个自然日的个股或行业催化，并明确区分事实、老师观点和程序判断。

**Architecture:** 在 provider 层新增批量主营资料 capability；在 `services/tail_scan/industry_logic.py` 内完成 Tushare→AkShare 补齐、本地三类证据检索、状态归一和产业链位置受控归纳；`scorer` 只负责把增强结果并入事实卡，`pk` 只接收紧凑摘要，`renderer` 输出逐股三段式内容。现有筛选阈值、粗分权重、PK 状态机、14:40 调度和只读边界均不改变。

**Tech Stack:** Python 3、pytest、SQLite 只读查询、Tushare `stock_company`、AkShare `stock_zyjs_ths`、现有 `ProviderRegistry` / `DataResult`、Markdown / DingTalk 渲染。

---

## 方案基线与硬约束

- 设计规格真源：`docs/superpowers/specs/2026-07-13-tail-scan-industry-logic-design.md`。
- `[事实·主营]` 只能来自 Tushare/AkShare 原始字段；不得让 LLM 或模板补造客户、份额、国产替代、供需或涨价结论。
- `[判断·产业链位置]` 只能重组 `sw_l2`、主营与产品原词；无法安全归纳时退化为具体行业名加“相关企业”（例如“电子化学品相关企业”）或“产业链位置暂无可核验归纳”。
- 近期证据闭区间固定为 `[scan_date - 30 days, scan_date]`；未来日期和无日期证据一律拒绝。
- `teacher_notes.mentioned_stocks` 按标准代码精确命中；慧博按股票名称精确命中；`industry_info` 只能产生行业催化；热门概念成分只能保留为市场标签。
- 单票最多 2 条催化，直接证据优先、日期倒序；行业证据不得伪装成个股直接受益。
- 主营或催化增强失败只降级该维度，不得中断整批实时扫描。
- 不新增或修改 SQLite schema、API、YAML、计划层、关注池、定时配置。
- 测试必须 mock 外网和密钥；真实 `--dry-run` 只做最终抽检，不替代单元测试。

## 实施前置：独立改动项与逃逸条款

共 5 项独立改动：

| # | 独立改动项 | 预估 | 主要文件 |
|---|---|---:|---|
| A | Provider 主营资料契约、Tushare 主源、AkShare 降级源及测试 | 中，20–30 分钟 | `scripts/providers/base.py`、`scripts/providers/tushare_provider.py`、`scripts/providers/akshare_provider.py`、`scripts/tests/test_stock_business_profiles.py` |
| B | 产业证据聚合、日期/匹配/去重/状态纯逻辑及测试 | 长，30–60 分钟 | `scripts/services/tail_scan/industry_logic.py`、`scripts/services/tail_scan/constants.py`、`scripts/tests/test_tail_scan_industry_logic.py` |
| C | `scorer` / `pk` / `renderer` / CLI 文案接线及回归测试 | 长，30–60 分钟 | `scripts/services/tail_scan/{scorer,pk,renderer}.py`、`scripts/cli/tail_scan.py`、`scripts/tests/test_tail_scan_{scorer,pk,renderer}.py` |
| D | Agent/Skill/索引文档同步 | 中，10–20 分钟 | `AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、`.agents/skills/INDEX.md`、`.agents/rules/skills-sync.md` |
| E | 分层验证、真实 dry-run、阶段审查门和提交整理 | 中，20–30 分钟 | 测试命令、git diff、阶段 review 输出 |

逃逸条款判定：`N=5 >= 3`；总预估超过 20 分钟；改动横跨 provider、service、渲染、测试和文档，且不集中在同一文件。因此不命中逃逸条款，必须使用下述并行分组。真正可并行的是阶段一的 A/B，以及阶段三的文档同步与仓库级测试；C 必须等待 A/B 契约稳定。

## 并行分组

执行环境映射：仓库规则中的“Claude Code 主 agent / subagent”在本 Codex 会话分别映射为“Codex 根 agent / collaboration 写入型子 agent”；职责、文件边界和审查门不变。只有用户选择“子代理驱动执行”后才启动写入型子 agent。

| 分组 | 角色 | 执行 Agent | 专项关注 | 职责边界 | 文件范围 | 禁区 | 冲突标注 |
|---|---|---|---|---|---|---|---|
| G1 Provider | 后端 + 测试（后端主、测试辅） | Claude Code subagent（当前环境映射：collaboration 写入型子 agent） | 性能：AkShare 受控并发与总超时；可观测性：逐票状态 | 交付 capability、两个 provider 的标准化返回与隔离测试 | 允许改 `scripts/providers/base.py`、`tushare_provider.py`、`akshare_provider.py`、`scripts/tests/test_stock_business_profiles.py` | 不得改 tail-scan service、CLI、DB、文档；若要改变 `DataResult` 或 `ProviderRegistry` 全局语义必须先询问 | `base.py` 只归 G1；G2/G3 只消费契约，不得同时编辑 |
| G2 产业逻辑 | 后端 + 测试（后端主、测试辅） | Claude Code 主 agent（当前环境映射：Codex 根 agent） | 合规：事实/判断分层、未来数据拒绝、行业证据不冒充个股证据 | 交付纯逻辑与本地只读聚合模块及测试 | 允许改 `scripts/services/tail_scan/industry_logic.py`、`constants.py`、`scripts/tests/test_tail_scan_industry_logic.py` | 不得改 provider、scorer/pk/renderer、CLI、DB；若主营契约不够用先回报 G1，不得私改 | `constants.py` 唯一归 G2；G3 只读取新增常量 |
| G3 接线与展示 | 后端 + 测试（后端主、测试辅） | Claude Code 主 agent（当前环境映射：Codex 根 agent） | 合规：粗分不变、PK payload 限长、报告标签准确 | 把 G1/G2 结果接入事实卡、PK 和 Markdown，补回归测试 | 允许改 `scorer.py`、`pk.py`、`renderer.py`、`scripts/cli/tail_scan.py` 和三个对应测试 | 不得改 provider、industry_logic 内部算法、筛选阈值、粗分权重、调度、DB | `scorer.py` / `pk.py` / `renderer.py` 唯一归 G3 |
| G4 文档同步 | 文档 | Claude Code subagent（当前环境映射：collaboration 写入型子 agent） | 合规：CLI/Skill/Agent 语义一致 | 在代码语义稳定后同步 5 个真源文档 | 允许改 `AGENTS.md`、`CLAUDE.md`、`.agents/skills/market-tasks/SKILL.md`、`.agents/skills/INDEX.md`、`.agents/rules/skills-sync.md` | 不得改代码或测试；发现行为与计划不一致时只报告，不自行改代码 | 5 个文档唯一归 G4；G3 不同时编辑 |
| G5 验证与审查 | 架构师 + 测试（架构师主、测试辅） | Codex 原生 `adversarial-review --wait`（foreground）+ Codex 根 agent 运行测试 | 正确性、边界、接口一致性、回归、合规 | 分阶段执行门1/门2、消化 finding、跑完整验证并给出处置摘要 | 只读全 diff；修复分别退回 G1/G2/G3 唯一归属文件 | 不得直接跨组大改；不得后台运行 review；不得跳过测试后复审 | codex review 的具体指派点固定在 G5；finding 修复回到文件所属组 |

依赖关系：`G1 || G2 → 阶段一审查 → G3 → 阶段二审查 → G4 || 全量测试 → 最终 G5 审查`。

## 分层测试设计

| 层级 | 覆盖对象 | 隔离方式 | 主要完成标准 |
|---|---|---|---|
| Provider 数据层 | capability、字段映射、按代码过滤、部分失败、并发超时状态 | mock `pro.stock_company` 和 `ak.stock_zyjs_ths`，不连网 | 所有请求代码均得到 `ok/missing/source_failed` 可判定状态；registry 现有语义不变 |
| 产业逻辑层 | 30 日闭区间、日期优先级、代码/名称精确命中、行业词元边界、去重、状态归一、受控位置归纳 | 内存 SQLite + `tmp_path` 慧博 JSON + fake registry | 直接证据优先；未来/无日期拒绝；宽泛词不误匹配；全失败与真实无命中分开 |
| 事实卡/PK/渲染层 | 新字段接线、PK 紧凑 payload、粗分不变、三段式 Markdown、降级脚注 | mock `industry_logic.build_industry_logic_map` 和 LLM runner | 现有 tail-scan 用例不回归；每只候选固定出现主营/位置/近期证据位置 |
| CLI/仓库层 | argparse 兼容、全部后端检查、真实 dry-run | 先纯测试，最后才用真实 provider | `test_cli_smoke`、`make check-scripts` 全绿；dry-run 不落盘不推送且报告结构完整 |

自底向上执行：Provider → 产业逻辑 → scorer/PK/renderer → CLI smoke → 仓库级检查 → 真实 dry-run。任何层未绿不得推进上层。

## 阶段级审查硬门

**每个大阶段结束且阶段测试通过后，立即跑一次门1（`/simplify` → 重跑该阶段 pytest → `/code-review`）+ 门2（Codex 原生 `adversarial-review --wait`）；不允许把多阶段 review 全部攒到最后。**

每阶段都必须满足：

- [`code-review-gate.md`](../../../.agents/rules/code-review-gate.md) 的 4 条二值结束条件；门1最多 2 轮。
- [`post-dev-codex-review.md`](../../../.agents/rules/post-dev-codex-review.md) 的 6 条二值结束条件；门2最多 3 轮。
- 高/严重 finding 必修；中/中等 finding 必须“已修 / 明确 defer+触发条件 / 反驳并落代码注释”三选一；低/轻微 finding 至少进入汇报。
- `/simplify` 和门1之间不得 commit；任何自动清理或 review 修订后必须重跑对应测试。

---

## Task 0：创建隔离实现工作区并记录基线

**Files:** 不修改业务文件。

- [ ] 调用 `superpowers:using-git-worktrees`，从包含已确认规格与本计划的当前 HEAD 创建 `codex/tail-scan-industry-logic` 隔离 worktree；不得从旧 `origin/main` 丢失规格提交。
- [ ] `data/` 被 gitignore，不会自动进入新 worktree。单元测试继续用内存库/`tmp_path`；只在 Task 5 真实抽检前设置 `TRADE_DB_PATH=/Users/alyx/tradeSystem/data/trade.db`，并把 worktree 的 `data/reports/huibo/summaries` 软链到原工作区同名只读目录。不得复制数据库、不得软链整个 `data/`、不得在非 dry-run 下使用这套共享数据配置。
- [ ] 在新 worktree 运行：

```bash
git status --short --branch
git log -3 --oneline
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
```

Expected：工作树没有本任务之外的修改；最后一条命令保持当前基线 `32 passed` 或当前仓库等价的全绿计数。若基线失败，先记录为既有失败，不得把它混进本功能修复。

## Task 1：Provider 主营资料契约与双源实现

**Files:**

- Modify: `scripts/providers/base.py`
- Modify: `scripts/providers/tushare_provider.py`
- Modify: `scripts/providers/akshare_provider.py`
- Create: `scripts/tests/test_stock_business_profiles.py`

### 1.1 定义逐票返回契约

每个请求代码必须映射到一个 dict，统一字段如下：

```python
{
    "ts_code": "688106.SH",
    "profile_status": "ok",  # ok | missing | source_failed
    "introduction": "公司介绍原文",
    "main_business": "主营业务原文",
    "business_scope": "经营范围原文",
    "product_types": ["电子特气"],
    "product_names": ["超纯氨", "高纯氧化亚氮"],
    "source": "tushare:stock_company",
    "error": "",
}
```

`missing` 表示数据源调用成功但没有该股票资料；`source_failed` 表示该票对应调用异常或超时。这个逐票状态是为满足“部分成功不吞掉缺失票”，不改变 `DataResult.success` 和 registry 的全局语义。

### 1.2 RED：先写 provider 测试

- [ ] 新建 `scripts/tests/test_stock_business_profiles.py`，至少包含以下用例：

```python
import time

import pandas as pd

import providers.akshare_provider as akshare_module
from providers.akshare_provider import AkshareProvider
from providers.tushare_provider import TushareProvider


def test_business_profile_capability_declared():
    ts = TushareProvider({})
    ak = AkshareProvider({})
    assert "get_stock_business_profiles" in ts.get_capabilities()
    assert "get_stock_business_profiles" in ak.get_capabilities()


def test_tushare_profiles_filter_requested_codes_and_normalize_fields():
    class Pro:
        def stock_company(self, **kwargs):
            assert kwargs["exchange"] == "SSE"
            return pd.DataFrame([
                {"ts_code": "688106.SH", "introduction": "气体综合服务商",
                 "main_business": "气体研发、生产、销售和服务",
                 "business_scope": "危险化学品生产与销售"},
                {"ts_code": "600000.SH", "introduction": "不应下传",
                 "main_business": "银行业务", "business_scope": "银行"},
            ])

    provider = TushareProvider({})
    provider.pro = Pro()
    provider._initialized = True
    result = provider.get_stock_business_profiles(["688106.SH"])
    assert result.success
    assert set(result.data) == {"688106.SH"}
    assert result.data["688106.SH"]["profile_status"] == "ok"
    assert result.data["688106.SH"]["main_business"] == "气体研发、生产、销售和服务"
    assert result.data["688106.SH"]["source"] == "tushare:stock_company"


def test_akshare_profiles_keep_other_stocks_when_one_call_fails():
    class Ak:
        def stock_zyjs_ths(self, symbol):
            if symbol == "605090":
                raise RuntimeError("temporary upstream error")
            return pd.DataFrame([{
                "主营业务": "气体研发、生产、销售和服务",
                "产品类型": "特种气体;大宗气体",
                "产品名称": "超纯氨;高纯氧化亚氮",
                "经营范围": "气体生产与销售",
            }])

    provider = AkshareProvider({})
    provider.ak = Ak()
    provider._initialized = True
    result = provider.get_stock_business_profiles(["688106.SH", "605090.SH"])
    assert result.success
    assert result.data["688106.SH"]["profile_status"] == "ok"
    assert result.data["688106.SH"]["product_names"] == ["超纯氨", "高纯氧化亚氮"]
    assert result.data["605090.SH"]["profile_status"] == "source_failed"


def test_akshare_empty_row_is_missing_not_source_failed():
    class Ak:
        def stock_zyjs_ths(self, symbol):
            return pd.DataFrame()

    provider = AkshareProvider({})
    provider.ak = Ak()
    provider._initialized = True
    result = provider.get_stock_business_profiles(["600428.SH"])
    assert result.data["600428.SH"]["profile_status"] == "missing"


def test_akshare_uninitialized_returns_top_level_error():
    provider = AkshareProvider({})
    result = provider.get_stock_business_profiles(["600428.SH"])
    assert not result.success
    assert result.error == "provider_not_initialized: get_stock_business_profiles"


def test_akshare_batch_timeout_returns_without_waiting_for_hung_call(monkeypatch):
    class Ak:
        def stock_zyjs_ths(self, symbol):
            time.sleep(1.0)
            return pd.DataFrame()

    provider = AkshareProvider({})
    provider.ak = Ak()
    provider._initialized = True
    monkeypatch.setattr(akshare_module, "BUSINESS_PROFILE_TIMEOUT_SECONDS", 0.05)
    started = time.monotonic()
    result = provider.get_stock_business_profiles(["600428.SH"])
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert result.data["600428.SH"]["profile_status"] == "source_failed"
    assert result.data["600428.SH"]["error"] == "timeout"
```

- [ ] Run：

```bash
python3 -m pytest scripts/tests/test_stock_business_profiles.py -v
```

Expected：FAIL，原因是 capability 和 provider 方法尚不存在；不得因 import 或 fixture 本身错误而红。

### 1.3 GREEN：实现基类和 Tushare 主源

- [ ] 在 `DataProvider` 的基础信息区域新增：

```python
def get_stock_business_profiles(self, ts_codes: list[str]) -> DataResult:
    """按标准 ts_code 批量获取公司主营资料；返回逐代码状态映射。"""
    return DataResult(data=None, source=self.name, error="not implemented")
```

- [ ] 把 capability 加入 Tushare/AkShare 的 `get_capabilities()`。
- [ ] 在 `TushareProvider` 中按交易所最多 3 次调用 `stock_company`，并只下传请求代码。核心实现必须等价于：

```python
def get_stock_business_profiles(self, ts_codes: list[str]) -> DataResult:
    if not ts_codes:
        return DataResult(data={}, source="tushare:stock_company")
    missing = self._ensure_pro("get_stock_business_profiles")
    if missing is not None:
        return missing

    requested = {self._normalize_stock_code(code) for code in ts_codes if str(code or "").strip()}
    exchange_of = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
    grouped: dict[str, set[str]] = {}
    for code in requested:
        exchange = exchange_of.get(code.rsplit(".", 1)[-1])
        if exchange:
            grouped.setdefault(exchange, set()).add(code)

    profiles: dict[str, dict] = {}
    failed_exchanges: dict[str, str] = {}
    successful_exchanges: set[str] = set()
    fields = "ts_code,introduction,main_business,business_scope"
    for exchange, codes in grouped.items():
        try:
            df = self.pro.stock_company(exchange=exchange, fields=fields)
            successful_exchanges.add(exchange)
            for row in self._df_to_records(df):
                code = str(row.get("ts_code") or "").strip().upper()
                if code not in codes:
                    continue
                profiles[code] = {
                    "ts_code": code,
                    "profile_status": "ok",
                    "introduction": _to_clean_str(row.get("introduction")),
                    "main_business": _to_clean_str(row.get("main_business")),
                    "business_scope": _to_clean_str(row.get("business_scope")),
                    "product_types": [],
                    "product_names": [],
                    "source": "tushare:stock_company",
                    "error": "",
                }
        except Exception as exc:
            failed_exchanges[exchange] = str(exc)

    if grouped and not successful_exchanges:
        detail = "; ".join(f"{k}: {v}" for k, v in sorted(failed_exchanges.items()))
        return DataResult(data=None, source="tushare:stock_company", error=detail or "stock_company failed")

    for exchange, codes in grouped.items():
        status = "source_failed" if exchange in failed_exchanges else "missing"
        for code in codes - profiles.keys():
            profiles[code] = {
                "ts_code": code, "profile_status": status,
                "introduction": "", "main_business": "", "business_scope": "",
                "product_types": [], "product_names": [], "source": "", "error": failed_exchanges.get(exchange, ""),
            }
    return DataResult(data=profiles, source="tushare:stock_company")
```

### 1.4 GREEN：实现 AkShare 受控并发 + 可硬终止降级源

- [ ] AkShare 当前实现内部 `requests.get` 没有暴露 timeout 参数，不能靠 parent daemon thread 提供硬超时；在 `akshare_provider.py` 增加 `multiprocessing/queue/re/threading/time` imports，使用 macOS 可用的 `fork` 子进程包住最多 4 个线程，父进程到 deadline 后直接 terminate 子进程，避免残留挂起线程和连接。模块级新增：

```python
BUSINESS_PROFILE_MAX_WORKERS = 4
BUSINESS_PROFILE_TIMEOUT_SECONDS = 20.0


def _normalize_business_profile_code(stock_code: str) -> str:
    code = str(stock_code or "").strip().upper()
    if not code or "." in code:
        return code
    if code.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{code}.BJ"
    if code.startswith(("60", "68", "90")):
        return f"{code}.SH"
    return f"{code}.SZ"
```

- [ ] 复用现有 `_to_clean_str`，新增产品拆分 helper：

```python
def _split_profile_items(value) -> list[str]:
    raw = _to_clean_str(value)
    if not raw:
        return []
    parts = re.split(r"[;；,，、/]+", raw)
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))
```

- [ ] 子进程 worker 逐票把完成结果传回父进程；父进程保留已完成票，超时后硬终止子进程，仅把尚未完成票标为 `source_failed`：

```python
def _akshare_business_profile_process(ak_module, requested: list[str], output) -> None:
    def fetch_one(code: str) -> dict:
        try:
            df = ak_module.stock_zyjs_ths(symbol=code.split(".")[0])
            if df is None or df.empty:
                return {"ts_code": code, "profile_status": "missing", "introduction": "",
                        "main_business": "", "business_scope": "", "product_types": [],
                        "product_names": [], "source": "", "error": ""}
            row = df.iloc[0]
            return {
                "ts_code": code,
                "profile_status": "ok",
                "introduction": "",
                "main_business": _to_clean_str(row.get("主营业务")),
                "business_scope": _to_clean_str(row.get("经营范围")),
                "product_types": _split_profile_items(row.get("产品类型")),
                "product_names": _split_profile_items(row.get("产品名称")),
                "source": "akshare:stock_zyjs_ths",
                "error": "",
            }
        except Exception as exc:
            return {"ts_code": code, "profile_status": "source_failed", "introduction": "",
                    "main_business": "", "business_scope": "", "product_types": [],
                    "product_names": [], "source": "", "error": str(exc)}

    tasks: queue.Queue[str] = queue.Queue()
    for code in requested:
        tasks.put(code)

    def worker() -> None:
        while True:
            try:
                code = tasks.get_nowait()
            except queue.Empty:
                return
            output.put((code, fetch_one(code)))

    threads = [
        threading.Thread(target=worker)
        for _ in range(min(BUSINESS_PROFILE_MAX_WORKERS, len(requested)))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def get_stock_business_profiles(self, ts_codes: list[str]) -> DataResult:
    if self.ak is None or not self._initialized:
        return DataResult(data=None, source=self.name,
                          error="provider_not_initialized: get_stock_business_profiles")
    requested = list(dict.fromkeys(
        _normalize_business_profile_code(code) for code in ts_codes if str(code or "").strip()
    ))
    if not requested:
        return DataResult(data={}, source="akshare:stock_zyjs_ths")

    try:
        context = multiprocessing.get_context("fork")
    except ValueError as exc:
        return DataResult(data=None, source=self.name, error=f"fork_unavailable: {exc}")
    output = context.Queue()
    process = context.Process(
        target=_akshare_business_profile_process,
        args=(self.ak, requested, output),
        daemon=True,
    )
    process.start()

    profiles: dict[str, dict] = {}
    pending = set(requested)
    deadline = time.monotonic() + BUSINESS_PROFILE_TIMEOUT_SECONDS
    timed_out = False
    while pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        try:
            code, profile = output.get(timeout=min(0.1, remaining))
        except queue.Empty:
            if not process.is_alive():
                break
            continue
        profiles[code] = profile
        pending.discard(code)
    if process.is_alive():
        process.terminate()
    process.join(timeout=1.0)
    output.close()
    output.join_thread()
    for code in pending:
        profiles[code] = {"ts_code": code, "profile_status": "source_failed", "introduction": "",
                          "main_business": "", "business_scope": "", "product_types": [],
                          "product_names": [], "source": "",
                          "error": "timeout" if timed_out else "worker_process_failed"}
    return DataResult(data=profiles, source="akshare:stock_zyjs_ths")
```

不得跨类调用 Tushare 私有方法；上面的 `_normalize_business_profile_code` 是 AkShare 自己的稳定边界。`fork` 是本仓库 macOS launchd 生产环境的显式约束；若未来迁移到不支持 `fork` 的平台，provider 必须返回清晰错误并由上层显示降级，不能静默退回不可终止线程。

### 1.5 验证、阶段审查与提交

- [ ] Run：

```bash
python3 -m pytest scripts/tests/test_stock_business_profiles.py scripts/tests/test_provider_registry.py scripts/tests/test_tushare_p0_interfaces.py scripts/tests/test_akshare_free_backends.py -v
```

Expected：全部 PASS；现有 registry “首个成功即返回 / 全失败才自动降级”用例不变。

- [ ] 阶段一与 Task 2 一起完成后执行门1/门2；审查修订后重跑本命令。
- [ ] 仅在审查收敛后按具体路径 stage，并提交功能层 commit：`feat(providers): add stock business profile sources`。commit body 写清双源、逐票三态、受控并发和本阶段实际 TDD 微循环数。

> 完成标准：上述 provider pytest 全绿 + 阶段一 `/simplify`、`/code-review`、Codex adversarial-review 均满足两份 review 规则结束条件，才能进入 Task 3。

## Task 2：新增产业逻辑与近期证据聚合模块

**Files:**

- Modify: `scripts/services/tail_scan/constants.py`
- Create: `scripts/services/tail_scan/industry_logic.py`
- Create: `scripts/tests/test_tail_scan_industry_logic.py`

### 2.1 先固化常量和输出类型

- [ ] 在 `constants.py` 新增：

```python
INDUSTRY_LOGIC_LOOKBACK_DAYS = 30
INDUSTRY_LOGIC_MAX_CATALYSTS = 2
INDUSTRY_LOGIC_MAX_PRODUCTS = 4
INDUSTRY_LOGIC_TEXT_MAX_CHARS = 120
```

- [ ] `build_industry_logic_map()` 的每个值固定为：

```python
{
    "sw_l2": "电子化学品",
    "business_summary": "气体研发、生产、销售和服务",
    "product_names": ["超纯氨", "高纯氧化亚氮"],
    "business_source": "akshare:stock_zyjs_ths",
    "business_status": "ok",
    "industry_position": "电子化学品产业链企业，核心产品包括超纯氨、高纯氧化亚氮",
    "catalyst_evidence": [
        {"kind": "huibo_stock", "label": "事实·个股催化", "date": "2026-06-25",
         "source": "慧博研报", "text": "AI算力集群拉动相关高纯材料及氦气需求"}
    ],
    "catalyst_status": "exact",
}
```

允许的 evidence `kind/label`：

| kind | label | 状态贡献 |
|---|---|---|
| `teacher_stock` | `老师观点·个股` | `exact` |
| `huibo_stock` | `事实·个股催化` | `exact` |
| `huibo_relation` | `事实·个股关联` | `exact`，只原样展示 `source` 关系，不扩写 |
| `industry` | `事实·行业催化` | `sector` |

### 2.2 RED：主营补齐与三态合并测试

- [ ] 先写 fake registry 和失败测试：

```python
import json
import sqlite3

import pytest

from providers.base import DataResult
from services.tail_scan import industry_logic


class Registry:
    def __init__(self):
        self.specific_calls = []

    def call(self, capability, codes):
        assert capability == "get_stock_business_profiles"
        return DataResult(data={
            "688106.SH": {"ts_code": "688106.SH", "profile_status": "ok",
                           "main_business": "气体研发、生产、销售和服务",
                           "product_names": [], "source": "tushare:stock_company"},
            "605090.SH": {"ts_code": "605090.SH", "profile_status": "missing",
                           "main_business": "", "product_names": [], "source": ""},
        }, source="tushare:stock_company")

    def call_specific(self, provider, capability, codes):
        self.specific_calls.append((provider, capability, codes))
        return DataResult(data={
            "605090.SH": {"ts_code": "605090.SH", "profile_status": "ok",
                           "main_business": "清洁能源综合服务",
                           "product_names": ["LNG", "液氦"],
                           "source": "akshare:stock_zyjs_ths"},
        }, source="akshare:stock_zyjs_ths")


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE teacher_notes (id INTEGER, date TEXT, title TEXT, mentioned_stocks TEXT)")
    db.execute("CREATE TABLE industry_info (id INTEGER, date TEXT, sector_name TEXT, content TEXT, source TEXT)")
    try:
        yield db
    finally:
        db.close()


def test_partial_tushare_result_only_falls_back_for_non_ok_codes():
    registry = Registry()
    profiles = industry_logic._load_profiles(registry, ["688106.SH", "605090.SH"])
    assert registry.specific_calls == [
        ("akshare", "get_stock_business_profiles", ["605090.SH"])
    ]
    assert profiles["688106.SH"]["source"] == "tushare:stock_company"
    assert profiles["605090.SH"]["source"] == "akshare:stock_zyjs_ths"


def test_profile_calls_that_raise_degrade_to_per_stock_source_failed():
    class RaisingRegistry:
        def call(self, capability, codes):
            raise RuntimeError("registry bug")

    profiles = industry_logic._load_profiles(RaisingRegistry(), ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == "source_failed"


def test_call_specific_exception_does_not_escape_batch():
    class RaisingFallbackRegistry:
        def call(self, capability, codes):
            return DataResult(data={
                "688106.SH": {"ts_code": "688106.SH", "profile_status": "source_failed",
                               "main_business": "", "product_names": [], "source": ""}
            }, source="tushare:stock_company")

        def call_specific(self, provider, capability, codes):
            raise RuntimeError("fallback bug")

    profiles = industry_logic._load_profiles(RaisingFallbackRegistry(), ["688106.SH"])
    assert profiles["688106.SH"]["profile_status"] == "source_failed"
```

- [ ] Run：`python3 -m pytest scripts/tests/test_tail_scan_industry_logic.py -k profiles -v`

Expected：FAIL，`industry_logic` 模块或 `_load_profiles` 尚不存在。

### 2.3 GREEN：实现 profile 编排和受控归纳

- [ ] 新模块先实现代码归一、裁剪和 fallback：

```python
from __future__ import annotations

import datetime as dt
import json
import pathlib
import re

from services.tail_scan import constants as C

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_HUIBO_DIR = REPO_ROOT / "data/reports/huibo/summaries"
STOP_TOKENS = {"科技", "行业", "概念", "设备", "材料"}
DIRECT_KINDS = {"teacher_stock", "huibo_stock", "huibo_relation"}
KIND_PRIORITY = {"teacher_stock": 0, "huibo_stock": 1, "huibo_relation": 2, "industry": 3}


def _code(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    if "." in raw:
        return raw
    if raw.startswith(("43", "82", "83", "87", "88", "89", "92")):
        return f"{raw}.BJ"
    if raw.startswith(("60", "68", "90")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _clip(value, limit: int = C.INDUSTRY_LOGIC_TEXT_MAX_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _evidence(kind: str, label: str, date: str, source, text) -> dict | None:
    clean_source = _clip(source, 60)
    clean_text = _clip(text, C.INDUSTRY_LOGIC_TEXT_MAX_CHARS)
    if not date or not clean_text:
        return None
    return {"kind": kind, "label": label, "date": date,
            "source": clean_source or "来源未标注", "text": clean_text}


def _select_evidence(items: list[dict]) -> list[dict]:
    deduped = {
        (item["kind"], item["date"], item["source"], item["text"]): item
        for item in items
    }
    return sorted(
        deduped.values(),
        key=lambda item: (KIND_PRIORITY[item["kind"]],
                          -int(item["date"].replace("-", "")),
                          item["source"], item["text"]),
    )[: C.INDUSTRY_LOGIC_MAX_CATALYSTS]


def _empty_profile(code: str, status: str) -> dict:
    return {"ts_code": code, "profile_status": status, "introduction": "",
            "main_business": "", "business_scope": "", "product_types": [],
            "product_names": [], "source": "", "error": ""}


def _load_profiles(registry, ts_codes: list[str]) -> dict[str, dict]:
    codes = sorted({_code(code) for code in ts_codes if _code(code)})
    try:
        result = registry.call("get_stock_business_profiles", codes)
    except Exception:
        return {code: _empty_profile(code, "source_failed") for code in codes}
    if not getattr(result, "success", False) or not isinstance(result.data, dict):
        return {code: _empty_profile(code, "source_failed") for code in codes}

    profiles = {code: result.data.get(code, _empty_profile(code, "missing")) for code in codes}
    if str(getattr(result, "source", "")).startswith("tushare:"):
        fallback_codes = sorted(code for code, row in profiles.items()
                                if row.get("profile_status") != "ok")
        if fallback_codes:
            try:
                fallback = registry.call_specific(
                    "akshare", "get_stock_business_profiles", fallback_codes
                )
            except Exception:
                fallback = None
            if getattr(fallback, "success", False) and isinstance(fallback.data, dict):
                for code in fallback_codes:
                    primary = profiles[code]
                    secondary = fallback.data.get(code, _empty_profile(code, "missing"))
                    if secondary.get("profile_status") == "ok":
                        profiles[code] = secondary
                    elif "missing" in {primary.get("profile_status"), secondary.get("profile_status")}:
                        profiles[code] = _empty_profile(code, "missing")
                    else:
                        profiles[code] = _empty_profile(code, "source_failed")
            else:
                for code in fallback_codes:
                    if profiles[code].get("profile_status") == "source_failed":
                        profiles[code] = _empty_profile(code, "source_failed")
    return profiles


def _industry_position(sw_l2: str, business: str, products: list[str]) -> str:
    product_text = "、".join(products[: C.INDUSTRY_LOGIC_MAX_PRODUCTS])
    if sw_l2 and product_text:
        return _clip(f"{sw_l2}产业链企业，核心产品包括{product_text}")
    if sw_l2 and business:
        return _clip(f"{sw_l2}领域企业，主营{business}")
    if sw_l2:
        return f"{sw_l2}相关企业"
    if business:
        return _clip(f"主营{business}")
    return ""
```

- [ ] 新增测试证明不凭空加入“上游/国产替代/涨价”：

```python
def test_industry_position_only_reuses_supplied_business_and_products():
    text = industry_logic._industry_position("电子化学品", "气体研发和销售", ["超纯氨"])
    assert text == "电子化学品产业链企业，核心产品包括超纯氨"
    for invented in ("上游", "国产替代", "涨价", "市占率"):
        assert invented not in text
```

### 2.4 RED/GREEN：老师观点与慧博精确证据

- [ ] 用内存 SQLite 建最小 `teacher_notes` / `industry_info` 表，用 `tmp_path` 建慧博 summary；不要依赖真实 `data/trade.db` 或真实报告目录。
- [ ] 覆盖以下精确断言：

```python
def test_teacher_note_matches_exact_normalized_code(conn):
    conn.execute(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(1,?,?,?)",
        ("2026-07-12", "冰点修复期控仓试错与硬科技轮动",
         json.dumps([{"code": "605090", "name": "九丰能源", "reason": "液氦链条跟踪"}], ensure_ascii=False)),
    )
    evidence, ok = industry_logic._read_teacher_evidence(
        conn, "2026-06-13", "2026-07-13", {"605090.SH": "九丰能源"}
    )
    assert ok is True
    assert evidence["605090.SH"][0]["label"] == "老师观点·个股"
    assert evidence["605090.SH"][0]["text"] == "液氦链条跟踪"


def test_huibo_empty_viewpoint_keeps_relation_without_expansion(conn, tmp_path):
    payload = {"reader_results": [{
        "title": "华丰科技深度",
        "huibo_list_time": "2026-07-10",
        "reader": {"pdf_report_date": "2026-07-08", "mentioned_stocks": [{
            "name": "四川长虹", "viewpoint": "",
            "source": "控股股东关系 / 5 / 股权结构",
        }]},
    }]}
    (tmp_path / "2026-07-12.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"600839.SH": "四川长虹"}
    )
    item = evidence["600839.SH"][0]
    assert ok is True
    assert item["kind"] == "huibo_relation"
    assert item["text"] == "控股股东关系 / 5 / 股权结构"
    assert "受益" not in item["text"]
    assert item["date"] == "2026-07-08"


def test_huibo_rejects_future_and_undated_evidence(conn, tmp_path):
    payload = {"reader_results": [
        {"title": "未来研报", "huibo_list_time": "2026-07-14",
         "reader": {"mentioned_stocks": [{"name": "金宏气体", "viewpoint": "未来证据", "source": "正文"}]}},
        {"title": "无日期研报", "huibo_list_time": "",
         "reader": {"mentioned_stocks": [{"name": "金宏气体", "viewpoint": "无日期证据", "source": "正文"}]}},
    ]}
    (tmp_path / "not-a-date.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    evidence, ok = industry_logic._read_huibo_evidence(
        tmp_path, "2026-06-13", "2026-07-13", {"688106.SH": "金宏气体"}
    )
    assert ok is True
    assert evidence.get("688106.SH", []) == []
```

- [ ] 实现 `_read_teacher_evidence()`：SQL 只取 `date BETWEEN ? AND ?` 的 `id,date,title,mentioned_stocks`；JSON 非数组/非对象行安全跳过；代码用 `_code()` 精确比较；`text` 只取 `reason` 或 `viewpoint`，都为空时使用“老师笔记直接提及股票名”。
- [ ] 实现 `_read_huibo_evidence()`：只遍历现有目录中的 `*.json`；读取 `reader_results`；日期优先级固定 `reader.pdf_report_date > row.huibo_list_time > 文件名日期`；名称必须完全相等；`viewpoint` 非空为 `huibo_stock`，否则只用原始 `source` 生成 `huibo_relation`。
- [ ] 三个 reader 的整个入口分别用 `try/except Exception` 收敛为 `({}, False)`；慧博单文件损坏时跳过该文件继续读其它文件，目录内所有文件均损坏时才返回 `ok=False`。所有 reader 都必须通过 `_evidence()` 统一去换行和限长；空 viewpoint 且空 source 的慧博关系项直接丢弃。
- [ ] 再补具名边界用例：
  - `test_huibo_date_falls_back_to_list_time_then_filename`：分别断言二级和三级日期 fallback。
  - `test_corrupt_huibo_file_isolated_and_all_corrupt_is_failed`：一坏一好仍 `ok=True`，全部损坏 `ok=False`。
  - `test_teacher_code_and_huibo_name_require_exact_match`：相近裸码、股票名子串均不得命中。
  - `test_registry_result_already_from_akshare_is_used_without_second_fallback`：模拟 Tushare 整体失败后 registry 已返回 AkShare，确保不再 `call_specific`。
  - `test_both_business_sources_failed_and_both_missing_are_distinct`：分别断言最终 `business_status=source_failed` 与 `missing`。

### 2.5 RED/GREEN：行业证据匹配、去重和状态

- [ ] 先写边界测试：

```python
def test_industry_match_rejects_broad_stopword_but_accepts_specific_token(conn):
    conn.executemany(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        [
            (1, "2026-07-12", "材料", "宽泛词不应命中", "测试源"),
            (2, "2026-07-11", "电子特气/工业气体", "电子特气需求提升", "行业笔记"),
        ],
    )
    evidence, ok = industry_logic._read_industry_evidence(
        conn, "2026-06-13", "2026-07-13",
        {"688106.SH": {"sw_l2": "电子化学品", "business_summary": "电子特气研发生产",
                         "product_names": ["超纯氨"], "concept_names": []}},
    )
    assert ok is True
    assert [item["text"] for item in evidence["688106.SH"]] == ["电子特气需求提升"]


def test_industry_content_never_relabels_explicit_judgment_as_fact():
    mixed = "[事实]电子特气需求提升。[判断]价格可能继续上涨。"
    assert industry_logic._fact_only_industry_text(mixed) == "电子特气需求提升。"
    assert industry_logic._fact_only_industry_text("[判断]行业景气向上。") == ""


def test_lookback_is_closed_interval_and_exact_beats_sector(conn, tmp_path):
    conn.execute(
        "INSERT INTO teacher_notes(id,date,title,mentioned_stocks) VALUES(1,?,?,?)",
        ("2026-06-13", "窗口起点老师笔记",
         json.dumps([{"code": "688106.SH", "name": "金宏气体", "reason": "起点直接证据"}], ensure_ascii=False)),
    )
    conn.executemany(
        "INSERT INTO industry_info(id,date,sector_name,content,source) VALUES(?,?,?,?,?)",
        [
            (1, "2026-06-12", "电子特气", "窗口外证据", "行业笔记"),
            (2, "2026-07-13", "电子特气", "扫描日行业证据", "行业笔记"),
        ],
    )
    result = industry_logic.build_industry_logic_map(
        conn, Registry(),
        [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13",
        industry_map={"688106.SH": {"sw_l2": "电子化学品"}},
        concept_map={"688106": ["电子特气"]},
        huibo_dir=tmp_path,
    )
    evidence = result["688106.SH"]["catalyst_evidence"]
    assert [item["text"] for item in evidence] == ["起点直接证据", "扫描日行业证据"]
    assert evidence[0]["kind"] in industry_logic.DIRECT_KINDS
    assert all(item["text"] != "窗口外证据" for item in evidence)
    assert result["688106.SH"]["catalyst_status"] == "exact"


def test_no_evidence_and_all_sources_failed_are_distinct(conn, tmp_path):
    none_result = industry_logic.build_industry_logic_map(
        conn, Registry(), [{"code": "688106.SH", "name": "金宏气体"}],
        scan_date="2026-07-13", industry_map={}, concept_map={}, huibo_dir=tmp_path,
    )
    assert none_result["688106.SH"]["catalyst_status"] == "none"

    broken_conn = sqlite3.connect(":memory:")
    try:
        failed_result = industry_logic.build_industry_logic_map(
            broken_conn, Registry(), [{"code": "688106.SH", "name": "金宏气体"}],
            scan_date="2026-07-13", industry_map={}, concept_map={},
            huibo_dir=tmp_path / "missing-directory",
        )
    finally:
        broken_conn.close()
    assert failed_result["688106.SH"]["catalyst_status"] == "source_failed"


def test_evidence_is_single_line_and_length_bounded():
    item = industry_logic._evidence(
        "huibo_stock", "事实·个股催化", "2026-07-12",
        "慧博\n研报" * 20, "产业催化\n" * 80,
    )
    assert item is not None
    assert "\n" not in item["source"] and len(item["source"]) <= 60
    assert "\n" not in item["text"] and len(item["text"]) <= 120


def test_teacher_priority_precedes_newer_huibo_and_dedupes_stably():
    items = [
        {"kind": "huibo_stock", "label": "事实·个股催化", "date": "2026-07-13",
         "source": "慧博B", "text": "新慧博B"},
        {"kind": "teacher_stock", "label": "老师观点·个股", "date": "2026-07-01",
         "source": "老师笔记", "text": "较早老师观点"},
        {"kind": "huibo_stock", "label": "事实·个股催化", "date": "2026-07-12",
         "source": "慧博A", "text": "新慧博A"},
        {"kind": "teacher_stock", "label": "老师观点·个股", "date": "2026-07-01",
         "source": "老师笔记", "text": "较早老师观点"},
    ]
    selected = industry_logic._select_evidence(items)
    assert [(item["kind"], item["text"]) for item in selected] == [
        ("teacher_stock", "较早老师观点"),
        ("huibo_stock", "新慧博B"),
    ]
```

- [ ] 行业 token helper 必须等价于：

```python
def _sector_tokens(sector_name: str) -> list[str]:
    raw_tokens = re.split(r"[/、,，]+", str(sector_name or ""))
    tokens = []
    for raw in raw_tokens:
        token = re.sub(r"\s+", "", raw)
        chinese_count = len(re.findall(r"[\u4e00-\u9fff]", token))
        if chinese_count >= 2 and token not in STOP_TOKENS:
            tokens.append(token)
    return list(dict.fromkeys(tokens))


def _token_matches(token: str, haystacks: list[str]) -> bool:
    usable = [re.sub(r"\s+", "", text) for text in haystacks
              if len(re.sub(r"\s+", "", text)) >= 2
              and re.sub(r"\s+", "", text) not in STOP_TOKENS]
    return any(token == text or token in text or text in token for text in usable)


def _fact_only_industry_text(content: str) -> str:
    text = str(content or "").strip()
    parts = re.split(r"(\[(?:事实|判断|观点|老师观点)\])", text)
    if len(parts) == 1:
        return _clip(text)
    current = ""
    facts = []
    for part in parts:
        if part in {"[事实]", "[判断]", "[观点]", "[老师观点]"}:
            current = part
        elif current == "[事实]" and part.strip():
            facts.append(part.strip())
    return _clip(" ".join(facts))
```

- [ ] `_read_industry_evidence()` 只读 `date,sector_name,content,source`；匹配 haystack 只能来自该票 `sw_l2/business_summary/product_names/concept_names`；正文必须先过 `_fact_only_industry_text()`，显式 `[判断]/[观点]/[老师观点]` 段不得进入事实催化；命中结果固定 `kind=industry,label=事实·行业催化`。
- [ ] `_read_teacher_evidence()` / `_read_industry_evidence()` SQL 成功但 0 行时返回 `ok=True`；`_read_huibo_evidence()` 目录存在但无文件时返回 `ok=True`，目录不存在或读取整体失败才返回 `ok=False`。只有三类来源都 `ok=False` 时 `catalyst_status=source_failed`。
- [ ] 新增 `test_empty_candidates_short_circuits_without_registry_db_or_filesystem_calls`，用会抛异常的 registry/conn 传空候选并断言返回 `{}`。
- [ ] `build_industry_logic_map()` 完成以下编排：

```python
def build_industry_logic_map(
    conn,
    registry,
    candidates: list[dict],
    *,
    scan_date: str,
    industry_map: dict,
    concept_map: dict,
    lookback_days: int = C.INDUSTRY_LOGIC_LOOKBACK_DAYS,
    huibo_dir: pathlib.Path | str | None = None,
) -> dict[str, dict]:
    if not candidates:
        return {}
    end = dt.datetime.strptime(scan_date, "%Y-%m-%d").date()
    start = end - dt.timedelta(days=lookback_days)
    start_s, end_s = start.isoformat(), end.isoformat()
    code_to_name = {_code(row.get("code")): str(row.get("name") or "") for row in candidates}
    profiles = _load_profiles(registry, list(code_to_name))

    base: dict[str, dict] = {}
    for code, name in code_to_name.items():
        profile = profiles.get(code, _empty_profile(code, "source_failed"))
        status = profile.get("profile_status") or "source_failed"
        business = _clip(profile.get("main_business") or profile.get("introduction"))
        products = list(dict.fromkeys(_clip(item, 40) for item in profile.get("product_names", []) if _clip(item, 40)))
        sw_l2 = str((industry_map.get(code) or {}).get("sw_l2") or "")
        base[code] = {
            "sw_l2": sw_l2,
            "business_summary": business if status == "ok" else "",
            "product_names": products[: C.INDUSTRY_LOGIC_MAX_PRODUCTS] if status == "ok" else [],
            "business_source": profile.get("source", "") if status == "ok" else "",
            "business_status": status,
            "industry_position": _industry_position(sw_l2, business, products) if status == "ok" or sw_l2 else "",
            "concept_names": concept_map.get(code.split(".")[0], []),
        }

    teacher, teacher_ok = _read_teacher_evidence(conn, start_s, end_s, code_to_name)
    huibo, huibo_ok = _read_huibo_evidence(
        pathlib.Path(huibo_dir) if huibo_dir is not None else DEFAULT_HUIBO_DIR,
        start_s, end_s, code_to_name,
    )
    industry, industry_ok = _read_industry_evidence(conn, start_s, end_s, base)
    all_sources_failed = not any((teacher_ok, huibo_ok, industry_ok))

    output: dict[str, dict] = {}
    for code, row in base.items():
        combined = teacher.get(code, []) + huibo.get(code, []) + industry.get(code, [])
        ordered = _select_evidence(combined)
        if any(item["kind"] in DIRECT_KINDS for item in ordered):
            catalyst_status = "exact"
        elif ordered:
            catalyst_status = "sector"
        else:
            catalyst_status = "source_failed" if all_sources_failed else "none"
        output[code] = {
            key: value for key, value in row.items() if key != "concept_names"
        } | {"catalyst_evidence": ordered, "catalyst_status": catalyst_status}
    return output
```

### 2.6 验证、阶段审查与提交

- [ ] Run：

```bash
python3 -m pytest scripts/tests/test_tail_scan_industry_logic.py scripts/tests/test_stock_business_profiles.py -v
```

Expected：所有 profile fallback、日期边界、精确命中、行业匹配、去重和状态用例 PASS；无真实网络/真实库读取。

- [ ] 对 Task 1+2 的阶段一 diff 执行 `/simplify`，重跑上述命令，再执行 `/code-review`。
- [ ] 门1收敛后前台运行：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait "重点:provider部分成功语义/并发超时/未来数据/精确匹配/行业误匹配/事实与判断边界/测试缺口"
```

- [ ] 审查收敛后提交第二个功能层 commit：`feat(tail-scan): aggregate industry logic evidence`。测试与实现必须同 commit，body 记录实际 TDD 微循环数。

> 完成标准：`test_tail_scan_industry_logic.py` + `test_stock_business_profiles.py` 全绿 + **立即完成门1（`/simplify` + `/code-review`）+ 门2 Codex 原生 adversarial-review，满足 4 条和 6 条结束条件，才能进入 Task 3。**

## Task 3：接入事实卡、PK 和逐股报告

**Files:**

- Modify: `scripts/services/tail_scan/scorer.py`
- Modify: `scripts/services/tail_scan/pk.py`
- Modify: `scripts/services/tail_scan/renderer.py`
- Modify: `scripts/cli/tail_scan.py`
- Modify: `scripts/tests/test_tail_scan_scorer.py`
- Modify: `scripts/tests/test_tail_scan_pk.py`
- Modify: `scripts/tests/test_tail_scan_renderer.py`

### 3.1 RED：scorer 接线与粗分不变

- [ ] 在 `test_tail_scan_scorer.py` 为既有测试加 autouse stub，防止它读取真实慧博目录或真实主营源；新模块自身已由 Task 2 单测覆盖：

```python
import pytest


@pytest.fixture(autouse=True)
def _stub_industry_logic(monkeypatch):
    def build(_conn, _registry, candidates, **_kwargs):
        return {
            row["code"]: {
                "sw_l2": "", "business_summary": "", "product_names": [],
                "business_source": "", "business_status": "missing",
                "industry_position": "", "catalyst_evidence": [],
                "catalyst_status": "none",
            }
            for row in candidates
        }
    monkeypatch.setattr(scorer.industry_logic, "build_industry_logic_map", build)
```

- [ ] 新增接线和粗分护栏测试：

```python
def test_build_fact_cards_keeps_industry_logic_fields(monkeypatch):
    monkeypatch.setattr(scorer.industry_logic, "build_industry_logic_map", lambda *a, **k: {
        "600001.SH": {"sw_l2": "电子化学品", "business_summary": "电子特气研发生产",
                       "product_names": ["超纯氨"], "business_source": "akshare:stock_zyjs_ths",
                       "business_status": "ok", "industry_position": "电子化学品产业链企业，核心产品包括超纯氨",
                       "catalyst_evidence": [{"kind": "industry", "label": "事实·行业催化",
                                               "date": "2026-07-12", "source": "行业笔记",
                                               "text": "电子特气需求提升"}],
                       "catalyst_status": "sector"}
    })
    card = scorer.build_fact_cards(_mk_conn(), _RegPos(), _scan1(), params={"date": "2026-07-13"})[0]
    assert card["sw_l2"] == "电子化学品"
    assert card["business_summary"] == "电子特气研发生产"
    assert card["catalyst_status"] == "sector"


def test_build_fact_cards_survives_industry_logic_batch_exception(monkeypatch):
    def raising_builder(*args, **kwargs):
        raise RuntimeError("unexpected enrichment bug")

    monkeypatch.setattr(scorer.industry_logic, "build_industry_logic_map", raising_builder)
    card = scorer.build_fact_cards(
        _mk_conn(), _RegPos(), _scan1(), params={"date": "2026-07-13"}
    )[0]
    assert card["business_status"] == "source_failed"
    assert card["catalyst_status"] == "source_failed"
    assert card["code"] == "600001.SH"


def test_new_industry_fields_do_not_change_coarse_score():
    base = _card(code="600001.SH")
    enriched = {**base, "business_summary": "强主营", "industry_position": "强位置",
                "catalyst_evidence": [{"text": "强催化"}], "catalyst_status": "exact"}
    assert scorer._coarse_score(base) == scorer._coarse_score(enriched)
```

- [ ] Run：`python3 -m pytest scripts/tests/test_tail_scan_scorer.py -v`

Expected：FAIL，缺少 `scorer.industry_logic` 或新增字段。

### 3.2 GREEN：scorer 调用产业逻辑模块

- [ ] 在 `scorer.py` 导入 `logging` 与 `industry_logic`，定义 module logger；取得 `industry_map` 和 `concept_map` 后批量调用一次，并用最后一道 batch fail-safe 保证增强模块 bug 也不挡整批：

```python
try:
    logic_map = industry_logic.build_industry_logic_map(
        conn,
        registry,
        cands,
        scan_date=date,
        industry_map=industry_map,
        concept_map=concept_map,
        lookback_days=params.get("industry_logic_lookback", C.INDUSTRY_LOGIC_LOOKBACK_DAYS),
        huibo_dir=params.get("huibo_summary_dir"),
    )
except Exception:
    logger.warning("[tail-scan] 产业逻辑增强异常，整批降级但继续渲染", exc_info=True)
    logic_map = {}
```

- [ ] 在逐票 card 中用安全默认值并入字段，且显式保留 `sw_l2`：

```python
logic = logic_map.get(code) or {
    "sw_l2": industry, "business_summary": "", "product_names": [],
    "business_source": "", "business_status": "source_failed",
    "industry_position": "", "catalyst_evidence": [], "catalyst_status": "source_failed",
}

# append card dict 内新增
"sw_l2": logic.get("sw_l2") or industry,
"business_summary": logic.get("business_summary", ""),
"product_names": logic.get("product_names", []),
"business_source": logic.get("business_source", ""),
"business_status": logic.get("business_status", "source_failed"),
"industry_position": logic.get("industry_position", ""),
"catalyst_evidence": logic.get("catalyst_evidence", []),
"catalyst_status": logic.get("catalyst_status", "source_failed"),
```

不得修改 `_coarse_score()`。

### 3.3 RED/GREEN：PK 只接紧凑证据

- [ ] 新增测试：

```python
def test_payload_includes_compact_industry_logic_and_excludes_coarse_score():
    a, b = _cards()
    a.update({
        "total": 99.0,
        "sw_l2": "电子化学品",
        "business_summary": "电子特气研发生产" * 20,
        "industry_position": "电子化学品产业链企业",
        "business_status": "ok",
        "catalyst_status": "exact",
        "catalyst_evidence": [{"kind": "huibo_stock", "label": "事实·个股催化",
                                "date": "2026-06-25", "source": "慧博研报",
                                "text": "氦气需求提升" * 30}],
    })
    payload = pk._payload(a, b)
    assert "total" not in payload["A"]
    assert payload["A"]["sw_l2"] == "电子化学品"
    assert len(payload["A"]["business_summary"]) <= 120
    assert len(payload["A"]["catalyst_evidence"]) == 1
    assert len(payload["A"]["catalyst_evidence"][0]["text"]) <= 120
```

- [ ] 在 `_FACT_FIELDS` 新增 `sw_l2/business_summary/product_names/business_source/business_status/industry_position/catalyst_evidence/catalyst_status`；`one()` 对长文本和证据做裁剪：

```python
def _compact_text(value, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _payload(card_a, card_b):
    def one(card):
        result = {key: card.get(key) for key in _FACT_FIELDS if key != "catalyst_evidence"}
        result["business_summary"] = _compact_text(result.get("business_summary"))
        result["industry_position"] = _compact_text(result.get("industry_position"))
        result["product_names"] = list(result.get("product_names") or [])[:4]
        result["catalyst_evidence"] = [
            {"label": item.get("label", ""), "date": item.get("date", ""),
             "source": _compact_text(item.get("source"), 60),
             "text": _compact_text(item.get("text"))}
            for item in (card.get("catalyst_evidence") or [])[:2]
        ]
        return result
    return {"A": one(card_a), "B": one(card_b)}
```

- [ ] 调整 `_PROMPT`：把输入称为“带边界标签的证据卡”，明确 `business_summary/catalyst_evidence` 是事实或老师观点，`industry_position` 是程序 `[判断]`，不得把行业催化升级为公司已受益事实。PK 仍只判断相对观察优先级，不给买卖建议。
- [ ] Run：`python3 -m pytest scripts/tests/test_tail_scan_pk.py -v`

Expected：全部 PASS，既有熔断、重试和红线用例不回归。

### 3.4 RED/GREEN：三段式报告与降级文案

- [ ] 扩展 renderer fixture，新增成功和降级测试：

```python
def test_render_shows_business_position_and_catalyst_with_source_date():
    scored = _scored()
    scored[0].update({
        "sw_l2": "电子化学品",
        "business_summary": "气体研发、生产、销售和服务",
        "product_names": ["超纯氨", "高纯氧化亚氮"],
        "business_source": "akshare:stock_zyjs_ths",
        "business_status": "ok",
        "industry_position": "电子化学品产业链企业，核心产品包括超纯氨、高纯氧化亚氮",
        "catalyst_evidence": [{"kind": "huibo_stock", "label": "事实·个股催化",
                                "date": "2026-06-25", "source": "慧博研报",
                                "text": "AI算力集群拉动相关高纯材料及氦气需求"}],
        "catalyst_status": "exact",
    })
    md = renderer.render_daily(_scan(), scored, None)
    assert "[事实·主营]" in md and "超纯氨" in md
    assert "[判断·产业链位置]" in md
    assert "[事实·个股催化]" in md
    assert "2026-06-25" in md and "慧博研报" in md
    assert "全为 [判断]" not in md
    assert "排序为 [判断]，主营/催化按行内标签" in md


def test_render_distinguishes_missing_from_source_failed():
    missing = _scored()[0] | {"business_status": "missing", "business_summary": "",
                              "product_names": [], "industry_position": "",
                              "catalyst_status": "none", "catalyst_evidence": []}
    failed = _scored()[0] | {"business_status": "source_failed", "business_summary": "",
                             "product_names": [], "industry_position": "",
                             "catalyst_status": "source_failed", "catalyst_evidence": []}
    md_missing = renderer.render_daily(_scan(), [missing], None)
    md_failed = renderer.render_daily(_scan(), [failed], None)
    assert "暂无可核验主营资料" in md_missing
    assert "最近30日暂无可核验产业催化" in md_missing
    assert "主营资料源失败" in md_failed
    assert "催化证据源失败" in md_failed
```

- [ ] 在 renderer 中新增 source 显示和逐票子项 helper：

```python
def _business_line(card: dict) -> str:
    status = card.get("business_status")
    if status == "source_failed":
        return "  - [事实·主营] 主营资料源失败，本次未取得。\n"
    if status != "ok":
        return "  - [事实·主营] 暂无可核验主营资料。\n"
    products = "、".join((card.get("product_names") or [])[:4])
    body = card.get("business_summary") or "主营原文为空"
    if products:
        body += f"；产品包括{products}"
    source = {"tushare:stock_company": "Tushare 公司资料",
              "akshare:stock_zyjs_ths": "AkShare 主营介绍"}.get(
                  card.get("business_source"), card.get("business_source") or "来源未标注"
              )
    return f"  - [事实·主营] {body}。（{source}）\n"


def _position_line(card: dict) -> str:
    text = card.get("industry_position") or "暂无可核验归纳"
    return f"  - [判断·产业链位置] {text}。\n"


def _catalyst_lines(card: dict) -> str:
    evidence = (card.get("catalyst_evidence") or [])[:2]
    if evidence:
        return "".join(
            f"  - [{item.get('label', '事实·近期催化')}] {item.get('text', '')}"
            f"。（{item.get('date', '')}，{item.get('source', '')}）\n"
            for item in evidence
        )
    if card.get("catalyst_status") == "source_failed":
        return "  - [事实·近期催化] 催化证据源失败，本次未取得。\n"
    return "  - [事实·近期催化] 最近30日暂无可核验产业催化。\n"
```

- [ ] 在每只候选头行之后依次 append `_business_line`、`_position_line`、`_catalyst_lines`。
- [ ] 更新 renderer 模块 docstring、disclaimer 和候选章节标题；标题从“全为 `[判断]`”改为“排序为 `[判断]`，主营/催化按行内标签”，避免新增事实行仍被总标题误标成判断。
- [ ] `_degradation_note()` 仅把 `business_status=source_failed` 和 `catalyst_status=source_failed` 计为降级；`missing/none` 是真实无资料，不得写成失败。
- [ ] 更新 disclaimer：实时行情仍为 T 日快照，主线/概念仍为 T-1；主营是静态公司资料，催化是截至扫描日最近 30 日的本地证据。
- [ ] 更新 `scripts/cli/tail_scan.py` 的 module docstring / help 文案为“实时筛选→四维事实卡+产业逻辑→PK→渲染”，不新增或修改 argparse 参数。

### 3.5 阶段验证、审查与提交

- [ ] Run：

```bash
python3 -m pytest scripts/tests/test_tail_scan_scorer.py scripts/tests/test_tail_scan_pk.py scripts/tests/test_tail_scan_renderer.py -v
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
```

Expected：全部 tail-scan 测试 PASS；既有筛选、T-1、防看未来、熔断、排序和红线测试无回归。

- [ ] 执行阶段二 `/simplify`，重跑上述命令，再执行 `/code-review`。
- [ ] 门1收敛后前台运行：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait "重点:粗分是否被悄改/事实卡状态默认值/PK输入边界/Markdown事实判断标签/真实无证据与源失败区分/既有T-1语义回归/测试缺口"
```

- [ ] 审查收敛后提交：`feat(tail-scan): render per-stock industry logic`。实现和对应测试同 commit，body 写实际 TDD 微循环数。

> 完成标准：三个接线测试模块与全部 `test_tail_scan_*.py` 全绿 + **立即完成门1和门2并满足两份 review 规则全部结束条件，才能进入 Task 4。**

## Task 4：同步 Agent / Skill / 索引文档

**Files:**

- Modify: `AGENTS.md`
- Modify: `CLAUDE.md`
- Modify: `.agents/skills/market-tasks/SKILL.md`
- Modify: `.agents/skills/INDEX.md`
- Modify: `.agents/rules/skills-sync.md`

- [ ] 在 `AGENTS.md` 和 `CLAUDE.md` 的 `tail-scan` 单行说明中，把“四维事实卡”后追加同一段语义：

```text
+ 产业逻辑增强[主营:Tushare stock_company 主源/AkShare stock_zyjs_ths 补缺；产业链位置仅基于申万二级+主营+产品受控归纳；近30自然日催化只读 teacher_notes 精确代码/慧博精确名称/industry_info 行业匹配，个股/行业/老师观点分层，失败仅降级该维度]
```

- [ ] 更新 `.agents/skills/market-tasks/SKILL.md` 的 tail-scan “数据/输出”说明：明确每票显示 `[事实·主营]`、`[判断·产业链位置]`、近期证据；不新增命令参数示例。
- [ ] 更新 `.agents/skills/INDEX.md` 的 tail-scan 行，保持 CLI 签名原样，只补充 provider capability、30 日窗口和事实边界。
- [ ] 在 `.agents/rules/skills-sync.md` 的 globs 加 `scripts/cli/tail_scan.py` 与 `scripts/services/tail_scan/*.py`，并在“受影响的 SKILL.md”表新增 tail-scan → `market-tasks/SKILL.md` + `INDEX.md` + `AGENTS.md` / `CLAUDE.md`。
- [ ] `market-tasks` 目录没有 `agents/openai.yaml`，记录为“不适用”，不得创建无需求的新文件。
- [ ] 检查 `README.md`：本次没有新增命令、参数、安装步骤或对外 API，因此记录“无需更新”，不得为了凑文档范围改动 README。
- [ ] 文档中不得声称新增 API、DB 写入、筛选权重或调度变化。
- [ ] 提交：`docs(tail-scan): document industry logic enrichment`。

Task 4 是纯文档阶段，不触发代码 review 双门；但必须由根 agent逐项对照代码和规格，确认所有文档使用同一 30 日、双源、三类证据与只读语义。

## Task 5：完整验证、真实 dry-run 与最终审查

### 5.1 分层验收命令

- [ ] Provider：

```bash
python3 -m pytest scripts/tests/test_stock_business_profiles.py scripts/tests/test_provider_registry.py scripts/tests/test_tushare_p0_interfaces.py scripts/tests/test_akshare_free_backends.py -v
```

- [ ] 产业逻辑：

```bash
python3 -m pytest scripts/tests/test_tail_scan_industry_logic.py -v
```

- [ ] tail-scan 全量回归：

```bash
python3 -m pytest scripts/tests/test_tail_scan_*.py -v
```

- [ ] CLI 签名（本次不改参数，但验证现有 `ARCHITECTURE_COMMANDS`）：

```bash
python3 -m pytest scripts/tests/test_cli_smoke.py -v
```

- [ ] Skills/rules symlink 与仓库后端全量：

```bash
python3 -m pytest scripts/tests/test_agent_symlinks.py -v
make check-scripts
```

Expected：全部命令退出码 0；不接受 test failure、collection error、traceback、CLI parse error、symlink drift。外部库 deprecation warning 可记录但不得掩盖 failure；新增代码不得产生未处理协程、线程异常或资源泄漏 warning。

### 5.2 真实只读抽检

- [ ] 在 worktree 根目录准备只读依赖；`ln -s` 仅首次执行，若链接已存在先验证它仍指向该绝对目录：

```bash
mkdir -p data/reports/huibo
ln -s /Users/alyx/tradeSystem/data/reports/huibo/summaries data/reports/huibo/summaries
test ! -e data/reports/tail-scan/2026-07-13.md
```

- [ ] 在 `scripts/` 工作目录执行，不落盘、不推送、不跑 LLM；若 worktree 没有 `.env`，只在当前 shell source 原工作区 `.env`，禁止打印环境变量：

```bash
set -a
source /Users/alyx/tradeSystem/.env
set +a
export TRADE_DB_PATH=/Users/alyx/tradeSystem/data/trade.db
python3 main.py tail-scan daily --date 2026-07-13 --dry-run --no-llm
```

- [ ] 回到 worktree 根目录机械验证 dry-run 未创建报告：

```bash
test ! -e data/reports/tail-scan/2026-07-13.md
```

Expected：

- 命令退出码 0，且没有生成/改写 `data/reports/tail-scan/2026-07-13.md`。
- 每只候选都有 `[事实·主营]`、`[判断·产业链位置]` 和至少一个 `[事实·近期催化]` 占位或具体证据行。
- 具体催化均带日期与来源；老师/个股/行业标签不混用。
- 四川长虹若只有慧博关系项，只展示原始关系，不扩写为产业受益；九丰能源若命中老师笔记，明确标 `[老师观点·个股]`。
- 主营双源均失败时报告仍完成，文案显示源失败；真实无资料显示“暂无”，两者可区分。
- 筛选候选数、粗分权重、PK 状态机和 T-1 主线/概念语义无变化。

### 5.3 最终 diff 审查

- [ ] 检查范围和意外文件：

```bash
git status --short
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected：只有计划列出的代码、测试和文档；无 SQLite、真实报告、临时文件、用户既有未跟踪文件被 stage；`git diff --check` 无输出。

- [ ] 最终整合层再跑一次门1；任何修订后重跑 `make check-scripts`。
- [ ] 最终门2前台运行：

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
node "$COMPANION" adversarial-review --wait --base main "重点:bug/行为回归/边界/测试缺口/类型与接口一致性/事实判断合规/外部源降级/性能"
```

- [ ] 按严重/中等/轻微逐条处置；最多 3 轮。若 companion 缺失或未鉴权，停止并请用户执行 `/codex:setup`，不得跳过后声称完成。
- [ ] 最终汇报必须按仓库格式列出：pytest 通过数、`make check-scripts` 状态、真实 dry-run 摘要、门1和门2 finding 的“已修/已修+补测/反驳/defer+触发条件/接受为已知”处置标签、遗留限制。

> 完成标准：所有分层命令全绿、真实 dry-run 结构完整且不落盘不推送、最终门1 4 条和门2 6 条结束条件全部满足，才可声明功能完成。

## Commit 边界

按功能层次提交，禁止 `git add -A` / `git add .`，禁止把用户现有未跟踪文件带入：

1. `feat(providers): add stock business profile sources` —— provider 契约 + 双源实现 + provider 测试。
2. `feat(tail-scan): aggregate industry logic evidence` —— 新模块 + constants + 纯逻辑/只读聚合测试。
3. `feat(tail-scan): render per-stock industry logic` —— scorer/PK/renderer/CLI 文案 + 对应测试。
4. `docs(tail-scan): document industry logic enrichment` —— 5 个文档真源同步。

每个 commit body 写 What、Why、未做事项和实际 TDD 微循环数；同一逻辑单元的实现与测试必须同 commit；每个 commit 在其所属层测试全绿且阶段 review 收敛后再创建。未经用户明确 `push` 不推远端。

## 风险与回滚

| 风险 | 防护 | 回滚 |
|---|---|---|
| Tushare `stock_company` 权限/限流 | AkShare 仅补非 ok 代码；逐票状态可见 | 移除 capability 消费，报告回退原标签 |
| AkShare 单票慢或挂起 | 最多 4 worker 运行在隔离子进程；批次 20 秒后父进程硬终止并逐票标失败，不残留后台线程 | 关闭 AkShare 补齐，保留 Tushare 与缺失态 |
| 行业词误匹配 | 受控拆词、宽词停用、只标行业催化 | 禁用 `industry_info` 分支，不影响直接证据 |
| 慧博关系项被误写成催化 | 空 viewpoint 固定 `huibo_relation`，原样展示 source | 只保留有 viewpoint 项 |
| 报告过长 / PK 超时 | 产品最多 4、催化最多 2、文本最多 120 字 | PK 去掉新增字段，渲染仍可保留 |
| 新增强字段误改排序 | `_coarse_score()` 不改并加等分测试 | 移除 PK/score 接线，不影响 scanner |

本次无 schema、数据迁移或调度变更，因此回滚不需要数据库操作。

## 方案审查结论

只读方案审查 agent 已从可行性、健壮性、遗漏风险、测试覆盖、并行分组边界五个维度完成检查。初审发现 2 项高、5 项中、2 项可修低优问题，均已在本版计划中消化：

- `[高] 已修`：`registry.call/call_specific`、三个 reader 和 scorer 均增加分层异常收敛；新增 fallback 抛异常、聚合器整体抛异常、DB/慧博全失败测试，确保增强维度不能挡住整批报告。
- `[高] 已修+补测`：renderer 模块说明、disclaimer 和候选标题改成“排序为 `[判断]`，主营/催化按行内标签”；测试禁止旧“全为 `[判断]`”覆盖事实行。
- `[中] 已修+补测`：所有证据入口统一去换行并限制 `text<=120/source<=60`；空关系项丢弃。
- `[中] 已修+补测`：来源优先级显式固定为 `teacher > Huibo direct > Huibo relation > industry`，再按日期倒序并稳定去重。
- `[中] 已修+补测`：补齐主源整体失败、双源失败/缺失、日期三级 fallback、损坏 JSON、精确负向匹配、全源失败/真实无命中等具名测试，并把现有 Tushare/AkShare provider 回归套件加入验收。
- `[中] 已修`：放弃会残留挂起线程的 parent daemon 方案，改为 macOS `fork` 隔离进程内受控线程；deadline 到达后父进程硬终止子进程。
- `[低] 已修`：空候选立即返回 `{}`；dry-run 在隔离 worktree 前后用文件不存在断言做机械校验。
- `[边界] 通过`：G1/G2 文件互斥，G3 顺序依赖明确，G4 文档唯一归属，G5 review 有具体执行体；未命中 6 项并行反模式。

修订后无未处置的高/中问题，无阻塞执行的已知低优项；计划可进入用户选择的执行模式。
