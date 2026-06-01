---
name: market-tasks
description: 手动触发或自动定时执行盘前/盘后行情采集任务、行业推荐推送，并将结果摘要推送回 channel
version: "1.3"
---

# Skill: 市场数据任务（盘前 / 盘后采集）

## 使用场景

当用户说：

- 「帮我跑一下盘前采集」
- 「执行今天的盘后任务」
- 「补跑 2026-04-01 的盘后」
- 「打开市场看板 / 看盘后信封」
- 「行业推荐定时推送」/「最近值得看的行业」

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

## 行业推荐定时推送

把最近 3 / 7 日数据按本质拆成**三段**推送钉钉自定义机器人，三段各归各位、不相互冒充：

- **📌 近期大盘判断** ← `teacher_notes.core_view`（note 级大盘观点，去重置顶）。可用 Gemini CLI 提炼成 2-4 条要点；gemini 不可用 / 命中红线则降级展示最近 3 条原始观点。
- **🔥 行业热度榜（按老师提及）** ← `teacher_notes.sectors` 提及次数（`score = mentions × recency_decay`），仅排名 + 提及数。
- **💡 有具体催化的行业** ← `industry_info`（真行业逻辑，按 confidence → date 倒序）。

> 红线扫描只作用于 Gemini 生成的大盘判断；降级原文与催化原文是用户录入的事实层，不扫（见 `formatter.py` 注释）。

定时入口（已挂 APScheduler）：

- 日报：工作日 07:10（与盘前 07:00 错峰 10 分钟）
- 周报：每周日 20:00（独占周日，与盘后 mon-fri 20:00 不冲突）

手动入口（CLI 优先用 `make`）：

```bash
# 仅打印不推送
make recommend-daily-dry      # 等价 python3 main.py recommend daily --dry-run
make recommend-weekly-dry     # 等价 python3 main.py recommend weekly --dry-run

# 真推送（需先 export DINGTALK_WEBHOOK_TOKEN / DINGTALK_WEBHOOK_SECRET）
make recommend-daily
make recommend-weekly

# 自定义窗口（直接调底层）
python3 main.py recommend daily  --lookback-days 5
python3 main.py recommend weekly --lookback-days 14
```

环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `DINGTALK_WEBHOOK_TOKEN` | — | 钉钉机器人 webhook access_token（必填，不入 git） |
| `DINGTALK_WEBHOOK_SECRET` | — | 钉钉机器人加签 secret（必填，不入 git） |
| `GEMINI_BIN` | `/opt/homebrew/bin/gemini` | gemini CLI 可执行路径 |
| `LLM_TIMEOUT_SECONDS` | `90` | LLM 调用超时（硬上限 180s） |
| `GEMINI_MODEL` | 空 | 指定模型，留空走 gemini 默认 |

## 成交额板块集中度监控（volume-watch）

每交易日 21:00 自动跑（launchd `com.alyx.tradesystem.volume-watch`），也可手动：

```bash
make volume-watch-daily        # = python3 main.py volume-watch daily（采集+落库+渲染+钉钉推送）
make volume-watch-daily-dry    # = ... --dry-run（仅打印,不落库不推送,预览用）
make volume-watch-trend        # = python3 main.py volume-watch trend（只读打印最近 30 日趋势）

# 指定日期 / 窗口（直接调底层）
python3 main.py volume-watch daily --date 2026-05-29 --dry-run
python3 main.py volume-watch trend --date 2026-05-29 --days 10

# 回填历史：--refetch 强制重拉，绕过 daily_market 陈旧缓存（如换算 fix 前采集的旧数据）
#   批量回填时建议 env -u DINGTALK_* 屏蔽推送，只落库不刷屏：
for d in 2026-05-27 2026-05-28 2026-05-29; do
  env -u DINGTALK_WEBHOOK_TOKEN -u DINGTALK_WEBHOOK_SECRET \
    python3 main.py volume-watch daily --date "$d" --refetch
done
```

