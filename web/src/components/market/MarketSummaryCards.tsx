import type {
  DailyInfoRow,
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

function Ma5wBadge({ label, value }: { label: string; value: boolean | number | null | undefined }) {
  const above = value === true || value === 1
  const below = value === false || value === 0
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${
      above ? 'bg-red-50 text-red-700' : below ? 'bg-green-50 text-green-700' : 'bg-gray-100 text-gray-500'
    }`}>
      {label}
      <span>{above ? '线上' : below ? '线下' : '-'}</span>
    </span>
  )
}

export default function MarketSummaryCards({
  market,
  marketMoneyflow,
  dailyInfoRows,
}: {
  market: MarketFullData
  marketMoneyflow: MarketMoneyflowSummary | null
  dailyInfoRows: DailyInfoRow[]
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">成交与资金</h2>
        <div className="grid grid-cols-2 gap-4">
          <StatCard label="两市成交额" value={fmtAmount(market.total_amount)} />
          <StatCard label="北向净额" value={market.northbound_net} suffix="亿" />
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
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">市场交易结构</h2>
        {dailyInfoRows.length > 0 ? (
          <div className="space-y-2">
            {dailyInfoRows.slice(0, 5).map((row) => (
              <div key={row.ts_code || row.ts_name} className="flex items-center justify-between gap-3 text-sm border-b border-gray-50 pb-2 last:border-0 last:pb-0">
                <div className="min-w-0">
                  <div className="font-medium text-gray-800 truncate">{row.ts_name || row.ts_code || '-'}</div>
                  <div className="text-xs text-gray-400">
                    总市值 {row.total_mv != null ? fmtAmount(Number(row.total_mv)) : '-'}
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="font-semibold text-gray-800">{row.amount != null ? fmtAmount(Number(row.amount)) : '-'}</div>
                  <div className="text-xs text-gray-400">
                    换手 {row.tr != null ? `${Number(row.tr).toFixed(2)}%` : '-'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-gray-400 py-6 text-center">暂无市场结构数据</div>
        )}
      </div>
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">5周均线状态</h2>
        <div className="flex flex-wrap gap-2">
          <Ma5wBadge label="上证" value={market.sh_above_ma5w} />
          <Ma5wBadge label="深证" value={market.sz_above_ma5w} />
          <Ma5wBadge label="创业板" value={market.chinext_above_ma5w} />
          <Ma5wBadge label="科创50" value={market.star50_above_ma5w} />
          <Ma5wBadge label="均价" value={market.avg_price_above_ma5w} />
        </div>
      </div>
    </div>
  )
}
