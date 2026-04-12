import { useEffect, useMemo } from 'react'
import { type StepProps, Row, SelectField, TextField, NumberField, CheckField, PrefillBanner, DynamicList } from './widgets'
import type { Holding, HoldingSignalItem } from '../../lib/types'

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
  const m = (stock || '').match(/\(([0-9]{6}(?:\.[A-Z]{2})?)\)/i)
  return m ? m[1].toUpperCase() : ''
}

function buildPrefillHoldingsMap(holdings: Holding[]): Map<string, { cost: unknown; price: unknown; pnl: number | null }> {
  const m = new Map<string, { cost: unknown; price: unknown; pnl: number | null }>()
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
  const c = Number(cost)
  const p = Number(cur)
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
  const holdings = useMemo(() => prefill?.holdings || [], [prefill?.holdings])
  const holdingSignals = useMemo(() => buildHoldingSignalsMap(prefill?.holding_signals?.items), [prefill?.holding_signals?.items])

  const positions: PositionItem[] = (d.positions as PositionItem[] | undefined) || (holdings.length > 0
    ? holdings.map((h) => ({
        stock: `${h.stock_name}(${h.stock_code})`,
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

  // localStorage 草稿里已有 positions 时，仍应用服务端最新现价/成本/盈亏预填（避免跑 post 后草稿不更新）
  useEffect(() => {
    if (!holdings.length) return
    const raw = (data || {}).positions as PositionItem[] | undefined
    if (!raw?.length) return
    const map = buildPrefillHoldingsMap(holdings)
    let changed = false
    const next = raw.map((item) => {
      const key = normStockCode(extractCodeFromStockLabel(String(item.stock || '')))
      const p = key ? map.get(key) : undefined
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
      if (p && p.pnl != null && item.prefill_pnl_pct == null) {
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
  }, [holdings, data?.positions, onChange, data, holdingSignals])

  return (
    <div className="space-y-6">
      {holdings.length > 0 && !d.positions && (
        <PrefillBanner>
          <span>
            已从持仓池自动导入 {holdings.length} 只股票；现价字段在跑过当日{' '}
            <code className="bg-amber-100 px-1 rounded text-gray-800">post</code> 后由收盘价预填；若当日{' '}
            <code className="bg-amber-100 px-1 rounded text-gray-800">holdings_data</code> 采集失败则无法填现价
          </span>
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
              <NumberField label="成本" value={item.cost} onChange={v => upd('cost', v)} />
              <div className="space-y-1">
                <NumberField label="现价" value={item.current_price} onChange={v => upd('current_price', v)} />
                {(() => {
                  const hint = pnlHint(item.cost, item.current_price, item.prefill_pnl_pct)
                  return hint
                    ? <div className="text-xs text-gray-500">浮动盈亏（参考） {hint}</div>
                    : null
                })()}
              </div>
              <NumberField label="仓位" value={item.position_pct} onChange={v => upd('position_pct', v)} suffix="%" />
            </Row>
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
                  : '非主线'
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
                    <div><span className="font-medium text-gray-700">互动易：</span>
                      {signal.info_signals.investor_qa.slice(0, 3).map((qa, i) => (
                        <span key={i}>{i > 0 ? '；' : ''}{qa.question ? `Q: ${qa.question.slice(0, 80)}${qa.question.length > 80 ? '…' : ''}` : ''}{qa.answer ? ` A: ${qa.answer.slice(0, 100)}${qa.answer.length > 100 ? '…' : ''}` : ''}</span>
                      ))}
                    </div>
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
