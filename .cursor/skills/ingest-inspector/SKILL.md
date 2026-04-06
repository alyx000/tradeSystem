---
name: ingest-inspector
description: 查看接口注册表、运行采集阶段任务、检查失败项和原始事实层状态
version: "0.2"
---

# Skill: 采集诊断与检查

## 使用场景

当用户说：

- 「看一下今天采集哪里失败了」
- 「跑一下 post_core」
- 「单独跑 block_trade」
- 「看看接口注册表」
- 「重试失败的采集」
- 「把卡住的 running 记录收掉」

时激活此 skill。

## 优先入口

优先使用仓库根目录：

```bash
make ingest-run-post DATE=YYYY-MM-DD
make ingest-run-interface NAME=block_trade DATE=YYYY-MM-DD
make ingest-inspect DATE=YYYY-MM-DD
make today-ingest-health
make ingest-health DATE=YYYY-MM-DD DAYS=7
make ingest-reconcile STALE_MINUTES=5
make ingest-list
```

需要细粒度参数时再退回：

```bash
python3 main.py ingest run --stage post_core --date YYYY-MM-DD
python3 main.py ingest run-interface --name block_trade --date YYYY-MM-DD
python3 main.py ingest inspect --date YYYY-MM-DD --stage post_extended --interface margin
python3 main.py ingest retry --stage post_extended --interface margin
python3 main.py ingest health --date YYYY-MM-DD --days 7 --stage post_extended
python3 main.py ingest reconcile --stale-minutes 5
```

## 核心流程

1. 先用 `inspect` / `health` 确认失败范围、stage、interface 和时间窗口。
2. 再决定是补跑单接口、重试、还是清理陈旧 `running` 审计。
3. 执行后回看 `inspect` / `runs` / `errors`，确认状态变化。
4. 若问题指向 provider、service 或状态流转缺陷，再切到仓库维护工作流。

## 禁止事项

- 不要直接写 SQLite 修复采集状态。
- 不要把未实现接口伪装为成功。
- 不要跳过 `inspect` 就直接大范围补跑。
- 不要把业务层计划诊断问题误当成采集成功与否。

## 最小验证

- `make ingest-inspect DATE=YYYY-MM-DD` 能返回期望 stage / interface 状态。
- 补跑后，`make ingest-health` 或 `python3 main.py ingest inspect ... --json` 能看到状态更新。
- 若执行了 `reconcile`，确认陈旧 `running` 已被处理。

## 切换条件

- 若问题已变成 CLI / API / service 漂移，切到 [`repo-maintenance-workflows/SKILL.md`](../repo-maintenance-workflows/SKILL.md)。
- 若采集失败进一步影响 `plan diagnose`，切到 [`plan-workbench/SKILL.md`](../plan-workbench/SKILL.md)。
- 若用户其实要跑盘前 / 盘后整套日报流程，切到 [`market-tasks/SKILL.md`](../market-tasks/SKILL.md)。

## 结果汇报格式

1. 已检查 / 已执行的 ingest 动作
2. 关键接口、stage 与状态摘要
3. 验证结果
4. 剩余失败项或后续建议
