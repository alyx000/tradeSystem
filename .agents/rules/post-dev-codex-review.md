---
description: 实质性代码改动完成后，必须主动跑 codex:codex-rescue review，并按 6 条二值结束条件收敛，防止无限循环
alwaysApply: true
---

# 开发后 Codex Review 规范（防无限循环）

> **与 [`code-review-gate.md`](code-review-gate.md) 的关系（触发矩阵）**：两条规则触发条件相同，但执行**串行**，不替代：
> 1. 改动完成 + 测试通过 → **先**跑门1（`code-review-gate.md`：`/simplify` 清理 → `/code-review` 本地多 agent 审查，快速发现明显 bug / 质量问题）；
> 2. 门1 高优先级问题修完后 → **再**跑本规则门2（codex:codex-rescue，独立审查 + 6 条结束条件 + 3 轮上限）；
> 3. codex review 通过后才向用户汇报。
>
> 单文件改 / 纯文档改 / 单测试增改 → 两条都不触发。

## 触发条件

完成一轮**实质性代码改动**后必触发(**本节同时是 [`code-review-gate.md`](code-review-gate.md) 的触发真源**,两条规则触发同步):

- 修改了 3 个或以上文件的业务逻辑
- 引入新函数、新 CLI 命令、新 DB 表/列、新 API 路由
- 涉及 schema 迁移、跨数据层 / 跨服务变更
- 前端类型接口或核心契约变更

纯文档 / 注释 / 格式化 / 单文件 typo 修复不触发。

### 关于"实质性"的边界——禁止攒到收尾才一次性 review

"实质性代码改动"包括但不限于:

- 单 PR / 单 feature 完整交付
- **多阶段 plan 中的单个大阶段交付**(如 plan G1 数据层、G2 service、G3 CLI 各算一次)
- 复杂任务的"自然单元"完成(如一组同主题相关函数 + 测试)

**绝对禁止**:把多个阶段的 review 全部攒到 plan 收尾的 G4 才一次性做。

**Why**:trade_thesis 中间层 v24 实施(2026-05-17)按 plan G4 把 review 推到阶段 6 收尾,等于把 6 个独立的"实质性改动"攒成一波过审。代价:
- codex 严重 1(事务边界)其实在阶段 4 写完就能查出来,但因为攒到阶段 6 才 review,变成"已经写完 6 阶段才回头修阶段 4 的代码"
- 没出大事是运气;如果严重 1 是个 schema 缺陷(阶段 1 留下来的),要回滚 5 个阶段
- 单阶段 diff vs 全实施 diff,后者 review 质量明显下降(codex 一次性吞 2645 行 diff,信号被稀释)

**How to apply**:**每个大阶段结束 + 阶段测试通过后,立即跑门1（`/simplify` + `/code-review`）+ 门2（codex review）**;不允许"等所有阶段都做完才一次性 review"。对应 [`implementation-plan.md`](implementation-plan.md) 步骤 3 阶段触发约束。

## 强制流程

1. **代码改动完成、pytest / make check-scripts 跑通后**，立刻用 `Agent(subagent_type="codex:codex-rescue")` 把改动 diff + 关键文件路径发给 codex，明确要求：
   - **前台运行(foreground),禁止 `--background` / BG 模式**——见下方"为什么 review 必须前台"。
   - 按"严重 / 中等 / 轻微 / 无问题"分级输出
   - 每条给"现象 / 为什么是问题 / 修正方向"
   - 重点检查：bug / 行为回归 / 边界 / 测试缺口 / 类型与接口一致性 / 安全
2. 不要等用户提醒，不要在汇报"完成"之后才补审查。
3. codex review 与代码修改是同步动作：先审查、按结论改代码 / 测试、再向用户汇报。

### 为什么 review 必须前台(foreground)

post-dev codex review 是**卡门型门禁**:短(截图实测 ~3 分钟回)、必须当场拿到结论才能推进、ghost 一旦发生会堵死整条 dev 流。

