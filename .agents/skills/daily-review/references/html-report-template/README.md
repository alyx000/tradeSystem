# 盘后复盘 HTML 报告模板

配合 [`../multi-agent-review.md`](../multi-agent-review.md) 使用。默认产物 = `data/reports/复盘_YYYY-MM-DD.html`（只读，不写工作台/计划层）；回归样例必须用 `--output` 写非 canonical 文件，避免覆盖原报告。

## 文件

- `review_style.css` — 报告样式 **v2「纸面档案阅读器」**：米纸底色 + 衬线大标题 + 粘性顶栏/导航 + 阅读进度 + 表格横向滚动 + 证据展开/收起 + 回到顶部，并包含 USD/CNY/1Y C-Swap 静态 SVG 在宽屏、窄屏与系统暗色模式下的样式。正文 class 语义不变（tag t-fact/t-judge / kpi / callout.note/.risk/.warn / tr.hl / up/.down/.warn / p-up/.p-mid/.p-down / twocol / mono / num / src / sub）。
- `build_capacity_manifest.py` — 容量排名 sidecar 官方生成器：只读镜像、最多 3 个申万二级方向、全市场 `daily.amount` 排名、最近 5 个开放日连续性；禁止 Agent 手写 sidecar。
- `build_new_high_structure_manifest.py` — 历史新高结构生成器：只读最近 251 个开放日，按 `daily.high × adj_factor` 生成滚动 60/120/250 日双日计数、行业结构与代表票；不写库。
- `assemble_report.py` — 组装脚本（结构、Claim、证据、预算、外部依赖校验 + 阅读器外壳 + 原生 JS）；同时自动读取同日情绪核心 JSON 注入 `s3`，按最近 20 个开放日注入最高连板静态趋势图，把打开高度对应的启动日候选注入 `s6`，并从已验证复盘档案汇总人民币外汇历史、向 `s1` 注入静态趋势图。用法见文件头。

## Body chunk 骨架（7 块）

分块写便于逐节迭代；每块是纯 HTML 片段（无 `<html>`/`<head>`/`<body>`）：

| chunk | 内容 |
|---|---|
| `head` | `tldr`, `factor`：header + 6 个 KPI + 3 条短结论 + 🧩三位一体重点因子 |
| `s0` | `s0`：第 0 节前日对照判分 |
| `s1` | `s1`：①大势（境内大盘 + 大类资产 + 外汇掉期） |
| `s2` | `s2`：②板块 |
| `s456` | `s3`, `s4`, `s5`, `s6`：③情绪 + ④风格 + ⑤龙头 + ⑥节点 |
| `s7t` | `s7`, `teachers`, `industry`, `cognition`：⑦持仓 + 🎓老师观点 + 📰行业信息 + 🧠认知对照 |
| `s8ops` | `s8`, `ops`：⑧次日计划 + 🔧数据缺口 + footer |

## 组装与硬校验

正式组装前先用官方 helper 分别生成容量与滚动新高 sidecar，再跑组装脚本。默认读取 `<scratchpad目录>/capacity_<REPORT_DATE>.json` 和 `<scratchpad目录>/new_high_<REPORT_DATE>.json`：

```bash
python3 .agents/skills/daily-review/references/html-report-template/build_capacity_manifest.py \
  <REPORT_DATE> --as-of <TRADE_DATE> \
  --direction <申万二级方向1> [--direction <申万二级方向2>] [--direction <申万二级方向3>] \
  --output <scratchpad目录>/capacity_<REPORT_DATE>.json

python3 .agents/skills/daily-review/references/html-report-template/build_new_high_structure_manifest.py \
  <REPORT_DATE> --as-of <TRADE_DATE> \
  --output <scratchpad目录>/new_high_<REPORT_DATE>.json

python3 .agents/skills/daily-review/references/html-report-template/assemble_report.py <scratchpad目录> <YYYY-MM-DD>
python3 .agents/skills/daily-review/references/html-report-template/assemble_report.py <scratchpad目录> <YYYY-MM-DD> \
  --output data/reports/复盘_YYYY-MM-DD_compact.html
```

