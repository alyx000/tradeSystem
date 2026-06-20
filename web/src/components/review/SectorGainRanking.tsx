import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import type { SectorGainRow } from '../../lib/types'
import { Section } from './widgets'

// 5/10/20 日三档独立排名：组按组内涨幅最大个股降序、平手比次大。
const PERIODS = [
  { key: '5d', label: '5日' },
  { key: '10d', label: '10日' },
  { key: '20d', label: '20日' },
] as const
type PeriodKey = (typeof PERIODS)[number]['key']

// 维度：申万二级板块（单标签）/ 同花顺概念题材（多标签，一票可进多榜）。
const DIMENSIONS = [
  { key: 'sector', label: '申万板块' },
  { key: 'concept', label: '同花顺题材' },
] as const
type DimensionKey = (typeof DIMENSIONS)[number]['key']

// 区间涨幅由真实收盘价算得，属 [事实]；守红线不出价位目标、不给买卖建议。
const REDLINE = '区间涨幅排名 · 客观 [事实]（真实收盘价算得）· 不含价位目标、不构成买卖建议。'

function fmtGain(v: number): string {
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

// 与本步「行业排行 / 节奏信号」一致：本仓库板块涨跌用 绿=涨 / 红=跌。
function gainClass(v: number): string {
  return v >= 0 ? 'text-green-600' : 'text-red-500'
}

function RankTable({ rows, groupLabel }: { rows: SectorGainRow[]; groupLabel: string }) {
  const ranked = rows.filter((r) => r.max_gain != null)
  if (ranked.length === 0) {
    return <div className="px-3 py-6 text-center text-xs text-gray-400">本周期暂无有效区间涨幅</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-xs text-gray-600">
        <thead>
          <tr className="text-left text-gray-400">
            <th className="py-1 pr-3 font-medium">#</th>
            <th className="py-1 pr-4 font-medium">{groupLabel}</th>
            <th className="py-1 pr-4 font-medium">领涨股</th>
            <th className="py-1 pr-4 font-medium text-right">涨幅</th>
            <th className="py-1 font-medium">{groupLabel}内其余</th>
          </tr>
        </thead>
        <tbody>
          {ranked.map((row, i) => {
            const lead = row.stocks[0]
            const others = row.stocks.slice(1)
            return (
              <tr key={row.industry} className="border-t border-gray-200/70 align-top">
                <td className="py-1.5 pr-3 text-gray-400">{i + 1}</td>
                <td className="py-1.5 pr-4 font-medium text-gray-700">{row.industry}</td>
                <td className="py-1.5 pr-4 text-gray-700">{lead?.name ?? '-'}</td>
                <td className={`py-1.5 pr-4 text-right font-medium ${gainClass(row.max_gain as number)}`}>
                  {fmtGain(row.max_gain as number)}
                </td>
                <td className="py-1.5 text-gray-500">
                  {others.length === 0 ? (
                    '—'
                  ) : (
                    <span className="flex flex-wrap gap-x-2 gap-y-0.5">
                      {others.map((stock) => (
                        <span key={stock.code ?? stock.name}>
                          {stock.name} <span className={gainClass(stock.gain)}>{fmtGain(stock.gain)}</span>
                        </span>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** 成交额前50 区间涨幅排名（申万板块 / 同花顺题材 双维度；自带按复盘日取数；date 缺失不渲染）。 */
export default function SectorGainRanking({ date }: { date: string | undefined }) {
  const [period, setPeriod] = useState<PeriodKey>('5d')
  const [dimension, setDimension] = useState<DimensionKey>('sector')
  const { data, isLoading, isError } = useQuery({
    queryKey: ['sector-gain-ranking', date],
    queryFn: () => api.getSectorGainRanking(date as string),
    enabled: !!date,
  })

  if (!date) return null

  const periods = dimension === 'sector' ? data?.rankings : data?.concept_rankings
  const hasAny =
    !!periods && (periods['5d'].length > 0 || periods['10d'].length > 0 || periods['20d'].length > 0)
  const emptyHint =
    dimension === 'concept'
      ? '暂无题材数据（同花顺概念取数失败或当日未采集）'
      : '暂无区间涨幅数据（需先跑 volume-watch daily 采集）'

  return (
    <Section title="区间涨幅排名（成交额前50）">
      <div className="bg-amber-50 border border-amber-100 text-amber-800 text-xs rounded px-3 py-2 mb-3">
        {REDLINE}
      </div>

      <div className="flex gap-1 mb-2" role="tablist" aria-label="排名维度">
        {DIMENSIONS.map(({ key, label }) => (
          <button
            key={key}
            id={`sgr-dim-${key}`}
            type="button"
            role="tab"
            aria-selected={dimension === key}
            onClick={() => setDimension(key)}
            className={`px-3 py-1 text-xs rounded-full transition-colors ${
              dimension === key ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="flex gap-1 mb-3" role="tablist" aria-label="区间涨幅周期">
        {PERIODS.map(({ key, label }) => (
          <button
            key={key}
            id={`sgr-tab-${key}`}
            type="button"
            role="tab"
            aria-selected={period === key}
            onClick={() => setPeriod(key)}
            className={`px-3 py-1 text-xs rounded-full transition-colors ${
              period === key ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* aria-labelledby 指向当前选中档的 tab，屏幕阅读器能播报「面板属于 5日/10日/20日」 */}
      <div role="tabpanel" aria-labelledby={`sgr-tab-${period}`}>
        {isLoading && <div className="px-3 py-6 text-center text-xs text-gray-400">加载中...</div>}
        {isError && <div className="px-3 py-6 text-center text-xs text-gray-400">加载失败</div>}
        {!isLoading && !isError &&
          (!hasAny ? (
            <div className="px-3 py-6 text-center text-xs text-gray-400">{emptyHint}</div>
          ) : (
            <RankTable rows={periods![period]} groupLabel={dimension === 'concept' ? '题材' : '板块'} />
          ))}
      </div>
    </Section>
  )
}
