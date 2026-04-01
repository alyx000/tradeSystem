import { type StepProps, get, set, Section, Row, PrefillBanner, SelectField, TextField, TagsField, TextareaField, DynamicList } from './widgets'

const IMPACT = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
]
const CONFIDENCE = [
  { value: '高', label: '高' },
  { value: '中', label: '中' },
  { value: '低', label: '低' },
  { value: '看不懂', label: '看不懂' },
]

export default function StepPlan({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const g = (p: string, fb: any = '') => get(d, p, fb)
  const s = (p: string, v: any) => onChange(set(d, p, v))
  const calEvents = prefill?.calendar_events || []

  return (
    <div className="space-y-6">
      <Section title="三位一体重点因子">
        <div className="space-y-4">
          <TextField label="当前最重要的因子" value={g('key_factor')} onChange={v => s('key_factor', v)} placeholder="大盘节点 / 板块轮动 / 风格切换..." />
          <TagsField label="次要因子" value={g('secondary_factors', [])} onChange={v => s('secondary_factors', v)} />
        </div>
      </Section>

      <DynamicList
        title="关注方向"
        items={d.watch_directions || []}
        onChange={v => onChange({ ...d, watch_directions: v })}
        defaultItem={{ direction: '', reason: '', target_stocks: [], entry_condition: '' }}
        renderItem={(item, upd) => (
          <div className="space-y-3">
            <Row>
              <TextField label="方向" value={item.direction} onChange={v => upd('direction', v)} />
              <TextField label="原因" value={item.reason} onChange={v => upd('reason', v)} />
            </Row>
            <Row>
              <TagsField label="目标票" value={item.target_stocks || []} onChange={v => upd('target_stocks', v)} />
              <TextField label="介入条件" value={item.entry_condition} onChange={v => upd('entry_condition', v)} />
            </Row>
          </div>
        )}
      />

      <DynamicList
        title="风险提示"
        items={d.risks || []}
        onChange={v => onChange({ ...d, risks: v })}
        defaultItem={{ description: '', impact: '' }}
        renderItem={(item, upd) => (
          <Row>
            <TextField label="风险描述" value={item.description} onChange={v => upd('description', v)} />
            <SelectField label="影响程度" value={item.impact} onChange={v => upd('impact', v)} options={IMPACT} />
          </Row>
        )}
      />

      <Section title="操作纪律">
        <Row cols={3}>
          <TextField label="最大仓位" value={g('discipline.max_position')} onChange={v => s('discipline.max_position', v)} />
          <TextField label="止损规则" value={g('discipline.stop_loss_rule')} onChange={v => s('discipline.stop_loss_rule', v)} />
          <TextField label="备注" value={g('discipline.note')} onChange={v => s('discipline.note', v)} />
        </Row>
      </Section>

      <Section title="综合结论">
        <div className="space-y-4">
          <TextField label="一句话总结" value={g('summary.one_sentence')} onChange={v => s('summary.one_sentence', v)} placeholder="一句话概括今日市场..." />
          <TextareaField label="三位一体结论" value={g('summary.trinity')} onChange={v => s('summary.trinity', v)} rows={3} />
          <SelectField label="次日判断信心" value={g('summary.confidence')} onChange={v => s('summary.confidence', v)} options={CONFIDENCE} />
        </div>
      </Section>

      {calEvents.length > 0 && (
        <PrefillBanner>
          <div className="text-xs text-gray-500 mb-1">当日投资日历事件</div>
          <ul className="space-y-1">
            {calEvents.map((e: any) => (
              <li key={e.id} className="flex items-center gap-2 text-sm">
                <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                  e.impact === 'high' ? 'bg-red-100 text-red-700' :
                  e.impact === 'medium' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-600'
                }`}>
                  {e.impact || '一般'}
                </span>
                <span className="text-gray-700">{e.event}</span>
              </li>
            ))}
          </ul>
        </PrefillBanner>
      )}
    </div>
  )
}