组装器默认读取 `data/reports/emotion-leader/<REPORT_DATE>.json`，并从 `data/reports/复盘_*.html` 取最近最多 15 个已验证同日外汇点；仅在回归或迁移时使用 `--emotion-leader-report` / `--fx-history-dir` 改读路径。情绪源失败会生成显式 `missing-data`，外汇同日点少于 8 个工作日会保留历史不足说明，不补 0、不插值。

sidecar 使用其他 helper 输出路径时显式传入；两个参数都只改变读取路径，不是跳过门禁：

```bash
python3 .agents/skills/daily-review/references/html-report-template/assemble_report.py <scratchpad目录> <REPORT_DATE> \
  --capacity-manifest <容量sidecar路径> \
  --new-high-manifest <滚动新高sidecar路径> \
  --output <报告路径>
```

要点（`compact-v2` 外壳，当前 15-anchor 布局）：

- 7 个正式 chunk 必须齐全；15 个锚点 `tldr/factor/s0/s1/s2/s3/s4/s5/s6/s7/teachers/industry/cognition/s8/ops` 必须按此顺序各出现一次。缺失、重复或乱序均拒绝生成。HTML 展示路径固定为“速览 → 三位一体重点因子 → ⓪前日判分 → ①–⑦ → 老师观点 → 行业信息 → 认知对照 → ⑧次日计划 → 数据缺口”。
- 「仓位环境与纪律参考（影子）」和独立「次日推演」已从正式生成链路、桌面/移动导航与锚点契约移除。旧 17-anchor 静态 HTML 仍可直接阅读和迁移校验，但不得作为新报告模板。
- 每节默认「1 句裁决 + 最多 3 条证据 + 1 条证伪或缺口」；无新增只写一行，不生成空表。
- `s1` 不能只交付境内大盘。它必须有唯一默认可见 `data-big-picture="verdict"`，以及折叠证据中的唯一 `data-cross-asset-context` 和 `data-rmb-fx-observation`；报告日休市时三者的 `data-as-of` 仍指向最近事实日，`data-reviewed-through` 等于报告日。缺源只能使用结构化 `missing-data` 并在 `ops` 可见，不得静默通过。
- `s1` 还必须由组装器生成唯一 `data-rmb-fx-chart="v1|missing-data"`：至少 8 个、最多 15 个同日工作日点；上图即期买卖算术中值与 1Y C-Swap 全价共用汇率轴，下图单列掉期点 Pips，图后附折叠数据表。chunk 自行注入、日期乱序、数值越界或价格语义缺失均拒绝生成。
- `s3` 的情绪核心生命周期由组装器从同日 JSON 自动注入唯一 `data-emotion-leader="v1|none|missing-data"`。`v1` 默认可见状态/覆盖/刷新/计数/晋级与二板候选，前 12 只活跃核心进入折叠证据；波段逐行标 `[判断]`。只有 `ok` 真空池允许 `none`，`partial/source_failed` 不得伪装为空池或 0。
- `s3` 还必须由组装器按 `trade_calendar.date` 最近最多 20 个开放日对齐情绪日报，生成唯一、默认可见的 `data-emotion-height-chart="v1|missing-data"` 静态 SVG。每点取 `height_breakthrough.source_status=complete` 的当日非 ST 二板及以上最高连板，图后附日期/高度/状态折叠表；至少 2 个有效日才画。确认无符合项才允许高度 0；日报缺失、损坏或高度不可判时保留空行、折线断开并标 `partial`，不得补 0 或插值。chunk 自行注入、日期乱序、点数/状态不一致均拒绝生成。
- `s6` 还由组装器从同一 JSON 自动注入唯一 `data-emotion-node="v1|none|missing-data"`。仅当目标日非 ST 二板及以上的最高连板数严格超过此前 20 个完整开放日的最高值时使用 `v1`：高度对比标 `[事实]`，打开高度核心的生命周期启动日只标为 `[判断] 情绪节点日候选`。比较窗、目标日涨停事实或启动日对账不完整必须用 `missing-data`，不得写成 `none`。
- `factor` 默认显示主因子、第一辅助、其余因子变化和切换/失效状态。章节容器必须是默认可见的 `<section class="blk" id="factor">`，并声明 `data-factor-mode="formal|rule_only|shadow|no_data"`：有分析时须有且仅有 1 个默认可见 judgment Claim、1～3 个默认可见 `<li>`、1 个默认可见 `data-factor-role="status"`；Claim / `<li>` 必须各自有可见的 `[事实]/[判断]` 标签和实质正文，正文不得藏进后代 `hidden`。状态节点不得藏入普通 `<details>` / `hidden`，且只接受与 mode 对应的规范模板：`formal=正式 factor-score 已完成`、`rule_only=rule_only 结果`；`shadow` 只能使用“正式评分停在日期 / 本日未运行 / 本日尚未评分 / 完成条件未满足”之一，并同时写明“影子口径、不写库”。唯一 `data-evidence-kind="factor-detail"` 折叠证据须实际含 `market_node / sector_rhythm / style_regime / leader_signal`。`no_data` 只允许标题和唯一 `<p data-factor-role="no-data">[事实] 本日无新增/本日无可判数据</p>`。完整四因子证据卡与切换对账不再归到 `s8`。
- Claim owner 使用唯一 `id="claim-*"`；跨节引用使用匹配的 `data-claim-ref="claim-*"`，每个 owner 最多 1 个短引用。悬空引用、重复 owner 或多次引用均拒绝生成。
- 完整证据使用默认收起的 `<details class="evidence" data-as-of="YYYY-MM-DD" data-items="N"><summary>…</summary>…</details>`；summary 后必须有非空文本、表格或内嵌媒体。搜索命中折叠证据时临时展开，退出搜索后恢复原状态；“展开/收起全部”只控制证据区。
- 外壳纯静态、无外部依赖；报告正文禁止 `script/style/form/iframe/object/embed`、事件属性和 `javascript:` URL，禁止远程 `src`/样式、CDN、`fetch`、XHR 与 WebSocket；普通来源超链接可以保留。

