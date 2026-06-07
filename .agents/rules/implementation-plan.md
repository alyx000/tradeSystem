---
description: 实施计划必须默认包含测试验证方案（无需用户触发词）
alwaysApply: true
---

# 实施计划与测试验证（默认强制）

## 适用范围

凡使用 **Plan mode**、**CreatePlan**，或向用户交付**实施计划 / 改造方案**（含架构、存储、多文件 CLI/API 变更），均视为「实施计划」。

## 默认要求（不必等用户说触发词）

1. **第一版计划即须包含「测试验证方案」**，与实现清单并列，不得事后才补（纯文案/单文件注释除外）。
2. 方案至少写明：
   - **分层**：数据层 / 业务或采集 / CLI 或 API / 可选 UI 中，本次涉及哪些层；
   - **验收命令**：具体 `pytest` 路径（或 `scripts/tests/` 下模块）+ 是否跑 `make check-scripts` / `make check`；
   - **完成标准**：哪些命令全绿、哪些 Warning 不可接受（与 [`dev-workflow.md`](dev-workflow.md) 一致）。
3. 若触发 [`test-design.md`](test-design.md) 中的条件（新模块、存储/协议变更、3+ 文件等），分层测试方案须**符合**该文件的金字塔与隔离原则。
4. **多 Agent 并行执行方案（复杂任务必须）**：当计划涉及 3 个以上独立改动项**且不命中下方「逃逸条款」**（见「输出计划前的强制步骤」步骤 1）时，必须在计划中输出「并行分组」章节，每组须含以下 7 个字段：
   - **角色（role）**：从主角色枚举中选 —— `前端 / 后端 / 测试 / 架构师 / DevOps / 文档 / 数据`；允许复合（例如「后端 + 测试」），但**禁止以「全栈」笼统化**；复合时须写明主角色 + 辅助角色。
   - **执行 Agent（executor）**：本组由谁执行，从 4 类中选一项 —— ① `Claude Code 主 agent`（当前会话主 Claude，串行，有完整对话上下文）；② `Claude Code subagent`（`Task` 工具，细分 `Explore`=readonly / `generalPurpose`=全工具）；③ `Codex`（`codex:codex-rescue` subagent 或 `codex-companion task --background`）；④ `Antigravity`（Antigravity CLI）。**一组只能一个执行体**；若同一职能需要多执行体协作（如 backend 实现 + codex review），拆成两组。选择规则见下方「执行 Agent 三轴决策」。
   - **专项关注（focus，条件必填）**：当本组交付物是横切改进（`安全 / 性能 / 可观测性 / 合规`）时必填；否则可省。
   - **职责边界（responsibility）**：一句话说明本组要交付的产物（功能 / 接口 / 测试 / 文档 等）。
   - **文件范围（files）**：本组将触碰的目录与文件清单；同一文件的不同区域改动尽量归入同组。
   - **禁区（off-limits）**：写成三类可执行清单 —— `允许改 / 不得改 / 需要先询问`；至少覆盖 ① 另一 agent 正在改的区域（防 StrReplace 冲突）；② 不属于本组职能的代码（防越权，例如「测试」组不得修改业务实现）。
   - **冲突标注**：明确标出多组共享的文件，并指定唯一归属组。

   执行阶段按各组「执行 Agent」字段并行启动：Claude Code subagent 用 `Task` 工具（`Explore` / `generalPurpose`）；Codex 用 `Agent(subagent_type="codex:codex-rescue")` 或 `codex-companion task --background`；Antigravity 用对应 CLI；Claude Code 主 agent 自行处理。一条消息内可同时发出多种执行体的调用。每组 prompt 中须把上述全部 7 个字段原文带入（含冲突标注与唯一归属组）。

   **禁区默认矩阵**（按主角色，作为模板，可在具体计划中覆盖；具体路径以仓库当前结构为准）：

   | 角色 | 允许改 | 不得改 | 需要先询问 |
   |---|---|---|---|
   | 前端 | `web/`、UI 组件、前端类型 | 后端 service、DB schema | API 契约（需后端确认） |
   | 后端 | `scripts/api/`、`scripts/services/` | UI 组件、DB migration | CLI 入口签名 |
   | 测试 | `scripts/tests/`、fixture | 业务实现 | 共享 helper |
   | 数据 | DB migration、ETL | UI、API 路由 | service 数据消费层 |
   | 架构师 | 跨层接口契约、目录结构 | 单层内部实现 | （上层裁定） |
   | DevOps | `Makefile`、CI、部署脚本 | 业务代码 | 入口脚本 |
   | 文档 | `docs/`、`AGENTS.md`、`CLAUDE.md`、`.agents/rules/` | 代码 | INDEX/索引同步项 |

   > **执行 Agent 不由职能决定**：即「前端」组不强制走主 agent、「文档」组不强制走 Antigravity —— 而是按下方「执行 Agent 三轴决策」判断。同一职能在不同上下文下可走不同执行体。

