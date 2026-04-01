import { type StepProps, get, set, Section, Row, SelectField, NumberField, TextField, TextareaField } from './widgets'

const CAP_SIZE = [
  { value: '大盘股', label: '大盘股' },
  { value: '中盘股', label: '中盘股' },
  { value: '小盘股', label: '小盘股' },
]
const STYLE = [
  { value: '基本面', label: '基本面' },
  { value: '情绪面', label: '情绪面' },
  { value: '趋势', label: '趋势' },
  { value: '连板', label: '连板' },
]
const TREND_OR_BOARD = [
  { value: '趋势票主导', label: '趋势票主导' },
  { value: '连板票主导', label: '连板票主导' },
  { value: '混合', label: '混合' },
]
const EFFECT = [
  { value: '正', label: '正' },
  { value: '负', label: '负' },
  { value: '中性', label: '中性' },
]

const PROFIT_ITEMS = [
  { key: 'new_theme', label: '新题材首板', premium: false },
  { key: 'board_20cm', label: '20cm首板溢价', premium: true },
  { key: 'second_board', label: '二板涨停溢价', premium: true },
  { key: 'board_30cm', label: '30cm首板溢价', premium: true },
  { key: 'consecutive', label: '连板赚钱效应', premium: false },
  { key: 'big_cap', label: '容量票赚钱效应', premium: false },
  { key: 'first_open', label: '涨停一字首次开板', premium: false },
]

export default function StepStyle({ data, onChange }: StepProps) {
  const d = data || {}
  const g = (p: string, fb: any = '') => get(d, p, fb)
  const s = (p: string, v: any) => onChange(set(d, p, v))

  return (
    <div className="space-y-6">
      <Section title="当前市场审美偏好">
        <Row cols={3}>
          <SelectField label="市值偏好" value={g('preference.cap_size')} onChange={v => s('preference.cap_size', v)} options={CAP_SIZE} />
          <SelectField label="风格偏好" value={g('preference.style')} onChange={v => s('preference.style', v)} options={STYLE} />
          <SelectField label="主导类型" value={g('preference.trend_or_board')} onChange={v => s('preference.trend_or_board', v)} options={TREND_OR_BOARD} />
        </Row>
      </Section>

      <Section title="各风格赚钱效应">
        <div className="space-y-2">
          {PROFIT_ITEMS.map(item => (
            <div key={item.key} className="flex flex-wrap items-end gap-3 border-b border-gray-100 pb-2">
              <div className="w-32 shrink-0 text-sm text-gray-600 py-1.5">{item.label}</div>
              <div className="w-24">
                <SelectField label="" value={g(`effects.${item.key}.effect`)} onChange={v => s(`effects.${item.key}.effect`, v)} options={EFFECT} placeholder="效应" />
              </div>
              {item.premium && (
                <div className="w-24">
                  <NumberField label="" value={g(`effects.${item.key}.premium`, null)} onChange={v => s(`effects.${item.key}.premium`, v)} suffix="%" placeholder="溢价" />
                </div>
              )}
              <div className="flex-1 min-w-[200px]">
                <TextField label="" value={g(`effects.${item.key}.note`)} onChange={v => s(`effects.${item.key}.note`, v)} placeholder="备注" />
              </div>
            </div>
          ))}
        </div>
      </Section>

      <TextareaField label="异动监管反馈" value={g('regulatory_feedback')} onChange={v => s('regulatory_feedback', v)} placeholder="监管层面的异动反馈..." rows={2} />
      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="风格化相关补充..." rows={2} />
    </div>
  )
}
