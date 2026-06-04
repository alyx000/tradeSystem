---
description: 每次完成实质性代码改动后，先用 /simplify 做质量清理，再用 /code-review 做门1 审查（替代旧的本地 Explore subagent 门），并按 4 条二值结束条件收敛
alwaysApply: true
---

# 开发后代码审查门1：/simplify + /code-review 规范

> **与 [`post-dev-codex-review.md`](post-dev-codex-review.md) 的关系**：本规则是**门1**（本地 `/code-review`，多 agent + 置信打分，快速发现明显 bug / 质量问题），codex review 是**门2**（独立 codex:codex-rescue，6 条结束条件 + 3 轮上限）。**串行不替代**：门1 的 4 条结束条件全部满足后，才进门2 codex review 流程；门2 通过后才向用户汇报。
>
> 单文件改 / 纯文档改 / 单测试增改 → 两条都不触发。

> **边界声明（务必区分两类用途）**：本规则只替代**开发后代码 diff 审查门**。[`implementation-plan.md`](implementation-plan.md) 的「方案 Review（CreatePlan 后强制）」用 `Task` 启动 Explore subagent 审**方案文档**，那条**不在本规则替代范围内**——`/code-review` 只能审 diff，审不了方案文档。别把方案 review 的 Explore 误当成被本门替代的对象。

## 触发条件

触发条件以 [`post-dev-codex-review.md` 的「触发条件」](post-dev-codex-review.md) 为**唯一真源**。本规则与 codex review 触发同步，不复写一份避免漂移。

## 执行步骤

### 第〇步（前置质量清理）：/simplify

在代码改动完成、pytest 跑通之后，**先跑一次 `/simplify`**（内置原生命令）：

1. `/simplify` 只做**质量清理**（reuse / simplification / efficiency / altitude），**默认自动 apply 到工作树，不查 bug**（bug 留给门1 `/code-review` 与门2 codex）。它是"激进质量自动清理器"，先把低级重复 / 冗余清掉，给后续审查门降噪。
2. **`/simplify` 改完后必须重跑 pytest / make check-scripts**，验证清理没破坏功能。

**两条不变量（必须守住）**：

- **门1 审查范围 = 实现 diff ∪ simplify diff**。`/simplify` 产出的代码紧接着就被门1 `/code-review` 审到，所以不存在"未审代码进 commit"。
- **`/simplify` 与门1 之间禁止插入 commit**；进门1 的 pytest "绿"**以 simplify 之后那次为准**，不是实现后那次（防 simplify 破坏功能后用旧绿蒙混进门）。

### 第一步（门1）：/code-review

跑 **`/code-review`（默认 effort=medium）**：

1. `/code-review` 审**当前 diff** 的 correctness bug + 残留质量（reuse / simplification / efficiency），多 agent + 置信打分，medium 档给"少而高置信"的结论。
2. 默认只报告；如需把质量类建议直接落地，用 `/code-review --fix`。
3. 按「高 / 中 / 低」优先级消化结论，每条给出文件+行号。重点检查：
   - Bug / 行为回归
   - 边界与健壮性（None、类型、迁移冲突）
   - 测试缺口（已有测试未覆盖的场景）
   - 类型/接口不一致（前后端 / CLI 参数与 queries 白名单）

### 第二步：分类处理审查结论

- **高优先级**：必须在本轮修复，修完重跑 pytest
- **中优先级**：原则上本轮修复；若工作量较大，在回复中明确列出「暂不修复原因」
- **低优先级**：记录，可推迟或作为 tech-debt

### 第三步：向用户输出验证报告

**呈现铁律**(memory `feedback_review_digest_before_report`):**先打处置标签,再附 review 细节**;**禁止把 `/code-review` 原文（含置信分）整段丢给用户**让其当"reviewer 的 reviewer"。每条必须带以下标签之一,且 1 行内让用户看懂"已处理"还是"等用户拍板":

- `已修` / `已修+补测` —— 代码或测试已落地
- `反驳` —— 不同意,**反驳理由必须落代码注释**(不只是汇报)
- `defer + 触发条件` —— 同意但本轮不改,显式写后续何时回头
- `接受为已知` —— 不改不修,但让用户知道(仅限「低」级)

格式:

