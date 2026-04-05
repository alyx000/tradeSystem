---
name: ingest-inspector
description: 查看接口注册表、运行采集阶段任务、检查失败项和原始事实层状态
version: "0.1"
---

# Skill: 采集诊断与检查

## 使用场景

当用户说：

- 「看一下今天采集哪里失败了」
- 「跑一下 post_core」
- 「单独跑 block_trade」
- 「看看接口注册表」
- 「重试失败的采集」

时激活此 skill。

## 当前标准 CLI

```bash
python3 main.py ingest run --stage post_core --date 2026-04-04
python3 main.py ingest run-interface --name block_trade --date 2026-04-04
python3 main.py ingest list-interfaces
python3 main.py ingest inspect --date 2026-04-04
python3 main.py ingest retry
```

若需要结构化输出，附加：

```bash
--json
```

## 协作规则

- Agent 可通过此 skill 检查采集状态和原始事实层设计
- 不得直接写 SQLite 或绕过 CLI 手工修数据
- 接口新增时，优先更新注册表与蓝图，而不是先改业务 collector

## 当前能力

这些命令已经接入真实 service，并会写入：

- `raw_interface_payloads`
- `market_fact_snapshots`
- `fact_entities`（当前先支持部分接口）
- `ingest_runs`
- `ingest_errors`

对应最小 API：

- `GET /api/ingest/interfaces`
- `GET /api/ingest/inspect?date=YYYY-MM-DD`
- `GET /api/ingest/runs?date=YYYY-MM-DD`
- `GET /api/ingest/errors?date=YYYY-MM-DD`
- `POST /api/ingest/run`
- `POST /api/ingest/run-interface`
- `GET /api/ingest/retry`

当前限制：

- 仅已实现 provider 的接口会真实执行
- 未实现的接口会诚实写失败，不会伪装成功
