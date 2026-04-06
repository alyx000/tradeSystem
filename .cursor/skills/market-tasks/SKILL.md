---
name: market-tasks
description: 手动触发或自动定时执行盘前/盘后行情采集任务，并将结果摘要推送回 channel
version: "1.2"
---

# Skill: 市场数据任务（盘前 / 盘后采集）

## 使用场景

当用户说：

- 「帮我跑一下盘前采集」
- 「执行今天的盘后任务」
- 「补跑 2026-04-01 的盘后」
- 「打开市场看板 / 看盘后信封」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make market-open DATE=YYYY-MM-DD
make market-json DATE=YYYY-MM-DD
make market-envelope DATE=YYYY-MM-DD
make today-open
make today-close
make today-pre DATE=YYYY-MM-DD
make today-post DATE=YYYY-MM-DD
```

需要底层命令时在 `scripts/` 目录运行：

```bash
python3 main.py pre --date YYYY-MM-DD
python3 main.py post --date YYYY-MM-DD
```

## 核心流程

1. 先确认任务类型、日期和是否属于历史补跑。
2. 手动补跑前先提醒覆盖影响，确认后再执行。
3. 运行后提取关键信息：
   - 文件输出
   - 推送状态
   - 关键市场摘要
4. 若失败属于 ingest 层问题，再切到 ingest 诊断。

## 禁止事项

- 不要在未提醒风险的情况下直接补跑历史日期。
- 不要直接手改 `daily/` 或 DB 伪造结果。
- 不要把 provider 降级误报为任务失败。
- 不要把复盘、计划问题混入采集执行本身。

## 最小验证

- `make market-json DATE=YYYY-MM-DD` 或 `make market-envelope DATE=YYYY-MM-DD` 能读取产物。
- 若执行了 `pre` / `post`，确认 `daily/YYYY-MM-DD/` 下对应文件存在。
- 若任务失败，明确记录失败点并建议切换 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。

## 切换条件

- 若用户要继续做复盘，切到 [`daily-review/SKILL.md`](../daily-review/SKILL.md)。
- 若问题落在单接口、重试或健康检查，切到 [`ingest-inspector/SKILL.md`](../ingest-inspector/SKILL.md)。
- 若任务本身命令 / 文档 / 调度逻辑漂移，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。

## 结果汇报格式

1. 已执行的任务、日期与模式
2. 关键市场摘要与产物路径
3. 验证结果
4. 剩余风险或后续建议