## 执行 Agent 三轴决策

并行分组的「执行 Agent」字段不按职能机械填空，而是按以下三轴判断 —— **是主线 × 打断频率 × 时长**。

### 三轴定义

- **是主线?** 主线 = 用户当前会话直接关注、需要主 agent 推进与回应的任务；子任务 = 主任务下的独立小组，或与主线解耦的边路工作。
- **打断频率?** 高 = 边做边需要与用户确认边界 / 看 lint / 改完顺手跑 review；低 = 边界清晰、prompt 可自包含、放手跑也不会跑偏。
- **时长?** 短 < 5 分钟 / 中 5-30 分钟 / 长 > 30 分钟（估算量级即可，不必精确）。

### 实施类任务决策表

| 主线? | 打断频率 | 时长 | 推荐执行 Agent | 备注 |
|---|---|---|---|---|
| 主线 | 高 | 任意 | Claude Code 主 agent | 用户随时打断 / 即时确认 |
| 主线 | 低 | 短 / 中 | Claude Code 主 agent | 短任务串行最简，BG 编排成本不划算 |
| 主线 | 低 | 长 | **Codex BG**（`codex-companion task --background`）| 主 agent 不阻塞，**必须 ScheduleWakeup 回访**（见全局 `~/.claude/CLAUDE.md` 「BG 委托」规则） |
| 子任务 | 低 | 短 / 中 | Claude Code `generalPurpose` subagent | 与其他组真并行；prompt 必须自包含 |
| 子任务 | 低 | 长 | **Codex BG** | 同上 |
| 子任务 | 高 | 任意 | （反模式）合并回主线由主 agent 处理 | "子任务 + 高频打断"自相矛盾，通常意味着边界没切清，先重切再决定 |

### 角色型执行体（独立于三轴，按职能直接选）

| 职能场景 | 执行 Agent | 备注 |
|---|---|---|
| 只读探查 / 文件勘察 / grep 概念 | Claude Code `Explore` subagent | readonly，成本最低；**禁止指派"写代码"任务给 Explore** |
| 方案级审查 / 代码级审查 / 独立第二意见 | Codex（`codex:codex-rescue`） | 独立模型链路，避开同源 bias；参见 [`post-dev-codex-review.md`](post-dev-codex-review.md) |
| 多语种 / 文档校对 / 备用第二意见 | Antigravity CLI | 性价比补充，目前用例少；无强场景时优先 Codex |

### 具名场景：三类常见任务的默认执行体

矩阵里有三个高频职能值得显式声明默认，其余职能按三轴决策推。

**(1) 后端 / 跨文件实现 —— 默认：Claude Code 主 agent**

理由：后端改动通常涉及 service / API / DB 多层联动，需要与用户即时确认契约、改完顺手跑 lint / 起门1（`/simplify` + `/code-review`）+ 门2 codex review，打断频率天然偏高。

升级 Codex BG 的触发条件（三条**同时**满足）：
1. 不是当前主线 —— 主任务是另一件事，后端只是顺带跑；
2. 边界清晰、prompt 可自包含 —— 用户与你已经把 service 契约、字段语义、影响面谈清，接下来只是"按方案落地"；
3. 估算时长 > 30 分钟 —— 短任务交 BG 等不偿失。

任一条件不满足 → 留在主 agent。

**(2) 测试编写 / 执行 / 修复 —— 默认：Codex BG**（`codex-companion task --background`）

理由：测试任务天然边界清晰（给定代码段 + 测试场景列表）、与用户实时交互需求低（用户不会边写测试边改 API 契约）、可长可短；主 agent 跑测试 = 阻塞主线程，Codex BG 是更优选择。**必须 ScheduleWakeup 回访**（见全局 `~/.claude/CLAUDE.md` 「BG 委托」规则）。

降级回主 agent 的触发条件（任一满足即降级）：
1. **TDD 紧密循环** —— R-G-R 微循环中，每次跑测试都决定下一步红/绿，主 agent 串行节奏更顺；
2. **短任务**（< 5 分钟）—— 跑现有套件验证一个小改动，主 agent 直接 Bash 跑比 BG 编排快；
3. **测试设计仍在迭代** —— fixture / mock 结构还没定稿，边界未清，留主 agent 与用户协商。

