import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

const today = new Date().toISOString().slice(0, 10)

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

export default function Dashboard() {
  const { data: review } = useQuery({ queryKey: ['review', today], queryFn: () => api.getReview(today) })
  const { data: holdings } = useQuery({ queryKey: ['holdings'], queryFn: api.getHoldings })
  const { data: calendar } = useQuery({
    queryKey: ['calendar', today],
    queryFn: () => api.getCalendarRange(today, today),
  })
  const { data: market } = useQuery({
    queryKey: ['market', today],
    queryFn: () => api.getMarket(today),
  })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-800">仪表盘 - {today}</h1>

      {/* 市场摘要卡片 */}
      {market?.available && (
        <Link to={`/market/${today}`} className="block">
          <div className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-gray-500">今日市场</h2>
              <span className="text-xs text-blue-500">查看详情 &rarr;</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div>
                <div className="text-xs text-gray-400">上证</div>
                <div className={`text-sm font-semibold ${market.sh_index_change_pct == null ? 'text-gray-500' : market.sh_index_change_pct >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {fmtPct(market.sh_index_change_pct)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">深证</div>
                <div className={`text-sm font-semibold ${market.sz_index_change_pct == null ? 'text-gray-500' : market.sz_index_change_pct >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {fmtPct(market.sz_index_change_pct)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">成交额</div>
                <div className="text-sm font-semibold text-gray-800">
                  {market.total_amount != null
                    ? market.total_amount >= 10000
                      ? `${(market.total_amount / 10000).toFixed(2)}万亿`
                      : `${market.total_amount.toFixed(0)}亿`
                    : '-'}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">涨停</div>
                <div className="text-sm font-semibold text-red-600">{market.limit_up_count ?? '-'}</div>
              </div>
              <div>
                <div className="text-xs text-gray-400">跌停</div>
                <div className="text-sm font-semibold text-green-600">{market.limit_down_count ?? '-'}</div>
              </div>
            </div>
          </div>
        </Link>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">复盘状态</h2>
          <div className="text-lg font-semibold">
            {review?.exists ? (
              <span className="text-green-600">已完成</span>
            ) : (
              <Link to={`/review/${today}`} className="text-amber-600 hover:underline">
                待复盘 →
              </Link>
            )}
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">持仓数量</h2>
          <div className="text-lg font-semibold text-gray-800">
            {holdings?.length ?? 0} 只
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">今日日历事件</h2>
          <div className="text-lg font-semibold text-gray-800">
            {calendar?.length ?? 0} 条
          </div>
        </div>
      </div>

      {calendar && calendar.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-3">今日投资日历</h2>
          <ul className="space-y-2">
            {calendar.map((e: any) => (
              <li key={e.id} className="flex items-center gap-2 text-sm">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  e.impact === 'high' ? 'bg-red-100 text-red-700' :
                  e.impact === 'medium' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-600'
                }`}>
                  {e.impact || '一般'}
                </span>
                <span className="text-gray-800">{e.event}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
