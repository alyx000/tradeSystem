---
name: record-notes
description: 录入老师观点、行业板块信息、宏观经济信息到 SQLite 数据库；OpenClaw/Copaw 需先结构化提炼再写入（支持文字/图片/混合内容，适配 Discord/QQ/微信 channel 场景）
version: "1.2"
---

# Skill: 记录信息（老师观点 / 行业 / 宏观）

## 使用场景

当用户或 channel 消息包含：

- 老师观点、复盘结论、直播要点
- 行业 / 板块动态
- 宏观经济信息
- 图片、OCR、PDF 提取结果

时激活此 skill。

详细结构化规则与长文本入口见 [references/ingestion-rules.md](references/ingestion-rules.md)。

## 优先入口

查询类优先使用仓库根目录：

```bash
make teachers-open
make notes-search KEYWORD=AI FROM=YYYY-MM-DD TO=YYYY-MM-DD
make db-search KEYWORD=锂电 FROM=YYYY-MM-DD TO=YYYY-MM-DD
```

写操作在 `scripts/` 目录执行：

```bash
python3 main.py db add-note ...
python3 main.py db add-industry ...
python3 main.py db add-macro ...
```

## 核心流程

1. 先判断是老师观点、行业信息还是宏观信息。
2. 对老师观点先做结构化提炼，再给用户确认摘要。
3. 长文本优先使用 `--raw-content-file` 或 `stdin`，不要把超长正文直接塞进命令行参数。
4. 写入后回查结果，并保留附件与原文路径。

## 禁止事项

- 不要把老师原话未经提炼直接原样落库。
- 不要把截图 / OCR 长文本直接塞进 `--raw-content` 导致命令行溢出。
- 不要猜测标题、要点、标签或个股代码。
- 不要把老师观点写到 `knowledge_assets`。

## 最小验证

- `db add-note` 成功后，用 `query-notes` 或 API 回查目标标题 / 老师 / 日期。
- 带附件时确认输出里有附件数量，并能从 API 看到 `attachments`。
- 长文本场景优先验证 `--raw-content-file` 或 `--raw-content-file -` 能正确落库。

## 切换条件

- 若用户要把老师观点或资料继续转成计划草稿，切到 [`knowledge-to-plan/SKILL.md`](../knowledge-to-plan/SKILL.md)。
- 若用户其实要管理关注池 / 持仓，切到 [`portfolio-manager/SKILL.md`](../portfolio-manager/SKILL.md)。
- 若 CLI / API 写入语义异常，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已写入的信息类型与对象
2. 结构化摘要与附件 / 原文说明
3. 验证结果
4. 剩余风险或待确认项
