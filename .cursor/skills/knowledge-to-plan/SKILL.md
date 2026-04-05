---
name: knowledge-to-plan
description: 从老师观点、课程笔记、新闻资料等知识资产触发 MarketObservation 和 TradeDraft
version: "0.1"
---

# Skill: 资料提炼到计划

## 使用场景

当用户说：

- 「把这条老师观点转成计划草稿」
- 「从资料里提炼一下交易线索」
- 「从这篇笔记生成 observation」

时激活此 skill。

## 当前标准 CLI

```bash
python3 main.py knowledge add-note
python3 main.py knowledge list
python3 main.py knowledge draft-from-asset --asset-id asset_xxx
```

若需要结构化输出，附加：

```bash
--json
```

## 协作规则

- 资料先进入 `knowledge_assets`
- 再由资料触发 `MarketObservation`
- 再由 observation 生成 `TradeDraft`
- Agent 可触发 observation，但不得跳过人工确认直接生成正式计划

## 当前能力

这些命令已经接入真实 service，并会：

- 写入 `knowledge_assets`
- 创建 `MarketObservation(source_type=knowledge_asset)`
- 生成 `TradeDraft`

当前限制：

- 资料提炼先走规则抽取，不依赖 LLM
- 生成的检查项仍停留在 draft 候选层，正式计划需人工确认
