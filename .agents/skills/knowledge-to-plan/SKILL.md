---
name: knowledge-to-plan
description: 从课程笔记、新闻资料等知识资产触发 MarketObservation 和 TradeDraft；老师观点走 teacher_notes 唯一事实源
version: "0.3"
---

# Skill: 资料提炼到计划

## 使用场景

当用户说：

- 「把这条新闻转成计划草稿」
- 「从资料里提炼一下交易线索」
- 「从这篇笔记生成 observation」
- 「把老师观点生成草稿」

时激活此 skill。

详细分流规则见 [references/source-routing.md](references/source-routing.md)。

## 优先入口

优先使用仓库根目录：

```bash
make knowledge-open
make knowledge-list
make knowledge-add-note
make knowledge-draft-from-asset ASSET_ID=asset_xxx
make knowledge-draft-from-teacher-note NOTE_ID=42 DATE=YYYY-MM-DD
```

需要细粒度参数时再退回：

```bash
python3 main.py knowledge add-note
python3 main.py knowledge list
python3 main.py knowledge draft-from-asset --asset-id asset_xxx --date YYYY-MM-DD
python3 main.py knowledge draft-from-teacher-note --note-id 42 --date YYYY-MM-DD
```

老师观点的唯一事实源仍是：

```bash
python3 main.py db add-note --teacher "小鲍" --date YYYY-MM-DD --title "..." --input-by openclaw
```

## 核心流程

1. 先判断来源是老师观点还是普通资料。
2. 选择正确入口：
   - 老师观点：`teacher_notes`
   - 普通资料：`knowledge_assets`
3. 写入后再触发 observation / draft 生成。
4. 返回草稿结果，并提醒正式计划仍需人工确认。

## 禁止事项

- 不要用 `knowledge add-note` 冒充老师观点。
- 不要把 `teacher_note` / `course_note` 写回 `knowledge_assets`。
- 不要绕过人工确认直接生成正式 `TradePlan`。
- 不要在未确认来源的情况下自行猜测分流目标。

## 最小验证

- `make knowledge-list` 或对应 API 能看到新增资产 / 笔记来源。
- `draft-from-asset` 或 `draft-from-teacher-note` 能成功返回 `TradeDraft`。
- 若分流失败，先回查 [references/source-routing.md](references/source-routing.md) 再决定是否切换 skill。

## 切换条件

- 若输入本质上是老师原始观点录入，先切到 [`record-notes/SKILL.md`](../record-notes/SKILL.md)。
- 若用户要确认正式次日计划，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若发现 CLI / API / Web 语义漂移，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 采用的分流路径与写入对象
2. 生成的 observation / draft 摘要
3. 验证结果
4. 剩余风险或待人工确认项
