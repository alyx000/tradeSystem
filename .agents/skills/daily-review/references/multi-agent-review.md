# 多 Agent 盘后复盘（9 路并行采集 → HTML 报告）

> 本文档沉淀「9 路 subagent 并行采集 → 主会话汇总裁决 → 固定章节 HTML 报告」的复盘工作流，
> 与 SKILL.md 主流程（单 Agent 表单式 review workbench + 写库）互补：**当用户要求「像之前那样出
> HTML 复盘」「用多 agent 复盘 YYYY-MM-DD」时走本文档**。产物是只读 HTML（`data/reports/复盘_YYYY-MM-DD.html`），
> 不写复盘工作台、不碰交易计划层（回写 workbench 需用户显式确认走 `PUT /api/review/{date}`）。

## 编排总览

1. **9 路 subagent 并行发车**：每路一个数据面，仍执行完整采集和计算；各自把结构化主输出与完整证据写到 scratchpad（`<job>/tmp/<面>_<date>.md`）。
2. **主会话汇总裁决**：读 9 份素材，做口径仲裁、第 0 节判分和跨面冲突对齐；完成八步复盘与认知对照的综合后再生成重点因子裁决。正文只接收变化、冲突、会改变总裁决或直接影响持仓检视的内容。
3. **生成确定性容量 sidecar**：主会话从板块结论中选定 1～3 个申万二级方向，调用模板内 `build_capacity_manifest.py` 从只读镜像生成 `capacity_<REPORT_DATE>.json`；Agent 不得自行计算后手写排名。
4. **生成滚动新高 sidecar**：调用 `build_new_high_structure_manifest.py` 对最近 251 个开放日的 `daily.high × adj_factor` 只读重算 60/120/250 日结构；不得用 `daily_new_high_stats` 的全历史高水位口径替代。
5. **分块渲染 HTML**：固定 8 个 body chunk（见下）→ 结构、Claim、证据、预算、容量 sidecar、滚动新高契约和外部依赖校验 → 与 `review_style.css` 组装 → 只读落盘。任何硬门不通过都拒绝生成，不自动删字。

## 9 路分工

| 路 | 面 | 主要数据源 | 素材文件 |
|---|---|---|---|
| 1 | 大盘/择时/期指/两融 | 镜像 index_daily/fut_daily + 库 market_timing_signal / margin_index_correlation_daily | market_YYYY-MM-DD.md |
| 2 | 板块/集中度/主升主跌辨识度/资金流/研报业绩 | 库 daily_volume_concentration + 镜像 daily/sw_daily/moneyflow_ind_ths + data/reports | sector_*.md |
| 3 | 情绪/涨跌停/连板梯队 | 镜像 limit_list_d(U/D/Z) + daily；断板反包读 board-break 报告 | emotion_*.md |
| 4 | 龙虎榜 | 镜像 top_list/top_inst | lhb_*.md |
| 5 | ETF 份额申赎 | 镜像 fund_share/fund_daily | etf_flow_*.md |
| 6 | 板块相关性 | 库 sector_correlation_daily | sector_corr_*.md |
| 7 | 历史同类日基率 | 镜像 index_daily 全历史 | analog_days_*.md |
| 8 | 龙头/趋势池/滚动新高/派生报告 | 库 trend_leader_pool + 镜像 251 个开放日 daily/adj_factor + data/reports 四份派生报告 | trend_*.md |
| 9 | 老师观点/持仓/行业信息/未来事件窗 | 库 teacher_notes/holdings/broker_executions/trade_thesis/industry_info/calendar_events + 交易日历 | teacher_portfolio_*.md |

## 单路输出契约（采集完整，主输出精简）

9 路不得因正文预算而减少采集。每路素材固定包含：

| 字段 | 约束 |
|---|---|
| `verdict` | 1 条本路裁决，标 `[事实]` 或 `[判断]` |
| `delta_facts` | 最多 3 条，只写相对上一交易日发生变化的事实 |
| `conflicts_or_gaps` | 最多 2 条，记录来源冲突或会影响可信度的数据缺口 |
| `confirm_if` / `invalidate_if` | 各 1 条，必须是下一交易日可核验条件 |
| `freshness` | 同时记录来源数据日、所归属交易日和抓取时间；三者不可相互替代 |
| `evidence` | 完整必备事实、原始表格、老师原文、历史序列与计算口径；不得因未进入正文而丢弃 |

