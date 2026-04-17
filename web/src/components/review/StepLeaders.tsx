import { useState } from 'react'
import { type StepProps, Section, Row, SelectField, TextField, CheckField, TextareaField, DynamicList } from './widgets'
import CognitionPanel from './CognitionPanel'
import { get, set } from './formState'

interface TopLeaderItem {
  stock: string
  sector: string
  attribute_type: string
  attribute: string
  clarity: string
  position: string
  is_new: boolean
  is_prefilled?: boolean
  logic_purity?: string
  visibility?: string
  style_match?: boolean
  expectation_gap?: boolean
}

const ATTRIBUTE_TYPE = [
  { value: '走势引领', label: '走势引领' },
  { value: '最先板', label: '最先板' },
  { value: '最高标', label: '最高标' },
  { value: '容量最大', label: '容量最大' },
  { value: '基本面最正宗', label: '基本面最正宗' },
  { value: '连板最高', label: '连板最高' },
  { value: '风格化最强', label: '风格化最强' },
  { value: '其他', label: '其他' },
]
const CLARITY = [
  { value: '一眼看出', label: '一眼看出' },
  { value: '需要辨别', label: '需要辨别' },
  { value: '不清晰', label: '不清晰' },
]
const POSITION = [
  { value: '启动', label: '启动' },
  { value: '主升', label: '主升' },
  { value: '高位', label: '高位' },
  { value: '分歧', label: '分歧' },
  { value: '充分演绎', label: '充分演绎' },
]
const LOGIC_PURITY = [
  { value: '很正宗', label: '很正宗' },
  { value: '一般', label: '一般' },
  { value: '蹭概念', label: '蹭概念' },
]
const VISIBILITY = [
  { value: '极高', label: '极高' },
  { value: '中等', label: '中等' },
  { value: '低', label: '低' },
]
const PATTERN = [
  { value: '盘中半路引领', label: '盘中半路引领' },
  { value: '尾盘强分', label: '尾盘强分' },
  { value: '首阴', label: '首阴' },
  { value: '分歧低吸', label: '分歧低吸' },
  { value: '其他', label: '其他' },
]

function MultiFactorPanel({ item, upd }: { item: TopLeaderItem; upd: (k: string, v: unknown) => void }) {
  const [expanded, setExpanded] = useState(
    !!(item.logic_purity || item.visibility || item.style_match || item.expectation_gap)
  )

  return (
    <div className="border-t border-gray-100 dark:border-gray-700 pt-2 mt-1">
      <button
        type="button"
        className="text-xs text-blue-600 dark:text-blue-400 hover:underline flex items-center gap-1"
        onClick={() => setExpanded(!expanded)}
      >
        <span className={`transition-transform ${expanded ? 'rotate-90' : ''}`}>▸</span>
        引领性多因子
      </button>
      {expanded && (
        <div className="mt-2 space-y-2">
          <Row cols={2}>
            <SelectField label="逻辑正宗度" value={item.logic_purity || ''} onChange={v => upd('logic_purity', v)} options={LOGIC_PURITY} />
            <SelectField label="辨识度" value={item.visibility || ''} onChange={v => upd('visibility', v)} options={VISIBILITY} />
          </Row>
          <Row cols={2}>
            <div className="flex items-end pb-1">
              <CheckField label="匹配风格化偏好" checked={!!item.style_match} onChange={v => upd('style_match', v)} />
            </div>
            <div className="flex items-end pb-1">
              <CheckField label="预期差节点主动走强" checked={!!item.expectation_gap} onChange={v => upd('expectation_gap', v)} />
            </div>
          </Row>
        </div>
      )}
    </div>
  )
}

export default function StepLeaders({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const g = <T = string,>(p: string, fb?: T) => get<T>(d, p, (fb ?? '') as T)
  const s = (p: string, v: unknown) => onChange(set(d, p, v))
  const topLeaders = (d.top_leaders as TopLeaderItem[] | undefined) || []

  const [prefillApplied, setPrefillApplied] = useState(false)
  const prefillLeaders = prefill?.step5_leaders?.top_leaders
  if (!prefillApplied && prefillLeaders?.length && !topLeaders.length) {
    setPrefillApplied(true)
    onChange({ ...d, top_leaders: prefillLeaders as unknown as TopLeaderItem[] })
  }

  return (
    <div className="space-y-6">
      <CognitionPanel
        stepKey="step5_leaders"
        cognitions={prefill?.cognitions_by_step?.step5_leaders}
      />
      <DynamicList
        title="当前辨识度最高的最票"
        items={topLeaders}
        onChange={v => onChange({ ...d, top_leaders: v })}
        defaultItem={{
          stock: '', sector: '', attribute_type: '', attribute: '',
          clarity: '', position: '', is_new: false, is_prefilled: false,
          logic_purity: '', visibility: '', style_match: false, expectation_gap: false,
        }}
        renderItem={(item, upd) => (
          <div className={`space-y-3 ${item.is_prefilled ? 'bg-blue-50/50 dark:bg-blue-900/10 rounded-lg p-3 -m-1' : ''}`}>
            {item.is_prefilled && (
              <span className="text-[10px] font-medium text-blue-500 dark:text-blue-400 bg-blue-100 dark:bg-blue-900/30 px-1.5 py-0.5 rounded">
                系统候选
              </span>
            )}
            <Row cols={3}>
              <TextField label="股票" value={item.stock} onChange={v => upd('stock', v)} />
              <TextField label="所属板块" value={item.sector} onChange={v => upd('sector', v)} />
              <SelectField label="最的属性" value={item.attribute_type || ''} onChange={v => upd('attribute_type', v)} options={ATTRIBUTE_TYPE} />
            </Row>
            <TextField label="补充说明" value={item.attribute} onChange={v => upd('attribute', v)} placeholder="对最票属性的进一步说明..." />
            <Row cols={3}>
              <SelectField label="清晰度" value={item.clarity} onChange={v => upd('clarity', v)} options={CLARITY} />
              <SelectField label="当前位置" value={item.position} onChange={v => upd('position', v)} options={POSITION} />
              <div className="flex items-end pb-1">
                <CheckField label="新最" checked={item.is_new} onChange={v => upd('is_new', v)} />
              </div>
            </Row>
            <MultiFactorPanel item={item} upd={upd} />
          </div>
        )}
      />

      <Section title="龙头更替">
        <Row cols={2}>
          <TextField label="旧龙头" value={g('transition.old')} onChange={v => s('transition.old', v)} />
          <TextField label="新龙头" value={g('transition.new')} onChange={v => s('transition.new', v)} />
        </Row>
        <Row cols={2}>
          <TextField label="更替原因" value={g('transition.reason')} onChange={v => s('transition.reason', v)} />
          <SelectField label="启动模式" value={g('transition.pattern')} onChange={v => s('transition.pattern', v)} options={PATTERN} />
        </Row>
      </Section>

      <TextareaField label="补充备注" value={g('notes')} onChange={v => s('notes', v)} placeholder="龙头相关补充..." rows={2} />
    </div>
  )
}
