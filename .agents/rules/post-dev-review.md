# 开发后代码审查门（分档 + 双门并行）

> 本文件合并了旧 `code-review-gate.md`（门1）与 `post-dev-codex-review.md`（门2）。
> **两个门都还在、职责不变**；改的只是「何时跑」（分档）、「怎么跑」（并行）、「怎么收尾」（3 条结束条件）。

## 触发条件（唯一真源）

完成一轮**实质性代码改动**后触发：

- 修改 3 个或以上文件的业务逻辑
- 引入新函数、新 CLI 命令、新 DB 表/列、新 API 路由
- 涉及 schema 迁移、跨数据层 / 跨服务变更
- 前端类型接口或核心契约变更

**免门**：纯文档 / 注释 / 格式化 / 单文件 typo / 单测试增改。

### 禁止攒到收尾

「实质性改动」包括**多阶段 plan 中的单个大阶段**（如 G1 数据层、G2 service、G3 CLI 各算一次）。**禁止**把多个阶段的 review 攒到 plan 收尾一次性做。

**Why**：trade_thesis v24（2026-05-17）把 6 个阶段攒成一波过审——codex 报的严重 1（事务边界）其实阶段 4 写完就能查出，结果变成写完 6 个阶段才回头修阶段 4；且 codex 一次性吞 2645 行 diff，信号被稀释。

> **例外——紧耦合层**：数据模型变更 + 消费方必须同步改才绿的，算 **1 个单元**，一次审、一次提交，不按阶段硬拆（拆开审只会让 reviewer 对着「消费方还引用已删字段」的半破状态报一堆噪音）。

## 第一步：定档

按本次 diff **触碰的路径**定档。**拿不准一律按双门**（fail-closed）。

| 档 | 命中条件 | 跑什么 |
|---|---|---|
| **双门** | ① `scripts/db/schema.py` / `scripts/db/migrate.py`（schema / 迁移）<br>② 写入与状态流转：`scripts/db/cli.py` 写入子命令、`scripts/services/**` 的状态机与写库路径、`scripts/api/routes/*.py` 的 POST / PUT / DELETE<br>③ 副作用出口：`scripts/pushers/**`、`deploy/**`、`main.py` 的 `cmd_post` / `schedule` 接线<br>④ 跨层契约：新增顶层 subparser、新 API 路由、provider capability 注册、前后端共享类型<br>⑤ 凭据 / env 读取路径 | 门1 ∥ 门2 |
| **单门** | 纯渲染 `**/renderer.py` `**/formatter.py` 与报告文案；只读子命令（`list` / `show` / `trend` / `signals` / `pool`）；`**/constants.py` 阈值调参；测试文件重构 | 只跑门1（可加 `--fix`） |
| **单门 + 前端专项** | `web/src/**` 纯 UI（非契约层） | 门1 + `ui-reviewer` agent，**不跑 codex**（项目规则本就禁 codex 审前端语义） |

多阶段 plan **逐阶段各自定档**：典型 5 阶段 plan 通常只有 1~2 个阶段命中双门。

## 第二步：跑门（双门并行）

前置：改动完成且 `make check-scripts` 已绿。

```
   ├─ 门2 codex（Bash run_in_background 先起）
   └─ 门1 /code-review（前台）
   ↓ 两边回齐 → 合并去重 findings → 一次性修 → 重跑测试 → 汇报
```

### 门2 —— codex 原生 adversarial-review

跨模型独立第二意见（避同源 bias）。它**自读 git 状态**，无需手工拼 diff / 贴文件路径，输出结构化分级 findings。

```bash
COMPANION="$(ls -t /Users/alyx/.claude/plugins/cache/openai-codex/codex/*/scripts/codex-companion.mjs 2>/dev/null | head -1)"
# 改动在工作区未提交（默认 auto / working-tree）：
node "$COMPANION" adversarial-review --wait --model gpt-5.5 "重点:bug/行为回归/边界/测试缺口/类型与接口一致性/安全"
# 改动已提交在 feature 分支：追加 --base main（或分支起点 commit）
```

用 Bash 工具的 `run_in_background: true` 起它，随即前台跑门1。

- **`--model gpt-5.5` 必带**：`~/.codex/config.toml` pin 了 `gpt-5.6-sol`，ChatGPT 账号鉴权下后端拒绝报 400。哪天 config 改了可去掉，带着也安全。
- **`--wait` 必带、`--background` 禁用**：`--wait` 让 codex 侧同步返回。禁的是 `codex-companion task --background`（codex 自身静默 BG → 幽灵 job，须 `ScheduleWakeup` 回访）。**Bash 工具的 `run_in_background` 不是那条路径**——harness 跟踪进程、退出即自动回调，不进幽灵风险面。两者别混。
- companion 路径随插件升级漂移，**用 glob 取最新，别硬编码版本号**。
- 报 codex 缺失 / 未鉴权 → 停下让用户 `/codex:setup`。
- **严重度映射**（原生 4 档 → 本项目 3 档）：`critical` + `high` → **严重**；`medium` → **中等**；`low` → **轻微**。
- 用 `adversarial-review` 而非中性的 `review`：对抗式立场（「尽力证明这次改动不该上线」）最贴合独立第二意见。
- **方案级（设计文档）review 不走这条**：原生 reviewer 的 target 只有 `uncommittedChanges` / `baseBranch`，本质是 git diff 审查器，喂不进设计 prose。方案级独立意见按用户明确要求触发，不属本规则门禁。

