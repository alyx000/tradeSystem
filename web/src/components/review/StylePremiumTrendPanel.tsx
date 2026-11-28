import { useEffect, useState } from 'react'
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ReferenceLine,
} from 'recharts'
import { api } from '../../lib/api'
import type { StyleFactorSeriesItem } from '../../lib/types'

// 类目型多线：用区分度高、避开红绿(规避 A 股涨跌语义混淆)的调色板，正负由 0 基准线表达。
const METRICS = [
  { key: 'premium_10cm', label: '10cm首板', color: '#3b82f6' },
  { key: 'premium_20cm', label: '20cm首板', color: '#f59e0b' },
  { key: 'premium_30cm', label: '30cm首板', color: '#8b5cf6' },
  { key: 'premium_second_board', label: '二板', color: '#ec4899' },
  { key: 'premium_capacity', label: '容量票', color: '#06b6d4' },
] as const

const METRIC_KEYS = [...METRICS.map((m) => m.key), 'premium_first_open'].join(',')
const FIRST_OPEN_LABELS = {
  legacy_unsplit: '旧口径（含继续一字）',
  opened_only: '真实首开',
  unverified: '口径待核',
} as const

function sampleCount(row: StyleFactorSeriesItem) {
  const count = row.premium_first_open_count
  return typeof count === 'number' && Number.isInteger(count) && count >= 0 ? count : null
}

