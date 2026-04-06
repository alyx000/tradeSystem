import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { addDaysLocal, localDateString } from '../lib/date'
import type { CalendarEvent } from '../lib/types'

function getDefaultDateRange() {
  const now = new Date()
  const today = localDateString(now)
  const next30 = localDateString(addDaysLocal(now, 30))
  return { today, next30 }
}

export default function Calendar() {
  const { today, next30 } = getDefaultDateRange()
  const [dateFrom, setDateFrom] = useState(today)
  const [dateTo, setDateTo] = useState(next30)

  const { data: events, isLoading } = useQuery({
    queryKey: ['calendar-range', dateFrom, dateTo],
    queryFn: () => api.getCalendarRange(dateFrom, dateTo),
  })

  const grouped = (events || []).reduce((acc: Record<string, CalendarEvent[]>, evt: CalendarEvent) => {
    if (!acc[evt.date]) acc[evt.date] = []
    acc[evt.date].push(evt)
    return acc
  }, {})

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">投资日历</h1>
        <div className="flex gap-2 items-center text-sm">
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="border rounded px-2 py-1" />
          <span className="text-gray-400">~</span>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="border rounded px-2 py-1" />
        </div>
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-sm">加载中...</div>
      ) : Object.keys(grouped).length === 0 ? (
        <div className="text-gray-400 text-sm py-4 text-center">该日期范围内无事件</div>
      ) : (
        Object.entries(grouped).sort().map(([date, evts]) => (
          <div key={date} className="bg-white rounded-lg shadow">
            <div className="px-4 py-2 bg-gray-50 rounded-t-lg text-sm font-medium text-gray-700 border-b">
              {date} ({new Date(date).toLocaleDateString('zh-CN', { weekday: 'short' })})
            </div>
            <div className="divide-y">
              {evts.map((e: CalendarEvent) => (
                <div key={e.id} className="px-4 py-3 text-sm flex items-center gap-3">
                  <span className={`shrink-0 w-2 h-2 rounded-full ${
                    e.impact === 'high' ? 'bg-red-500' :
                    e.impact === 'medium' ? 'bg-amber-500' : 'bg-gray-300'
                  }`} />
                  <div className="flex-1">
                    <span className="text-gray-800">{e.event}</span>
                    {e.country && <span className="text-gray-400 text-xs ml-2">{e.country}</span>}
                  </div>
                  {e.time && <span className="text-gray-400 text-xs">{e.time}</span>}
                </div>
              ))}
            </div>
          </div>
        ))
      )}
    </div>
  )
}
