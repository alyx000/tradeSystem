import { useQuery } from '@tanstack/react-query'
import { Fragment, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import type { RegulatoryMonitorRecord } from '../lib/types'

const today = localDateString()

type TypeFilter = 'all' | '1' | '2' | '3'

const TYPE_OPTIONS: Array<{ value: TypeFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: '1', label: '已监管' },
  { value: '2', label: '潜在' },
  { value: '3', label: '重点监控' },
]

function typeLabel(t: number): string {
  if (t === 1) return '已监管'
  if (t === 2) return '潜在'
  if (t === 3) return '重点监控'
  return String(t)
}

function formatDetailJson(raw: RegulatoryMonitorRecord['detail_json']): string {
  if (raw == null) return ''
  if (typeof raw === 'string') {
    try {
      return JSON.stringify(JSON.parse(raw), null, 2)
    } catch {
      return raw
    }
  }
  return JSON.stringify(raw, null, 2)
}

function getSuspendApiFields(
  detail: RegulatoryMonitorRecord['detail_json'],
): Record<string, string> | null {
  if (detail == null || typeof detail !== 'object' || Array.isArray(detail)) return null
  const v = (detail as Record<string, unknown>).suspend_api
  if (v == null || typeof v !== 'object' || Array.isArray(v)) return null
  const out: Record<string, string> = {}
  for (const [k, val] of Object.entries(v)) {
    if (val != null && String(val).trim() !== '') out[k] = String(val)
  }
  return Object.keys(out).length ? out : null
}

const SUSPEND_FIELD_LABEL: Record<string, string> = {
  change_reason: '停牌原因',
  suspend_reason: '停牌原因(备)',
  reason: '说明',
  suspend_time: '停复牌时间',
  resump_date: '复牌日期',
  resume_date: '复牌日期',
  suspend_date: '停牌日期',
  suspend_type: '停复牌类型',
  change_reason_type: '原因类型代码',
}

