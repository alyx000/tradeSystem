import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

const TIER_LABELS: Record<string, string> = {
  tier1_core: '核心标的',
  tier2_watch: '观察标的',
  tier3_sector: '板块标的',
}

export default function Watchlist() {
  const { data: watchlist, isLoading } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => api.getWatchlist(),
  })

  const grouped = (watchlist || []).reduce((acc: Record<string, any[]>, item: any) => {
    const tier = item.tier || 'other'
    if (!acc[tier]) acc[tier] = []
    acc[tier].push(item)
    return acc
  }, {})

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-gray-800">关注池</h1>

      {isLoading ? (
        <div className="text-gray-500 text-sm">加载中...</div>
      ) : (
        Object.entries(grouped).map(([tier, items]) => (
          <div key={tier} className="bg-white rounded-lg shadow">
            <div className="px-4 py-3 border-b bg-gray-50 rounded-t-lg font-medium text-gray-800">
              {TIER_LABELS[tier] || tier}
              <span className="text-gray-500 text-sm ml-2">({items.length})</span>
            </div>
            <div className="divide-y">
              {items.map((item: any) => (
                <div key={item.id} className="px-4 py-3 text-sm flex justify-between items-start">
                  <div>
                    <span className="font-medium text-gray-800">{item.stock_name}</span>
                    <span className="text-gray-400 font-mono ml-2">{item.stock_code}</span>
                    {item.sector && (
                      <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded ml-2">
                        {item.sector}
                      </span>
                    )}
                  </div>
                  <div className="text-right text-xs text-gray-500">
                    {item.add_date && <div>{item.add_date}</div>}
                    {item.add_reason && <div className="text-gray-400">{item.add_reason}</div>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))
      )}
      {!isLoading && (!watchlist || watchlist.length === 0) && (
        <div className="text-gray-400 text-sm py-4 text-center">暂无关注标的</div>
      )}
    </div>
  )
}
