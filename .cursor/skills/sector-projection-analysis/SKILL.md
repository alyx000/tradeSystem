---
name: sector-projection-analysis
description: 根据《0524板块推演术》和当日盘后预填数据，对热门板块做阶段、节奏、回流、结束风险与逻辑审美分析，输出次日优先级、板块结论与跟踪方向
version: "1.0"
---

# Skill: 板块推演分析

本文件只保留**工作流和输出格式**。具体方法论细节、判断口径和概念说明见 [references/methodology.md](references/methodology.md)。

## 使用场景

当用户说：

- 「帮我做板块推演」
- 「分析一下今天最值得看的板块」
- 「次日重点看哪些板块」
- 「这个板块现在属于什么阶段」
- 「判断一下板块回流 / 是否结束 / 逻辑审美」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make review-prefill DATE=YYYY-MM-DD
make market-envelope DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

需要调试时再用 API：

- `GET /api/review/{date}/prefill`
- `GET /api/market/{date}`

## 核心流程

1. 先确认分析日期，优先用盘后日期。
2. 拉取 `review prefill`，板块分析以这些字段为主：
   - `review_signals.sectors.projection_candidates`
   - `review_signals.sectors.strongest_rows`
   - `review_signals.sectors.ths_moneyflow_rows`
   - `review_signals.sectors.dc_moneyflow_rows`
   - `market.sector_rhythm_industry`
   - `main_themes`
   - `teacher_notes`
   - `industry_info`
3. 对每个候选板块至少给出 6 个结论：
   - 大级别阶段：`将成龙 / 主升 / 震荡 / 二波 / 衰退`
   - 连接点判断：`加强 / 减弱 / 不清楚`
   - 是否匹配大势所需节奏
   - 回流判断：`预期回流 / 仅跟踪 / 不看`
   - 是否处于充分演绎后的结束风险
   - 逻辑审美：增量、落地、容量、谁主导
4. 对候选板块做横向比较，不追求“每个都判断得特别准”，重点是找出：
   - 最可能成为当天最强的
   - 最值得次日继续跟踪的
   - 已经不值得再看的
5. 输出时优先引用系统给出的 `emotion_leader / capacity_leader`；若为空，就明确写“系统暂无高置信领涨锚点”，不要自行硬编。
6. 若用户要把结果写入复盘，切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。若用户要转次日计划，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。

## 输出格式

默认按下面四段输出：

1. `今日板块结构`
   - 当前主线 / 最强 / 轮动 / 活跃震荡板块
2. `候选板块矩阵`
   - 每个板块一行：阶段、连接点、回流、结束风险、情绪龙头、容量中军、核心依据
3. `次日优先级`
   - `优先跟踪`
   - `仅观察`
   - `暂不看`
4. `关键不确定性`
   - 哪些地方属于判断而非事实

## 禁止事项

- 不要把 `[判断]` 伪装成 `[事实]`。
- 不要因为一个板块涨得多，就自动等同于“值得做”。
- 不要纠缠精确区分 `发酵/高潮` 这类细枝末节，优先看连接点是加强还是减弱。
- 不要在 `emotion_leader / capacity_leader` 为空时，自行脑补“领涨股”。
- 不要把老师笔记自由文本直接当成板块名。

## 最小验证

- `make review-prefill DATE=YYYY-MM-DD` 能返回 `projection_candidates`。
- 输出中至少区分了 1 个优先跟踪板块和 1 个不看板块；若当天没有，明确写“今日无高置信板块机会”。
- 若用户提供的板块判断和系统事实冲突，明确指出冲突点，而不是直接顺着写。

## 切换条件

- 若用户要完整复盘，切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。
- 若缺少当日盘后数据，切到 [`market-tasks/SKILL.md`](../market-tasks/SKILL.md)。
- 若用户要沉淀老师观点或行业信息，切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。