```
门1 /code-review 结论:
- [高] 已修:<一句话描述>(文件:行号)
- [中] 已修+补测:<描述>(测试用例名)
- [中] 反驳:<理由一句>(反驳已落 文件:行号 注释)
- [中] defer + 触发条件:<原因 + 后续何时改>
- [低] 接受为已知:<描述>

验证结果:
- [✅/❌] pytest:<通过数/失败说明>
- [✅/❌] make check-web(如涉及前端)
```

**反例(违反"先判断后呈现")**:

```
[高] 现象:... 为什么是问题:... 修正方向:...   # ← 把 /code-review 原文整段抛给用户
```
用户拿到这种汇报会被迫"当 reviewer 的 reviewer";Claude 必须先消化、判断、然后只把结论 + 一行支撑给用户;review 原文留在代码注释 / 内部审查报告附件里。

## 三道质量关分工（重叠是设计意图，不是冗余）

| 维度 | `/simplify`（前置） | `/code-review`（门1, medium） | codex（门2） |
|---|---|---|---|
| correctness bug | ❌ 不查 | ✅ 查 | ✅ 查（独立第二意见） |
| 简化/复用/效率 | ✅ 查，最激进，**默认自动改** | ✅ 复查残留 + 带 bug 视角 | —（不主查） |
| 模型来源 | Claude 同源 | Claude 同源（多 agent + 置信分） | **跨模型**（避同源 bias） |
| 结束条件 | 仅"重跑 pytest 绿" | 4 条 + 软上限 2 轮（本节） | 6 条 + 3 轮硬上限（门2 规则） |

- **bug 维度故意查两遍**（`/code-review` 同源 + codex 跨源）：跨模型独立第二意见是 codex 门不可被 `/code-review` 替代的**唯一理由**，不是浪费。
- **质量维度分工**：`/simplify` 粗清理 + 自动改，`/code-review` 复查 simplify 后的残留并带 bug 视角，互补。

## Review 完成的 4 条二值结束条件（全部满足才算收敛）

每条都必须达到，缺一不可；否则不得进入门2 codex review 流程：

1. **所有「高」优先级问题已修复**——代码或测试中有对应修订；不接受「已知悉」「后续考虑」。
2. **所有「中」优先级问题已显式处置**——三选一：① 已修；② 显式 defer 并写理由（含触发条件 / 后续计划）；③ 反驳并说明（如属预期行为或可接受折中）。不允许沉默。
3. **「低」优先级问题至少在汇报中被提及**——可以不改，但用户须知道存在。
4. **门1 修订后 pytest / make check-scripts 重跑通过**——不能拿改动前那次的绿当数；前端改动须额外跑 `make check-web`。

## 防无限循环：门1 软上限 2 轮

- **同一改动周期门1 最多 2 轮 `/code-review`**。`/code-review` 是本地多 agent + 置信打分，比单个 Explore 重，旧"不设上限"的理由（Explore 成本极低）已部分失效。
- 若同一类问题在门1 反复出现，**多半是改动设计本身有问题，应停下来重新设计而不是反复刷**。
- 门2 codex 的 3 轮硬上限**不变**（见 `post-dev-codex-review.md`）。

## 注意事项

- `/code-review` 结论**不等于必须全改**：评估合理性后再决定是否修复
- 若发现的问题属于「预期行为」或「可接受折中」，须在报告中说明理由
- 不要因为审查建议而引入过度工程（如为了测试而改变已验证的 API 契约）
- **`/code-review ultra`（云端多 agent，计费、Claude 无法自动启动）只能由用户手动跑**：大改动 / 合并前在汇报里**提示**用户考虑手动 `/code-review ultra`，但它**不是结束条件、不阻塞 commit**（Claude 无法执行的步骤不能设成二值门槛）。

## Why

防止：① 代码改动后直接交付，跳过快速本地审查；② 高优先级问题被「中/低」挤压沉默；③ 门1 修订后没再跑 pytest，引入新回归；④ 本可在门1 挡住的明显 bug 进入门2 codex review 浪费成本（codex review 是独立模型调用，比本地 `/code-review` 贵得多）；⑤ 质量维度长期无人把关（旧双门只查 bug，不查简化 / 复用 / 效率）。

## How to apply

- **检查时机**：代码改动 + pytest 跑通后**立刻** `/simplify` → 重跑 pytest → 门1 `/code-review`；不要先汇报再补。
- **失败时机**：转入门2 codex review 之前自检 4 条结束条件，缺任何一条就回去补。
- 与门2 codex review 串行，不并行；门1 通过后才进门2 流程。
