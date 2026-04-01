import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts'
import { api } from '../lib/api'

function fmtAmount(v: number | null | undefined) {
  if (v == null) return '-'
  return v >= 10000 ? `${(v / 10000).toFixed(2)}万亿` : `${v.toFixed(0)}亿`
}

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function pctColor(v: number | null | undefined) {
  if (v == null) return 'text-gray-500'
  return v >= 0 ? 'text-red-600' : 'text-green-600'
}

function Ma5wBadge({ label, value }: { label: string; value: any }) {
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

function IndexCard({ label, close, pct }: { label: string; close: any; pct: any }) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-800">{close ?? '-'}</div>
      <div className={`text-sm font-medium ${pctColor(pct)}`}>{fmtPct(pct)}</div>
    </div>
  )
}

function StatCard({ label, value, suffix }: { label: string; value: any; suffix?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-400">{label}</span>
      <span className="text-sm font-semibold text-gray-800">{value ?? '-'}{suffix && ` ${suffix}`}</span>
    </div>
  )
}

type SectorTab = 'industry' | 'concept' | 'fund_flow'
type MarketViewTab = 'summary' | 'envelope'
type SortOrder = 'gain' | 'loss'

const PHASE_STYLE: Record<string, string> = {
  '超跌': 'bg-gray-100 text-gray-600',
  '启动': 'bg-blue-100 text-blue-700',
  '信不信加速': 'bg-blue-200 text-blue-800',
  '主升': 'bg-red-100 text-red-700',
  '首次分歧': 'bg-orange-100 text-orange-700',
  '震荡': 'bg-orange-100 text-orange-700',
  '轮动': 'bg-purple-100 text-purple-700',
}

function boardColor(board: number) {
  if (board >= 7) return { bar: '#ef4444', text: 'text-red-700 font-bold' }
  if (board >= 5) return { bar: '#f97316', text: 'text-orange-700 font-semibold' }
  if (board >= 3) return { bar: '#3b82f6', text: 'text-blue-700' }
  return { bar: '#9ca3af', text: 'text-gray-600' }
}