任一条件命中 → 留在主 agent；否则默认 Codex BG。

**(3) 前端编写 / 重构（`web/` 目录、React / Tailwind / 组件 / 样式）—— 禁止用 Codex 编写**

**硬规则：Codex 在前端编写任务上效果不达标，本仓库内一律不指派**（包括 `codex:codex-rescue` subagent 与 `codex-companion task --background`）。前端写代码任务只在以下三者中选：

- **Claude Code 主 agent**（默认 —— 主线 / 高频迭代 / 短任务）
- **Claude Code `generalPurpose` subagent**（独立子任务 / 并行）
- **Claude Code `Explore` subagent**（仅限只读勘察组件结构 / grep 概念）

例外（允许 Codex 介入前端的两种场景）：
- **代码 review / 第二意见**：`codex:codex-rescue` 看前端 diff 找 bug —— 审查是"看出问题"，不要求"写得好"，允许；
- **安全审查**（XSS / CSRF / 依赖漏洞 / 敏感信息泄漏）：Codex 的安全视角有价值，允许。

理由：Codex 在前端框架 idiom（React hooks / Tailwind class 习惯 / Vue composition / 组件命名 / 视觉细节）上质量不稳定；前端是用户直接看见的产物，质量代价高；Claude 系在前端项目里有更稳的输出基线。

### 反模式清单（并行分组评审时硬挡）

- **Explore 写代码**：Explore 是 readonly，出现在"实施"组里直接判错。
- **`generalPurpose` subagent 跑长任务**：与其他组并行的优势在中短任务；>30 分钟应升 Codex BG。
- **Codex BG 跑主线 + 高频打断任务**：BG 沉默模式与"边做边确认"冲突，会导致用户体感"主 agent 不知道 codex 在干啥"。
- **Codex 写前端**：`web/` / React / Tailwind / Vue / 组件类编写任务交给 Codex（任何形式）直接判错；Codex 在前端 idiom 上质量不稳定。审查 / 安全视角的 Codex 介入除外。
- **"全栈" / "什么都行" 笼统化**：角色与执行 Agent 都不能笼统化。

## 轻量分支（N < 3 但跨 ≥2 个职能）

不要求完整并行分组与并行启动，但计划中须新增「角色边界」小节，至少声明**角色 + 执行 Agent + 职责边界 + 禁区**四项；目的是让单 agent 在跨职能改动时仍保持边界自觉。

## 输出计划前的强制步骤（必须按顺序执行）

调用 `CreatePlan` 或交付计划文字之前，**必须先完成以下步骤，再开始写计划正文**：

**步骤 1：列出独立改动项 + 标注耗时 + 应用逃逸条款**

显式写出「共 N 项独立改动：A（预估 X 分钟，文件 `./a.py`）、B（预估 Y 分钟，文件 `./b.py`）、…」。每项**必须**标耗时数量级(短 <5min / 中 5-30min / 长 >30min)与主要文件位置，否则逃逸条款无法判定。

**逃逸条款**(满足任一即降级为单 agent 串行，跳过 7 字段并行分组；但若跨 ≥2 职能，仍须按「轻量分支」声明角色边界):

- **总预估时长 < 20 分钟** —— 小任务并行编排成本(prompt 自包含 + 起 subagent + 等回访)往往超过省下的时间;主 agent 串行最简
- **改动集中在同一文件 / 紧邻代码块** —— 并行 subagent 在同一文件做 StrReplace 冲突率高,串行反而更快;同一文件分给两组 = 反模式

**触发判定**:

- N ≥ 3 **且不命中逃逸条款** → 进入步骤 2(7 字段并行分组)
- 命中逃逸条款(无论 N 多少) → 跨 ≥2 职能时走「轻量分支」(角色 + 执行 Agent + 职责边界 + 禁区 四要素),否则跳到步骤 3
- N < 3 但跨 ≥2 职能 → 走「轻量分支」
- N < 3 且单一职能 → 跳到步骤 3

**步骤 2：草拟并行分组（N ≥ 3 时强制；N < 3 但跨 ≥2 职能时降级为「角色边界」小节）**
在写任何实现细节之前，先确定：
- 每组的**角色（role）** —— 从 `前端 / 后端 / 测试 / 架构师 / DevOps / 文档 / 数据` 中选，允许复合，禁止以「全栈」笼统化。
- 每组的**执行 Agent** —— 对照「执行 Agent 三轴决策」（是主线? 打断频率? 时长?）填出推荐执行体；若选 Codex BG，显式写明 ScheduleWakeup 回访延时与触发的 `jobId` 占位符；前端组**禁止**选 Codex 编写。
- 每组的**专项关注（focus）** —— 若涉及 `安全 / 性能 / 可观测性 / 合规` 等横切交付，必须显式写出。
- 每组的**职责边界**与**文件范围**。
- 哪些改动可以并行（文件不冲突）。
- 哪些文件被多组共用（指定唯一归属组）。
- 每组 Agent 的**禁区声明**（三类清单：允许改 / 不得改 / 需要先询问；含 StrReplace 冲突区 + 职能越权区）。

