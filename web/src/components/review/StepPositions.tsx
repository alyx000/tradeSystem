import { useEffect, useMemo } from 'react'
import { type StepProps, Row, SelectField, TextField, NumberField, CheckField, PrefillBanner, DynamicList, InvestorQaList } from './widgets'
import CognitionPanel from './CognitionPanel'
import type { ReviewHolding, HoldingSignalItem } from '../../lib/types'

interface PositionItem {
  stock: string
  cost: number | null
  current_price: number | null
  prefill_pnl_pct: number | null
  position_pct: number | null
  in_hot_sector: boolean
  price_trend: string
  volume_vs_avg: string
  amplitude_ok: boolean
  action_plan: string
}

function normStockCode(code: string): string {
  const s = (code || '').trim().toUpperCase()
  return s.replace(/\.(SZ|SH|BJ)$/i, '')
}

function extractCodeFromStockLabel(stock: string): string {
  const m = (stock || '').trim().match(/(?:^|[(（])([0-9]{6}(?:\.[A-Z]{2})?)(?:$|[)）])/i)
  return m ? m[1].toUpperCase() : ''
}

function buildPrefillHoldingsMap(holdings: ReviewHolding[]) {
  const m = new Map<string, { cost: number | null; price: number | null; pnl: number | null }>()
  for (const h of holdings) {
    const raw = String(h.stock_code || '').trim()
    const k = normStockCode(raw)
    if (!k) continue
    const pnl = h.prefill_pnl_pct
    m.set(k, {
      cost: h.entry_price ?? null,
      price: h.current_price ?? null,
      pnl: typeof pnl === 'number' && Number.isFinite(pnl) ? pnl : null,
    })
  }
  return m
}

function buildHoldingSignalsMap(items: HoldingSignalItem[] | undefined): Map<string, HoldingSignalItem> {
  const out = new Map<string, HoldingSignalItem>()
  for (const item of items || []) {
    const key = normStockCode(item.stock_code)
    if (key) out.set(key, item)
  }
  return out
}

function pnlHint(cost: unknown, cur: unknown, prefillPnl: unknown): string | null {
  if (typeof prefillPnl === 'number' && Number.isFinite(prefillPnl)) return `${prefillPnl.toFixed(2)}%`
  if (typeof cost !== 'number' || typeof cur !== 'number') return null
  const c = cost
  const p = cur
  if (!Number.isFinite(c) || c === 0 || !Number.isFinite(p)) return null
  return `${(((p - c) / c) * 100).toFixed(2)}%`
}

const PRICE_TREND = [
  { value: '上涨', label: '上涨' },
  { value: '下跌', label: '下跌' },
  { value: '横盘', label: '横盘' },
]
const VOL_VS_AVG = [
  { value: '以上', label: '均量线以上' },
  { value: '以下', label: '均量线以下' },
]

