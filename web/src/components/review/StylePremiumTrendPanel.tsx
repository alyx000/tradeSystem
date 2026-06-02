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
  { key: 'premium_first_open', label: '一字首开', color: '#6366f1' },
] as const

const METRIC_KEYS = METRICS.map((m) => m.key).join(',')

function shiftDays(isoDate: string, delta: number): string {
  const d = new Date(`${isoDate}T00:00:00`)
  d.setDate(d.getDate() + delta)
  return d.toISOString().slice(0, 10)
}

/**
 * 各风格赚钱效应 · 次日开盘溢价中位趋势（复盘「风格」页透出）。
 * 数据源 /api/style-factors/series（daily_market 各 premium_* 列），按实现日升序、缺口断线。
 * 自取数：以 date 为窗口右端，向前约 150 自然日（≈ 3 个月交易日）。
 * fetch 失败时静默返回 null，避免在 prefill 渲染场景抛错。
 */
export default function StylePremiumTrendPanel({ date }: { date?: string }) {
  const [series, setSeries] = useState<StyleFactorSeriesItem[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    // 无 date 时不猜「今天」(历史复盘场景会取到错误/未来窗口)，不取数；渲染侧直接返回 null。
    if (!date) return
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

  if (!date || failed) return null
  if (series === null) {
    return <div className="text-xs text-gray-400 py-4">加载各风格赚钱效应趋势…</div>
  }

  const hasData = series.some((row) => METRICS.some((m) => typeof row[m.key] === 'number'))
  if (!hasData) {
    return <div className="text-xs text-gray-400 py-4">暂无各风格赚钱效应趋势数据</div>
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4">
      <div className="text-sm font-medium text-gray-700 mb-1">各风格赚钱效应 · 次日开盘溢价中位趋势</div>
      <div className="text-xs text-gray-400 mb-3">
        口径：T-1 各档位涨停 → T 日开盘溢价 (T开−T-1收)/T-1收 中位；横轴=实现日(旧左新右)，缺口断线
      </div>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={series} margin={{ top: 8, right: 16, bottom: 4, left: -12 }}>
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
            formatter={(value, name) => {
              const num = typeof value === 'number' ? value : Number(value ?? 0)
              return [`${num > 0 ? '+' : ''}${num}%`, name]
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
        </LineChart>
      </ResponsiveContainer>
      <div className="text-[11px] text-gray-400 mt-2 leading-relaxed">
        注：容量票为「全市场成交额前10」口径、10cm首板已剔除 ST，均自口径修订日起生效；更早历史沿用旧口径（容量票=涨停池、首板含 ST），存在口径过渡带。
      </div>
    </div>
  )
}
