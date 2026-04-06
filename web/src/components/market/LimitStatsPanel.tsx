import { useState } from 'react'
import type { BoardCountItem, LimitStepRow, MarketFullData } from '../../lib/types'

function StatCard({
  label,
  value,
  suffix,
}: {
  label: string
  value: string | number | null | undefined
  suffix?: string
}) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-gray-400">{label}</span>
      <span className="text-sm font-semibold text-gray-800">{value ?? '-'}{suffix && ` ${suffix}`}</span>
    </div>
  )
}

function boardColor(board: number) {
  if (board >= 7) return { bar: '#ef4444', text: 'text-red-700 font-bold' }
  if (board >= 5) return { bar: '#f97316', text: 'text-orange-700 font-semibold' }
  if (board >= 3) return { bar: '#3b82f6', text: 'text-blue-700' }
  return { bar: '#9ca3af', text: 'text-gray-600' }
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

export default function LimitStatsPanel({
  market,
  boards,
  maxBoardCount,
  highMarkRows,
}: {
  market: MarketFullData
  boards: BoardCountItem[]
  maxBoardCount: number
  highMarkRows: LimitStepRow[]
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">涨跌停统计</h2>
        <div className="grid grid-cols-3 gap-4 mb-3">
          <StatCard label="涨停" value={market.limit_up_count} />
          <StatCard label="跌停" value={market.limit_down_count} />
          <StatCard label="最高板" value={market.highest_board} suffix="板" />
        </div>
        <div className="grid grid-cols-2 gap-4 mb-3">
          <StatCard label="封板率" value={market.seal_rate != null ? `${market.seal_rate.toFixed(1)}` : null} suffix="%" />
          <StatCard label="炸板率" value={market.broken_rate != null ? `${market.broken_rate.toFixed(1)}` : null} suffix="%" />
        </div>
        {boards.length > 0 && (
          <div>
            <span className="text-xs text-gray-400 mb-2 block">连板梯队</span>
            <div className="space-y-2">
              {boards.map((b) => {
                const { bar, text } = boardColor(b.board)
                const barWidth = Math.max(4, Math.round((b.count / maxBoardCount) * 100))
                return (
                  <BoardRow
                    key={b.board}
                    board={b.board}
                    count={b.count}
                    stocks={b.stocks}
                    bar={bar}
                    textCls={text}
                    barWidth={barWidth}
                  />
                )
              })}
            </div>
          </div>
        )}
        {highMarkRows.length > 0 && (
          <div className="mt-4">
            <span className="text-xs text-gray-400 mb-2 block">高标明细</span>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-gray-500">
                    <th className="py-2 pr-4">股票</th>
                    <th className="py-2 pr-0 text-right">连板数</th>
                  </tr>
                </thead>
                <tbody>
                  {highMarkRows.slice(0, 10).map((row) => (
                    <tr key={`${row.ts_code}-${row.nums}`} className="border-b border-gray-50">
                      <td className="py-1.5 pr-4 font-medium text-gray-800">{row.name || row.ts_code}</td>
                      <td className={`py-1.5 pr-0 text-right ${boardColor(Number(row.nums || 0)).text}`}>{row.nums || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
      <div className="bg-white rounded-lg shadow p-4">
        <h2 className="text-sm font-semibold text-gray-700 mb-3">溢价率</h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="10cm首板" value={market.premium_10cm != null ? market.premium_10cm.toFixed(2) : null} suffix="%" />
          <StatCard label="20cm首板" value={market.premium_20cm != null ? market.premium_20cm.toFixed(2) : null} suffix="%" />
          <StatCard label="30cm首板" value={market.premium_30cm != null ? market.premium_30cm.toFixed(2) : null} suffix="%" />
          <StatCard label="二板" value={market.premium_second_board != null ? market.premium_second_board.toFixed(2) : null} suffix="%" />
        </div>
      </div>
    </div>
  )
}