- `daily`：read-through 读 `daily_market.top_volume_stocks`（缺则重拉）→ 申万二级打标（三级降级：申万成分命中 → `stock_basic` 兜 name → 「未分类」）→ 聚合 → 落 `daily_volume_concentration` → 渲染（含 **Top20 个股明细表**：名称(代码)/申万二级行业/成交额/带符号涨跌，成交额降序）→ 钉钉。非交易日无数据自动跳过（不写库不推送）。
- `--refetch`：跳过 read-through，强制走 provider 重拉 top20。用于**回填历史**——库里 `top_volume_stocks` 可能是某次换算 fix（如 `/1e4`→`/1e5`）之前采集的陈旧值，read-through 命中即用会灌坏数据；`--refetch` 用当前（已修复）provider 代码重取。
- `trend`：只读最近 N 日（默认 30），输出板块轮动 / 头部量级环比 / 个股连续在榜；不采集、不落库、不推送。
- **报告结构 v2**（全事实层，守红线，钉钉手机端友好）：头部摘要(合计/占两市 + 涨跌分布红/绿/平+均值+最强弱) → CR3 行(环比 pp + 窗口分位 + 连升/连降) → 板块集中度(**列表非表格**——钉钉手机端不渲染 markdown 表格) → 🔥 板块热度趋势(各行业占 Top20 比重 vs 前期,🔴升温/🟢降温,A股红涨绿跌) → 💰 头部资金(量级 vs 近期均值放/缩量 + 新陈代谢核心/今日新进 + 今日新进资金流向行业) → 🔄 异动个股(今日新进带行业+涨跌 / 退出,替代逐只罗列 Top20) → 📌 连续在榜(streak≥2,Top8)。不足 2 交易日出兜底文案、不渲染跨日块。
- 行业口径=**申万二级**（联动 `get_sector_rankings`）；「未分类」（次新等）不计入前3行业集中度，报告标 `industry_coverage`。
- 依赖 env：`TUSHARE_TOKEN`（`scripts/.env`，`index_member_all` 需积分）、`DINGTALK_WEBHOOK_TOKEN/SECRET`（`~/.config/tradeSystem.env`，daily 推送）。

## 板块相关性监控（sector-correlation）

每交易日 21:15 自动跑（launchd `com.alyx.tradesystem.sector-correlation`，错开 volume-watch 21:00），也可手动：

```bash
python3 main.py sector-correlation daily --date 2026-05-29 --dry-run   # 预览,不落库不推送
python3 main.py sector-correlation matrix --date 2026-05-29            # 打印完整矩阵(不推送)
python3 main.py sector-correlation trend --date 2026-05-29 --days 20   # 只读漂移趋势
```

- **数据源 Tushare 主源**（镜像 `tushare.xyz`，区间拉取）：指数 `index_daily`(`pct_chg`)、申万二级 `sw_daily`(`pct_change`/`amount`)、同花顺概念 `ths_daily`(`pct_change`/`turnover_rate`，**无成交额列**)。akshare/eastmoney 降为 fallback（实测当前不通）。
- **多日活跃选板块（固定配额）**：行业按**多日平均成交额** Top-`--top-industries`(默认15)、概念按**多日平均换手率** Top-`--top-concepts`(默认10)，各排各的；逐日快照取近 `--activity-days`(默认10) 天。概念名撞行业名自动加 `(概念)` 后缀防丢数据。
- **多窗 5/20/60**：原始日涨跌幅 Pearson（联动）+ **剔除上证后的残差超额相关**（真跷跷板，逆向以此为准）+ 板块对 4 指数（上证/创业板/沪深300/科创50）的相关与 β。日报头条出**近5日联动榜**（短期共振）+ **结构联动榜 60 日**；反向榜双窗对照取 **5日/60日**（两窗都显著=稳定跷跷板，仅 5 日显著=近期偶然，5 个点相关噪声大需结合 60 日）。20 日窗仍入库 / matrix。
- 行业口径=申万二级（同 volume-watch）；概念=同花顺，名撞行业时自动加 `(概念)` 后缀防丢数据。
- `daily`：采集→落 `sector_correlation_daily`（一天一行 JSON 列）→ 渲染→钉钉；`matrix`：读当天(缓存命中纯只读免初始化 Tushare，`--refetch` 强制现算)打印完整矩阵不推送；`trend`：只读最近 N 日漂移。无指数/有效板块<5/各窗有效列<5 → 跳过(不落库不推送)。
- **守红线**：报告标注"相关为同期统计共现，非因果、非买卖建议"；窗口/样本天数显式展示。
- 依赖 env 同 volume-watch（`TUSHARE_TOKEN` + 钉钉）。详见 [`sector-projection-analysis`](../sector-projection-analysis/SKILL.md) 的定性推演可调取本定量证据。

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
