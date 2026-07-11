---
name: sector-projection-analysis
description: 根据《0524板块推演术》和当日盘后预填数据，对热门板块做阶段、节奏、回流、结束风险与逻辑审美分析，输出次日优先级、板块结论与跟踪方向
version: "1.1"
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
python3 main.py review factor-score --date YYYY-MM-DD --input-by USER [--json]
python3 main.py review factor-metrics [--days 20] [--json]
```

需要调试时再用 API：

- `GET /api/review/{date}/prefill`
- `GET /api/market/{date}`
- `POST /api/review-factors/{date}/score`
- `GET /api/review-factors/metrics?days=20`

**定量板块联动证据**（定性推演的客观补充）：可调取 `python3 main.py sector-correlation matrix --date YYYY-MM-DD`（见 [`market-tasks`](../market-tasks/SKILL.md) 的 sector-correlation），拿板块×板块联动/跷跷板、板块×指数同向/逆向（双窗 20/60）。本 skill 的"节奏匹配 / 回流预期 / 连接点"判断可用相关性数据交叉印证，但二者方法论不同（此为定性推演、那为定量统计），不互相替代。

## 核心流程

1. 先确认分析日期，优先用盘后日期。
2. 拉取 `review prefill`，板块分析以这些字段为主：
   - `review_signals.sectors.projection_candidates`
   - `review_signals.sectors.strongest_rows`
   - `review_signals.sectors.industry_moneyflow_rows`
   - `review_signals.sectors.concept_moneyflow_rows`
   - `market.sector_rhythm_industry`
   - `main_themes`
   - `teacher_notes`
   - `industry_info`
3. 20 日影子期可追加三位一体双层评分作**对照**：第 1 层固定比较 `market_node / sector_rhythm / style_regime / leader_signal`，程序确认主因子后把该因子的受控证据卡传入第 2 层，对确定性排序中的最多 6 个 `core` 板块评分；`watch/context` 不得升格，非客观 leader context 不得伪装为事实。规则门、证据质量、封顶和规则排序不喂给 LLM，LLM 分只代表相对重要度，不代表胜率。人工确认会在原子事务内重建当前预填与第 1～6 步证据摘要；证据变化必须先重跑评分，旧 run 不得继续确认；确认后实际改写第 1～6 步会自动清除旧决定。
4. 对每个候选板块至少给出 6 个结论：
   - 大级别阶段：`将成龙 / 主升 / 震荡 / 二波 / 衰退`
   - 连接点判断：`加强 / 减弱 / 不清楚`
   - 是否匹配大势所需节奏
   - 回流判断：`预期回流 / 仅跟踪 / 不看`
   - 是否处于充分演绎后的结束风险
   - 逻辑审美：增量、落地、容量、谁主导
5. 对候选板块做横向比较，不追求“每个都判断得特别准”，重点是找出：
   - 最可能成为当天最强的
   - 最值得次日继续跟踪的
   - 已经不值得再看的
6. 输出时优先引用系统给出的 `emotion_leader / capacity_leader`；若为空，就明确写“系统暂无高置信领涨锚点”，不要自行硬编。
7. 双层评分只展示系统建议；人工接受、改选或标记看不懂，以及严格 T+1 回验，统一切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。板块层失败时保留已成立主因子并展示确定性 `core` 排序，不得伪造板块分。
8. 若用户要把定性结果写入复盘，切到 `daily-review`。若用户独立提出次日计划需求，可切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)，但 factor 分数、确认、T+1 回验与 20 日指标**禁止进入 `TradeDraft` / `TradePlan`**。

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
5. `影子评分对照`（仅在已有 factor run 时）
   - 系统主因子、条件化 `core` 板块排序、人工是否接受/改选；明确注明“相对重要度，不是胜率”

## 禁止事项

- 不要把 `[判断]` 伪装成 `[事实]`。
- 不要因为一个板块涨得多，就自动等同于“值得做”。
- 不要纠缠精确区分 `发酵/高潮` 这类细枝末节，优先看连接点是加强还是减弱。
- 不要在 `emotion_leader / capacity_leader` 为空时，自行脑补“领涨股”。
- 不要把老师笔记自由文本直接当成板块名。
- 不要用 `watch/context` 候选补足 6 个 `core` 名额，也不要把规则门、证据质量或规则排序当作 LLM 结论。
- 不要用 factor 影子评分自动改写板块推演结论，更不得将其写入 `TradeDraft`、`TradePlan` 或交易池。

## 最小验证

- `make review-prefill DATE=YYYY-MM-DD` 能返回 `projection_candidates`。
- 输出中至少区分了 1 个优先跟踪板块和 1 个不看板块；若当天没有，明确写“今日无高置信板块机会”。
- 若用户提供的板块判断和系统事实冲突，明确指出冲突点，而不是直接顺着写。
- 若使用三位一体评分，确认第 2 层仅在主因子成立时执行、候选最多 6 个 `core`；影子期用 `python3 main.py review factor-metrics --days 20 [--json]` 复核累计指标。

## 切换条件

- 若用户要完整复盘，切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。
- 若缺少当日盘后数据，切到 [`market-tasks/SKILL.md`](../market-tasks/SKILL.md)。
- 若用户要沉淀老师观点或行业信息，切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。
