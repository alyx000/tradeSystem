import { useEffect } from 'react'
import { type StepProps, Row, SelectField, TextField, NumberField, CheckField, PrefillBanner, DynamicList } from './widgets'

function normStockCode(code: string): string {
  const s = (code || '').trim().toUpperCase()
  return s.replace(/\.(SZ|SH|BJ)$/i, '')
}

function extractCodeFromStockLabel(stock: string): string {
  const m = (stock || '').match(/\(([0-9]{6}(?:\.[A-Z]{2})?)\)/i)
  return m ? m[1].toUpperCase() : ''
}

function buildPrefillHoldingsMap(holdings: any[]): Map<string, { cost: unknown; price: unknown; pnl: number | null }> {
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
  const holdings = prefill?.holdings || []

  const positions = d.positions || (holdings.length > 0
    ? holdings.map((h: any) => ({
        stock: `${h.stock_name}(${h.stock_code})`,
        cost: h.entry_price,
        current_price: h.current_price,
        prefill_pnl_pct: h.prefill_pnl_pct ?? null,
        position_pct: null,
        in_hot_sector: false,
        price_trend: '',
        volume_vs_avg: '',
        amplitude_ok: false,
        action_plan: '',
      }))
    : [])

  // localStorage 草稿里已有 positions 时，仍应用服务端最新现价/成本/盈亏预填（避免跑 post 后草稿不更新）
  useEffect(() => {
    if (!holdings.length) return
    const raw = (data || {}).positions as any[] | undefined
    if (!raw?.length) return
    const map = buildPrefillHoldingsMap(holdings)
    let changed = false
    const next = raw.map((item) => {
      const key = normStockCode(extractCodeFromStockLabel(String(item.stock || '')))
      const p = key ? map.get(key) : undefined
      if (!p) return item
      const np = { ...item }
      if (p.price != null && p.price !== '' && (item.current_price == null || item.current_price === '')) {
        np.current_price = p.price
        changed = true
      }
      if (p.cost != null && p.cost !== '' && (item.cost == null || item.cost === '')) {
        np.cost = p.cost
        changed = true
      }
      if (p.pnl != null && (item.prefill_pnl_pct == null || item.prefill_pnl_pct === '')) {
        np.prefill_pnl_pct = p.pnl
        changed = true
      }
      return np
    })
    if (changed) onChange({ ...(data || {}), positions: next })
  }, [prefill?.holdings, data?.positions, onChange, data, holdings.length])

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
            <TextField label="操作计划" value={item.action_plan} onChange={v => upd('action_plan', v)} placeholder="持有 / 加仓 / 减仓 / 止损..." />
          </div>
        )}
      />
    </div>
  )
}