板块路与事件窗有额外必备素材，不能只写进自由文本：

| 路 | 专属字段 | 强制内容 |
|---|---|---|
| 2 | `sector_concentration_verdict` | 1 句集中度裁决，至少对照当日与上一交易日；即使无法裁决也必须输出结构化状态 |
| 2 | `sector_concentration_evidence` | 完整集中度事实表；完整无记录与来源不完整必须分别标 `none / missing-data` |
| 2 | `rising_recognition` / `falling_recognition` | 主升与主跌方向 × 辨识度个股矩阵必须成对采集；任一侧无合格项也保留该侧 `none`，来源不足标 `missing-data`，不得只交付主跌矩阵 |
| 8 | `new_high_structure_verdict` / `new_high_structure_evidence` | 前复权滚动 60/120/250 日双日计数、60 日行业 Top3/CR3、名单延续与最多 5 个代表票；不同于全历史高水位任务 |
| 9 | `event_window_verdict` / `event_window_evidence` | 1 句节点裁决 + 报告日后 7 个自然日的事件窗；事件日期须标交易/休市并说明是否影响次日验证 |

主会话按优先级取材：

- **P0**：总裁决、active 持仓变化/失效条件，以及足以改变结论可信度的关键缺口；必须保持可见。
- **P1**：相对前日的有效变化、多来源冲突和直接服务次日确认/证伪的证据；在正文预算内可见。
- **P2**：无变化的存量跟踪、完整历史、原始明细、正常运维状态和方法说明；只进入折叠证据层。重复解释不进入任一层。

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

### 容量中军独立筛选（硬契约）

“容量中军”是成交容量身份，“中军健康度”是对已取得容量身份者的量价/趋势复核；必须先过容量门槛，再谈健康度。板块路须用当日全市场 `daily` 成交额生成容量候选，同时计算最近 5 个开放交易日的 Top50 次数作为连续性展示，主会话再与归属方向成交排名合并。趋势池路只提供历史身份、active/exited 变化和派生扫描，**不得提供容量筛选宇宙**。

| `tier` | 必须同时满足 | 报告归属 |
|---|---|---|
| `core` | 当日全市场成交额排名 ≤30 且归属方向成交额排名 ≤2 | ⑤“容量中军健康度”正文裁决（有变化时）与折叠健康表 |
| `candidate` | 当日全市场成交额排名 31～50 且归属方向成交额排名 ≤2 | ⑤折叠健康表，显式标“候选” |
| 不合格 | 上述两档均不满足 | 另放“趋势池历史代表”或②“辨识度票”分表，不得进入容量中军健康表 |

执行边界：

