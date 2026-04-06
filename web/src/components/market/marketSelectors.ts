import type { StatusSignalItem } from './StatusSignalPanel'
import type {
  BoardCountItem,
  DailyInfoRow,
  LimitStepRow,
  MarketFullData,
  MarketMoneyflowSummary,
  SectorMoneyflowDcRow,
  SectorMoneyflowThsRow,
  SectorSnapshotRow,
  SectorTab,
  StrongestSectorRow,
} from '../../lib/types'

type MarketSelectorSource = Partial<MarketFullData>
type MarketHistoryAmountRow = Pick<MarketFullData, 'date' | 'total_amount'>

function fmtSignedYi(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}亿`
}

function extractRows<T>(section: T[] | { data: T[] } | undefined): T[] {
  if (!section) return []
  if (Array.isArray(section)) return section
  return Array.isArray(section.data) ? section.data : []
}

export function parseBoardCounts(raw: unknown): BoardCountItem[] {
  if (!raw) return []
  try {
    const parsed: unknown = typeof raw === 'string' ? JSON.parse(raw) : raw
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return Object.entries(parsed)
        .map(([k, v]): BoardCountItem | null => {
          const board = parseInt(k)
          if (isNaN(board)) return null
          if (Array.isArray(v)) return { board, count: v.length, stocks: v as string[] }
          return { board, count: Number(v), stocks: [] }
        })
        .filter((item): item is BoardCountItem => item !== null)
        .sort((a, b) => b.board - a.board)
    }
    if (Array.isArray(parsed)) {
      return parsed
        .map((item): BoardCountItem | null => {
          if (!item || typeof item !== 'object') return null
          const row = item as Partial<BoardCountItem>
          const board = Number(row.board)
          const count = Number(row.count)
          if (Number.isNaN(board) || Number.isNaN(count)) return null
          return { board, count, stocks: Array.isArray(row.stocks) ? row.stocks : [] }
        })
        .filter((item): item is BoardCountItem => item !== null)
    }
  } catch {
    /* ignore malformed board payloads */
  }
  return []
}

export function getSectorData(m: MarketSelectorSource, tab: SectorTab): SectorSnapshotRow[] {
  const key = tab === 'industry' ? 'sector_industry'
    : tab === 'concept' ? 'sector_concept'
    : 'sector_fund_flow'
  return extractRows(m[key])
}

export function getDailyInfoRows(m: MarketSelectorSource): DailyInfoRow[] {
  return extractRows(m.daily_info)
}

export function getLimitStepRows(m: MarketSelectorSource): LimitStepRow[] {
  const rows = extractRows(m.limit_step)
  return rows
    .slice()
    .sort((a, b) => Number(b?.nums || 0) - Number(a?.nums || 0))
}

export function getStrongestSectorRows(m: MarketSelectorSource): StrongestSectorRow[] {
  const rows = extractRows(m.limit_cpt_list)
  return rows
    .slice()
    .sort((a, b) => Number(a?.rank || 9999) - Number(b?.rank || 9999))
}

export function getSectorMoneyflowRows(m: MarketSelectorSource, source: 'ths'): SectorMoneyflowThsRow[]
export function getSectorMoneyflowRows(m: MarketSelectorSource, source: 'dc'): SectorMoneyflowDcRow[]
export function getSectorMoneyflowRows(m: MarketSelectorSource, source: 'ths' | 'dc') {
  const rows = source === 'ths'
    ? extractRows(m.sector_moneyflow_ths)
    : extractRows(m.sector_moneyflow_dc)
  if (source === 'ths') {
    return rows
      .slice()
      .sort((a, b) => Number(b?.net_amount || 0) - Number(a?.net_amount || 0))
  }

  return rows
    .map((row) => ({
      ...row,
      net_amount_yi: row?.net_amount != null ? Number(row.net_amount) / 1e8 : null,
    }))
    .sort((a, b) => Number(b?.net_amount_yi || 0) - Number(a?.net_amount_yi || 0))
}

export function getMarketMoneyflowSummary(m: MarketSelectorSource): MarketMoneyflowSummary | null {
  const rows = extractRows(m.market_moneyflow_dc)
  if (rows.length === 0) return null
  const row = rows[0] || {}
  const toYi = (value: unknown) => value != null ? Number(value) / 1e8 : null
  return {
    netAmountYi: toYi(row.net_amount),
    netAmountRate: row.net_amount_rate != null ? Number(row.net_amount_rate) : null,
    superLargeYi: toYi(row.buy_elg_amount),
    largeYi: toYi(row.buy_lg_amount),
  }
}

export function getMarketSignals(m: MarketSelectorSource, history: MarketHistoryAmountRow[]): StatusSignalItem[] {
  const signals: StatusSignalItem[] = []
  const currentDate = m.date
  if (!currentDate) return signals

  const priorHistory = (history || [])
    .filter((item) => item?.date && item.date < currentDate && item?.total_amount != null)
    .sort((a, b) => a.date.localeCompare(b.date))

  const amountWindow = priorHistory.slice(-5)
  if (m.total_amount != null && amountWindow.length > 0) {
    const avgAmount = amountWindow.reduce((sum, item) => sum + Number(item.total_amount || 0), 0) / amountWindow.length
    const ratio = avgAmount > 0 ? Number(m.total_amount) / avgAmount : null
    if (ratio != null) {
      const isExpansion = ratio >= 1.03
      const isContraction = ratio <= 0.97
      signals.push({
        label: '量能',
        value: isExpansion ? '放量' : isContraction ? '缩量' : '平量',
        tone: isExpansion ? 'positive' : isContraction ? 'negative' : 'neutral',
        detail: `较近${amountWindow.length}日均额 ${ratio >= 1 ? '+' : ''}${((ratio - 1) * 100).toFixed(1)}%`,
      })
    }
  }

  if (m.advance_count != null && m.decline_count != null) {
    const advance = Number(m.advance_count)
    const decline = Number(m.decline_count)
    const ratio = decline > 0 ? advance / decline : advance > 0 ? Infinity : 1
    const breadth = ratio >= 1.5 ? '普涨' : ratio <= 0.67 ? '普跌' : '分化'
    signals.push({
      label: '广度',
      value: breadth,
      tone: breadth === '普涨' ? 'positive' : breadth === '普跌' ? 'negative' : 'neutral',
      detail: `${advance} / ${decline}`,
    })
  }

  const marketFlow = getMarketMoneyflowSummary(m)
  if (marketFlow?.netAmountYi != null) {
    const value = marketFlow.netAmountYi >= 5 ? '净流入' : marketFlow.netAmountYi <= -5 ? '净流出' : '小幅波动'
    signals.push({
      label: '主力资金',
      value,
      tone: marketFlow.netAmountYi > 0 ? 'positive' : marketFlow.netAmountYi < 0 ? 'negative' : 'neutral',
      detail: fmtSignedYi(marketFlow.netAmountYi),
    })
  }

  const maStates = [
    m.sh_above_ma5w,
    m.sz_above_ma5w,
    m.chinext_above_ma5w,
    m.star50_above_ma5w,
    m.avg_price_above_ma5w,
  ]
  const validMaStates = maStates.filter((value) => value === true || value === false)
  if (validMaStates.length > 0) {
    const aboveCount = validMaStates.filter(Boolean).length
    signals.push({
      label: '5周锚定',
      value: aboveCount >= 4 ? '线上占优' : aboveCount <= 1 ? '线下占优' : '分化',
      tone: aboveCount >= 4 ? 'positive' : aboveCount <= 1 ? 'negative' : 'neutral',
      detail: `${aboveCount}/${validMaStates.length} 在线上`,
    })
  }

  return signals
}

export function getEmotionSignals(
  m: MarketSelectorSource,
  strongestSectors: StrongestSectorRow[],
  highMarkRows: LimitStepRow[],
): StatusSignalItem[] {
  const signals: StatusSignalItem[] = []

  if (m.limit_up_count != null && m.limit_down_count != null) {
    const limitUp = Number(m.limit_up_count || 0)
    const limitDown = Number(m.limit_down_count || 0)
    const value = limitUp >= 80 && limitDown <= 10
      ? '涨停扩散'
      : limitDown >= Math.max(10, limitUp * 0.4)
        ? '退潮承压'
        : '结构分化'
    signals.push({
      label: '涨停生态',
      value,
      tone: value === '涨停扩散' ? 'positive' : value === '退潮承压' ? 'negative' : 'neutral',
      detail: `${limitUp} / ${limitDown}`,
    })
  }

  if (m.seal_rate != null) {
    const sealRate = Number(m.seal_rate)
    const value = sealRate >= 80 ? '封板稳' : sealRate <= 65 ? '炸板偏多' : '正常'
    signals.push({
      label: '封板质量',
      value,
      tone: value === '封板稳' ? 'positive' : value === '炸板偏多' ? 'negative' : 'neutral',
      detail: `封板率 ${sealRate.toFixed(1)}%`,
    })
  }

  const highestBoard = Number(m.highest_board || 0)
  if (highestBoard > 0) {
    let value = '中位推进'
    let tone: 'positive' | 'negative' | 'neutral' = 'neutral'
    if (highestBoard >= 6) {
      value = '高标打开'
      tone = 'positive'
    } else if (highestBoard <= 3) {
      value = '高度受限'
      tone = 'negative'
    }
    const highMark = highMarkRows[0]
    signals.push({
      label: '高标空间',
      value,
      tone,
      detail: highMark?.name ? `${highestBoard}板 · ${highMark.name}` : `${highestBoard}板`,
    })
  }

  if (strongestSectors.length > 0) {
    const leadSector = strongestSectors[0]
    const upNums = Number(leadSector?.up_nums || 0)
    const consNums = Number(leadSector?.cons_nums || 0)
    const value = upNums >= 10 || consNums >= 3 ? '主线集中' : '轮动分散'
    signals.push({
      label: '主线聚焦',
      value,
      tone: value === '主线集中' ? 'positive' : 'neutral',
      detail: `${leadSector?.name || '-'} ${upNums}家涨停`,
    })
  }

  return signals
}

export function extractIndex(m: MarketSelectorSource, index: string, field: 'close' | 'pct'): number | null {
  const entry = m.indices?.[index]
  if (!entry) return null
  return field === 'close' ? (entry.close ?? null) : (entry.change_pct ?? null)
}
