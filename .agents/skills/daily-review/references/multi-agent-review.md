# 多 Agent 盘后复盘（9 路并行采集 → HTML 报告）

> 本文档沉淀「9 路 subagent 并行采集 → 主会话汇总裁决 → 12+2 节 HTML 报告」的复盘工作流，
> 与 SKILL.md 主流程（单 Agent 表单式 review workbench + 写库）互补：**当用户要求「像之前那样出
> HTML 复盘」「用多 agent 复盘 YYYY-MM-DD」时走本文档**。产物是只读 HTML（`data/reports/复盘_YYYY-MM-DD.html`），
> 不写复盘工作台、不碰交易计划层（回写 workbench 需用户显式确认走 `PUT /api/review/{date}`）。

## 编排总览

1. **9 路 general-purpose subagent 并行发车**（一条消息内多个 Agent 调用，真并行）：每路一个数据面，
   各自写素材 md 到 scratchpad（`<job>/tmp/<面>_<date>.md`），主会话不进原始数据、只收 subagent 的结论。
2. **主会话汇总裁决**：读 9 份素材 md，做跨面交叉裁决（口径仲裁、矛盾证据对齐、第 0 节判分）。
3. **分块渲染 HTML**：8 个 body chunk（见下）→ 标签配平校验（开闭标签数相等）→ 与 `review_style.css` 组装 → 落盘 → open + SendUserFile。

## 9 路分工

| 路 | 面 | 主要数据源 | 素材文件 |
|---|---|---|---|
| 1 | 大盘/择时/期指/两融 | 镜像 index_daily/fut_daily + 库 market_timing_signal / margin_index_correlation_daily | market_YYYY-MM-DD.md |
| 2 | 板块/资金流/研报业绩 | 库 daily_volume_concentration + 镜像 sw_daily/moneyflow_ind_ths + data/reports | sector_*.md |
| 3 | 情绪/涨跌停/连板梯队 | 镜像 limit_list_d(U/D/Z) + daily；断板反包读 board-break 报告 | emotion_*.md |
| 4 | 龙虎榜 | 镜像 top_list/top_inst | lhb_*.md |
| 5 | ETF 份额申赎 | 镜像 fund_share/fund_daily | etf_flow_*.md |
| 6 | 板块相关性 | 库 sector_correlation_daily | sector_corr_*.md |
| 7 | 历史同类日基率 | 镜像 index_daily 全历史 | analog_days_*.md |
| 8 | 龙头/趋势池/派生报告 | 库 trend_leader_pool + data/reports 四份派生报告 | trend_*.md |
| 9 | 老师观点/持仓/行业信息 | 库 teacher_notes/holdings/broker_executions/trade_thesis/industry_info | teacher_portfolio_*.md |

## 口径基线（每次复盘必须携带进 subagent prompt，防复发陷阱）

### 单位换算（provider API 事实，易错）

| 接口 | 字段 | 单位 → 亿 |
|---|---|---|
| tushare `index_daily` | amount | 千元 → 亿 = /1e5 |
| tushare `sw_daily` | amount | **万元** → 亿 = /1e4（与 index_daily 不同！） |
| tushare `top_list`/`top_inst` | 金额 | **元** → 亿 = /1e8 |
| tushare `fund_share` | fd_share | **万份** → 亿份 = /1e4 |
| tushare `moneyflow_ind_ths` | net_amount | 打印一行核对量级（历史用 半导体 +103 亿 校准过） |

### 两市成交额口径（血泪教训）

- **综指口径 = 上证综指 000001.SH + 深证综指 399106.SZ**。深市腿**必须** 399106（深证综指，全深市），
  **不是** 399001（深证成指，仅约 500 成分股）。2026-07-07~09 深市腿曾退化为 399001 成指口径，两市总额
  从 3 万亿+ 压到 2 万亿静默落库三日。
- 库内 `daily_volume_concentration.market_total_billion` 与 `market_timing_signal.market_amount_yi` 的取数指数
  见代码常量 `scripts/services/market_timing/constants.py:MARKET_AMOUNT_INDICES`（勿在本文抄值，指回代码防漂移）；
  provider 侧口径见 `get_market_volume`（tushare/akshare）。历史坏行已于 2026-07-11 用 399106 重采修正。
- **复盘取量能一律用镜像综指口径自算**，别直接引库内值（历史坏行已修，但保持自算习惯 + 与 collector 三段守卫互证）。

### 其他固定口径

- 中证2000 = `932000.CSI`（微盘股代理，万得 8841431 免费源取不到）。
- 平均股价 = 通达信 880003，镜像不可取，用库内 `market_timing_signal` avg_price 兜底（pytdx 专路）。
- ths_daily 无 amount 列；sw_daily 列名 `pct_change`（非 `pct_chg`）；index_daily/daily 列名 `pct_chg`。
- **ETF 拆分污染名单**（份额跳变≠申赎，登记日剔除或按拆分比还原 + 验价格连续性）：512480/515880/588170/562590/515070/159995 等，遇份额跳变+价格同日近似等比落位即疑拆分，单独标注剔除。
- 龙虎榜 top_inst 同席位跨买/卖榜双计需去重；3 日段净额含前两日累计，与单日段不可直加。

### 红线与禁用（写进每路 prompt）

- **北向资金维度一律不采**（用户裁定无效数据）。
- 输出标 `[事实]`/`[判断]`；不做买卖建议、不预测价格目标、不将 `[判断]` 伪装成 `[事实]`。
- 股通席位仅作单票承接事实，不升格为市场级维度。
- 只读取数：`sqlite3 "file:<db>?mode=ro"` 纯 SELECT，SQL 禁含 REPLACE 关键词；查表前先 `PRAGMA table_info`。
- 镜像脚本样板：`load_dotenv('scripts/.env')` 取 TUSHARE_TOKEN；`api=ts.pro_api(token); api._DataApi__http_url='http://tushare.xyz'`。
- 沙箱够不到 eastmoney（akshare 东财后端），板块/指数历史改用 tushare 镜像；akshare sina/申万官网/同花顺后端可达但慢一量级。

## HTML 报告结构（12+2 节）

8 个 body chunk（分块写、便于逐节迭代）：`head`（header+toc+速览7条）、`s0`（第 0 节前日对照判分：锚点对账 + 老师 T-1 判分）、`s1`（①大盘）、`s2`（②板块）、`proj`（🔭推演 + ③情绪）、`s456`（④风格/⑤龙头/⑥节点）、`s7t`（⑦持仓 + 🎓老师观点 + 📰行业信息）、`s8ops`（⑧次日计划 + 🧠认知库对照 + 🔧数据缺口 + footer）。

组装规范见 `references/html-report-template/`（review_style.css + 配平校验脚本 + chunk 骨架说明）。
每轮改动后：标签配平校验（section/table/tr/td/th/ul/li/h2/h3/div/p/span/b 等开闭数相等）→ 重建 → open + SendUserFile。

## 常见交叉裁决点（主会话汇总时必查）

- **成交额三口径冲突**：库内值 vs 板块路占比反推 vs 同类日路——一律以镜像综指口径自算值仲裁。
- **同类日基率判分**：前一日报告预埋的 T+1 基率方向 vs 当日实际，逐指数命中/落空 + 左尾条件是否触发。
- **矛盾证据对齐**：龙虎榜席位 × ETF 申赎 × 相关性 × 情绪，四路独立数据的方向是否自洽（如 ETF 申赎常滞后盘面一日）。
- **持仓 thesis 失效条件核验**：holdings active × open thesis 逐条重算失效线；残留行（active/closed 重复）标数据缺口，走 CLI 清理需用户确认。