### 大势、大类资产与外汇掉期硬门

`section#s1` 固定承载境内市场与外部资产背景，不新增导航锚点：

- 默认可见摘要必须有且仅有一个 `<p data-big-picture="verdict" data-as-of="YYYY-MM-DD" data-reviewed-through="<报告日>">`。它必须带 `[判断]`，同时覆盖“大势”、“大类资产/跨资产”、“外汇/人民币即期/USD-CNY”和“掉期/C-Swap”四组语义，且位于 `details` / `hidden` 之外。
- 大类资产证据必须在折叠层两态之一且全页唯一：非空 `<table data-cross-asset-context="v1">` 或 `<p data-cross-asset-context="missing-data">[事实] 大类资产数据不完整，本日无法判定</p>`。v1 行使用 `data-asset-class="global-equity|china-risk|commodity|volatility|rates"`、唯一 `data-instrument`、非空 `data-source`、受控 `data-status`、`data-date-kind`、严格本地时间和可见 `data-primary-value`。`complete` 必须覆盖五类且每行都有真实来源日；出现任一 `fetch-only` 时只能标 `partial`。`partial` 至少保留三类并在 `ops` 可见披露；只有抓取时间没有来源交易日时，`data-source-date` 留空且可见文字固定写“源交易日缺失”。`source-date` 不得晚于 `observed-at`，两者均不得晚于观察日；来源日和主数值必须同时在可见单元格保留，不能只藏在属性中。
- 人民币外汇证据同样全页唯一：非空 `<table data-rmb-fx-observation="v1">` 或 `<p data-rmb-fx-observation="missing-data">[事实] 人民币即期与1Y C-Swap数据不完整，本日无法判定</p>`。v1 表固定表头加 `spot` 与 `c-swap-1y` 两条身份行。`complete` 时两腿都必须有效；`partial` 时必须保留一腿完整事实，并把另一腿写成 `data-status="missing" data-price-kind="missing"` 的可见缺失行，同时在 `ops` 披露；两腿均缺才使用整块 `missing-data`。有效行固定 `data-pair="USD/CNY"`，只接受中国货币网对应域名和来源，来源日、观察时间、抓取时间必须严格可解析且同日。spot 使用 `computed_bid_ask_mid` 且 `bid <= mid <= ask`、`mid=(bid+ask)/2`；掉期使用 `c_swap_fixing`、`data-tenor="1Y"`、`data-quote-source="报价数据"`、有限 Pips 与正全价，并在可见单元格保留“即期 / 买 / 卖 / 算术中值 / C-Swap / Pips / 定盘 / 报价数据”语义。
- 两张表与默认可见摘要的 `data-as-of` 必须一致，三者 `data-reviewed-through` 都必须等于报告日。`partial / failed` 必须在 `section#ops` 保持相应可见缺口；不允许用 `none` 把来源失败伪装成“没有事件”。