export default function MarketOverview() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const [sectorTab, setSectorTab] = useState<SectorTab>('industry')
  const [viewTab, setViewTab] = useState<MarketViewTab>('summary')
  const [sortOrder, setSortOrder] = useState<SortOrder>('gain')
  const [showAllSectors, setShowAllSectors] = useState(false)

  const { data: market, isLoading } = useQuery({
    queryKey: ['market-full', date],
    queryFn: () => api.getMarket(date!),
    enabled: !!date,
  })

  const { data: history } = useQuery({
    queryKey: ['market-history'],
    queryFn: () => api.getMarketHistory(20),
  })

  const { data: postEnvelope, isLoading: envelopeLoading } = useQuery({
    queryKey: ['post-market-envelope', date],
    queryFn: () => api.getPostMarket(date!),
    enabled: !!date && viewTab === 'envelope',
  })

  const { data: mainThemes } = useQuery({
    queryKey: ['main-themes'],
    queryFn: api.getMainThemes,
  })

  const chartData = (history || [])
    .slice()
    .sort((a: any, b: any) => a.date.localeCompare(b.date))
    .map((d: any) => ({
      ...d,
      date_short: d.date.slice(5),
    }))

  if (isLoading) {
    return <div className="text-center py-12 text-gray-400">加载中...</div>
  }

  const hasSummary = market?.available === true
  const m = hasSummary ? market : null
  const boards = m ? parseBoardCounts(m.continuous_board_counts) : []
  const maxBoardCount = boards.length > 0 ? Math.max(...boards.map(b => b.count)) : 1
  const rawSectorData = m ? getSectorData(m, sectorTab) : []
  const sectorData = sortOrder === 'loss' ? rawSectorData.slice().reverse() : rawSectorData
  const visibleSectors = showAllSectors ? sectorData : sectorData.slice(0, 10)
  const activeThemes = (mainThemes || []).filter((t: any) => t.status === 'active')

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-bold text-gray-800">市场数据</h1>
        <input type="date" value={date} onChange={e => navigate(`/market/${e.target.value}`)}
          className="border rounded px-2 py-1 text-sm" />
        {history && history.length > 0 && (
          <select
            value={date}
            onChange={e => navigate(`/market/${e.target.value}`)}
            className="border rounded px-2 py-1 text-sm text-gray-700 max-w-[11rem]"
          >
            {(history as any[]).map((h: any) => (
              <option key={h.date} value={h.date}>历史 {h.date}</option>
            ))}
          </select>
        )}
        <div className="flex rounded-lg border border-gray-200 overflow-hidden text-sm">
          <button
            type="button"
            onClick={() => setViewTab('summary')}
            className={`px-3 py-1.5 ${viewTab === 'summary' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
          >
            摘要看板
          </button>
          <button
            type="button"
            onClick={() => setViewTab('envelope')}
            className={`px-3 py-1.5 border-l border-gray-200 ${viewTab === 'envelope' ? 'bg-blue-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}
          >
            盘后信封
          </button>
        </div>
      </div>

      {viewTab === 'envelope' && (
        <PostMarketEnvelopePanel
          date={date!}
          loading={envelopeLoading}
          data={postEnvelope}
        />
      )}

      {viewTab === 'summary' && !hasSummary && (
        <div className="bg-white rounded-lg shadow p-8 text-center">
          <div className="text-gray-400 text-lg mb-4">暂无 {date} 的摘要看板数据（daily_market）</div>
          <div className="text-sm text-gray-500 space-y-1">
            <p>可切换到「盘后信封」查看原始 YAML/信封，或通过 CLI 采集：</p>
            <code className="block bg-gray-50 rounded px-4 py-2 text-xs text-gray-700 mt-2">
              cd scripts && python3 main.py post --date {date}
            </code>
          </div>
        </div>
      )}

      {viewTab === 'summary' && hasSummary && m && (
      <>
      {/* 指数概览 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <IndexCard label="上证指数" close={m.sh_index_close} pct={m.sh_index_change_pct} />
        <IndexCard label="深证成指" close={m.sz_index_close} pct={m.sz_index_change_pct} />
        <IndexCard label="创业板指" close={extractIndex(m, 'chinext', 'close')} pct={extractIndex(m, 'chinext', 'pct')} />
        <IndexCard label="科创50" close={extractIndex(m, 'star50', 'close')} pct={extractIndex(m, 'star50', 'pct')} />
      </div>

      {/* 成交与资金 + 5周均线 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">成交与资金</h2>
          <div className="grid grid-cols-2 gap-4">
            <StatCard label="两市成交额" value={fmtAmount(m.total_amount)} />
            <StatCard label="北向净额" value={m.northbound_net} suffix="亿" />
            <StatCard label="上涨家数" value={m.advance_count} />
            <StatCard label="下跌家数" value={m.decline_count} />
          </div>
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">5周均线状态</h2>
          <div className="flex flex-wrap gap-2">
            <Ma5wBadge label="上证" value={m.sh_above_ma5w} />
            <Ma5wBadge label="深证" value={m.sz_above_ma5w} />
            <Ma5wBadge label="创业板" value={m.chinext_above_ma5w} />
            <Ma5wBadge label="科创50" value={m.star50_above_ma5w} />
            <Ma5wBadge label="均价" value={m.avg_price_above_ma5w} />
          </div>
        </div>
      </div>

      {/* 涨跌停统计 + 溢价率 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">涨跌停统计</h2>
          <div className="grid grid-cols-3 gap-4 mb-3">
            <StatCard label="涨停" value={m.limit_up_count} />
            <StatCard label="跌停" value={m.limit_down_count} />
            <StatCard label="最高板" value={m.highest_board} suffix="板" />
          </div>
          <div className="grid grid-cols-2 gap-4 mb-3">
            <StatCard label="封板率" value={m.seal_rate != null ? `${m.seal_rate.toFixed(1)}` : null} suffix="%" />
            <StatCard label="炸板率" value={m.broken_rate != null ? `${m.broken_rate.toFixed(1)}` : null} suffix="%" />
          </div>
          {boards.length > 0 && (
            <div>
              <span className="text-xs text-gray-400 mb-2 block">连板梯队</span>
              <div className="space-y-2">
                {boards.map(b => {
                  const { bar, text } = boardColor(b.board)
                  const barWidth = Math.max(4, Math.round((b.count / maxBoardCount) * 100))
                  return (
                    <BoardRow key={b.board} board={b.board} count={b.count} stocks={b.stocks}
                      bar={bar} textCls={text} barWidth={barWidth} />
                  )
                })}
              </div>
            </div>
          )}
        </div>
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">溢价率</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <StatCard label="10cm首板" value={m.premium_10cm != null ? m.premium_10cm.toFixed(2) : null} suffix="%" />
            <StatCard label="20cm首板" value={m.premium_20cm != null ? m.premium_20cm.toFixed(2) : null} suffix="%" />
            <StatCard label="30cm首板" value={m.premium_30cm != null ? m.premium_30cm.toFixed(2) : null} suffix="%" />
            <StatCard label="二板" value={m.premium_second_board != null ? m.premium_second_board.toFixed(2) : null} suffix="%" />
          </div>
        </div>
      </div>

      {/* 主线板块节奏面板 */}
      {activeThemes.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">主线板块节奏</h2>
          <div className="space-y-2">
            {activeThemes.map((t: any, i: number) => {
              let keyStocks: string[] = []
              try {
                const ks = typeof t.key_stocks === 'string' ? JSON.parse(t.key_stocks) : t.key_stocks
                if (Array.isArray(ks)) keyStocks = ks
              } catch { /* ignore */ }
              const phaseStyle = PHASE_STYLE[t.phase] || 'bg-gray-100 text-gray-600'
              return (
                <div key={i} className="flex flex-wrap items-center gap-2 py-1.5 border-b border-gray-50 last:border-0">
                  <span className="font-medium text-gray-800 text-sm min-w-[6rem]">{t.theme_name}</span>
                  {t.phase && (
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${phaseStyle}`}>
                      {t.phase}
                    </span>
                  )}
                  {t.duration_days != null && (
                    <span className="text-xs text-gray-400">{t.duration_days}天</span>
                  )}
                  {keyStocks.length > 0 && (
                    <div className="flex gap-1 flex-wrap">
                      {keyStocks.slice(0, 4).map((s: string) => (
                        <span key={s} className="bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded text-xs">{s}</span>
                      ))}
                    </div>
                  )}
                  {t.note && <span className="text-xs text-gray-400 flex-1">{t.note}</span>}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* 板块排行 */}
      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <h2 className="text-sm font-semibold text-gray-700">板块排行</h2>
          <div className="flex gap-1">
            {([['industry', '行业'], ['concept', '概念'], ['fund_flow', '资金流向']] as const).map(([key, label]) => (
              <button key={key} onClick={() => { setSectorTab(key); setShowAllSectors(false) }}
                className={`px-3 py-1 text-xs rounded-full transition-colors ${
                  sectorTab === key ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}>
                {label}
              </button>
            ))}
          </div>
          <div className="flex gap-1 ml-auto">
            <button onClick={() => setSortOrder('gain')}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                sortOrder === 'gain' ? 'bg-red-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}>
              涨幅↑
            </button>
            <button onClick={() => setSortOrder('loss')}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                sortOrder === 'loss' ? 'bg-green-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}>
              跌幅↓
            </button>
          </div>
        </div>
        {sectorData.length > 0 ? (
          <>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-gray-500">
                  <th className="py-2 pr-4">排名</th>
                  <th className="py-2 pr-4">板块名称</th>
                  <th className="py-2 pr-4 text-right">涨跌幅</th>
                  {sectorTab === 'fund_flow' && <th className="py-2 pr-4 text-right">净流入(亿)</th>}
                </tr>
              </thead>
              <tbody>
                {visibleSectors.map((s: any, i: number) => (
                  <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                    <td className="py-1.5 pr-4 text-gray-400">{i + 1}</td>
                    <td className="py-1.5 pr-4 font-medium text-gray-800">{s.name || s.sector_name || '-'}</td>
                    <td className={`py-1.5 pr-4 text-right ${pctColor(s.pct_change ?? s.change_pct)}`}>
                      {fmtPct(s.pct_change ?? s.change_pct)}
                    </td>
                    {sectorTab === 'fund_flow' && (
                      <td className={`py-1.5 pr-4 text-right ${(s.net_inflow ?? 0) >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                        {s.net_inflow != null ? s.net_inflow.toFixed(2) : '-'}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sectorData.length > 10 && (
            <button
              onClick={() => setShowAllSectors(v => !v)}
              className="mt-2 w-full text-xs text-blue-500 hover:text-blue-700 py-1.5 border-t border-gray-100"
            >
              {showAllSectors ? '收起' : `展开全部 ${sectorData.length} 条`}
            </button>
          )}
          </>
        ) : (
          <div className="text-sm text-gray-400 text-center py-6">暂无板块数据</div>
        )}
      </div>

      {/* 历史趋势图 */}
      {chartData.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">近期趋势</h2>
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="amount" orientation="left" tick={{ fontSize: 11 }}
                tickFormatter={v => v >= 10000 ? `${(v / 10000).toFixed(1)}万亿` : `${v}`} />
              <YAxis yAxisId="count" orientation="right" tick={{ fontSize: 11 }} />
              <Tooltip
                formatter={(value: any, name: string) => {
                  if (name === '成交额') return fmtAmount(value)
                  return value
                }}
              />
              <Legend />
              <Bar yAxisId="count" dataKey="limit_up_count" name="涨停数" fill="#ef4444" opacity={0.6} />
              <Line yAxisId="amount" dataKey="total_amount" name="成交额"
                stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* 涨跌家数折线图 */}
      {chartData.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">涨跌家数</h2>
          <ResponsiveContainer width="100%" height={220}>
            <ComposedChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Line dataKey="advance_count" name="上涨家数" stroke="#ef4444" strokeWidth={2} dot={{ r: 2 }} />
              <Line dataKey="decline_count" name="下跌家数" stroke="#22c55e" strokeWidth={2} dot={{ r: 2 }} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      </>
      )}
    </div>
  )
}

function PostMarketEnvelopePanel({
  date,
  loading,
  data,
}: {
  date: string
  loading: boolean
  data: any
}) {
  if (loading) {
    return <div className="text-center py-12 text-gray-400">加载盘后信封…</div>
  }
  if (!data?.available) {
    return (
      <div className="bg-white rounded-lg shadow p-8 text-center text-gray-500 text-sm">
        暂无 {date} 的盘后信封（需先执行 post 采集并写入 DB 或 daily/post-market.yaml）
      </div>
    )
  }
  const payload = { ...data }
  delete payload.available
  const jsonText = JSON.stringify(payload, null, 2)
  const keys = Object.keys(payload)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="text-sm px-3 py-1.5 rounded-md bg-gray-800 text-white hover:bg-gray-700"
          onClick={() => navigator.clipboard.writeText(jsonText)}
        >
          复制完整 JSON
        </button>
      </div>
      <div className="space-y-2">
        {keys.map(k => (
          <details key={k} className="bg-white rounded-lg shadow border border-gray-100 open:shadow-md">
            <summary className="cursor-pointer px-4 py-2 text-sm font-medium text-gray-800 border-b border-gray-100">
              {k}
            </summary>
            <pre className="text-xs p-4 overflow-auto max-h-80 text-gray-700 bg-gray-50/80">
              {JSON.stringify(payload[k], null, 2)}
            </pre>
          </details>
        ))}
      </div>
    </div>
  )
}

function BoardRow({
  board, count, stocks, bar, textCls, barWidth,
}: {
  board: number; count: number; stocks: string[]; bar: string; textCls: string; barWidth: number
}) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div>
      <div className="flex items-center gap-2">
        <span className={`w-10 text-xs font-medium shrink-0 ${textCls}`}>{board}板</span>
        <div className="flex-1 bg-gray-100 rounded-full h-3 overflow-hidden">
          <div className="h-full rounded-full" style={{ width: `${barWidth}%`, backgroundColor: bar, opacity: 0.8 }} />
        </div>
        <span className={`text-xs font-semibold shrink-0 w-10 text-right ${textCls}`}>{count}只</span>
        {stocks.length > 0 && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="text-xs text-gray-400 hover:text-gray-600 shrink-0"
          >
            {expanded ? '▲' : '▼'}
          </button>
        )}
      </div>
      {expanded && stocks.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5 pl-12">
          {stocks.map((s: string) => (
            <span key={s} className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{s}</span>
          ))}
        </div>
      )}
    </div>
  )
}

function parseBoardCounts(raw: any): { board: number; count: number; stocks: string[] }[] {
  if (!raw) return []
  try {
    const parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.entries(parsed)
        .map(([k, v]) => {
          const board = parseInt(k)
          if (isNaN(board)) return null
          if (Array.isArray(v)) return { board, count: v.length, stocks: v as string[] }
          return { board, count: Number(v), stocks: [] }
        })
        .filter(Boolean)
        .sort((a: any, b: any) => b.board - a.board) as { board: number; count: number; stocks: string[] }[]
    }
    if (Array.isArray(parsed)) {
      return parsed.map((b: any) => ({ ...b, stocks: b.stocks ?? [] }))
    }
  } catch { /* ignore */ }
  return []
}

function getSectorData(m: any, tab: SectorTab): any[] {
  const key = tab === 'industry' ? 'sector_industry'
    : tab === 'concept' ? 'sector_concept'
    : 'sector_fund_flow'
  const section = m[key]
  if (!section) return []
  if (Array.isArray(section)) return section
  if (section.data && Array.isArray(section.data)) return section.data
  return []
}

function extractIndex(m: any, index: string, field: 'close' | 'pct'): number | null {
  const entry = m.indices?.[index]
  if (!entry) return null
  return field === 'close' ? (entry.close ?? null) : (entry.change_pct ?? null)
}