function formatPct(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '待核'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

// 日期本身缺行时，普通 null 检测不起作用。在相邻行之间遇到明确断点时切换线段。
function firstOpenChart(series: StyleFactorSeriesItem[]) {
  type Basis = 'legacy_unsplit' | 'opened_only'
  const lines = new Map<string, { basis: Basis; showLegend: boolean }>()
  const seen = new Set<Basis>()
  let segment = 0
  let previousBasis: Basis | undefined
  const rows = series.map((row) => {
    const basis = row.premium_first_open_basis
    const count = sampleCount(row)
    if ((basis !== 'legacy_unsplit' && basis !== 'opened_only') || row.premium_first_open_partial ||
        !count || !Number.isFinite(row.premium_first_open)) {
      previousBasis = undefined
      return row
    }
    if (row.premium_first_open_break_before && previousBasis === basis) segment = 1 - segment
    previousBasis = basis
    const key = `first_open_${basis}_${segment}`
    if (!lines.has(key)) {
      lines.set(key, { basis, showLegend: !seen.has(basis) })
      seen.add(basis)
    }
    return { ...row, [key]: row.premium_first_open }
  })
  return { rows, lines }
}

function shiftDays(isoDate: string, delta: number): string {
  const d = new Date(`${isoDate}T00:00:00`)
  d.setDate(d.getDate() + delta)
  return d.toISOString().slice(0, 10)
}

/**
 * 各风格赚钱效应 · 次日开盘溢价中位趋势（复盘「风格」页透出）。
 * 数据源 /api/style-factors/series（daily_market 各 premium_* 列），按实现日升序、缺口断线。
 * 自取数：以 date 为窗口右端，向前约 150 自然日（≈ 3 个月交易日）。
 * 日期切换重新加载该窗口，失败时给出当前窗口的读取状态。
 */
export default function StylePremiumTrendPanel({ date }: { date?: string }) {
  return date ? <PremiumTrendForDate key={date} date={date} /> : null
}

function PremiumTrendForDate({ date }: { date: string }) {
  const [series, setSeries] = useState<StyleFactorSeriesItem[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const from = shiftDays(date, -150)
    const to = date
    let alive = true
    // 包一层 Promise.resolve 以兜住 api.getStyleFactors / request 的同步抛出（如 jsdom 无 fetch），
    // 保证在 prefill 渲染场景下永不抛出未处理异常。
    Promise.resolve()
      .then(() => api.getStyleFactors(METRIC_KEYS, from, to))
      .then((rows) => { if (alive) setSeries(Array.isArray(rows) ? rows : []) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [date])

  if (failed) return <div className="text-xs text-gray-400 py-4">当期溢价趋势读取失败</div>
  if (series === null) {
    return <div className="text-xs text-gray-400 py-4">加载各风格赚钱效应趋势…</div>
  }

  if (!series.length) {
    return <div className="text-xs text-gray-400 py-4">暂无各风格赚钱效应趋势数据</div>
  }

  const firstOpen = firstOpenChart(series)
  const hasData = firstOpen.lines.size > 0 || series.some((row) => METRICS.some((m) => Number.isFinite(row[m.key])))

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-medium text-gray-700 mb-1">各风格赚钱效应 · 次日开盘溢价中位趋势</div>
      <div className="text-xs text-gray-400 mb-3">
        开盘溢价为（T 日开盘价 − T−1 日收盘价）/ T−1 日收盘价，按实现日展示各组中位数。
      </div>
      {hasData ? <ResponsiveContainer width="100%" height={280}>
        <LineChart data={firstOpen.rows} margin={{ top: 8, right: 16, bottom: 4, left: -12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            tickFormatter={(d: string) => (d ? d.slice(5) : '')}
            minTickGap={24}
          />
          <YAxis
            tick={{ fontSize: 10, fill: '#94a3b8' }}
            tickFormatter={(v: number) => `${v}%`}
            width={46}
          />
          <ReferenceLine y={0} stroke="#cbd5e1" strokeDasharray="4 2" />
          <Tooltip
            formatter={(value, name, item) => {
              const text = formatPct(value)
              if (firstOpen.lines.has(String(item.dataKey))) {
                return [`${text}（${sampleCount(item.payload)} 只）`, name]
              }
              return [text, name]
            }}
            labelFormatter={(d) => `实现日 ${d}`}
            contentStyle={{ fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {METRICS.map((m) => (
            <Line
              key={m.key}
              type="monotone"
              dataKey={m.key}
              name={m.label}
              stroke={m.color}
              strokeWidth={m.key === 'premium_10cm' ? 2.4 : 1.5}
              dot={false}
              connectNulls={false}
              activeDot={{ r: 4 }}
            />
          ))}
          {[...firstOpen.lines].map(([key, { basis, showLegend }]) => (
            <Line key={key} type="linear" dataKey={key} name={`一字连板 · ${FIRST_OPEN_LABELS[basis]}`}
              stroke="#6366f1" strokeDasharray={basis === 'legacy_unsplit' ? '4 3' : undefined}
              dot={{ r: 2 }} connectNulls={false} isAnimationActive={false}
              legendType={showLegend ? 'line' : 'none'} />
          ))}
        </LineChart>
      </ResponsiveContainer> : <p className="text-xs text-gray-400 py-4">暂无可连线的溢价数据</p>}
      <details className="text-xs text-gray-600 mt-3">
        <summary className="cursor-pointer">一字首开溢价 · 逐日口径与样本数</summary>
        <p className="my-2">旧口径按前一日一字连板取样，包含实现日继续一字的股票。真实首开仅统计实现日打开的样本。图中只连接口径与样本均已核验的点，部分覆盖日的中位数仅代表已核样本。</p>
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left">
            <thead><tr><th>实现日</th><th>样本口径</th><th>样本数</th><th>归档中位溢价</th></tr></thead>
            <tbody>{series.map((row, index) => {
              const count = sampleCount(row)
              const basis = row.premium_first_open_basis ?? 'unverified'
              const partial = row.premium_first_open_partial === true
              let countLabel = count === null ? '待核' : `${count} 只`
              let medianLabel = formatPct(row.premium_first_open)
              if (partial) {
                countLabel = count === null ? '总样本待核' : `已核 ${count} 只 / 总样本待核`
                if (count === 0) medianLabel = '待核'
              } else if (count === 0 && row.premium_first_open == null) {
                medianLabel = '无样本'
              }
              return (
                <tr key={row.date}>
                  <td>{row.date}</td><td>{FIRST_OPEN_LABELS[basis] ?? FIRST_OPEN_LABELS.unverified}
                    {index > 0 && row.premium_first_open_break_before && <small className="ml-1 text-gray-500">另起一段</small>}
                  </td>
                  <td>{countLabel}</td>
                  <td>{medianLabel}</td>
                </tr>
              )
            })}</tbody>
          </table>
        </div>
      </details>
      <div className="text-[11px] text-gray-400 mt-2 leading-relaxed">
        注：容量票为「全市场成交额前10」口径、10cm首板已剔除 ST，均自口径修订日起生效；更早历史沿用旧口径（容量票=涨停池、首板含 ST），存在口径过渡带。
      </div>
    </div>
  )
}
