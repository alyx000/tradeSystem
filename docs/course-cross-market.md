# 鞠磊课程盘中外盘观察

在上交所开放日，按上海时间保留 09:32 至 11:22、13:02 至 14:52 每十分钟的外盘价格证据。每个槽比较目标完成分钟与严格前十分钟的完成分钟。时间精度为分钟。交易所逐笔成交时刻、源发布时间、程序收包时刻是不同的事实。

| 标的 | 报价与合约 | 核验条件 |
|---|---|---|
| CME E-mini Nasdaq-100 期货 | Yahoo 明确季度合约，如 2026-11-27 的 NQZ26.CME | 响应必须同时匹配合约代码、CME、FUTURE 及一分钟周期。选月使用 CME 已公布的美国股指期货惯例换月日与到期日表，覆盖 2025–2028 年，表外停止。芝加哥 17:00 起按下一 Globex 交易日取换月日。2026 年 12 月合约于 12 月 14 日换月、18 日到期。 |
| SGX 富时中国 A50 指数期货 | 东财 104.CN00Y，A50期指当月连续 | 响应须含市场 104、合约族代码 CN00Y 和完整同名。源未披露该分钟所映射的具体合约月。归档分别保存两端原始分钟标签与报价。 |
| 恒生科技现货指数 | Yahoo HSTECH.HK | 响应须同时匹配代码、香港市场、INDEX 和一分钟周期。 |

Yahoo 返回的 Unix 秒是分钟 K 线起点。以一分钟后的完成边界对齐，原始 epoch 保留，零散的尾部快照不入收盘序列。NQ 依照芝加哥时区标注市场所在地，Yahoo 报价源时区另列。恒科使用香港时区。延迟以来源披露为准，未给出具体分钟数时保留未知。无目标时刻的报价仍可保留邻近且较早的源分钟、距槽位的秒数、以及该源时刻自身的十分钟前变化。仅两端严格匹配的标的计入完成数；不能将延迟源时点的比较算入严格窗口。

东财分时接口返回新加坡当地分钟标签（与上海同为 UTC+8），公开字段没有明确左右端点。目标边界的收盘价存在两个候选源标签，即该刻和前一分钟。两项价格一致才能确定边界价，删去其中一个、候选异价或有重复冲突均留下缺口。A50 单独在槽位后至少 60 秒发请求，以等候该标签可能代表的分钟完结。归档的实际收包时刻用于说明何时可知。其他两路在槽位开始时独立取数。三路均只使用同一标的、同一报价源的两端。跨时段无前端分钟（如恒科早盘开市或午后刚复市）会保留空值。

Yahoo 若提供覆盖该刻的具体常规开放区间，原值保留。只有分钟记录时只陈述有行情，缺行、节假日或半日市不据此判断开市或休市。两路 Yahoo 沿用系统网络配置；国内东财默认独立直连，显式设置既有 TRADESYSTEM_USE_HTTP_PROXY 后随系统代理取数。网络、权限、无分钟、身份冲突分别记录。

## 使用

```sh
/usr/bin/python3 scripts/tools/course_cross_market.py --scheduled-at 2026-11-30T10:02:00+08:00 --input-by launchd
/usr/bin/python3 scripts/tools/course_cross_market.py --date 2026-11-27 --slot 10:02 --replay --input-by USER
/usr/bin/python3 scripts/tools/course_cross_market.py --replay-day --date 2026-11-27 --input-by USER
```

`--dry-run` 只在内存计算，`--json` 打印完整回执。时区明确的定时实采必须在目标时刻起三分钟内发起和收到响应，每路独立计时。实时档位于 `data/reports/course-cross-market/YYYY-MM-DD/HHMM.json`，事后回放另存该日期的 `replay/HHMM.json`，回放不能证明盘中当时可获悉。完成项重跑保留，缺项下次可重试。回执含请求时间、来源、实际返回的身份、原始时间标签、价格、原响应 SHA-256 和归档校验。整槽加锁并原子替换。退出 0 表示完成或跳过，2 表示部分覆盖、缺源或参数错误。

全日模式仅在目标 SSE 开放日上海 15:00 后受理。它先读取当日 24 个回放槽，按缺项的实际代码/合约分组，各源只请求一次。Yahoo 请求覆盖组内最早槽向前 90 分钟至最晚槽，东财请求来源支持的最近五日分时，然后按原日期与分钟筛选。24 槽各自保存共享请求参数、原始请求/收包时刻与同期数据来源。日汇总 `replay/day.json` 列出实际请求数、每槽完整数和该槽文件校验，每个有效来源在单槽中独立保全。全日已完成且逐槽校验一致时复用旧收据。补回放以既有 20:00 流程调用。

2026-11-27 的实际全日回放保存于 `data/reports/course-cross-market/2026-11-27/replay/day.json`，10:02 槽位于同目录的 `1002.json`。当前运行环境访问 Yahoo 的代理连接失败，东财直连域名解析失败。实际共 3 次网络请求，24 槽 × 3 个标的中有完整两端报价的为 0/72。品种页核验了标的身份，未取得这两分钟的原始报价。已有盘后 A50 为日线，旧恒科栏曾走 3033.HK，不能用于该槽位。

## 口径来源

- [CME 美股指季度期货换月、到期日表](https://www.cmegroup.com/trading/equity-index/rolldates.html)
- [CME E-mini Nasdaq-100 产品规格](https://www.cmegroup.com/markets/equities/nasdaq/e-mini-nasdaq-100.contractSpecs.html)
- [Yahoo NQZ26.CME 实际合约资料](https://sg.finance.yahoo.com/quote/NQZ26.CME/)
- [SGX 富时中国 A50 期货](https://www.sgx.com/derivatives/products/chinaA50)
- [东财 104.CN00Y 品种及五日分时页面](https://hf-wap.eastmoney.com/quote/stock/104.CN00Y.html)
- [Yahoo 恒生科技指数现货报价](https://ca.finance.yahoo.com/quote/HSTECH.HK/history/)
