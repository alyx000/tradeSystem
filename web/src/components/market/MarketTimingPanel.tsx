import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import type { MarketTimingPayload, MarketTimingHistoryPayload, MarketTimingSignal } from '../../lib/types'

const FRACTAL_LABEL: Record<string, string> = {
  forming: '🟡 底分型成型',
  confirmed: '🟢 底分型确认',
  invalid: '⚪ 结构破坏',
  none: '—',
}

function fibCell(s: MarketTimingSignal) {
  if (s.fib_hit != null) {
    return <span className="text-red-600 font-medium">🎯 变盘窗口·第{s.fib_hit}交易日</span>
  }
  if (s.fib_near != null) {
    const diff = s.fib_day_count != null ? Math.abs(s.fib_day_count - s.fib_near) : '?'
    return <span className="text-amber-600">⏳ 临近变盘窗口（斐波那契{s.fib_near}，差{diff}日）</span>
  }
  if (s.fib_day_count != null) {
    return <span className="text-gray-400">未到变盘窗口（第{s.fib_day_count}日）</span>
  }
  return <span className="text-gray-400">—</span>
}

function pivotCell(s: MarketTimingSignal) {
  if (!s.swing_pivot_date) return '—'
  const label = s.swing_pivot_type === 'high' ? '高点' : s.swing_pivot_type === 'low' ? '低点' : s.swing_pivot_type ?? ''
  return `${label} ${s.swing_pivot_date}${s.swing_pivot_price != null ? `（${s.swing_pivot_price}）` : ''}`
}

function fmtPctile(v: number | null) {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}

export default function MarketTimingPanel({
  payload,
  history,
  asOfDate,
}: {
  payload: MarketTimingPayload
  history?: MarketTimingHistoryPayload
  asOfDate?: string
}) {
  if (!payload.available) {
    return (
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">大盘择时观察 [判断]</h2>
        <div className="text-sm text-gray-400">
          暂无 {payload.date} 的大盘择时数据（先执行 <code className="bg-gray-50 px-1 rounded">python3 main.py market-timing daily --date {payload.date}</code>）
        </div>
      </div>
    )
  }

  const { context, signals, resonance_count } = payload
  const lowVolume = context.amount_pctile_20d != null && context.amount_pctile_20d <= 0.2
  // 防前瞻偏差：复盘历史日期时只展示该日及之前的趋势（后端 to_date 已限窗，此处前端兜底）
  const series = (history?.series ?? []).filter((p) => !asOfDate || p.date <= asOfDate)

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-2">大盘择时观察 · {payload.date} [判断]</h2>
        <p className="text-xs text-gray-500 mb-3">
          以下全部为 <span className="font-medium">[判断]</span> 派生信号，
          <span className="font-medium">不构成买卖建议、不预测方向、不出价位</span>。仅供复盘参考。
        </p>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
          <div>
            <div className="text-xs text-gray-500">两市成交额 [事实]</div>
            <div className="font-semibold text-gray-800">
              {context.market_amount_yi != null ? `${Math.round(context.market_amount_yi)} 亿` : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500">近20日分位</div>
            <div className={`font-semibold ${lowVolume ? 'text-amber-600' : 'text-gray-800'}`}>
              {fmtPctile(context.amount_pctile_20d)}{lowVolume ? ' 地量' : ''}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500">涨跌家数 [事实]</div>
            <div className="font-semibold text-gray-800">
              {context.advance != null && context.decline != null
                ? <><span className="text-red-600">{context.advance}</span> / <span className="text-green-600">{context.decline}</span></>
                : '—'}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500">跌停家数 [事实]</div>
            <div className="font-semibold text-gray-800">{context.limit_down_count ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-500">共振变盘点</div>
            <div className="font-semibold text-gray-800">{resonance_count} 个指数</div>
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">时间周期 · 变盘点 [判断]</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="py-1 pr-4">指数</th>
                <th className="py-1 pr-4">起算拐点 [事实]</th>
                <th className="py-1 pr-4">距今交易日</th>
                <th className="py-1">变盘点判断</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.index_code} className="border-b last:border-0">
                  <td className="py-1.5 pr-4 text-gray-800">{s.index_name}</td>
                  <td className="py-1.5 pr-4 text-gray-600">{pivotCell(s)}</td>
                  <td className="py-1.5 pr-4 text-gray-800">{s.fib_day_count ?? '—'}</td>
                  <td className="py-1.5">{fibCell(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-gray-400 mt-3 leading-relaxed">
          变盘点 = 从最近拐点起算的交易日数命中斐波那契数（5/8/13/21/34/55），时间上大概率方向转折的窗口；
          只标「时间到位」，<span className="font-medium">不预判涨跌</span>，需结合多指数共振 / 底分型 / 成交额综合看。
        </p>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">底分型 [判断]</h2>
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead>
              <tr className="text-left text-gray-500 border-b">
                <th className="py-1 pr-4">指数</th>
                <th className="py-1 pr-4">状态</th>
                <th className="py-1 pr-4">结构低点 [事实]</th>
                <th className="py-1">确认日</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((s) => (
                <tr key={s.index_code} className="border-b last:border-0">
                  <td className="py-1.5 pr-4 text-gray-800">{s.index_name}</td>
                  <td className="py-1.5 pr-4">{FRACTAL_LABEL[s.fractal_status] ?? s.fractal_status}</td>
                  <td className="py-1.5 pr-4 text-gray-600">
                    {s.fractal_low_date ? `${s.fractal_low_date}${s.fractal_low_price != null ? `（${s.fractal_low_price}）` : ''}` : '—'}
                  </td>
                  <td className="py-1.5 text-gray-800">{s.fractal_confirm_date ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {series.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">共振 / 成交额地量分位 趋势 [判断]</h2>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={series}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="res" orientation="left" tick={{ fontSize: 11 }} allowDecimals={false} />
              <YAxis yAxisId="pct" orientation="right" tick={{ fontSize: 11 }} domain={[0, 1]}
                tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip formatter={(v, name) => (name === '地量分位' ? `${Math.round(Number(v) * 100)}%` : v)} />
              <Legend />
              <Bar yAxisId="res" dataKey="resonance_count" name="共振指数数" fill="#8b5cf6" opacity={0.6} />
              <Line yAxisId="pct" dataKey="amount_pctile_20d" name="地量分位" stroke="#f59e0b"
                strokeWidth={2} dot={{ r: 2 }} connectNulls />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
