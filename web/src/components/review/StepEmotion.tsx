import { type StepProps, Section, Row, PrefillBanner, Metric, SelectField, TextField, TextareaField, DynamicList } from './widgets'
import { get, set } from './formState'

interface SentimentStockItem {
  name: string
  status: string
}

const PHASE = [
  { value: '启动', label: '启动' },
  { value: '发酵', label: '发酵' },
  { value: '高潮', label: '高潮' },
  { value: '分歧', label: '分歧' },
  { value: '衰退', label: '衰退' },
]
const SUB_CYCLE = [
  { value: '1', label: '第一周期：混沌识别' },
  { value: '2', label: '第二周期：核心发酵' },
  { value: '3', label: '第三周期：高潮分化' },
  { value: '4', label: '第四周期：衰退演变' },
]
const TRANSITION = [
  { value: '加强', label: '加强' },
  { value: '减弱', label: '减弱' },
  { value: '无法判断', label: '无法判断' },
]
const CONFIDENCE = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
]
const SENTIMENT_STATUS = [
  { value: '持续', label: '持续' },
  { value: '分歧', label: '分歧' },
  { value: '跌停', label: '跌停' },
  { value: '二波', label: '二波' },
]

export default function StepEmotion({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const ec = prefill?.emotion_cycle
  const m = prefill?.market
  const sentimentStocks = (d.sentiment_stocks as SentimentStockItem[] | undefined) || []

  const g = <T = string,>(p: string, fb?: T) => {
    const fallback = (fb ?? '') as T
    const val = get<T | undefined>(d, p, undefined)
    if (val !== undefined && val !== '') return val

    if (ec) {
      if (p === 'phase') return (ec.phase || '') as T
      if (p === 'sub_cycle') return (ec.sub_cycle != null ? String(ec.sub_cycle) : '') as T
    }
    return fallback
  }
  const s = (p: string, v: unknown) => onChange(set(d, p, v))

  return (
    <div className="space-y-6">
      <Section title="情绪阶段">
        <Row>
          <SelectField label="整体情绪" value={g('phase')} onChange={v => s('phase', v)} options={PHASE} />
          <SelectField label="子周期" value={g('sub_cycle')} onChange={v => s('sub_cycle', v)} options={SUB_CYCLE} />
        </Row>
      </Section>

      {m && (
        <PrefillBanner>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            <Metric label="涨停" value={m.limit_up_count} />
            <Metric label="跌停" value={m.limit_down_count} />
            <Metric label="封板率" value={m.seal_rate} suffix="%" />
            <Metric label="炸板率" value={m.broken_rate} suffix="%" />
            <Metric label="最高板" value={m.highest_board} suffix="板" />
          </div>
          {ec?.strength_trend && (
            <div className="mt-2 text-xs text-gray-500">
              趋势方向: <span className="font-medium text-gray-700">{ec.strength_trend}</span>
              {ec.confidence && <span className="ml-2">置信度: {ec.confidence}</span>}
            </div>
          )}
        </PrefillBanner>
      )}

      <DynamicList
        title="人气票 / 连板票表现"
        items={sentimentStocks}
        onChange={v => onChange({ ...d, sentiment_stocks: v })}
        defaultItem={{ name: '', status: '' }}
        renderItem={(item, upd) => (
          <Row>
            <TextField label="股票" value={item.name} onChange={v => upd('name', v)} />
            <SelectField label="状态" value={item.status} onChange={v => upd('status', v)} options={SENTIMENT_STATUS} />
          </Row>
        )}
      />

      <Section title="阶段连接点判断">
        <div className="space-y-4">
          <Row>
            <SelectField label="趋势方向" value={g('transition.direction')} onChange={v => s('transition.direction', v)} options={TRANSITION} />
            <SelectField label="置信度" value={g('transition.confidence')} onChange={v => s('transition.confidence', v)} options={CONFIDENCE} />
          </Row>
          <TextareaField label="判断依据" value={g('transition.reason')} onChange={v => s('transition.reason', v)} rows={2} />
        </div>
      </Section>

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="情绪相关补充..." rows={2} />
    </div>
  )
}
