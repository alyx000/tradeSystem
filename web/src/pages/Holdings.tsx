import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api } from '../lib/api'

export default function Holdings() {
  const queryClient = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ stock_code: '', stock_name: '', entry_price: '', shares: '', sector: '' })

  const { data: holdings, isLoading } = useQuery({
    queryKey: ['holdings'],
    queryFn: api.getHoldings,
  })

  const createMut = useMutation({
    mutationFn: (data: any) => api.createHolding(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['holdings'] })
      setShowForm(false)
      setForm({ stock_code: '', stock_name: '', entry_price: '', shares: '', sector: '' })
    },
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteHolding(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['holdings'] }),
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">持仓池</h1>
        <button onClick={() => setShowForm(!showForm)}
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm hover:bg-blue-700">
          {showForm ? '取消' : '添加持仓'}
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 grid grid-cols-2 md:grid-cols-5 gap-3">
          <input placeholder="代码" value={form.stock_code}
            onChange={e => setForm(p => ({ ...p, stock_code: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="名称" value={form.stock_name}
            onChange={e => setForm(p => ({ ...p, stock_name: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="成本价" type="number" value={form.entry_price}
            onChange={e => setForm(p => ({ ...p, entry_price: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="数量" type="number" value={form.shares}
            onChange={e => setForm(p => ({ ...p, shares: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <button onClick={() => createMut.mutate({
            stock_code: form.stock_code, stock_name: form.stock_name,
            entry_price: parseFloat(form.entry_price) || undefined,
            shares: parseInt(form.shares) || undefined,
            sector: form.sector || undefined,
          })}
            className="bg-green-600 text-white rounded px-3 py-1.5 text-sm hover:bg-green-700">
            确认
          </button>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">代码</th>
              <th className="px-4 py-3 text-left">名称</th>
              <th className="px-4 py-3 text-right">成本价</th>
              <th className="px-4 py-3 text-right">数量</th>
              <th className="px-4 py-3 text-left">状态</th>
              <th className="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {isLoading ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">加载中...</td></tr>
            ) : holdings?.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-8 text-center text-gray-400">暂无持仓</td></tr>
            ) : (
              holdings?.map((h: any) => (
                <tr key={h.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono">{h.stock_code}</td>
                  <td className="px-4 py-3">{h.stock_name}</td>
                  <td className="px-4 py-3 text-right">{h.entry_price ?? '-'}</td>
                  <td className="px-4 py-3 text-right">{h.shares ?? '-'}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      h.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                    }`}>{h.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button onClick={() => deleteMut.mutate(h.id)}
                      className="text-red-500 hover:text-red-700 text-xs">删除</button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