export default function StepPositions({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const source = prefill?.holdings_source
  const holdings = useMemo(() => prefill?.holdings || [], [prefill?.holdings])
  const holdingSignals = useMemo(() => buildHoldingSignalsMap(prefill?.holding_signals?.items), [prefill?.holding_signals?.items])

  const hasDraft = Array.isArray(d.positions)
  const archiveByCode = useMemo(() => buildPrefillHoldingsMap(holdings), [holdings])
  const positions: PositionItem[] = (d.positions as PositionItem[] | undefined) || (holdings.length > 0
    ? holdings.map((h) => ({
        stock: `${h.stock_name || ''}(${h.stock_code})`,
        cost: h.entry_price,
        current_price: h.current_price,
        prefill_pnl_pct: h.prefill_pnl_pct ?? null,
        position_pct: null,
        in_hot_sector: (() => {
          const signal = holdingSignals.get(normStockCode(h.stock_code))
          return Boolean(signal?.theme_signals.is_main_theme || signal?.theme_signals.is_strongest_sector)
        })(),
        price_trend: '',
        volume_vs_avg: holdingSignals.get(normStockCode(h.stock_code))?.technical_signals.volume_vs_ma5 || '',
        amplitude_ok: false,
        action_plan: '',
      }))
    : [])

  // 用同日归档补齐空字段；保留旧草稿和人工填写的价格。
  useEffect(() => {
    if (!holdings.length) return
    const raw = (data || {}).positions as PositionItem[] | undefined
    if (!raw?.length) return
    let changed = false
    const next = raw.map((item) => {
      const key = normStockCode(extractCodeFromStockLabel(String(item.stock || '')))
      const p = key ? archiveByCode.get(key) : undefined
      const signal = key ? holdingSignals.get(key) : undefined
      const np = { ...item }
      if (!p && !signal) return item
      if (p && typeof p.price === 'number' && Number.isFinite(p.price) && item.current_price == null) {
        np.current_price = p.price
        changed = true
      }
      if (p && typeof p.cost === 'number' && Number.isFinite(p.cost) && item.cost == null) {
        np.cost = p.cost
        changed = true
      }
      if (p && p.pnl != null && item.prefill_pnl_pct == null && np.current_price === p.price && np.cost === p.cost) {
        np.prefill_pnl_pct = p.pnl
        changed = true
      }
      if (signal && item.volume_vs_avg === '') {
        np.volume_vs_avg = signal.technical_signals.volume_vs_ma5 || ''
        changed = changed || np.volume_vs_avg !== ''
      }
      if (signal && item.in_hot_sector === false && (signal.theme_signals.is_main_theme || signal.theme_signals.is_strongest_sector)) {
        np.in_hot_sector = true
        changed = true
      }
      return np
    })
    if (changed) onChange({ ...(data || {}), positions: next })
  }, [holdings, archiveByCode, onChange, data, holdingSignals])

  return (
    <div className="space-y-6">
      <CognitionPanel
        stepKey="step7_positions"
        cognitions={prefill?.cognitions_by_step?.step7_positions}
      />
      {prefill && (
        <PrefillBanner>
          {holdings.length > 0
            ? `${prefill.date} · ${source === 'quote_snapshot' ? '持仓报价快照（名单覆盖待核）' : '盘后持仓归档'}共 ${holdings.length} 只，收盘价覆盖 ${holdings.filter(h => h.current_price != null).length}/${holdings.length}`
            : `${prefill.date} · 缺少可核验的当日持仓记录`}
        </PrefillBanner>
      )}

      <DynamicList
        title="持仓检视"
        items={positions}
        onChange={v => onChange({ ...d, positions: v })}
        defaultItem={{ stock: '', cost: null, current_price: null, prefill_pnl_pct: null, position_pct: null, in_hot_sector: false, price_trend: '', volume_vs_avg: '', amplitude_ok: false, action_plan: '' }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={4}>
              <TextField label="股票" value={item.stock} onChange={v => upd('stock', v)} />
              <NumberField label={hasDraft ? '草稿成本' : '成本'} value={item.cost} onChange={v => upd('cost', v)} />
              <div className="space-y-1">
                <NumberField label={hasDraft ? '草稿价' : '归档收盘'} value={item.current_price} onChange={v => upd('current_price', v)} />
                {(() => {
                  const hint = pnlHint(item.cost, item.current_price, hasDraft ? null : item.prefill_pnl_pct)
                  return hint
                    ? <div className="text-xs text-gray-500">{hasDraft ? '草稿成本与价格计算' : '浮动盈亏（参考）'} {hint}</div>
                    : null
                })()}
              </div>
              <NumberField label="仓位" value={item.position_pct} onChange={v => upd('position_pct', v)} suffix="%" />
            </Row>
            {hasDraft && prefill && (() => {
              const archived = archiveByCode.get(normStockCode(extractCodeFromStockLabel(item.stock)))
              if (!archived) return <div className="text-xs text-gray-500">草稿持仓 · 当日归档未核验此票</div>
              const archivePnl = pnlHint(archived.cost, archived.price, archived.pnl)
              return <div className="text-xs text-gray-500">当日归档收盘 {archived.price ?? '待核'} · 归档成本 {archived.cost ?? '待核'} · 归档盈亏 {archivePnl ?? '待核'}</div>
            })()}
            <Row cols={4}>
              <div className="flex items-end pb-1">
                <CheckField label="在热点板块" checked={item.in_hot_sector} onChange={v => upd('in_hot_sector', v)} />
              </div>
              <SelectField label="价格趋势" value={item.price_trend} onChange={v => upd('price_trend', v)} options={PRICE_TREND} />
              <SelectField label="成交量vs均量" value={item.volume_vs_avg} onChange={v => upd('volume_vs_avg', v)} options={VOL_VS_AVG} />
              <div className="flex items-end pb-1">
                <CheckField label="振幅满足" checked={item.amplitude_ok} onChange={v => upd('amplitude_ok', v)} />
              </div>
            </Row>
            {(() => {
              const key = normStockCode(extractCodeFromStockLabel(String(item.stock || '')))
              const signal = key ? holdingSignals.get(key) : undefined
              if (!signal) return null
              const themeText = signal.theme_signals.is_main_theme
                ? `主线：${signal.theme_signals.main_theme_name || '是'}`
                : signal.theme_signals.is_strongest_sector
                  ? `最强板块：${signal.theme_signals.strongest_sector_name || '是'}`
                  : signal.theme_signals.is_main_theme === false && signal.theme_signals.is_strongest_sector === false
                    ? '非主线'
                    : '当日归属待核'
              const boardStrength = signal.technical_signals.sector_change_pct != null
                ? `板块涨跌幅 ${signal.technical_signals.sector_change_pct >= 0 ? '+' : ''}${signal.technical_signals.sector_change_pct.toFixed(2)}%`
                : '板块强弱：-'
              const technicalText = [
                signal.technical_signals.above_ma5 === true ? '站上MA5' : signal.technical_signals.above_ma5 === false ? '跌破MA5' : null,
                signal.technical_signals.above_ma10 === true ? '站上MA10' : signal.technical_signals.above_ma10 === false ? '跌破MA10' : null,
                signal.technical_signals.volume_vs_ma5 ? `量能${signal.technical_signals.volume_vs_ma5}均量` : null,
                signal.technical_signals.turnover_rate != null
                  ? `换手率 ${signal.technical_signals.turnover_rate.toFixed(2)}%${signal.technical_signals.turnover_status ? `（${signal.technical_signals.turnover_status}）` : ''}`
                  : null,
              ].filter(Boolean).join(' / ')
              const holdingMeta = holdings.find((h) => normStockCode(h.stock_code) === key)
              const stopTargetText = [
                holdingMeta?.stop_loss != null ? `止损价 ${holdingMeta.stop_loss}` : null,
                holdingMeta?.target_price != null ? `止盈价 ${holdingMeta.target_price}` : null,
              ].filter(Boolean).join(' / ')
              const riskText = signal.risk_flags?.length
                ? signal.risk_flags.map((flag) => flag.label).join(' / ')
                : ''
              const eventText = [
                signal.event_signals.is_st ? 'ST' : null,
                signal.event_signals.has_disclosure_plan && signal.event_signals.disclosure_dates[0]?.ann_date
                  ? `披露计划 ${signal.event_signals.disclosure_dates[0].ann_date}`
                  : null,
                signal.event_signals.has_recent_announcement ? '近 7 日有公告' : null,
              ].filter(Boolean).join(' / ')
              return (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-gray-600 space-y-1">
                  <div><span className="font-medium text-gray-700">主线归属：</span>{themeText}</div>
                  <div><span className="font-medium text-gray-700">板块强弱：</span>{boardStrength}</div>
                  <div><span className="font-medium text-gray-700">技术位置：</span>{technicalText || '-'}</div>
                  <div><span className="font-medium text-gray-700">止损止盈：</span>{stopTargetText || '-'}</div>
                  <div><span className="font-medium text-gray-700">边界提示：</span>{riskText || '-'}</div>
                  <div><span className="font-medium text-gray-700">事件风险：</span>{eventText || '-'}</div>
                  <div><span className="font-medium text-gray-700">昨日计划：</span>{signal.latest_task?.action_plan || '-'}</div>
                  {signal.info_signals?.investor_qa?.length ? (
                    <InvestorQaList items={signal.info_signals.investor_qa} />
                  ) : null}
                  {signal.info_signals?.research_reports?.length ? (
                    <div><span className="font-medium text-gray-700">研报：</span>
                      {signal.info_signals.research_reports.slice(0, 2).map((rr, i) => (
                        <span key={i}>{i > 0 ? '；' : ''}{rr.institution || ''}{rr.rating ? `「${rr.rating}」` : ''}{rr.target_price ? ` 目标价${rr.target_price}` : ''}</span>
                      ))}
                    </div>
                  ) : null}
                  {signal.info_signals?.news?.length ? (
                    <div><span className="font-medium text-gray-700">新闻：</span>
                      {signal.info_signals.news.slice(0, 2).map((n, i) => (
                        <span key={i}>{i > 0 ? '；' : ''}{n.title || ''}{n.time ? `（${n.time}）` : ''}</span>
                      ))}
                    </div>
                  ) : null}
                </div>
              )
            })()}
            <TextField label="操作计划" value={item.action_plan} onChange={v => upd('action_plan', v)} placeholder="持有 / 加仓 / 减仓 / 止损..." />
          </div>
        )}
      />
    </div>
  )
}