此步骤完成后，并行分组章节（或 N < 3 时的「角色边界」小节）才算确定，再继续写实现细节。

**步骤 3：确认测试验证方案**
计划正文中必须包含：分层 / 验收命令 / 完成标准，缺任何一项均视为计划不完整。

**步骤 3.1:阶段级 review 触发约束**(多阶段 plan 必须显式声明)

计划中如果包含**多阶段实施**(如阶段 1 数据层 / 阶段 2 service / 阶段 3 CLI / ...),plan 正文**必须显式规定**:

> "**每个大阶段结束 + 阶段测试通过后,立即跑一次门1（`/simplify` + `/code-review`）+ 门2（codex review）;不允许把多阶段的 review 全部攒到最后一个'后置审查'阶段才一次性做。**"

**为什么硬性规定**:trade_thesis 中间层 v24 实施(2026-05-17)的 plan 把 review 写成阶段 6 G4「后置审查」收尾,等于 6 个独立的"实质性改动"攒成一波过审。代价:
- codex 严重 1(事务边界)在阶段 4 写完就能查出来,但因为攒到阶段 6,变成"6 阶段写完才回头修阶段 4"
- 万一严重 1 是 schema 缺陷(阶段 1 留下来的),要回滚 5 个阶段才能补
- codex 一次性吞超长 diff,信号被稀释,review 质量下降

**plan 文档怎么写**(模板):每个阶段的"完成标准"段必须含一句:

```
> 完成标准:<阶段 pytest 路径> 全绿 + **立即跑门1（`/simplify` + `/code-review`）+ 门2 codex:codex-rescue review,
> 满足 [code-review-gate.md](.../code-review-gate.md) 4 条结束条件
> + [post-dev-codex-review.md](.../post-dev-codex-review.md) 6 条结束条件,才能进入下一阶段。**
```

阶段 N 的 G4「后置审查」可以保留作为**收尾整合**(对整个 PR 范围做最终独立审查),但不能替代阶段内的 review。

> 步骤 1 → 2 → 3 → 3.1 是固定顺序,不可跳过,不可事后补写。

## 方案 Review（CreatePlan 后强制）

`CreatePlan` 输出后、用户确认执行前，**必须启动一次 subagent 方案审查**：

1. 用 `Task` 工具启动一个 `explore` subagent（readonly），将完整方案正文传入 prompt，要求从**可行性、健壮性、遗漏风险、测试覆盖、并行分组角色 + 执行 Agent 边界自洽性**五个维度审查，按高/中/低优先级输出结论；其中「角色 + 执行 Agent 边界自洽性」需检查并行分组的角色、执行 Agent、专项关注、职责边界、文件范围与 off-limits 是否一致，并对照以下**反模式 6 项硬挡**(任一命中 = 方案不可推进,要求重派)：① Explore 被指派写代码；② `generalPurpose` subagent 跑 >30 分钟长任务；③ Codex BG 跑主线 + 高频打断任务；④ **Codex 写前端**（`web/` / React / Tailwind / Vue / 组件类）；⑤ 「全栈 / 什么都行」笼统化；⑥ codex review 的指派点未落到具体组。
2. 审查 prompt 须包含：
   - 完整的修改方案代码片段
   - 要求检查逻辑错误、边界条件、异常处理、测试数据合理性
   - 要求输出结构化审查报告
3. 收到审查结果后：
   - **高优先级**：必须修订方案后再向用户呈现
   - **中优先级**：原则上修订；若工作量大，注明「已知但暂缓」及理由
   - **低优先级**：记录在方案中的「已知低优项」，不阻塞执行
4. 将审查结论与修订要点写入计划文件的「方案审查结论」章节，向用户透明展示。

## 与现有规则的关系

- [`dev-workflow.md`](dev-workflow.md)：开发前设计验证方案、开发后跑通 pytest。
- [`test-design.md`](test-design.md)：重大变更的分层测试结构设计。

本规则解决的是：**计划文档阶段**就写出验证方案，避免「只写实现、后补测试」的遗漏。

## 例外

- 仅改文档、注释、格式化、与行为无关的配置文案：可注明「无单测变更」，不写分层测试。
