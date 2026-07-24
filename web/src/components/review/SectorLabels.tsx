import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { SectorLabelItem, SectorLabelsPayload } from '../../lib/types'
import { Section } from './widgets'

type FilterKey = 'all' | 'half_year' | 'year' | 'resonance' | 'year_resonance'

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'half_year', label: '半年线上' },
  { key: 'year', label: '年线上' },
  { key: 'resonance', label: '近期共振' },
  { key: 'year_resonance', label: '年线+共振' },
]

function filterCount(data: SectorLabelsPayload, filter: FilterKey): number {
  if (filter === 'half_year') return data.summary.above_half_year_ma
  if (filter === 'year') return data.summary.above_year_ma
  if (filter === 'resonance') return data.summary.recent_resonance
  if (filter === 'year_resonance') return data.summary.year_and_resonance
  return data.summary.total_l2
}

function matchesFilter(item: SectorLabelItem, filter: FilterKey): boolean {
  if (filter === 'half_year') return item.above_half_year_ma === true
  if (filter === 'year') return item.above_year_ma === true
  if (filter === 'resonance') return item.recent_price_volume_resonance === true
  if (filter === 'year_resonance') {
    return item.above_year_ma === true && item.recent_price_volume_resonance === true
  }
  return true
}

function fmtComparable(value: number | null): string {
  if (value == null) return '—'
  const [whole, fraction = ''] = value.toFixed(8).split('.')
  const significantFraction = fraction.replace(/0+$/, '')
  return `${whole}.${significantFraction.padEnd(2, '0')}`
}

function MaPositionLabel({
  above,
  name,
}: {
  above: boolean | null
  name: '半年线' | '年线'
}) {
  if (above == null) {
    return <span className="text-gray-400">{name}数据不足</span>
  }
  return (
    <span className={above ? 'text-blue-700 font-medium' : 'text-gray-500'}>
      {above ? `${name}上` : `未在${name}上`}
    </span>
  )
}

function ResonanceLabel({
  item,
  lookbackDays,
}: {
  item: SectorLabelItem
  lookbackDays: number
}) {
  if (item.recent_price_volume_resonance == null) {
    return <span className="text-gray-400">共振数据不足</span>
  }
  if (!item.recent_price_volume_resonance) {
    return <span className="text-gray-500">近{lookbackDays}个交易快照日无共振</span>
  }
  return <span className="text-amber-700 font-medium">近期共振</span>
}