export default function RegulatoryMonitor() {
  const [searchParams, setSearchParams] = useSearchParams()
  const effectiveDate = searchParams.get('date') || today
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [expandedKey, setExpandedKey] = useState<string | null>(null)

  const { data, isLoading, error } = useQuery({
    queryKey: ['regulatory-monitor', effectiveDate, typeFilter],
    queryFn: () => api.getRegulatoryMonitor(effectiveDate, typeFilter),
  })

  const rows = useMemo(() => data ?? [], [data])

  const applyDateToUrl = (d: string) => {
    const next = new URLSearchParams(searchParams)
    if (d === today) next.delete('date')
    else next.set('date', d)
    setSearchParams(next, { replace: true })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <label className="block text-sm text-gray-600 mb-1">日期</label>
          <input
            type="date"
            value={effectiveDate}
            onChange={e => applyDateToUrl(e.target.value)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1">类型</label>
          <div className="flex gap-2">
            {TYPE_OPTIONS.map(opt => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setTypeFilter(opt.value)}
                className={`px-3 py-2 rounded-lg text-sm border ${
                  typeFilter === opt.value
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-700 border-gray-300 hover:border-gray-400'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <p className="text-sm text-gray-500">
        Type1/2 来自 <code className="bg-gray-100 px-1 rounded">stock_regulatory_monitor</code>；
        重点监控来自 <code className="bg-gray-100 px-1 rounded">stock_regulatory_stk_alert</code>
        （Tushare <code className="bg-gray-100 px-1 rounded">stk_alert</code>，约 6000 积分）。
        请用 CLI <code className="bg-gray-100 px-1 rounded">regulatory</code> 按交易日采集；列表日期为快照日（与 App
        当日列表一致需在该日跑采集）。
      </p>

      {isLoading && <p className="text-gray-500">加载中…</p>}
      {error && (
        <p className="text-red-600 text-sm">{(error as Error).message}</p>
      )}

      {!isLoading && !error && rows.length === 0 && (
        <p className="text-gray-500">该日暂无记录。</p>
      )}

      {!isLoading && rows.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-left text-gray-600">
              <tr>
                <th className="px-4 py-3 font-medium w-10" />
                <th className="px-4 py-3 font-medium">代码</th>
                <th className="px-4 py-3 font-medium">名称</th>
                <th className="px-4 py-3 font-medium">类型</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">监控开始</th>
                <th className="px-4 py-3 font-medium whitespace-nowrap">监控结束</th>
                <th className="px-4 py-3 font-medium">L级</th>
                <th className="px-4 py-3 font-medium">评分</th>
                <th className="px-4 py-3 font-medium">监管原因</th>
                <th className="px-4 py-3 font-medium">来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: RegulatoryMonitorRecord) => {
                const rk = `${row.regulatory_type}-${row.id}`
                return (
                <Fragment key={rk}>
                  <tr className="border-t border-gray-100 hover:bg-gray-50/80">
                    <td className="px-4 py-2">
                      <button
                        type="button"
                        className="text-blue-600 text-xs"
                        onClick={() => setExpandedKey(expandedKey === rk ? null : rk)}
                      >
                        {expandedKey === rk ? '▼' : '▶'}
                      </button>
                    </td>
                    <td className="px-4 py-2 font-mono">{row.ts_code}</td>
                    <td className="px-4 py-2">{row.name}</td>
                    <td className="px-4 py-2">{typeLabel(row.regulatory_type)}</td>
                    <td className="px-4 py-2 text-gray-700 whitespace-nowrap">
                      {row.monitor_start_date ?? '—'}
                    </td>
                    <td className="px-4 py-2 text-gray-700 whitespace-nowrap">
                      {row.monitor_end_date ?? '—'}
                    </td>
                    <td className="px-4 py-2">{row.regulatory_type === 3 ? '—' : row.risk_level}</td>
                    <td className="px-4 py-2">
                      {row.risk_score != null ? row.risk_score.toFixed(2) : '—'}
                    </td>
                    <td
                      className="px-4 py-2 min-w-[10rem] max-w-2xl align-top text-gray-800 whitespace-pre-wrap break-words leading-relaxed"
                      title={row.reason}
                    >
                      {row.reason}
                    </td>
                    <td className="px-4 py-2 text-gray-500 truncate max-w-[140px]" title={row.source}>
                      {row.source}
                    </td>
                  </tr>
                  {expandedKey === rk && (
                    <tr className="bg-gray-50 border-t border-gray-100">
                      <td colSpan={10} className="px-4 py-3 space-y-4">
                        <div>
                          <div className="text-xs font-medium text-gray-500 mb-1">
                            监管原因（全文）
                          </div>
                          <p className="text-sm text-gray-800 whitespace-pre-wrap break-words">
                            {row.reason || '—'}
                          </p>
                        </div>
                        {(() => {
                          const snap = getSuspendApiFields(row.detail_json)
                          if (!snap) return null
                          return (
                            <div>
                              <div className="text-xs font-medium text-gray-500 mb-2">
                                停牌接口字段（suspend）
                              </div>
                              <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-sm">
                                {Object.entries(snap).map(([k, v]) => (
                                  <div key={k} className="flex gap-2">
                                    <dt className="shrink-0 text-gray-500">
                                      {SUSPEND_FIELD_LABEL[k] ?? k}
                                    </dt>
                                    <dd className="text-gray-900 break-words">{v}</dd>
                                  </div>
                                ))}
                              </dl>
                            </div>
                          )
                        })()}
                        <div>
                          <div className="text-xs font-medium text-gray-500 mb-1">
                            detail_json
                          </div>
                          <pre className="text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto max-h-64 overflow-y-auto border border-gray-200 rounded-lg p-2 bg-white">
                            {formatDetailJson(row.detail_json) || '（无）'}
                          </pre>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
