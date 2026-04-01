import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

const today = new Date().toISOString().slice(0, 10)

export default function Dashboard() {
  const { data: review } = useQuery({ queryKey: ['review', today], queryFn: () => api.getReview(today) })
  const { data: holdings } = useQuery({ queryKey: ['holdings'], queryFn: api.getHoldings })
  const { data: calendar } = useQuery({
    queryKey: ['calendar', today],
    queryFn: () => api.getCalendarRange(today, today),
  })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-800">仪表盘 - {today}</h1>

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