### 门1 —— `/code-review`

默认 `effort=medium`，本地多 agent + 置信打分。审同一 diff 的 correctness bug + 质量残留（reuse / simplification / efficiency / altitude）。

- **双门并行时禁用 `--fix`**：`--fix` 会改工作区，而 codex 并行读 git working tree 会读到移动靶。质量类 finding 与 bug finding 合并成一次修复，反而少跑一次全量测试。
- **单门路径无并行冲突**，可直接 `/code-review --fix`。

### `/simplify` 已从门禁摘除

降级为**可选工具**：改动本身是重构、或有大量重复代码时手动跑，跑完自己重跑测试。它的质量维度由门1 兜底（门1 本来就复查 reuse / simplification / efficiency）。

## 第三步：合并处置

两边 findings **合并去重**（同 `file:line` 同性质算一条，取描述更具体的那条），**一次性修完再重跑测试**——不要修一门再修另一门。

**收到 review 不等于全盘照做**：属预期行为或可接受折中的，反驳；但反驳须有技术理由，不是嫌麻烦。也不要为了让 reviewer 通过而无意义地大改代码——reviewer 是审查者不是设计者，处置权在 Claude + 用户。

## 结束条件（3 条，全部满足才算收敛）

1. **严重全消 + 绿**：门1「高」∪ 门2 `critical` / `high` 全部已修；修后 `make check-scripts` 绿，触碰前端另跑 `make check-web`。不接受「已知悉」「后续考虑」，也不能拿改动前那次的绿当数。
2. **中等零沉默**：每条中等打标签 `已修` / `反驳` / `defer`。**反驳理由必须落代码注释**（说明为什么不采纳——注释会被后续读者看到，汇报与 plan 用过即丢，下次改同一位置时判断依据就消失了）；`defer` 须写触发条件。**轻微问题至少在汇报里出现一行**（可以不改，但用户须知道存在）。
3. **2 轮上限**：合并 review 最多 2 轮。第 2 轮仍有严重未消 → **停下来交用户决策**（列出反复指出的问题、自己的处置思路、为什么改不动），不自己刷。同一类问题反复出现，多半是改动设计本身有问题，应重新设计而非反复刷门。

> `/code-review ultra`（云端多 agent、计费、Claude 无法自动启动）只能由用户手动跑。大改动 / 合并前可在汇报里**提示**用户考虑，但它**不是结束条件、不阻塞 commit**（Claude 无法执行的步骤不能设成二值门槛）。

## 汇报格式（硬约束）

**先打处置标签，再附一行支撑。禁止把 review 原文整段抛给用户**让其当「reviewer 的 reviewer」——判断责任必须留在 Claude 这边。

```
Review 结论（档位：双门 / 单门 / 单门+前端）：
- [严重] 已修：<一句话>（file:line）
- [中等] 已修+补测：<一句话>（测试用例名）
- [中等] 反驳：<理由一句>（注释已落 file:line）
- [中等] defer：<原因 + 何时回头>
- [轻微] 接受为已知：<一句话>

验证：
- [✅/❌] make check-scripts：<通过数 / 失败说明>
- [✅/❌] make check-web（如涉及前端）
```

**反例**：把 `[高] 现象:… 为什么是问题:… 修正方向:…` 原样贴给用户。

## Why

防止：① 跳过审查直接交付半成品；② 高优先级问题被中 / 低挤压沉默；③ review 指出的问题在新版本里静默消失；④ 无限 review 循环；⑤ 质量维度（简化 / 复用 / 效率）长期无人把关。

**分档与并行是为了让上面五条跑得起来**——旧流程是 3 引擎串行（`/simplify` → 门1 → 门2）+ 3~4 次全量 pytest + 10 条结束条件自检，成本高到会诱发「攒到收尾」和跳步，那才是真正的效果损失。

## 配套规则

- 计划阶段的范围与验证方案：[`implementation-plan.md`](implementation-plan.md)
- 开发前后验证与测试报告：[`dev-workflow.md`](dev-workflow.md)
- 分层测试设计：[`test-design.md`](test-design.md)
- 审查通过后如何切 commit：[`tdd-commit-strategy.md`](tdd-commit-strategy.md)
