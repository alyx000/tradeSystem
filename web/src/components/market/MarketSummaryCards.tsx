import type {
  MarketFullData,
  MarketMoneyflowSummary,
} from '../../lib/types'

function fmtAmount(v: number | null | undefined) {
  if (v == null) return '-'
  return v >= 10000 ? `${(v / 10000).toFixed(2)}万亿` : `${v.toFixed(0)}亿`
}

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function fmtSignedYi(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}亿`
}

function StatCard({
  label,
  value,
  suffix,
}: {
  label: string
  value: string | number | null | undefined
  suffix?: string
}) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-400">{label}</span>
      <span className="text-sm font-semibold text-gray-800">{value ?? '-'}{suffix && ` ${suffix}`}</span>
    </div>
  )
}

export default function MarketSummaryCards({
  market,
  marketMoneyflow,
}: {
  market: MarketFullData
  marketMoneyflow: MarketMoneyflowSummary | null
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">成交与资金</h2>
        <div className="grid grid-cols-2 gap-4">
          <StatCard label="两市成交额" value={fmtAmount(market.total_amount)} />
          {/* 北向净额已下线:沪深交易所 2024-08-16 起停更每日净额,tushare north_money 口径存疑(个股净额全 0/聚合非 0)。 */}
          <StatCard label="上涨家数" value={market.advance_count} />
          <StatCard label="下跌家数" value={market.decline_count} />
        </div>
      </div>
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">大盘资金流向</h2>
        {marketMoneyflow ? (
          <div className="grid grid-cols-2 gap-4">
            <StatCard label="主力净额" value={fmtSignedYi(marketMoneyflow.netAmountYi)} />
            <StatCard label="净占比" value={fmtPct(marketMoneyflow.netAmountRate)} />
            <StatCard label="超大单" value={fmtSignedYi(marketMoneyflow.superLargeYi)} />
            <StatCard label="大单" value={fmtSignedYi(marketMoneyflow.largeYi)} />
          </div>
        ) : (
          <div className="text-sm text-gray-400 py-6 text-center">暂无大盘资金流向</div>
        )}
      </div>
    </div>
  )
}
