---
name: plan-workbench
description: 生成、查看、确认、诊断和回写 TradeDraft / TradePlan / PlanReview 的工作台流程
version: "0.2"
---

# Skill: 交易计划工作台

## 使用场景

当用户说：

- 「生成明天的交易计划草稿」
- 「看一下今天的计划草稿」
- 「确认这份计划」
- 「诊断一下这份计划」
- 「回写今天计划执行结果」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make plan-open DATE=YYYY-MM-DD
make plan-draft DATE=YYYY-MM-DD
make plan-show-draft DATE=YYYY-MM-DD
make plan-confirm DRAFT_ID=draft_xxx DATE=YYYY-MM-DD
make plan-diagnose PLAN_ID=plan_xxx
make plan-review PLAN_ID=plan_xxx DATE=YYYY-MM-DD
```

需要细粒度参数时再退回：

```bash
python3 main.py plan draft --date YYYY-MM-DD
python3 main.py plan draft --date YYYY-MM-DD --from-review --input-by cursor
python3 main.py plan show-draft --draft-id draft_xxx
python3 main.py plan confirm --draft-id draft_xxx --date YYYY-MM-DD
python3 main.py plan diagnose --plan-id plan_xxx --date YYYY-MM-DD
python3 main.py plan review --plan-id plan_xxx --date YYYY-MM-DD
```

## 核心流程

1. 先确认用户是要草稿、确认、诊断还是复盘回写。
2. 生成草稿时只停留在 observation / draft 层；若来源是复盘，优先走 `plan draft --from-review` 或 `/api/review/{date}/to-draft`。
3. 诊断时优先读取事实快照；拿不到时接受 `missing_data`，不要伪造通过 / 失败。
4. 正式计划只能由人工确认后写入。

## 禁止事项

- 不要绕过人工确认直接写 `confirmed` 计划。
- 不要把主观判断塞进 `fact_checks`。
- 不要把 `missing_data` 硬解释成 pass / fail。
- 不要直接手改计划 JSON 取代结构化字段。

## 最小验证

- `make plan-show-draft` 或 `python3 main.py plan show-draft ...` 能读取目标草稿。
- `make plan-diagnose` 返回诊断结果，并在缺快照时显式出现 `missing_data`。
- 若做了确认或 review，回读对应 plan / review 结果确认已写入。

## 切换条件

- 若草稿来源尚未结构化，切到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)。
- 若诊断依赖的事实快照缺失或采集异常，切到 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。
- 若发现 CLI / API / Web 语义漂移，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已执行的计划动作与对象
2. 草稿 / 计划 / 诊断摘要
3. 验证结果
4. 剩余风险或待人工确认项
