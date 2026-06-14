import { useState } from 'react'
import {
  ResponsiveContainer, ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import type { MarketTimingPayload, MarketTimingHistoryPayload, MarketTimingSignal } from '../../lib/types'
import { FRACTAL_LABEL, fibTextFull, fibTone, pivotText } from './marketTimingFormat'

function fmtPctile(v: number | null): string {
  if (v == null) return '—'
  return `${Math.round(v * 100)}%`
}

// 「确认」底分型的高亮行（事实段按存在性拼接，整行作为单文本节点）。
// 仅用于 fractal_status==='confirmed' 的信号——文案写死「确认（放量中阳突破）」，
// 故函数名锁死语义，禁止复用到 forming/invalid（否则会给非确认状态打绿标脏文案）。
function confirmedFractalLine(s: MarketTimingSignal): string {
  const parts = [`🟢 ${s.index_name} 底分型确认（放量中阳突破）`]
  if (s.fractal_confirm_date) parts.push(`确认日 ${s.fractal_confirm_date}`)
  if (s.fractal_low_date) {
    parts.push(`结构低点 ${s.fractal_low_date}${s.fractal_low_price != null ? `（${s.fractal_low_price}）` : ''}`)
  }
  return parts.join(' · ')
}

// 仅当一组信号共享同一结构低点日期时返回它，否则 null（避免拿一个日期冒充全部）
function uniformLowDate(sigs: MarketTimingSignal[]): string | null {
  const dates = new Set(sigs.map((s) => s.fractal_low_date).filter(Boolean))
  return dates.size === 1 ? ([...dates][0] as string) : null
}

/**
 * 大盘择时「汇总条」：逐指数变盘窗口已上移到顶部指数卡片，这里只承载非单指数 /
 * 场外指数信息——共振、成交额分位、未上卡片的指数（cardCodes 之外）、底分型摘要、
 * 全指数明细（折叠）、共振/地量趋势。守红线：全 [判断]，不预判方向、不出价位。
 */
export default function MarketTimingPanel({
  payload,
  history,
  asOfDate,
  cardCodes = [],
}: {
  payload: MarketTimingPayload
  history?: MarketTimingHistoryPayload
  asOfDate?: string
  cardCodes?: string[]
}) {
  const [expanded, setExpanded] = useState(false)

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
  // 场外指数：变盘窗口未上顶部卡片的（如中证2000 微盘代理、平均股价）
  const offCard = signals.filter((s) => !cardCodes.includes(s.index_code))

  // 底分型：成型/确认/破坏分桶，确认/破坏醒目，成型仅计数（沿用折叠摘要决策）
  const forming = signals.filter((s) => s.fractal_status === 'forming')
  const confirmed = signals.filter((s) => s.fractal_status === 'confirmed')
  const invalid = signals.filter((s) => s.fractal_status === 'invalid')
  const formingLowDate = uniformLowDate(forming)  // 成型组统一的结构低点日（不一致则 null）

  return (
    <div className="bg-white rounded-lg shadow p-4 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-gray-700">大盘择时观察 · {payload.date} [判断]</h2>
        <p className="text-xs text-gray-500 mt-1">
          逐指数变盘窗口见上方指数卡片；以下为 <span className="font-medium">[判断]</span> 汇总，
          <span className="font-medium">不构成买卖建议、不预测方向、不出价位</span>。
        </p>
      </div>

      {/* 汇总行：共振 + 成交额分位 + 场外指数变盘窗口（文案预拼成单节点便于阅读与测试） */}
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span className="text-gray-700 font-medium">{`共振变盘点 ${resonance_count} 个指数`}</span>
        <span className={lowVolume ? 'text-amber-600 font-medium' : 'text-gray-700'}>
          {`成交额 ${context.market_amount_yi != null ? `${Math.round(context.market_amount_yi)} 亿` : '—'}（分位 ${fmtPctile(context.amount_pctile_20d)}${lowVolume ? ' 地量' : ''}）`}
        </span>
        {offCard.length > 0 && (
          <span className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-gray-500">场外：</span>
            {offCard.map((s) => (
              <span key={s.index_code} className={fibTone(s)}>{`${s.index_name} ${fibTextFull(s)}`}</span>
            ))}
          </span>
        )}
      </div>

      <p className="text-xs text-gray-400 leading-relaxed">
        变盘点 = 从最近拐点起算的交易日数命中斐波那契数（5/8/13/21/34/55），时间上大概率方向转折的窗口；
        只标「时间到位」，<span className="font-medium">不预判涨跌</span>，需结合多指数共振 / 底分型 / 成交额综合看。
      </p>

      {/* 底分型摘要 + 确认/破坏高亮 */}
      <div>
        <p className="text-sm text-gray-700">
          {`🔻 底分型：${forming.length} 成型${formingLowDate ? `（低点 ${formingLowDate}）` : ''} · ${confirmed.length} 确认 · ${invalid.length} 破坏`}
        </p>
        <div className="space-y-1 mt-1">
          {confirmed.map((s) => (
            <div key={s.index_code} className="text-sm text-green-700">{confirmedFractalLine(s)}</div>
          ))}
          {invalid.map((s) => (
            <div key={s.index_code} className="text-sm text-gray-500">{`⚪ ${s.index_name} 结构破坏`}</div>
          ))}
        </div>
      </div>

      {/* 全部指数明细（折叠，不丢信息） */}
      <div>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
          className="text-sm text-blue-600 hover:text-blue-700"
        >
          {expanded ? '▾ 收起明细' : `▸ 展开全部 ${signals.length} 指数明细`}
        </button>
        {expanded && (
          <div className="overflow-x-auto mt-2">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 border-b">
                  <th className="py-1 pr-4">指数</th>
                  <th className="py-1 pr-4">起算拐点 [事实]</th>
                  <th className="py-1 pr-4">距今</th>
                  <th className="py-1 pr-4">变盘点判断</th>
                  <th className="py-1 pr-4">底分型</th>
                  <th className="py-1 pr-4">结构低点 [事实]</th>
                  <th className="py-1">确认日</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr key={s.index_code} className="border-b last:border-0">
                    <td className="py-1.5 pr-4 text-gray-800">{s.index_name}</td>
                    <td className="py-1.5 pr-4 text-gray-600">{pivotText(s)}</td>
                    <td className="py-1.5 pr-4 text-gray-800">{s.fib_day_count ?? '—'}</td>
                    <td className={`py-1.5 pr-4 ${fibTone(s)}`}>{fibTextFull(s)}</td>
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
        )}
      </div>

      {/* 趋势图 */}
      {series.length > 0 && (
        <div className="border-t pt-3">
          <h3 className="text-sm font-medium text-gray-700 mb-2">共振 / 成交额地量分位 趋势 [判断]</h3>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={series} margin={{ top: 5, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
              <YAxis yAxisId="res" orientation="left" tick={{ fontSize: 11 }} allowDecimals={false} />
              <YAxis yAxisId="pct" orientation="right" tick={{ fontSize: 11 }} domain={[0, 1]}
                tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <Tooltip formatter={(v, name) => (name === '地量分位' ? `${Math.round(Number(v) * 100)}%` : v)} />
              {/* 图例移到 X 轴标签下方留白，避免与 06-09… 刻度重叠 */}
              <Legend verticalAlign="bottom" iconSize={10} wrapperStyle={{ fontSize: 12, paddingTop: 12 }} />
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
