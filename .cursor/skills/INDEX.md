# Skills 依赖索引

本文件记录每个 skill 所依赖的 CLI 命令和 API 端点。
**修改 `scripts/db/cli.py` 或 `scripts/api/routes/` 后，必须检查此索引。**

## CLI 命令依赖表

| Skill | CLI 子命令 | 说明 |
|-------|-----------|------|
| `record-notes` | `db add-note` | 录入老师观点（文字/图片/多附件） |
| `record-notes` | `db add-industry` | 录入行业板块信息 |
| `record-notes` | `db add-macro` | 录入宏观经济信息 |
| `portfolio-manager` | `db holdings-add` | 新增持仓 |
| `portfolio-manager` | `db holdings-remove` | 移除持仓（置 closed） |
| `portfolio-manager` | `db holdings-list` | 列出当前持仓 |
| `portfolio-manager` | `db watchlist-add` | 添加到关注池 |
| `portfolio-manager` | `db watchlist-remove` | 从关注池移除 |
| `portfolio-manager` | `db watchlist-update` | 更新关注池标的 |
| `portfolio-manager` | `db watchlist-list` | 列出关注池 |
| `portfolio-manager` | `db add-trade` | 录入交易记录 |
| `portfolio-manager` | `db blacklist-add` | 加入黑名单 |
| `daily-review` | `db query-notes` | 搜索老师笔记（用于复盘预填充） |
| `daily-review` | `db db-search` | 跨表关键词搜索 |
| `market-tasks` | `python main.py pre --date` | 盘前任务采集 |
| `market-tasks` | `python main.py post --date` | 盘后任务采集 |
| `daily-review` | `db add-calendar` | 手动录入投资日历事件（节假日/财经/财报等） |
| *(管理)* | `db init` | 初始化数据库 + 导入历史 YAML |
| *(管理)* | `db sync` | 重试 pending_writes 中的失败记录 |
| *(管理)* | `db reconcile` | 对账：YAML 与 DB 数据一致性比对 |

## API 端点依赖表

当前 skills 直接引用的端点：

| Skill | API 端点 | 方法 | 说明 |
|-------|---------|------|------|
| `daily-review` | `/api/review/{date}/prefill` | GET | 拉取八步复盘预填充数据 |
| `daily-review` | `/api/review/{date}` | GET | 读取已保存的复盘内容 |
| `daily-review` | `/api/review/{date}` | PUT | 提交复盘主观判断 |

## 可用 API 总览（供开发新 Skill 参考）

所有端点由 FastAPI 自动生成文档，启动后可访问 `http://localhost:8000/docs`。
下表为静态索引，方便离线查阅。标注 `★` 的端点已被现有 skill 引用。

### 复盘（`routes/review.py`，前缀 `/api/review`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/review/{date}` ★ | 读取指定日期复盘（含 exists 标志） |
| GET | `/api/review/{date}/prefill` ★ | 预填充数据（行情+笔记+持仓+日历） |
| PUT | `/api/review/{date}` ★ | 保存/更新复盘主观判断 |

### 老师观点（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/teachers` | 列出所有老师 |
| GET | `/api/teacher-notes` | 查询笔记列表（支持 keyword/teacher/from/to 过滤） |
| GET | `/api/teacher-notes/{note_id}` | 读取单条笔记 |
| POST | `/api/teacher-notes` | 新建笔记（含 teacher_name 自动创建老师） |
| PUT | `/api/teacher-notes/{note_id}` | 更新笔记 |
| DELETE | `/api/teacher-notes/{note_id}` | 删除笔记 |

### 持仓池（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/holdings` | 列出持仓（?status=active/closed/all） |
| GET | `/api/holdings/{hid}` | 读取单条持仓 |
| POST | `/api/holdings` | 新建/更新持仓（upsert） |
| PUT | `/api/holdings/{hid}` | 更新持仓字段 |
| DELETE | `/api/holdings/{hid}` | 删除持仓记录 |

### 关注池（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/watchlist` | 列出关注池（?tier=&status=watching） |
| GET | `/api/watchlist/{wid}` | 读取单条关注标的 |
| POST | `/api/watchlist` | 添加到关注池 |
| PUT | `/api/watchlist/{wid}` | 更新关注标的（层级/状态/备注） |
| DELETE | `/api/watchlist/{wid}` | 删除关注标的 |

### 黑名单（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/blacklist` | 列出黑名单 |
| POST | `/api/blacklist` | 加入黑名单 |
| DELETE | `/api/blacklist/{bid}` | 从黑名单移除 |

### 行业信息（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/industry` | 列出行业信息（?keyword= 触发全文搜索） |
| POST | `/api/industry` | 新建行业信息 |
| PUT | `/api/industry/{iid}` | 更新行业信息 |
| DELETE | `/api/industry/{iid}` | 删除行业信息 |

### 宏观信息（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/macro` | 列出宏观信息（?keyword= 触发全文搜索） |
| POST | `/api/macro` | 新建宏观信息 |
| PUT | `/api/macro/{mid}` | 更新宏观信息 |
| DELETE | `/api/macro/{mid}` | 删除宏观信息 |

### 投资日历（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/calendar` | 最近 100 条日历事件 |
| GET | `/api/calendar/range` | 按日期区间查询（?from=&to=&impact=&category=） |
| POST | `/api/calendar` | 新建日历事件 |
| PUT | `/api/calendar/{cid}` | 更新日历事件 |
| DELETE | `/api/calendar/{cid}` | 删除日历事件 |

### 交易记录（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trades` | 查询交易记录（?from=&to=&stock_code=） |
| GET | `/api/trades/{tid}` | 读取单条交易 |
| POST | `/api/trades` | 新建交易记录 |
| PUT | `/api/trades/{tid}` | 更新交易记录 |
| DELETE | `/api/trades/{tid}` | 删除交易记录 |

### 行情（`routes/crud.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/market/history` | 近 N 日行情摘要（`?days=`，不含 raw_data） |
| GET | `/api/market/{date}` | 读取指定日期全市场行情摘要（扁平列 + 部分从 raw_data 展开） |
| GET | `/api/post-market/{date}` | 整包盘后信封（与 post-market.yaml / DB raw_data 一致） |

### 搜索与分析（`routes/search.py`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/search/unified` | 跨表全文搜索（?q=&types=&from=&to=） |
| GET | `/api/search/export` | 搜索结果导出为 Markdown（同参数，返回纯文本） |
| GET | `/api/teachers/{teacher_id}/timeline` | 指定老师的笔记时间线 |
| GET | `/api/stock/{code}/mentions` | 个股被提及记录（跨笔记/行业/宏观） |
| GET | `/api/style-factors/series` | 风格因子时序数据（?metrics=&from=&to=） |

## 自动化检查

`scripts/tests/test_cli_smoke.py` 会验证上表中所有 **`db` 子命令**的 argparse 签名（不启动子进程、不连库）。**不包含** 顶层 `main.py pre` / `post`（二者由 `market-tasks` 文档与人工/定时流程保证；修改 `main.py` 参数时请同步更新对应 SKILL 并自行回归）。

每次 `pytest scripts/tests/test_cli_smoke.py` 都会同步检查：
- 依赖表所列 `db` 子命令名未被重命名
- 必需参数未被删除或改名
- choices 集合未缩减

## 变更流程

1. 修改 `cli.py` 或 API routes 时，同步更新此 INDEX.md
2. 运行 `python3 -m pytest scripts/tests/test_cli_smoke.py -v` 验证签名无破坏
3. 若命令参数有不向后兼容的变更，更新对应 SKILL.md 中的示例
