import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

const TIER_LABELS: Record<string, string> = {
  tier1_core: '核心标的',
  tier2_watch: '观察标的',
  tier3_sector: '板块标的',
}

const TIER_OPTIONS = [
  { value: 'tier1_core', label: '核心标的（tier1）' },
  { value: 'tier2_watch', label: '观察标的（tier2）' },
  { value: 'tier3_sector', label: '板块标的（tier3）' },
]

const ROLE_OPTIONS = ['龙头', '前排', '跟风', '中军', '弹性套利']

const today = new Date().toISOString().slice(0, 10)

type FormData = {
  stock_code: string
  stock_name: string
  tier: string
  sector: string
  add_date: string
  add_reason: string
  trigger_condition: string
  entry_condition: string
  role: string
  note: string
}

const EMPTY_FORM: FormData = {
  stock_code: '',
  stock_name: '',
  tier: 'tier2_watch',
  sector: '',
  add_date: today,
  add_reason: '',
  trigger_condition: '',
  entry_condition: '',
  role: '',
  note: '',
}

function AddModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const [form, setForm] = useState<FormData>(EMPTY_FORM)
  const [showMore, setShowMore] = useState(false)

  const addMutation = useMutation({
    mutationFn: (data: Record<string, string>) => api.createWatchlistItem(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['watchlist'] })
      onClose()
    },
  })

  const f = (k: keyof FormData) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm(prev => ({ ...prev, [k]: e.target.value }))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.stock_code.trim() || !form.stock_name.trim()) return
    const payload: Record<string, string> = {}
    for (const [k, v] of Object.entries(form)) {
      if (v.trim()) payload[k] = v.trim()
    }
    addMutation.mutate(payload)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <h2 className="font-semibold text-gray-800">添加到关注池</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg leading-none">✕</button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          {/* 核心字段 */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">股票代码 <span className="text-red-400">*</span></label>
              <input
                value={form.stock_code} onChange={f('stock_code')}
                placeholder="如 300750"
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
                required
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">股票名称 <span className="text-red-400">*</span></label>
              <input
                value={form.stock_name} onChange={f('stock_name')}
                placeholder="如 宁德时代"
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
                required
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">分层</label>
              <select value={form.tier} onChange={f('tier')}
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
                {TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">所属板块</label>
              <input
                value={form.sector} onChange={f('sector')}
                placeholder="如 AI算力"
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">添加日期</label>
              <input type="date" value={form.add_date} onChange={f('add_date')}
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">板块地位</label>
              <select value={form.role} onChange={f('role')}
                className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400">
                <option value="">不填</option>
                {ROLE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="block text-xs text-gray-500 mb-1">添加理由</label>
            <input value={form.add_reason} onChange={f('add_reason')}
              placeholder="为什么关注这只票"
              className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400" />
          </div>

          {/* 可展开的更多字段 */}
          <button type="button" onClick={() => setShowMore(v => !v)}
            className="text-xs text-blue-500 hover:text-blue-700">
            {showMore ? '▲ 收起更多选项' : '▼ 更多选项（触发条件 / 入场条件 / 备注）'}
          </button>

          {showMore && (
            <div className="space-y-3 border-t pt-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">触发条件</label>
                <input value={form.trigger_condition} onChange={f('trigger_condition')}
                  placeholder="价格突破 XX / 缩量回踩..."
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">入场条件</label>
                <input value={form.entry_condition} onChange={f('entry_condition')}
                  placeholder="首阴不破均线 / 缩量企稳..."
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">备注</label>
                <textarea value={form.note} onChange={f('note')} rows={2}
                  placeholder="其他注意事项"
                  className="w-full border rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400 resize-none" />
              </div>
            </div>
          )}

          {addMutation.isError && (
            <div className="text-xs text-red-500">添加失败，请重试</div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="px-4 py-1.5 text-sm text-gray-600 border rounded hover:bg-gray-50">
              取消
            </button>
            <button type="submit" disabled={addMutation.isPending}
              className="px-4 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
              {addMutation.isPending ? '添加中…' : '添加'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Watchlist() {
  const queryClient = useQueryClient()
  const [showAdd, setShowAdd] = useState(false)

  const { data: watchlist, isLoading } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => api.getWatchlist(),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteWatchlistItem(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['watchlist'] }),
  })

  const grouped = (watchlist || []).reduce((acc: Record<string, any[]>, item: any) => {
    const tier = item.tier || 'other'
    if (!acc[tier]) acc[tier] = []
    acc[tier].push(item)
    return acc
  }, {})

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">关注池</h1>
        <button
          type="button"
          onClick={() => setShowAdd(true)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
        >
          <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M10 3a1 1 0 011 1v5h5a1 1 0 110 2h-5v5a1 1 0 11-2 0v-5H4a1 1 0 110-2h5V4a1 1 0 011-1z" clipRule="evenodd"/>
          </svg>
          添加标的
        </button>
      </div>

      {showAdd && <AddModal onClose={() => setShowAdd(false)} />}

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
                <div key={item.id} className="px-4 py-3 text-sm flex justify-between items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-medium text-gray-800">{item.stock_name}</span>
                      <span className="text-gray-400 font-mono text-xs">{item.stock_code}</span>
                      {item.role && (
                        <span className="text-xs bg-amber-50 text-amber-700 px-1.5 py-0.5 rounded">{item.role}</span>
                      )}
                      {item.sector && (
                        <span className="text-xs bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded">{item.sector}</span>
                      )}
                    </div>
                    {item.add_reason && (
                      <div className="text-xs text-gray-400 mt-0.5">{item.add_reason}</div>
                    )}
                    {item.trigger_condition && (
                      <div className="text-xs text-amber-600 mt-0.5">触发：{item.trigger_condition}</div>
                    )}
                    {item.entry_condition && (
                      <div className="text-xs text-green-600 mt-0.5">入场：{item.entry_condition}</div>
                    )}
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {item.add_date && (
                      <span className="text-xs text-gray-400">{item.add_date}</span>
                    )}
                    <button
                      type="button"
                      onClick={() => {
                        if (window.confirm(`确认将「${item.stock_name}」从关注池移除？`)) {
                          deleteMutation.mutate(item.id)
                        }
                      }}
                      disabled={deleteMutation.isPending && deleteMutation.variables === item.id}
                      className="p-1 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-40"
                      title="移除"
                    >
                      {deleteMutation.isPending && deleteMutation.variables === item.id ? (
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
              ))}
            </div>
          </div>
        ))
      )}
      {!isLoading && (!watchlist || watchlist.length === 0) && (
        <div className="text-gray-400 text-sm py-4 text-center">暂无关注标的，点击右上角「添加标的」开始</div>
      )}
    </div>
  )
}
