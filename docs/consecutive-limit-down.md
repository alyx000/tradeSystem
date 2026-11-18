# 每个交易日盘后连续跌停个股数

既有交易日20:00 `main.py post` 采集，在 `MarketCollector._enrich_limit_down_structure` 复用本次跌停名单，读取历史盘后归档与SSE交易日历。结果进入 `raw_data.consecutive_limit_down`，随原 `post-market.yaml` 与 `daily_market` 信封双写。盘后报告展示摘要及折叠名单；不新增任务、推送或业务表，不改历史归档。

## 统计口径

- 截至事实日连续至少2个市场开放日收盘跌停，普通下跌或盘中触及后打开不算。按真实跌停名单，不用固定跌幅猜测。
- 非ST主统计剔除退市名称与B股，ST只另列已核验数量，不混加。
- 校验最近61自然日完整SSE日历，休市及当日上海16:00前跳过。只沿日历中的开放日向前逐日核验，不以已有目录推测相邻交易日。
- 历史档案必须日期匹配、名单来源有效、计数与行数一致、代码唯一；裸代码与带交易所代码统一后联结。
- 缺失/失败/损坏档案不等于空名单，来源切换时缺席不证明中断。已连续核验≥2日仍可计入已确认数量；更早缺口只影响精确天数和起始日。上一开放日未知则该票连续性待核。
- 最多回溯60自然日历史；未找到同源名单内的非跌停边界，只报告“至少N日”，真实起始日待核。周末/假日不计日，停牌不会跳过后拼接。

## 来源范围和字段

当前复用 `tushare:limit_list_d` / `akshare:stock_zt_pool_dtgc_em`。现有接口未提供全市场身份与分页覆盖证明，ST及北交所覆盖未核验，因此整个块标 `partial`，不宣称全市场完整。

| 字段 | 语义 |
|---|---|
| `count` | 当日来源名单内非ST连续≥2日数量；有待核非ST对象时为null |
| `verified_count` | 已确认连续≥2日的非ST数量；来源/日历失败时为null |
| `st_verified_count` | 已确认连续≥2日的ST数量，不代表完整ST总数 |
| `market_count` | 全市场总数；当前覆盖未核验，保持null |
| `rows` / `unknown_rows` | 已确认名单 / 连续性待核对象；含名称、代码、天数、精确标记、起始日、逐日证据 |
| `evidence` / `calendar_days` | 原始逐日跌停名单 / 完整日历确认的开放日序列 |
| `gaps` / `scope` | 来源覆盖和历史缺口；缺失不得补0 |

完整有效空名单允许在“来源范围内”记0，不代表全市场0。`source_failed`、`skipped`保留null。历史未计算的报告继续兼容原梯队展示。

## 验证

`python3 -m pytest scripts/tests/test_consecutive_limit_down.py scripts/tests/test_limit_down_structure.py -q`

覆盖正常数量、ST/B股/退市分离、代码标准化、周末、缺少整个日期目录、日期错位、来源切换、重复行、缺日历、盘中禁算、下界与起始日、失败非零及盘后信封落盘。全后端检查用 `make check-scripts`。
