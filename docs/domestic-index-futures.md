# 境内股指期货日行情、会员持仓和基差

收盘后按交易日归档中金所沪深 300 股指期货（IF）、中证 1000 股指期货（IM）各交割月。11 月 18 日鞠磊笔记 #1249 提到期指空单可能涉及套保。公开排名只展示会员口径，不能凭持仓增减确定客户、交易方向背后的目的或套保动机。

| 记录 | 数据口径 |
|---|---|
| 挂牌合约 | Tushare `fut_basic(exchange=CFFEX,fut_type=1)`。代码须是产品 + 四位交割月 + `.CFX`，核对交易标识、产品、交易所及挂牌日至最后交易日。来源须核到相邻的近两个月和随后的两个季月，才能确认覆盖通常的全部四个交割月。月份规则仅用于检查。合约表缺失时，来源能够证实的单个合约仍可留存，挂牌总数记未知。 |
| 日行情 | Tushare `fut_daily` 取实际合约当日的开高低收、结算、前结算（指数点）、成交量、持仓量和持仓变化（手）。日线不足时读取中金所日 CSV，并用带交易日的 XML 逐合约核对成交量、收盘和持仓。过去月份另可使用官方月包内明确日期的交易文件。 |
| 会员榜 | 依合约逐一请求 Tushare `fut_holding`，核对当日、交易所、具体月份，区分每家会员披露的持买 / 持卖数量及较上一交易日增减（手）。缺项时另核中金所该产品当日排名 CSV，再按具体合约拆分。成交、多、空榜单里的同一行可属于不同会员。缺席不补零，不跨月份拼接。 |
| 现货与基差 | IF 对应 000300.SH 沪深 300，IM 对应 000852.SH 中证 1000。优先复用当日盘后归档中已核身份的 `tushare:index_daily`，再尝试同日指数接口。基差为同日同标的 **期货收盘价 − 现货收盘价**，单位指数点。正数为升水，负数为贴水；结算价另外保存。有成交且两个收盘均有证据才计算。 |

每一侧会员列表的覆盖单独统计。单次来源给足 20 家唯一会员且每行数量与增减齐全，才将该侧标为完整；只披露少数、收到不全、或仅有某侧时保留实有条目，并标明覆盖。不同时间抓到的残缺榜单不能凑成完整证明。会员排名反映公开范围，不能据此推算全市场净空单。

使用既有 20:00 盘后任务。历史独立复核从仓库根目录执行：

```bash
/usr/bin/python3 scripts/tools/domestic_index_futures.py --date 2026-11-27 --input-by USER
```

使用上海 16:00 和完整 SSE 交易日历作运行守卫。结果为 `data/reports/domestic-index-futures/YYYY-MM-DD.json`。文件中 `receipts` 保存请求参数、时间、来源身份、内容哈希或错误，逐合约数据引用对应回执；`attempts` 保存手动和定时执行者。部分覆盖的日档可继续补齐，失败后保留已有有效行，来源冲突留痕。已完整日档校验通过后直接复用。退出 0 表示完整或跳过，2 表示缺源、部分覆盖或输入错误。`--dry-run` 在内存中复核。

如需核对已公开的收盘行情，可把博易该日单一品种的完整网页文本交给 `scripts/tools/domestic_futures_bulletin.py --web-text-file FILE --date YYYY-MM-DD --input-by USER`。独立参考档在 `data/reports/domestic-index-futures/references/YYYY-MM-DD/`，保留转载页地址、发布日期、全文哈希和本机取得文本的时间。各月合约对照主日档内已核验的同日现货。参考档与定时行情的覆盖分别统计。11 月 27 日的 [IM 收盘行情转载](https://webnewslt1.pobo.net.cn/DATA/STOCK/6831585.HTML) 列出四个交割月。

口径依据为 [Tushare 合约表](https://tushare.pro/document/2?doc_id=135)、[期货日线](https://tushare.pro/document/2?doc_id=138)、[持仓排名](https://tushare.pro/document/2?doc_id=139) 和 [中金所成交持仓排名](https://www.cffex.com.cn/ccpm/)。

独立转载的盘后收盘表使用 `scripts/tools/domestic_futures_bulletin.py`，证据放在 `data/reports/domestic-index-futures/references/YYYY-MM-DD/`。转载表与正式生产日档分别留存；按日合约总持仓和会员排名也分别计数。

同花顺逐会员公开网页可以保存为独立参考。只接受带具体合约及会员编号的详情页，摘录须含原页面标题、带行号的同名表标题、连续表头（手）和目标日唯一数据行。上一 SSE 开放日的同表记录也在摘录中时，核对成交量、持买和持卖的日增减。只披露一侧时，另一侧保留未知；会员客户的逐笔交易和目的不在该表内。正式日榜的覆盖单独统计。

```bash
/usr/bin/python3 scripts/tools/domestic_futures_member_reference.py --web-text-file PAGE.txt --date 2026-11-27 --input-by USER
```

单会员结果位于 `data/reports/domestic-index-futures/references/YYYY-MM-DD/members/合约-会员编号.json`。网页上新增其他交易日、检索标识或行号变化，不改变已核验的同日事实。原始摘录、哈希、接收时间上界、已核的前日增减均留档。目标日期的来源数字冲突时保留旧档。
