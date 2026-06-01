import {
  ResponsiveContainer, ComposedChart, AreaChart, LineChart,
  Area, Line, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts'
import type { ConcentrationTrendPayload } from '../../lib/types'

// 板块为类目型,用区分度高的调色板(非红绿,避免与涨跌语义混淆);「其他」固定灰。
const SECTOR_COLORS = ['#3b82f6', '#f59e0b', '#8b5cf6', '#10b981', '#06b6d4', '#ec4899', '#84cc16', '#6366f1']
const OTHER_COLOR = '#cbd5e1'

function sectorColor(key: string, idx: number) {
  return key === '其他' ? OTHER_COLOR : SECTOR_COLORS[idx % SECTOR_COLORS.length]
}

// A股口径:红涨绿跌
function changeClass(v: number | null) {
  if (v == null || v === 0) return 'text-gray-500'
  return v > 0 ? 'text-red-600' : 'text-green-600'
}

function fmtSignedPct(v: number | null) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v}%`
}

export default function ConcentrationTrendPanel({ payload }: { payload: ConcentrationTrendPayload }) {
  const { series, sector_keys, snapshot } = payload
  if (!series.length) return null

  return (
    <>
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">集中度 CR3 趋势（前3行业占 Top20）</h2>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={series}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} unit="%" domain={[0, 100]} />
            <Tooltip formatter={(v) => `${v}%`} />
            <Line dataKey="cr3" name="CR3" stroke="#8b5cf6" strokeWidth={2} dot={{ r: 2 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">头部成交额 / 占两市</h2>
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={series}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
            <YAxis yAxisId="amount" orientation="left" tick={{ fontSize: 11 }}
              tickFormatter={(v) => `${(v / 10000).toFixed(2)}万亿`} />
            <YAxis yAxisId="share" orientation="right" tick={{ fontSize: 11 }} unit="%" />
            <Tooltip />
            <Legend />
            <Bar yAxisId="amount" dataKey="total_amount_billion" name="头部成交额(亿)" fill="#3b82f6" opacity={0.5} />
            <Line yAxisId="share" dataKey="market_share_pct" name="占两市%" stroke="#f59e0b"
              strokeWidth={2} dot={{ r: 2 }} connectNulls />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">板块占比构成（占 Top20，堆叠）</h2>
        <ResponsiveContainer width="100%" height={280}>
          <AreaChart data={series}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date_short" tick={{ fontSize: 11 }} />
            <YAxis tick={{ fontSize: 11 }} unit="%" />
            <Tooltip />
            <Legend />
            {sector_keys.map((key, idx) => (
              // 函数式 dataKey:绕开 recharts 点路径解析,行业名含 "." 等字符也不断路径(审查中-1)
              <Area key={key} type="monotone" dataKey={(d) => d.sectors[key] ?? 0} name={key} stackId="s"
                stroke={sectorColor(key, idx)} fill={sectorColor(key, idx)} fillOpacity={0.7} />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>

      {snapshot && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">头部异动 · {snapshot.date}</h2>
          <div className="space-y-2 text-sm">
            <div>
              <span className="text-gray-500 mr-2">连续在榜</span>
              {snapshot.retention.length === 0
                ? <span className="text-gray-400">无（头部当日换手充分）</span>
                : snapshot.retention.map((r, i) => (
                    <span key={`${r.name}-${i}`} className="inline-flex items-center gap-1 mr-2 mb-1 px-2 py-0.5 rounded bg-gray-100 text-gray-700">
                      <span>{r.name}</span><span className="text-gray-500">{r.streak}天</span>
                    </span>
                  ))}
            </div>
            <div>
              <span className="text-gray-500 mr-2">今日新进</span>
              {snapshot.rotation.new.length === 0
                ? <span className="text-gray-400">无</span>
                : snapshot.rotation.new.map((s, i) => (
                    <span key={`${s.name}-${i}`} className="inline-flex items-center gap-1 mr-2 mb-1 px-2 py-0.5 rounded bg-red-50">
                      <span className="text-gray-800">{s.name}</span>
                      {s.industry && <span className="text-gray-500">{s.industry}</span>}
                      <span className={changeClass(s.change_pct)}>{fmtSignedPct(s.change_pct)}</span>
                    </span>
                  ))}
            </div>
            <div>
              <span className="text-gray-500 mr-2">今日退出</span>
              {snapshot.rotation.dropped.length === 0
                ? <span className="text-gray-400">无</span>
                : snapshot.rotation.dropped.map((s, i) => (
                    <span key={`${s.name}-${i}`} className="inline-block mr-2 mb-1 px-2 py-0.5 rounded bg-gray-100 text-gray-500">
                      {s.name}
                    </span>
                  ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