- `trend_leader_pool` / `leader_tracking` / `daily-leaders` membership、老师点名、涨停或趋势标签都不能代替成交容量；其中成员只有在独立排名重新过门后，才可进入容量中军健康表。
- 禁止生成“旧池中军”分类。历史池成员统一称“趋势池历史代表（含当日退池）”，并单列 `active / exited`；主跌辨识度个股统一归②板块证据，不与容量中军混表。
- 全市场排名、方向排名和最近 5 日 Top50 次数属于 `[事实]`；`core / candidate` 是规则分层；最近 5 日 Top50 次数、均线、量比、距前高等只用于连续性或健康度 `[判断]`，均不得反向补足当日容量资格。
- ⑤每份报告必须三选一：有合格项且来源完整时输出 `table[data-capacity-health="v1"]`；来源完整但两档均为空时输出 `data-capacity-health="none"`，固定文本为 `[事实] 本日无可确认容量中军`；来源不完整时输出 `data-capacity-health="missing-data"`，固定文本为 `[事实] 容量排名数据不完整，本日无法判定`，并在数据缺口章节保持可见。`none` 不得掩盖 `partial / failed`。
- HTML 必须使用模板规定的逐行排名元数据；该门禁不依赖标题是否出现，字段和拒绝规则见 [HTML 模板](html-report-template/README.md#容量中军元数据硬门)。

#### 容量 sidecar 生成与官方落盘

主会话只负责选择本日需要核验的 1～3 个**申万二级**方向；成交排名、方向排名、最近 5 个开放日 Top50 次数和资格分层全部交给 helper。标准命令：

```bash
python3 .agents/skills/daily-review/references/html-report-template/build_capacity_manifest.py \
  <REPORT_DATE> --as-of <TRADE_DATE> \
  --direction <申万二级方向1> [--direction <申万二级方向2>] [--direction <申万二级方向3>] \
  --output <scratchpad目录>/capacity_<REPORT_DATE>.json
```

- `REPORT_DATE` 是 HTML 报告日；`TRADE_DATE` 是不晚于报告日的最近行情归属交易日。helper 只读镜像，以分页后的 `daily.amount` 降序、同额 `ts_code` 升序做全市场排名，并用申万二级成员映射计算方向排名；除 4,000 行绝对地板外，还必须与上市股票基线及申万代码集合核对覆盖率。最近 5 个开放日支持跨年，含周末、异常断档、过期 `as_of` 或覆盖不足都不得产出完整资格结果。
- sidecar 是 helper 的原子输出，不是 Agent 草稿。禁止手写、编辑字段、复制历史文件或把 Agent 自算排名伪装为 sidecar；完整 sidecar 必须保留来源、全市场规模、5 个排名交易日、所选方向及全部合格行。
- helper 数据源失败时仍落 `status=failed / complete=false / rows=[] / errors!=[]` 的 sidecar 并返回退出码 1。此时保留该文件，HTML 使用 `data-capacity-health="missing-data"`，且数据缺口可见列出失败原因；不得改写成 `none`，也不得删除 sidecar 后跳过校验。
- 官方 CLI 组装默认强制读取 `<scratchpad目录>/capacity_<REPORT_DATE>.json`；只有 sidecar 位于其他 helper 输出路径时才传 `--capacity-manifest <PATH>`。缺失、手工损坏、日期/口径错位，或 HTML 资格行与 sidecar 不一致，均拒绝落盘。

### 历史新高结构（硬契约）

⑤中的“历史新高结构”固定指**前复权滚动 60/120/250 日新高**，不是 `daily_new_high_stats` 的全历史高水位任务。主会话必须运行：

```bash
python3 .agents/skills/daily-review/references/html-report-template/build_new_high_structure_manifest.py \
  REPORT_DATE --as-of TRADE_DATE \
  --output <scratchpad目录>/new_high_REPORT_DATE.json
```

- helper 只读最近 251 个开放日，按 `daily.high × adj_factor` 严格突破窗口此前高点计算；上市时间不足对应窗口的股票不计，行情/复权覆盖不足时 fail-closed，不写数据库。
- 官方组装默认强制读取 `<scratchpad目录>/new_high_REPORT_DATE.json`；仅路径不同时使用 `--new-high-manifest`。sidecar 必须保留 251 个开放日逐日市场覆盖并与上市股票基线对账，任一历史日覆盖低于 90%、sidecar 缺失/损坏、日期错位或 HTML 可见值不匹配都拒绝落盘。
- 可见正文必须有唯一 `p[data-new-high-structure="verdict"]`；证据层必须三选一：完整 `table[data-new-high-structure="v1"]`、完整来源下三窗口均为 0 的 `none`、或来源不足的 `missing-data`。后者固定在数据缺口保持可见。
- 完整表必须声明 `data-as-of`、`data-prev-as-of`、`data-market-count`、`data-basis="rolling-adjusted-high"`，以及双日 60/120/250 三组非负计数；默认可见裁决与表格可见双日计数须和 sidecar 一致，60 日行业 Top3/CR3、名单重合/延续率/换手率和最多 5 个代表票留在同一证据区，隐藏正确值不能替代可见对账。
- 代表票只说明“滚动新高中的高成交辨识度”。只有同时通过容量 sidecar 的股票才能标 `core/candidate`；趋势池或新高身份都不能反向补容量资格。

### 红线与禁用（写进每路 prompt）

- **北向资金维度一律不采**（用户裁定无效数据）。
- 输出标 `[事实]`/`[判断]`；不做买卖建议、不预测价格目标、不将 `[判断]` 伪装成 `[事实]`。
- 股通席位仅作单票承接事实，不升格为市场级维度。
- 只读取数：`sqlite3 "file:<db>?mode=ro"` 纯 SELECT，SQL 禁含 REPLACE 关键词；查表前先 `PRAGMA table_info`。
- 镜像脚本样板：`load_dotenv('scripts/.env')` 取 TUSHARE_TOKEN；`api=ts.pro_api(token); api._DataApi__http_url='http://tushare.xyz'`。
- 沙箱够不到 eastmoney（akshare 东财后端），板块/指数历史改用 tushare 镜像；akshare sina/申万官网/同花顺后端可达但慢一量级。

## 精简 HTML 输出契约

### 固定 chunks 与 anchors

8 个 chunk 与 16 个导航锚点必须全部存在、按下列精确顺序各出现一次：`tldr/factor/s0/s1/s2/s3/s4/s5/s6/s7/teachers/industry/cognition/proj/s8/ops`。无新增的章节也保留锚点，只写“本日无新增”或“本日无可判数据”，不生成空表。

| chunk | 固定 anchors | 正文归属 |
|---|---|---|
| `head` | `tldr`, `factor` | 6 个 KPI；“今天变了什么 / 核心矛盾 / 明天验证什么”3 条短结论；三位一体重点因子 |
| `s0` | `s0` | ⓪前日判分 |
| `s1` | `s1` | ①大盘 |
| `s2` | `s2` | ②板块 |
| `s456` | `s3`, `s4`, `s5`, `s6` | ③情绪、④风格、⑤龙头、⑥节点 |
| `s7t` | `s7`, `teachers`, `industry`, `cognition` | ⑦持仓、老师观点、行业信息、认知对照 |
| `proj` | `proj` | 🔭次日推演 |
| `s8ops` | `s8`, `ops` | ⑧次日计划、数据缺口 |

每个八步章节默认只有：**1 句裁决 + 最多 3 条关键证据 + 1 条证伪或缺口**。老师观点最多显示 3 个共识、3 个分歧、2 个新增变化；次日推演最多 3 个场景，每个只含优先级、确认和证伪。宽基 ETF 只归大盘，主题 ETF 只归板块；风格不再独立重复 ETF 内容。完整 P1-P5、行情/资金/梯队/持仓/老师原文/认知历史表进入证据层；同义总结、已关闭仓位的重复文字、无变化项目、正常运维状态和每节重复的方法/红线说明不生成。

#### 不可精简保留清单

| 章节 | 默认可见 | 折叠证据 | 无数据/缺失处理 |
|---|---|---|---|
| ②板块 | 唯一 1 句集中度裁决；可计入本节最多 3 条关键证据 | 唯一集中度表；唯一主升辨识度矩阵；唯一主跌辨识度矩阵。主升/主跌必须成对出现，不因结果为空而删侧 | 完整覆盖但无记录用该模块结构化 `none`；来源不完整用 `missing-data`，并在数据缺口章节可见 |
| ⑤龙头 | 1 句容量中军变化（确有变化时）+ 1 句滚动新高结构裁决 | 唯一容量中军健康表；唯一 60/120/250 日滚动新高结构；趋势池历史代表另表 | 容量与新高各自独立三态；任一来源不完整都用本模块 `missing-data` 并在数据缺口可见 |
| ⑥节点 | 唯一 1 句报告日后 7 个自然日事件窗裁决 | 唯一事件窗表，事件日期标交易/休市及对次日验证的影响 | 无相关事件用 `none`；来源不完整用 `missing-data`，并在数据缺口章节可见 |

上述三节即使无新增，也不能套用通用“只写一行、不生成空表”后静默省略模块；必须保留模板规定的结构化状态。⑤中的容量和滚动新高是两个独立模块，不能互相代替。具体 HTML 结构以 [模板硬门](html-report-template/README.md#组装与硬校验) 为准。

正文展示顺序固定为：`速览 → 三位一体重点因子 → ⓪前日判分 → ①–⑦ → 老师观点 → 行业信息 → 认知对照 → 次日推演 → ⑧次日计划 → 数据缺口`。这是展示前置，不是生成前置：重点因子仍必须在 9 路完整采集、八步复盘与认知对照综合完成后生成，再由次日推演消费；不得继续藏在 ⑧ 的折叠证据里。

重点因子章节默认只显示：1 条主因子裁决、最多 3 条证据（第一辅助与其余两因子可合并）、1 条切换/失效或数据状态。未运行正式评分时必须标为 `shadow` 并写明“影子口径、不写库”，不得伪装成正式评分结果；四因子完整对账和切换预埋进入本节证据层，⑧只保留次日核对清单。`data-factor-mode`、可见 Claim、`no_data` 与 `factor-detail` 的具体 HTML 硬契约只以 [HTML 模板“组装与硬校验”](html-report-template/README.md#组装与硬校验) 为准。

### Claim 唯一归属与证据折叠

- 每条完整结论只有一个归属章节，owner 使用全页唯一的 `id="claim-*"`；其他章节只能用 `<a href="#claim-..." data-claim-ref="claim-...">…</a>` 引用，不得复制原文。
- 同一结论最多出现两次：唯一归属章节中的完整解释 + 最多 1 个锚点短引（进入速览的总裁决优先在速览引用）。
- 原始证据紧跟归属章节，使用 `<details class="evidence" data-as-of="YYYY-MM-DD" data-items="N"><summary>…</summary>…</details>`；默认不带 `open`。`summary` 必须说明内容和数量，summary 后必须保留非空文本、表格或内嵌媒体，`data-as-of` 不得晚于报告交易日。
- `[事实]` / `[判断]` 标签必须留在可见结论和证据条目上；折叠不是删除事实来源，也不能用于藏匿影响总裁决或持仓的 P0 缺口。

### 预算与拒绝策略

| 区域 | 非空白字符 | 表格 | 表格行 |
|---|---:|---:|---:|
| 速览 | ≤ 500 | 计入正文 | 计入正文 |
| 默认可见正文 | 目标 ≤ 6,000，硬上限 10,000 | ≤ 12 | ≤ 80 |
| 折叠证据层 | ≤ 40,000 | ≤ 60 | ≤ 400 |

组装器按章节报告实际用量。超过任一硬上限、Claim 引用悬空/重复、锚点缺失/重复/乱序、证据元数据或正文不完整、出现静态外部依赖时，必须指出责任章节并拒绝生成；不得自动截断、删句或把冗余机械搬进折叠区。HTML 必须是单文件静态产物；报告正文禁止活动标签、事件属性和 `javascript:` URL，禁止 CDN、远程脚本/样式、`fetch`、XHR 和 WebSocket，普通来源链接不视为渲染依赖。

组装规范见 `references/html-report-template/`。主升/主跌矩阵、容量中军成交排名与均线结构表仍为镜像自算；`index_member_all` 与 `daily` 在镜像上均按 2000 行/页静默截断（limit>2000 被忽略），分页必须 offset 循环到空页，勿用 `len<limit` 判停。容量排名若未覆盖当日全市场完整股票行，必须报数据缺口，不得用已分页到的局部样本排名。

## 常见交叉裁决点（主会话汇总时必查）

- **成交额三口径冲突**：库内值 vs 板块路占比反推 vs 同类日路——一律以镜像综指口径自算值仲裁。
- **同类日基率判分**：前一日报告预埋的 T+1 基率方向 vs 当日实际，逐指数命中/落空 + 左尾条件是否触发。
- **矛盾证据对齐**：龙虎榜席位 × ETF 申赎 × 相关性 × 情绪，四路独立数据的方向是否自洽（如 ETF 申赎常滞后盘面一日）。
- **持仓 thesis 失效条件核验**：holdings active × open thesis 逐条重算失效线；残留行（active/closed 重复）标数据缺口，走 CLI 清理需用户确认。
