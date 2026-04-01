import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

const ENTITY_LABELS: Record<string, string> = {
  teacher_notes: '老师观点',
  industry_info: '行业信息',
  macro_info: '宏观信息',
}

export default function SearchCenter() {
  const [keyword, setKeyword] = useState('')
  const [query, setQuery] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const { data: results, isLoading } = useQuery({
    queryKey: ['search', query, dateFrom, dateTo],
    queryFn: () => api.unifiedSearch(query, {
      ...(dateFrom && { from: dateFrom }),
      ...(dateTo && { to: dateTo }),
    }),
    enabled: query.length > 0,
  })

  const handleSearch = () => { if (keyword.trim()) setQuery(keyword.trim()) }

  const handleExport = async () => {
    if (!query) return
    const md = await api.exportSearch(query)
    const blob = new Blob([md], { type: 'text/markdown' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `search-${query}.md`
    a.click()
  }

  const totalCount = results
    ? Object.values(results as Record<string, any[]>).reduce((s, arr) => s + arr.length, 0)
    : 0

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold text-gray-800">信息查询中心</h1>

      <div className="flex gap-2 items-end">
        <div className="flex-1">
          <input type="text" value={keyword} onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="输入关键词搜索…"
            className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
        </div>
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
          className="border rounded px-2 py-2 text-sm" />
        <span className="text-gray-400">~</span>
        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
          className="border rounded px-2 py-2 text-sm" />
        <button onClick={handleSearch}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">搜索</button>
        {results && totalCount > 0 && (
          <button onClick={handleExport}
            className="border border-gray-300 text-gray-700 px-3 py-2 rounded text-sm hover:bg-gray-50">
            导出 Markdown
          </button>
        )}
      </div>

      {isLoading && <div className="text-gray-500 text-sm">搜索中...</div>}

      {results && (
        <div className="space-y-4">
          {totalCount === 0 && (
            <div className="text-gray-500 text-sm py-4 text-center">
              未找到包含「{query}」的结果
            </div>
          )}
          {Object.entries(results as Record<string, any[]>).map(([entity, items]) => {
            if (!items?.length) return null
            return (
              <div key={entity} className="bg-white rounded-lg shadow">
                <div className="px-4 py-3 border-b bg-gray-50 rounded-t-lg">
                  <span className="font-medium text-gray-800">
                    {ENTITY_LABELS[entity] || entity}
                  </span>
                  <span className="text-gray-500 text-sm ml-2">({items.length})</span>
                </div>
                <div className="divide-y">
                  {items.map((item: any, idx: number) => (
                    <div key={idx} className="px-4 py-3 text-sm">
                      <div className="flex justify-between items-start">
                        <span className="font-medium text-gray-800">
                          {item.title || item.sector_name || item.event}
                        </span>
                        <span className="text-gray-400 text-xs">{item.date}</span>
                      </div>
                      {(item.core_view || item.content) && (
                        <p className="text-gray-600 mt-1 line-clamp-2">
                          {item.core_view || item.content}
                        </p>
                      )}
                      {item.teacher_name && (
                        <span className="text-xs text-blue-600 mt-1 inline-block">
                          {item.teacher_name}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {!query && (
        <div className="text-gray-400 text-sm py-8 text-center">
          输入关键词开始搜索，支持跨实体（老师观点 / 行业信息 / 宏观信息）聚合查询
        </div>
      )}
    </div>
  )
}