/** 申万二级板块半年线/年线位置 + 近期价量同日新高标签。 */
export default function SectorLabels({ date }: { date: string | undefined }) {
  const [filter, setFilter] = useState<FilterKey>('all')
  const { data, isLoading, isError } = useQuery({
    queryKey: ['sector-labels', date],
    queryFn: () => api.getSectorLabels(date as string),
    enabled: !!date,
  })

  if (!date) return null

  const rows = data?.items.filter(item => matchesFilter(item, filter)) ?? []
  const definitions = data?.definitions
  const missingMessage = data?.status === 'missing_l2'
    ? data.summary.missing_l2_count > 0
      ? `当日快照完全缺少申万二级；保留预期板块清单 ${data.summary.missing_l2_count} 个并按数据不足处理`
      : '当日快照缺少申万二级板块数据'
    : '暂无当日拥挤度快照（需先运行 sector-crowding daily/backfill）'

  return (
    <Section title="板块趋势标签（申万二级）">
      {definitions && (
        <div className="bg-slate-50 border border-slate-200 text-slate-700 text-xs rounded px-3 py-2 mb-3">
          <span className="font-medium">半年线上 [事实]</span>：
          收盘高于 MA{definitions.half_year_ma_window}
          （最近{definitions.half_year_ma_window}个交易快照收盘均值）；
          <span className="font-medium">年线上 [事实]</span>：
          收盘高于 MA{definitions.year_ma_window}
          （最近{definitions.year_ma_window}个交易快照收盘均值）；
          <span className="font-medium ml-1">近期价量共振 [判断]</span>：
          最近{definitions.resonance_lookback_days}个交易快照日内，指数收盘与成交额同日严格突破此前
          {definitions.resonance_breakout_window}个交易快照日高点。
        </div>
      )}

      {data?.available && (
        <div role="group" aria-label="板块标签筛选" className="flex flex-wrap gap-1 mb-3">
          {FILTERS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
              className={`px-3 py-1 text-xs rounded-full transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 ${
                filter === key
                  ? 'bg-indigo-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label} {filterCount(data, key)}
            </button>
          ))}
        </div>
      )}
      {data?.available && data.status === 'partial' && (
        <div role="alert" className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          当日申万二级覆盖不完整：缺失 {data.summary.missing_l2_count} 个板块；
          缺失项保留并按数据不足处理。
        </div>
      )}

      {isLoading && <div role="status" className="py-6 text-center text-xs text-gray-400">加载中...</div>}
      {isError && <div role="alert" className="py-6 text-center text-xs text-red-500">板块标签加载失败</div>}
      {!isLoading && !isError && data && !data.available && (
        <div role="alert" className="mb-3 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-center text-xs text-amber-800">
          {missingMessage}
        </div>
      )}
      {!isLoading && !isError && data?.available && rows.length === 0 && (
        <div aria-live="polite" className="py-6 text-center text-xs text-gray-400">
          当前筛选暂无板块
        </div>
      )}
      {!isLoading && !isError && data && rows.length > 0 && (
        <div className="max-h-[28rem] overflow-auto">
          <table className="min-w-full text-xs text-gray-600">
            <caption className="sr-only">申万二级板块趋势标签</caption>
            <thead className="sticky top-0 bg-white">
              <tr className="text-left text-gray-400">
                <th scope="col" className="py-1 pr-4 font-medium">板块</th>
                <th scope="col" className="py-1 pr-4 font-medium">半年线</th>
                <th scope="col" className="py-1 pr-4 font-medium">年线</th>
                <th scope="col" className="py-1 pr-4 font-medium">价量共振</th>
                <th scope="col" className="py-1 font-medium">事实证据</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(item => {
                const event = item.last_resonance
                return (
                  <tr key={item.code} className="border-t border-gray-200/70 align-top">
                    <td className="py-1.5 pr-4">
                      <div className="font-medium text-gray-700">{item.name}</div>
                      <div className="text-gray-400">{item.code}</div>
                      {!item.present_on_target && (
                        <div className="font-medium text-amber-700">当日缺行</div>
                      )}
                    </td>
                    <td className="py-1.5 pr-4">
                      <MaPositionLabel above={item.above_half_year_ma} name="半年线" />
                      <div className="text-gray-400">
                        收盘 {fmtComparable(item.close)} / MA{definitions?.half_year_ma_window}{' '}
                        {fmtComparable(item.half_year_ma)}
                      </div>
                    </td>
                    <td className="py-1.5 pr-4">
                      <MaPositionLabel above={item.above_year_ma} name="年线" />
                      <div className="text-gray-400">
                        收盘 {fmtComparable(item.close)} / MA{definitions?.year_ma_window}{' '}
                        {fmtComparable(item.year_ma)}
                      </div>
                    </td>
                    <td className="py-1.5 pr-4">
                      <ResonanceLabel
                        item={item}
                        lookbackDays={definitions?.resonance_lookback_days ?? 0}
                      />
                      {event && <div className="text-gray-400">最近 {event.date}</div>}
                    </td>
                    <td className="py-1.5 text-gray-500">
                      {event ? (
                        <>
                          <div>
                            指数 {fmtComparable(event.close)} &gt;{' '}
                            {fmtComparable(event.prior_close_high)}
                          </div>
                          <div>
                            成交额 {fmtComparable(event.amount_billion)}亿 &gt;{' '}
                            {fmtComparable(event.prior_amount_high_billion)}亿
                          </div>
                        </>
                      ) : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}
