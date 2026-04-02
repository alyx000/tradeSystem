import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

const INFO_TYPE_OPTIONS = [
  { value: '', label: '全部类型' },
  { value: 'news', label: '新闻' },
  { value: 'analysis', label: '分析' },
  { value: 'policy', label: '政策' },
  { value: 'data', label: '数据' },
  { value: 'other', label: '其他' },
]

const TYPE_COLOR: Record<string, string> = {
  news: 'bg-blue-50 text-blue-600',
  analysis: 'bg-purple-50 text-purple-700',
  policy: 'bg-green-50 text-green-700',
  data: 'bg-orange-50 text-orange-700',
}

function TypeBadge({ type }: { type: string }) {
  const cls = TYPE_COLOR[type] ?? 'bg-gray-50 text-gray-500'
  const label = INFO_TYPE_OPTIONS.find(o => o.value === type)?.label || type
  return <span className={`inline-block text-xs px-1.5 py-0.5 rounded ${cls}`}>{label}</span>
}

const today = new Date().toISOString().slice(0, 10)
const monthAgo = new Date(Date.now() - 30 * 86400_000).toISOString().slice(0, 10)

export default function IndustryInfo() {
  const queryClient = useQueryClient()

  // 筛选条件
  const [keyword, setKeyword] = useState('')
  const [dateFrom, setDateFrom] = useState(monthAgo)
  const [dateTo, setDateTo] = useState(today)
  const [infoType, setInfoType] = useState('')
  const [appliedKeyword, setAppliedKeyword] = useState('')
  const [appliedFrom, setAppliedFrom] = useState(monthAgo)
  const [appliedTo, setAppliedTo] = useState(today)

  // 新增表单
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({
    date: today,
    sector_name: '',
    info_type: 'news',
    content: '',
    source: '',
    confidence: '',
    timeliness: '',
  })

  const params: Record<string, string> = {}
  if (appliedKeyword) params.keyword = appliedKeyword
  if (appliedFrom) params.from = appliedFrom
  if (appliedTo) params.to = appliedTo
  params.limit = '200'

  const { data: items, isLoading } = useQuery({
    queryKey: ['industry-info', params],
    queryFn: () => api.getIndustryInfo(params),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteIndustryInfo(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['industry-info'] }),
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.createIndustryInfo(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['industry-info'] })
      setShowAdd(false)
      setForm({ date: today, sector_name: '', info_type: 'news', content: '', source: '', confidence: '', timeliness: '' })
    },
  })

  const handleSearch = () => {
    setAppliedKeyword(keyword)
    setAppliedFrom(dateFrom)
    setAppliedTo(dateTo)
  }

  const filteredItems = (items || []).filter((item: any) => {
    if (infoType && item.info_type !== infoType) return false
    return true
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <h1 className="text-xl font-bold text-gray-800">行业信息</h1>
        <button
          onClick={() => setShowAdd(v => !v)}
          className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700"
        >
          {showAdd ? '取消' : '+ 新增'}
        </button>
      </div>

      {/* 新增表单 */}
      {showAdd && (
        <div className="bg-white rounded-lg shadow p-4 space-y-3">
          <h2 className="text-sm font-semibold text-gray-700">新增行业信息</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">日期</label>
              <input type="date" value={form.date}
                onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
                className="w-full border rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">板块名称</label>
              <input type="text" value={form.sector_name}
                onChange={e => setForm(f => ({ ...f, sector_name: e.target.value }))}
                placeholder="如：油服工程"
                className="w-full border rounded px-2 py-1.5 text-sm" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">类型</label>
              <select value={form.info_type}
                onChange={e => setForm(f => ({ ...f, info_type: e.target.value }))}
                className="w-full border rounded px-2 py-1.5 text-sm">
                {INFO_TYPE_OPTIONS.filter(o => o.value).map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">来源</label>
              <input type="text" value={form.source}
                onChange={e => setForm(f => ({ ...f, source: e.target.value }))}
                placeholder="来源（可选）"
                className="w-full border rounded px-2 py-1.5 text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">置信度</label>
              <select value={form.confidence}
                onChange={e => setForm(f => ({ ...f, confidence: e.target.value }))}
                className="w-full border rounded px-2 py-1.5 text-sm">
                <option value="">不填</option>
                <option value="高">高 ★★★</option>
                <option value="中">中 ★★☆</option>
                <option value="低">低 ★☆☆</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">时效性</label>
              <select value={form.timeliness}
                onChange={e => setForm(f => ({ ...f, timeliness: e.target.value }))}
                className="w-full border rounded px-2 py-1.5 text-sm">
                <option value="">不填</option>
                <option value="实时">实时</option>
                <option value="近期">近期</option>
                <option value="滞后">滞后</option>
                <option value="历史">历史</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">内容 *</label>
            <textarea value={form.content}
              onChange={e => setForm(f => ({ ...f, content: e.target.value }))}
              placeholder="行业信息内容…"
              rows={3}
              className="w-full border rounded px-2 py-1.5 text-sm resize-none" />
          </div>
          <div className="flex justify-end gap-2">
            <button onClick={() => setShowAdd(false)}
              className="px-4 py-1.5 text-sm border rounded text-gray-600 hover:bg-gray-50">取消</button>
            <button
              disabled={!form.sector_name.trim() || !form.content.trim() || createMutation.isPending}
              onClick={() => {
                const payload: Record<string, string> = { ...form }
                Object.keys(payload).forEach(k => { if (!payload[k]) delete payload[k] })
                createMutation.mutate(payload)
              }}
              className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
              {createMutation.isPending ? '保存中…' : '保存'}
            </button>
          </div>
          {createMutation.isError && (
            <p className="text-xs text-red-500">{String((createMutation.error as Error)?.message)}</p>
          )}
        </div>
      )}

      {/* 筛选条件 */}
      <div className="bg-white rounded-lg shadow px-4 py-3">
        <div className="flex flex-wrap gap-2 items-end">
          <div className="flex-1 min-w-40">
            <input type="text" value={keyword}
              onChange={e => setKeyword(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              placeholder="关键词（板块/内容）…"
              className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none" />
          </div>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="border rounded px-2 py-2 text-sm" />
          <span className="text-gray-400">~</span>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="border rounded px-2 py-2 text-sm" />
          <select value={infoType} onChange={e => setInfoType(e.target.value)}
            className="border rounded px-2 py-2 text-sm">
            {INFO_TYPE_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
          <button onClick={handleSearch}
            className="bg-blue-600 text-white px-4 py-2 rounded text-sm hover:bg-blue-700">
            查询
          </button>
        </div>
        <div className="mt-1.5 text-xs text-gray-400">
          {isLoading ? '加载中…' : `共 ${filteredItems.length} 条`}
          {appliedKeyword && <span>，关键词：「{appliedKeyword}」</span>}
        </div>
      </div>

      {/* 列表 */}
      {filteredItems.length > 0 ? (
        <div className="space-y-2">
          {filteredItems.map((item: any) => (
            <InfoCard
              key={item.id}
              item={item}
              onDelete={() => {
                if (window.confirm(`确认删除「${item.sector_name}」这条行业信息？`)) {
                  deleteMutation.mutate(item.id)
                }
              }}
              deleting={deleteMutation.isPending && deleteMutation.variables === item.id}
            />
          ))}
        </div>
      ) : (
        !isLoading && (
          <div className="text-gray-400 text-sm py-8 text-center">
            {appliedKeyword ? `未找到包含「${appliedKeyword}」的行业信息` : '暂无行业信息，点击右上角「+ 新增」添加'}
          </div>
        )
      )}
    </div>
  )
}

function InfoCard({ item, onDelete, deleting }: {
  item: any
  onDelete: () => void
  deleting?: boolean
}) {
  return (
    <div className="bg-white rounded-lg shadow px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="font-medium text-gray-800 text-sm">{item.sector_name}</span>
            {item.info_type && <TypeBadge type={item.info_type} />}
            {item.timeliness && (
              <span className="text-xs text-gray-400 border border-gray-200 rounded px-1">{item.timeliness}</span>
            )}
            {item.confidence && (
              <span className="text-xs text-gray-500">置信：{item.confidence}</span>
            )}
            <span className="text-xs text-gray-400 ml-auto">{item.date}</span>
          </div>
          <p className="text-sm text-gray-700 leading-relaxed whitespace-pre-wrap">{item.content}</p>
          {item.source && (
            <p className="text-xs text-gray-400 mt-1">来源：{item.source}</p>
          )}
        </div>
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          className="p-1 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-40 shrink-0"
          title="删除"
        >
          {deleting ? (
            <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
            </svg>
          ) : (
            <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd"/>
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}
