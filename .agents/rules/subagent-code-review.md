---
description: 每次完成实质性代码改动后，自动启动 subagent 做 code review，并根据结论修正代码
alwaysApply: true
---

# 开发后 Subagent Code Review 规范

> **与 [`post-dev-codex-review.md`](post-dev-codex-review.md) 的关系**：本规则是**第一道**审查（本地 Explore，快速发现明显 bug / 测试缺口），codex review 是第二道（独立 codex:codex-rescue，6 条结束条件 + 3 轮上限）。**串行不替代**：subagent 4 条结束条件全部满足后，才进 codex review 流程；codex review 通过后才向用户汇报。
>
> 单文件改 / 纯文档改 / 单测试增改 → 两条都不触发。

## 触发条件

触发条件以 [`post-dev-codex-review.md` 的「触发条件」](post-dev-codex-review.md) 为**唯一真源**。本规则与 codex review 触发同步，不复写一份避免漂移。

## 执行步骤

### 第一步：启动 subagent 审查

在代码改动完成、pytest 跑通之后，**立即**用 `Task` 工具启动一个 `explore` subagent（readonly 模式），要求它：

1. 读取本次改动涉及的所有文件
2. 按「高 / 中 / 低」优先级输出发现，每条给出文件+行号+建议
3. 重点检查：
   - Bug / 行为回归
   - 边界与健壮性（None、类型、迁移冲突）
   - 测试缺口（已有测试未覆盖的场景）
   - 类型/接口不一致（前后端 / CLI 参数与 queries 白名单）

### 第二步：分类处理审查结论

- **高优先级**：必须在本轮修复，修完重跑 pytest
- **中优先级**：原则上本轮修复；若工作量较大，在回复中明确列出「暂不修复原因」
- **低优先级**：记录，可推迟或作为 tech-debt

### 第三步：向用户输出验证报告

格式：

```
subagent 审查结论：
- [高] 已修复：<描述>（文件:行号）
- [中] 已修复：<描述>
- [中] 暂缓：<原因>
- [低] 已知：<描述>

验证结果：
- [✅/❌] pytest：<通过数/失败说明>
- [✅/❌] make check-web（如涉及前端）
```

## Review 完成的 4 条二值结束条件（全部满足才算收敛）

每条都必须达到，缺一不可；否则不得进入 codex review 流程：

1. **所有「高」优先级问题已修复**——代码或测试中有对应修订；不接受「已知悉」「后续考虑」。
2. **所有「中」优先级问题已显式处置**——三选一：① 已修；② 显式 defer 并写理由（含触发条件 / 后续计划）；③ 反驳并说明（如属预期行为或可接受折中）。不允许沉默。
3. **「低」优先级问题至少在汇报中被提及**——可以不改，但用户须知道存在。
4. **subagent 修订后 pytest / make check-scripts 重跑通过**——不能拿改动前那次的绿当数；前端改动须额外跑 `make check-web`。

## 注意事项

- subagent 结论**不等于必须全改**：评估合理性后再决定是否修复
- 若 subagent 发现的问题属于「预期行为」或「可接受折中」，须在报告中说明理由
- 不要因为 subagent 建议而引入过度工程（如为了测试而改变已验证的 API 契约）
- **本规则不设轮次上限**：subagent 审查成本低（本地 Explore），目标是把明显问题挡在 codex review 之前；如果反复同一类问题，多半是改动设计本身有问题，应停下来重新设计而不是反复刷 subagent

## Why

防止：① 代码改动后直接交付，跳过快速本地审查；② 高优先级问题被「中/低」挤压沉默；③ subagent 修订后没再跑 pytest，引入新回归；④ 本可在本地审查阶段挡住的明显 bug 进入 codex review 浪费成本（codex review 是独立模型调用，比 Explore 贵得多）。

## How to apply

- **检查时机**：代码改动 + pytest 跑通后**立刻**审查；不要先汇报再补。
- **失败时机**：转入 codex review 之前自检 4 条结束条件，缺任何一条就回去补。
- 与 codex review 串行，不并行；subagent 通过后才进 codex review 流程。