最小结构：

```html
<p data-big-picture="verdict" data-as-of="2026-07-24"
   data-reviewed-through="2026-07-26">[判断] 大势需结合大类资产、外汇与掉期共同确认。</p>
<details class="evidence" data-as-of="2026-07-24" data-items="3">
  <summary>大势完整证据（3 项）</summary>
  <div class="evidence-body">
    <table data-cross-asset-context="v1" data-as-of="2026-07-24"
           data-reviewed-through="2026-07-26" data-source-status="complete">…</table>
    <table data-rmb-fx-observation="v1" data-as-of="2026-07-24"
           data-reviewed-through="2026-07-26" data-source-status="complete">…</table>
  </div>
</details>
```

### 容量中军元数据硬门

容量中军表只接收通过全市场成交额与方向成交额双门槛的股票，不能从趋势池 membership 推导。规则分层与“趋势池历史代表 / 辨识度票”隔离要求以 [`multi-agent-review.md`](../multi-agent-review.md#容量中军独立筛选硬契约) 为准。

- 官方 `assemble_report.py` 每次落盘都必须读取确定性 sidecar：默认 `<scratchpad目录>/capacity_<REPORT_DATE>.json`，或显式 `--capacity-manifest <PATH>`。sidecar 必须由同目录 `build_capacity_manifest.py` 本次生成；禁止手写、事后编辑、复制旧文件或由 Agent 自报排名。缺失、JSON/schema 无效、报告日或 `as_of` 错位都拒绝落盘。
- helper 要求 1～3 个申万二级方向，以分页后的全市场 `daily.amount` 排名和最近 5 个开放日生成 `capacity-health-v1`，并与上市股票基线和申万映射分别核对覆盖率；跨年日历、周末伪交易日、过期 `as_of` 或覆盖不足均 fail-closed。完整 sidecar 有资格行时，HTML 必须逐 `ts_code` 完整展示 sidecar 内全部行，并逐项匹配可见名称、方向、tier、全市场排名、方向排名、Top50 天数及成交额；不得挑选、增删或改写。

- 每份报告的 `section#s5` 无条件三选一，不以标题关键词决定是否校验：全页唯一、非空的 `<table data-capacity-health="v1">`，或全页唯一的 `<p data-capacity-health="none" ...>`，或全页唯一的 `<p data-capacity-health="missing-data" ...>`。多选、全缺或出现在 `s5` 之外均拒绝生成。普通趋势池、主跌辨识度或历史代表表禁止携带这些属性。
- 容量表必须携带 `data-as-of="YYYY-MM-DD"`、`data-source-status="complete"`、`data-universe-count="正整数"`、`data-rank-source="daily.amount"`；每个 `tbody > tr` 必须携带 `data-code="规范 ts_code"`、非空 `data-direction`、`data-tier="core|candidate"`、`data-market-rank="正整数"`、`data-direction-rank="正整数"`、`data-top50-days="0..5"`。表格可见列也必须展示 tier、全市场排名、方向排名和 Top50 天数，便于人工核对。
- 组装器必须按元数据重算资格并拒绝错分：`core` = `market-rank <= 30` 且 `direction-rank <= 2`；`candidate` = `31 <= market-rank <= 50` 且 `direction-rank <= 2`。`top50-days` 只展示最近 5 个开放交易日的容量连续性，不能覆盖当日 `market-rank` 门槛。`data-code` 必须匹配规范 ts_code，表内不得重复；`market-rank` 不得超过 `data-universe-count`。任一值缺失、越界，或不合格行进入容量表，均拒绝生成。
- `trend_leader_pool`、`leader_tracking`、最票或老师观点只能作为其他证据，不能替代上述字段。禁止使用“旧池中军”；未过门者另表命名为“趋势池历史代表（含当日退池）”或“辨识度票”。
- 若来源完整但 `core` 与 `candidate` 均为空，不生成空表；改用 `<p data-capacity-health="none" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 本日无可确认容量中军</p>`。若来源不完整，必须改用 `<p data-capacity-health="missing-data" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 容量排名数据不完整，本日无法判定</p>`，并在数据缺口章节保持可见。两种文本必须精确匹配，`data-as-of` 不得晚于报告交易日。
- helper 因全市场少于 4,000 行、最近 5 个开放日、申万二级映射或 provider 失败而写出失败 sidecar 时，组装器只接受与 sidecar `as_of/status` 一致的 `missing-data`。helper 返回退出码 1 不等于 sidecar 不可用：必须保留其 `errors` 供数据缺口展示；禁止手工把失败 sidecar 改成 complete 或用自然语言替代结构化状态。

合格行的最小骨架：

```html
<table data-capacity-health="v1" data-as-of="2026-07-17" data-source-status="complete"
       data-universe-count="5522" data-rank-source="daily.amount">
  <thead><tr><th>层级</th><th>方向</th><th>股票</th><th>全市 / 方向排名</th><th>近5日Top50</th><th>涨跌 / 成交额</th><th>量比</th><th>均线 / 前高</th><th>健康度</th></tr></thead>
  <tbody>
    <tr data-code="000977.SZ" data-direction="算力" data-tier="core"
        data-market-rank="17" data-direction-rank="1" data-top50-days="4">
      <td>core</td><td>算力</td><td>示例股票</td><td>17 / 1</td><td>4 / 5</td><td>+1.00% / 100.00 亿</td><td>1.10x</td><td>…</td><td>[判断] …</td>
    </tr>
  </tbody>
</table>
```

### 板块集中度与主升主跌辨识度硬门

`section#s2` 的精简只压缩解释，不得删除集中度和主升/主跌辨识度事实：

- 默认可见正文必须有且仅有 1 个 `<p data-sector-concentration="verdict">`，保持在 `details` / `hidden` 之外，只写 1 句带 `[事实]` 或 `[判断]` 标签的集中度裁决。来源不足时也必须明确“无法判定”，不能省略该句。
- 集中度证据必须在 `details.evidence` 内三选一且全页唯一：携带 `data-as-of="YYYY-MM-DD" data-source-status="complete"` 的非空 `<table data-sector-concentration="v1">`；完整覆盖但无记录时 `<p data-sector-concentration="none" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 本日无可用板块集中度数据</p>`；来源不完整时 `<p data-sector-concentration="missing-data" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 板块集中度数据不完整，本日无法判定</p>`。
- 主升和主跌为一对独立必交模块。主升模块必须三选一：携带 `data-as-of` 与 `data-source-status="complete"` 的唯一非空 `<table data-rising-recognition="v1">`；`<p data-rising-recognition="none" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 本日无符合规则的主升辨识度个股</p>`；或 `<p data-rising-recognition="missing-data" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 主升辨识度矩阵数据不完整，本日无法判定</p>`。
- 主跌模块同理三选一：携带 `data-as-of` 与 `data-source-status="complete"` 的唯一非空 `<table data-falling-recognition="v1">`；`<p data-falling-recognition="none" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 本日无符合规则的主跌辨识度个股</p>`；或 `<p data-falling-recognition="missing-data" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 主跌辨识度矩阵数据不完整，本日无法判定</p>`。两侧都必须存在一种状态，禁止只保留主跌、只保留主升或把两侧混成一张表；任一 `missing-data` 都须在数据缺口章节保持可见。
- 三张证据表或其状态元素必须位于 `section#s2`；同类多张、全部缺失、空表、放在正文默认可见区或用自然语言代替结构化状态，均拒绝生成。

### 板块趋势标签硬门

`section#s2` 每次还必须展示申万二级的半年线、年线与近期价量共振标签，不得用全行业拥挤度表或主升/主跌矩阵代替：

- 默认可见正文必须有且仅有 1 个 `<p data-sector-labels="verdict" data-as-of="YYYY-MM-DD">`。该句同时展示半年线上、年线上、近期价量共振、年线+共振四项命中数；半年线/年线标 `[事实]`，近期共振标 `[判断]`。部分覆盖或无法判定必须在本句显式披露。
- 证据层全页唯一三选一：有已确认命中时使用 `<table data-sector-labels="v1">`，只列三标签命中并集；完整覆盖且四项均为零时使用 `<p data-sector-labels="none">[事实] 本日半年线、年线与近期价量共振标签均无命中板块</p>`；无法形成任何可信判断时使用 `<p data-sector-labels="missing-data">[事实] 板块趋势标签数据不完整，本日无法判定</p>`。三者的 `data-as-of` 必须与可见摘要一致且不晚于报告日。
- 三种状态都固定携带 `data-half-year-window="144"`、`data-year-window="233"`、`data-resonance-lookback="10"`、`data-resonance-breakout-window="20"`；`v1 / none` 另须携带 L2 总数、目标日缺失数、四项命中数和三类数据不足数。`complete` 只允许所有缺失/不足数为零；仍有已确认命中但覆盖不完整时使用 `partial` 表，并在摘要与 `section#ops` 可见写明“板块趋势标签数据不完整”。
- `v1` 每行必须有唯一申万 `.SI` 代码、`data-above-half-year-ma`、`data-above-year-ma`、`data-recent-resonance` 三个明确布尔值，且至少一项为 `true`。半年线/年线命中在可见单元格标 `[事实]`；近期共振命中标 `[判断]` 并携带不晚于来源日的 `data-last-resonance-date`。逐行布尔值必须与四项汇总数完全对账。
- 来源全缺时 `missing-data` 使用 `data-source-status="partial|failed"`，并在可见摘要和 `section#ops` 登记；不得把缺失或历史不足伪装成 `none / false`。标签只读取已落库的 `sector_crowding_daily` 并现算，不在 HTML 流程重复采集或落派生值。

### 历史新高结构硬门

`section#s5` 的“历史新高结构”固定使用前复权滚动窗，不得拿 `daily_new_high_stats` 的全历史高水位结果代替：

- 每次正式落盘必须读取本次 helper 原子生成的 `rolling-new-high-structure-v1` sidecar，默认路径为 `<scratchpad目录>/new_high_<REPORT_DATE>.json`；缺失、手工损坏、日期/口径错位，或 HTML 与 sidecar 不一致都拒绝生成。sidecar 必须保留 251 个开放日逐日市场覆盖，任一日低于上市股票基线 90% 均 fail-closed。
- 默认可见正文必须有且仅有 1 个 `<p data-new-high-structure="verdict">`，位于折叠/隐藏区域之外，写 1 句带标签的 60/120/250 日结构裁决。
- 证据层必须三选一且全页唯一：非空 `<table data-new-high-structure="v1">`、`<p data-new-high-structure="none">` 或 `<p data-new-high-structure="missing-data">`；空壳表、重复模块、错放章节或 CSS 隐藏均拒绝生成。
- 完整表必须携带 `data-as-of`、严格更早的 `data-prev-as-of`、`data-source-status="complete"`、`data-market-count>=4000`、`data-basis="rolling-adjusted-high"`，以及 `data-current-60/120/250-count` 与 `data-prev-60/120/250-count` 六个非负整数；计数须满足 60 日 ≥ 120 日 ≥ 250 日。
- 组装器会对账默认可见裁决、表格可见双日计数、60 日行业 Top3/CR3、名单重合/延续率/换手率和最多 5 个代表票；用 `hidden`、CSS 隐藏或旧数字冒充正确值同样拒绝生成。代表票只有与容量 sidecar 对账通过后才能标 `core/candidate`；其余只称“滚动新高辨识度票”。
- 三窗口均为 0 且来源完整时使用 `<p data-new-high-structure="none" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 本日无符合 60/120/250 日滚动新高口径的个股</p>`。行情、复权因子、上市日期或行业映射不足时使用 `<p data-new-high-structure="missing-data" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 滚动新高结构数据不完整，本日无法判定</p>`，并在数据缺口章节保持可见。

### 未来七日事件窗硬门

`section#s6` 必须保留节点与未来一周事件窗：

- 连板高度趋势、情绪节点联动与未来事件窗是三个独立模块，互不替代。全页必须且只能有一份组装器生成的 `data-emotion-height-chart="v1|missing-data"` 和一份 `data-emotion-node="v1|none|missing-data"`；chunk 自行注入直接拒绝生成。
- `v1` 必须带 `data-lookback-open-days="20"`、目标日/此前窗口最高板数、此前窗口起止日以及至少一只打开高度核心；默认可见正文同时包含 `[事实] 打开非ST连板高度` 和 `[判断] 启动日…情绪节点日候选`，折叠表逐票显示代码、当日高度、启动日与启动日来源。启动日不得晚于报告日。
- 未打开高度时使用规范 `none` 事实句；20 个开放日比较窗、目标日涨停事实或启动日对账任一不完整时使用 `missing-data`，禁止把缺口解释为“未触发”。节点候选只是一条需结合事件日历和市场/板块结构复核的 `[判断]`。

- 默认可见正文必须有且仅有 1 个 `<p data-event-window="verdict">`，位于 `details` / `hidden` 之外，只写 1 句带 `[事实]` 或 `[判断]` 标签的事件窗裁决。
- 证据层必须三选一且全页唯一：非空 `<table data-event-window="v1">`、`<p data-event-window="none">` 或 `<p data-event-window="missing-data">`。三者均须携带 `data-window-start`（报告日 +1）、`data-window-end`（报告日 +7）、`data-as-of` 与 `data-source-status`，精确覆盖报告日后的 7 个自然日；表格的 `data-source-status` 只能为 `complete`。
- 表格每个事件行必须显示事件日期、该日 `交易 / 休市` 状态、事件内容及其是否影响次日验证；只收录会影响次日验证的时间窗口。
- 窗口内没有此类事件时使用 `<p data-event-window="none" data-window-start="YYYY-MM-DD" data-window-end="YYYY-MM-DD" data-as-of="YYYY-MM-DD" data-source-status="complete">[事实] 未来7个自然日无影响次日验证的新增事件</p>`。交易日历或事件来源不完整时使用 `<p data-event-window="missing-data" data-window-start="YYYY-MM-DD" data-window-end="YYYY-MM-DD" data-as-of="YYYY-MM-DD" data-source-status="partial|failed">[事实] 未来7个自然日事件窗数据不完整，本日无法判定</p>`，并在数据缺口章节保持可见。空表、窗口日期错位、静默省略或只写自然语言均拒绝生成。

### 内容预算

| 区域 | 非空白字符 | 表格 | 表格行 |
|---|---:|---:|---:|
| `tldr` | ≤ 500 | 计入正文 | 计入正文 |
| 默认可见正文 | 目标 ≤ 6,000；硬上限 10,500 | ≤ 12 | ≤ 80 |
| `details.evidence` 折叠层 | ≤ 40,000 | ≤ 60 | ≤ 400 |

组装器按章节计算并报告用量；超过硬上限时指出责任章节并非零退出，不自动截断、改写或把冗余移入折叠层。

## 迭代要点

- 逐节增改后重跑结构与预算校验；用户新增证据（老师观点/持仓变动/派生报告补跑）就地织入唯一归属章节再重建。
- 报告全程 `[事实]`/`[判断]` 分标；红线见 `multi-agent-review.md`。
- 量能一律用镜像综指口径 `000001.SH + 399106.SZ` 自算；北向资金维度继续禁用，只读边界不变。
