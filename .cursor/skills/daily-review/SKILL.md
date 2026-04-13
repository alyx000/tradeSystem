---
name: daily-review
description: 协助用户完成每日「八步复盘法」，自动拉取客观数据、引导填写主观判断，并将复盘写入数据库
version: "1.3"
---

# Skill: 每日复盘（八步复盘法）

本文件为**速查**；八步中每一步的**详细提问话术、占位示例与附录说明**见 [references/eight-step-prompt-templates.md](references/eight-step-prompt-templates.md)。

## 使用场景

当用户说：

- 「开始今天的复盘」
- 「帮我复盘」
- 「打开复盘工作台」
- 「看一下今天的复盘预填充」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make review-open DATE=YYYY-MM-DD
make review-prefill DATE=YYYY-MM-DD
make notes-search KEYWORD=主线 FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=情绪 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

需要调试时再使用 API：

- `GET /api/review/{date}/prefill`
- `GET /api/review/{date}`
- `PUT /api/review/{date}`
- `POST /api/review/{date}/to-draft`

## 核心流程

1. 先确认复盘日期。
2. 拉取预填充数据，先展示客观事实，再引导用户填写主观判断。（逐步提问时可打开 [附录话术模板](references/eight-step-prompt-templates.md)。）
   其中 `projection_candidates.facts` 会同时返回 `emotion_leader` / `capacity_leader`，兼容字段 `lead_stock` 默认跟随 `capacity_leader`；资金流源字段里的股票只作“资金流字段股”参考，不要直接当成板块龙头结论。老师笔记 `sectors` 若是自由文本且匹配不到已知板块，不应直接升格成候选板块。
3. 按八步复盘法汇总：
   - 大盘分析
   - 板块梳理
   - 情绪周期
   - 风格化赚钱效应
   - 龙头 / 最票识别（**Agent 主动推荐**：进入此步时，Agent 先读取预填候选 `step5_leaders` + 历史最票 `leader_tracking` + 前 4 步上下文 + [最票方法论](../sector-projection-analysis/references/leader-identification.md)，综合分析后输出结构化最票推荐，标注 `[判断]`，等用户确认/修改后再录入）
   - 节点判断
   - 持仓检视
   - 次日计划
4. 保存前先给用户一版结构化复盘摘要，用户确认后再写入。
5. 若用户要把复盘直接衔接到次日计划，保存后调用 `POST /api/review/{date}/to-draft`，只生成 observation / draft，不确认正式计划。

## 禁止事项

- 不要替用户臆造主观判断。
- 不要把 `[判断]` 写成 `[事实]`。
- 不要在用户未确认前直接提交 `PUT /api/review/{date}`。
- 不要把复盘工作台任务误当成 `TradePlan` 确认流程。
- 不要在用户否定最票推荐后反复质疑——用户拍板即以用户结论为准。

## 最小验证

- `make review-prefill DATE=YYYY-MM-DD` 或 `GET /api/review/{date}/prefill` 能返回数据。
- 保存后重新读取 `GET /api/review/{date}`，确认内容存在。
- 若当天数据缺失，明确提示先切到 `market-tasks` 补跑采集。

## 切换条件

- 若缺少当日行情或盘后数据，切到 [`market-tasks/SKILL.md`](../market-tasks/SKILL.md)。
- 若用户要生成或确认次日计划，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若用户要先录入老师观点，再作为复盘依据，切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。
- 若发现 API / Web / CLI 语义漂移或保存异常，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已读取 / 已保存的对象与日期
2. 客观事实摘要与用户主观结论
3. 验证结果
4. 剩余风险或待补充项
