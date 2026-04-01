import { type StepProps, Row, SelectField, TextField, NumberField, CheckField, PrefillBanner, DynamicList } from './widgets'

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
        position_pct: null,
        in_hot_sector: false,
        price_trend: '',
        volume_vs_avg: '',
        amplitude_ok: false,
        action_plan: '',
      }))
    : [])

  return (
    <div className="space-y-6">
      {holdings.length > 0 && !d.positions && (
        <PrefillBanner>
          <span>已从持仓池自动导入 {holdings.length} 只股票</span>
        </PrefillBanner>
      )}

      <DynamicList
        title="持仓检视"
        items={positions}
        onChange={v => onChange({ ...d, positions: v })}
        defaultItem={{ stock: '', cost: null, current_price: null, position_pct: null, in_hot_sector: false, price_trend: '', volume_vs_avg: '', amplitude_ok: false, action_plan: '' }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row cols={4}>
              <TextField label="股票" value={item.stock} onChange={v => upd('stock', v)} />
              <NumberField label="成本" value={item.cost} onChange={v => upd('cost', v)} />
              <NumberField label="现价" value={item.current_price} onChange={v => upd('current_price', v)} />
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
