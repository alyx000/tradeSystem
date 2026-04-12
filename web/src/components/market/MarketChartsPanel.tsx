import {
  ResponsiveContainer, ComposedChart, Line, Bar, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts'
import type { MarketChartItem } from '../../lib/types'

function fmtAmount(v: number | string | null | undefined) {
  if (v == null) return '-'
  const numeric = Number(v)
  if (Number.isNaN(numeric)) return `${v}`
  return numeric >= 10000 ? `${(numeric / 10000).toFixed(2)}万亿` : `${numeric}`
}

function normalizeTooltipValue(value: unknown): string | number | null | undefined {
  if (Array.isArray(value)) return value[0]
  if (typeof value === 'number' || typeof value === 'string' || value == null) return value
  return undefined
}

export default function MarketChartsPanel({ chartData }: { chartData: MarketChartItem[] }) {
  if (!chartData.length) return null

  return (
    <>
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">近期趋势</h2>
        <ResponsiveContainer width="100%" height={300}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
            <YAxis
              yAxisId="amount"
              orientation="left"
              tick={{ fontSize: 11 }}
              tickFormatter={v => v >= 10000 ? `${(v / 10000).toFixed(1)}万亿` : `${v}`}
            />
            <YAxis yAxisId="count" orientation="right" tick={{ fontSize: 11 }} />
            <Tooltip
              formatter={(value, name) => {
                const normalized = normalizeTooltipValue(value)
                if (name === '成交额') return fmtAmount(normalized)
                return normalized
              }}
            />
            <Legend />
            <Bar yAxisId="count" dataKey="limit_up_count" name="涨停数" fill="#ef4444" opacity={0.6} />
            <Line yAxisId="amount" dataKey="total_amount" name="成交额" stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

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

      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">情绪指标趋势</h2>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="pct" orientation="left" tick={{ fontSize: 11 }} unit="%" />
            <YAxis yAxisId="count" orientation="right" tick={{ fontSize: 11 }} />
            <Tooltip />
            <Legend />
            <Bar yAxisId="count" dataKey="limit_down_count" name="跌停数" fill="#22c55e" opacity={0.5} />
            <Line yAxisId="pct" dataKey="seal_rate" name="封板率" stroke="#3b82f6" strokeWidth={2} dot={{ r: 2 }} />
            <Line yAxisId="pct" dataKey="broken_rate" name="炸板率" stroke="#f59e0b" strokeWidth={2} dot={{ r: 2 }} />
            <Line yAxisId="count" dataKey="highest_board" name="最高板" stroke="#8b5cf6" strokeWidth={1.5} strokeDasharray="4 2" dot={{ r: 2 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}