- **派 `codex:codex-rescue` 做 review 时,prompt 里显式写"前台运行 / foreground / 不要 --background"**。rescue 子 agent 自身路由对"小而明确"任务本就 prefer foreground,但 review diff 可能被它误判为"复杂"而转 BG —— 显式钉死前台,杜绝这种漂移。
- **禁止把 review 交给 `codex-companion task --background` 或任何返回 `started in the background as task-` 的 BG 路径**。BG 静默路径是"幽灵 job"(worker 猝死、状态永久卡 running)的唯一温床;前台 review 同步返回,根本不进这个风险面。
- BG 模式仍保留给 **>30min 的实现 / 测试 / 大规模生成**(沿用 [`implementation-plan.md`](implementation-plan.md) 三轴),且由全局守卫脚本 `~/.claude/scripts/codex-reap-ghosts.py` + 回访机制兜底(见全局 `~/.claude/CLAUDE.md` 「委托 Codex 走 background 模式时必须主动回访」)。

## Review 完成的 6 条二值结束条件（全部满足才算收敛）

每条都必须达到，缺一不可：

1. **所有"严重"问题已修复**——代码或测试中有对应修订；不接受"已知悉""后续考虑"。
2. **所有"中等"问题已显式处置**——三选一：① 已修；② 显式 defer 并写入 follow-up TODO（须含触发条件 / 责任人 / 截止）；③ 反驳并给理由。不允许沉默。
3. **"轻微"问题至少在汇报中被提及**——可以不改，但用户须知道存在。
4. **codex 提出的问题不在最新版本中"消失"**——每条都能在改动 / 测试 / TODO / 反驳中找到痕迹。**反驳必须落到代码注释**（说明为什么这条意见不采纳）而非只写在 plan / 汇报里——代码注释会被后续读者看到，plan 与汇报用过即丢，下次同位置改动时反驳的判断依据就消失了。
5. **若 codex 结论是"不可推进"或"需要重新设计"**——必须重做后再跑一次 codex review，直到结论可推进；**但每个改动周期最多 3 轮 review**（见下"防无限循环上限"）。
6. **向用户汇报时附 codex review 摘要**——列出严重 / 中等 / 轻微的处置清单,让用户知道审查闭环已完成。**呈现铁律(memory `feedback_review_digest_before_report`)**:每条必须先打处置标签(`已修` / `已修+补测` / `反驳` / `defer + 触发条件` / `接受为已知`),再附支撑细节;**禁止把 codex 原文整段抛给用户**让其当"reviewer 的 reviewer"。反驳必须落代码注释(第 4 条已规定),呈现给用户时只需要一行结论 + 注释路径(如 `反驳:占位符是设计意图(见 lifecycle.py:131 注释)`)。

## 防无限循环上限

- **同一改动周期最多 3 轮 codex review**（v1 → v2 → v3）。
- 若第 3 轮仍有"严重"未消，**必须停下来向用户报告：列出 codex 反复指出的问题、自己的处置思路、为什么改不动**，让用户决策（可能需要拆分改动 / 调整设计 / 接受 known issue）。
- 不允许"为了让 codex 通过而无意义地大改代码"。codex 是审查者不是设计者，意见仅供参考；处置权在 Claude + 用户。

## 与方案级 codex review 的区分

| 维度 | 方案级（plan / 设计） | 代码级（本规则） |
|---|---|---|
| 触发 | 任何方案级产物呈报前 | 实质性代码改动完成后 |
| 内容 | 方案文档全文 | 代码 diff + 关键文件路径 |
| 关键工件 | plan v2 + 修订对照表 | 修订后的代码 + TODO + 测试 |
| 结束条件 | 6 条（同本规则结构） | 6 条（本节） |
| 上限 | 不限轮次（plan 可反复改） | 3 轮（代码改动有边际成本） |

## Why

防止：① 跳过审查直接交付半成品；② 反复 review 但每轮指出的问题都"消失"在新版本中；③ 无限 review 循环消耗时间。

## How to apply

- **检查时机**：代码改动 + 测试通过后立刻审查；不要先汇报再补。
- **失败时机**：向用户汇报"完成"前，自检 6 条结束条件，缺任何一条就回去补；超过 3 轮就停下来让用户决策。
- 与方案级 codex review 互补，不互相替代。
