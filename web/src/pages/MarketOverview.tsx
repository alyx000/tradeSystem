import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type {
  MainThemeItem,
  MarketChartItem,
  MarketFullData,
  SectorTab,
  SortOrder,
} from '../lib/types'
import LimitStatsPanel from '../components/market/LimitStatsPanel'
import MarketSummaryCards from '../components/market/MarketSummaryCards'
import SectorRankingPanel from '../components/market/SectorRankingPanel'
import StatusSignalPanel from '../components/market/StatusSignalPanel'
import ThemeRhythmPanel from '../components/market/ThemeRhythmPanel'
import {
  extractIndex,
  getDailyInfoRows,
  getEmotionSignals,
  getLimitStepRows,
  getMarketMoneyflowSummary,
  getMarketSignals,
  getSectorData,
  getSectorMoneyflowRows,
  getStrongestSectorRows,
  parseBoardCounts,
} from '../components/market/marketSelectors'

const MarketChartsPanel = lazy(() => import('../components/market/MarketChartsPanel'))

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function pctColor(v: number | null | undefined) {
  if (v == null) return 'text-gray-500'
  return v >= 0 ? 'text-red-600' : 'text-green-600'
}

function IndexCard({
  label,
  close,
  pct,
}: {
  label: string
  close: number | null | undefined
  pct: number | null | undefined
}) {
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className="text-lg font-semibold text-gray-800">{close ?? '-'}</div>
      <div className={`text-sm font-medium ${pctColor(pct)}`}>{fmtPct(pct)}</div>
    </div>
  )
}

type MarketViewTab = 'summary' | 'envelope'

export default function MarketOverview() {
  const { date } = useParams<{ date: string }>()
  const navigate = useNavigate()
  const [sectorTab, setSectorTab] = useState<SectorTab>('industry')
  const [viewTab, setViewTab] = useState<MarketViewTab>('summary')
  const [sortOrder, setSortOrder] = useState<SortOrder>('gain')
  const [showAllSectors, setShowAllSectors] = useState(false)
  const [loadCharts, setLoadCharts] = useState(false)
  const chartsRef = useRef<HTMLDivElement | null>(null)

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

  const chartData: MarketChartItem[] = (history || [])
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((d) => ({
      ...d,
      date_short: d.date.slice(5),
    }))

  useEffect(() => {
    if (loadCharts || chartData.length === 0) return
    if (typeof window === 'undefined' || typeof window.IntersectionObserver === 'undefined') {
      setLoadCharts(true)
      return
    }
    const node = chartsRef.current
    if (!node) return
    const observer = new window.IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setLoadCharts(true)
            observer.disconnect()
            break
          }
        }
      },
      { rootMargin: '240px 0px' }
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [chartData.length, loadCharts])

  if (isLoading) {
    return <div className="text-center py-12 text-gray-400">加载中...</div>
  }

  const hasSummary = market?.available === true
  const m: MarketFullData | null = hasSummary ? market : null
  const boards = m ? parseBoardCounts(m.continuous_board_counts) : []
  const maxBoardCount = boards.length > 0 ? Math.max(...boards.map(b => b.count)) : 1
  const rawSectorData = m ? getSectorData(m, sectorTab) : []
  const sectorData = sortOrder === 'loss' ? rawSectorData.slice().reverse() : rawSectorData
  const visibleSectors = showAllSectors ? sectorData : sectorData.slice(0, 10)
  const activeThemes: MainThemeItem[] = (mainThemes || []).filter((t) => t.status === 'active')
  const dailyInfoRows = m ? getDailyInfoRows(m) : []
  const strongestSectors = m ? getStrongestSectorRows(m) : []
  const highMarkRows = m ? getLimitStepRows(m) : []
  const thsMoneyflowRows = m ? getSectorMoneyflowRows(m, 'ths') : []
  const dcMoneyflowRows = m ? getSectorMoneyflowRows(m, 'dc') : []
  const marketMoneyflow = m ? getMarketMoneyflowSummary(m) : null
  const marketSignals = m ? getMarketSignals(m, history || []) : []
  const emotionSignals = m ? getEmotionSignals(m, strongestSectors, highMarkRows) : []

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
            {(history || []).map((h) => (
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

      <StatusSignalPanel title="市场状态观察" signals={marketSignals} />

      <StatusSignalPanel title="情绪状态观察" signals={emotionSignals} />

      <MarketSummaryCards
        market={m}
        marketMoneyflow={marketMoneyflow}
        dailyInfoRows={dailyInfoRows}
      />

      <LimitStatsPanel
        market={m}
        boards={boards}
        maxBoardCount={maxBoardCount}
        highMarkRows={highMarkRows}
      />

      <ThemeRhythmPanel activeThemes={activeThemes} />

      <SectorRankingPanel
        sectorTab={sectorTab}
        sortOrder={sortOrder}
        showAllSectors={showAllSectors}
        strongestSectors={strongestSectors}
        thsMoneyflowRows={thsMoneyflowRows}
        dcMoneyflowRows={dcMoneyflowRows}
        sectorData={sectorData}
        visibleSectors={visibleSectors}
        onSectorTabChange={(tab) => {
          setSectorTab(tab)
          setShowAllSectors(false)
        }}
        onSortOrderChange={setSortOrder}
        onToggleShowAll={() => setShowAllSectors((v) => !v)}
      />

      {chartData.length > 0 && (
        <div ref={chartsRef}>
          {loadCharts ? (
            <Suspense fallback={<ChartLoadingFallback />}>
              <MarketChartsPanel chartData={chartData} />
            </Suspense>
          ) : (
            <DeferredChartPlaceholder />
          )}
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
  data: Record<string, unknown> | null | undefined
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

function ChartLoadingFallback() {
  return (
    <div className="bg-white rounded-lg shadow p-4 text-center text-sm text-gray-400">
      图表加载中...
    </div>
  )
}

function DeferredChartPlaceholder() {
  return (
    <div className="bg-white rounded-lg shadow p-4 text-center text-sm text-gray-400">
      图表将在滚动到此区域后加载
    </div>
  )
}
