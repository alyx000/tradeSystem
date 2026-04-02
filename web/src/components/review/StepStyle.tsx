import { type StepProps, get, set, Section, Row, PrefillBanner, Metric, SelectField, NumberField, TextField, TextareaField } from './widgets'

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
  { key: 'new_theme', label: '新题材首板(10cm)', premium: true },
  { key: 'board_20cm', label: '20cm首板溢价', premium: true },
  { key: 'second_board', label: '二板涨停溢价', premium: true },
  { key: 'board_30cm', label: '30cm首板溢价', premium: true },
  { key: 'consecutive', label: '连板赚钱效应', premium: false },
  { key: 'big_cap', label: '容量票赚钱效应', premium: true },
  { key: 'first_open', label: '涨停一字首次开板', premium: true },
]

/** cap_preference.relative -> preference.cap_size 映射 */
function inferCapSize(relative: string | undefined): string | undefined {
  if (!relative) return undefined
  if (relative.includes('大盘')) return '大盘股'
  if (relative.includes('小盘')) return '小盘股'
  if (relative.includes('均衡')) return '中盘股'
  return undefined
}

/** 由溢价率推导 effect 方向 */
function inferEffect(prem: number | null | undefined): string | undefined {
  if (prem == null) return undefined
  return prem > 0 ? '正' : prem < 0 ? '负' : '中性'
}

export default function StepStyle({ data, onChange, prefill }: StepProps) {
  const d = data || {}
  const m = prefill?.market
  const sf = m?.style_factors as Record<string, any> | undefined
  const capPref = sf?.cap_preference as Record<string, any> | undefined
  const boardPref = sf?.board_preference as Record<string, any> | undefined
  const premSnap = sf?.premium_snapshot as Record<string, any> | undefined
  const premTrend = sf?.premium_trend as Record<string, any> | undefined
  const switchSigs = sf?.switch_signals as string[] | undefined

  const prevStyle = (() => {
    try {
      return JSON.parse(prefill?.prev_review?.step4_style || '{}')
    } catch {
      return {}
    }
  })()

  // 从 premium_snapshot 的 premium_median 中 fallback 溢价率标量
  const snapMedian = (key: string): number | null | undefined => {
    const g = premSnap?.[key]
    return typeof g?.premium_median === 'number' ? g.premium_median : undefined
  }

  // 溢价率：优先 market 标量列，再 fallback 到 snapshot median
  const premMap: Record<string, number | null | undefined> = m ? {
    new_theme: m.premium_10cm ?? snapMedian('first_board_10cm'),
    board_20cm: m.premium_20cm ?? snapMedian('first_board_20cm'),
    second_board: m.premium_second_board ?? snapMedian('second_board'),
    board_30cm: m.premium_30cm ?? snapMedian('first_board_30cm'),
    big_cap: snapMedian('capacity_top10'),
    first_open: snapMedian('yizi_first_open'),
  } : {}

  const g = (p: string, fb: any = '') => {
    const val = get(d, p, undefined)
    if (val !== undefined && val !== '') return val

    if (m) {
      // 溢价数值
      if (p === 'effects.new_theme.premium') return premMap.new_theme ?? null
      if (p === 'effects.board_20cm.premium') return premMap.board_20cm ?? null
      if (p === 'effects.second_board.premium') return premMap.second_board ?? null
      if (p === 'effects.board_30cm.premium') return premMap.board_30cm ?? null
      if (p === 'effects.big_cap.premium') return premMap.big_cap ?? null
      if (p === 'effects.first_open.premium') return premMap.first_open ?? null

      // 效应方向：由溢价正负推导
      if (p.endsWith('.effect')) {
        const itemKey = p.slice('effects.'.length, -'.effect'.length)
        const eff = inferEffect(premMap[itemKey])
        if (eff) return eff
      }

      // 市值偏好：从 cap_preference.relative 推导
      if (p === 'preference.cap_size') {
        const inferred = inferCapSize(capPref?.relative)
        if (inferred) return inferred
      }
    }

    // 审美偏好 + 无溢价的风格效应：从前日复盘 fallback
    if (p.startsWith('preference.') || p.startsWith('effects.')) {
      const prevVal = get(prevStyle, p, undefined)
      if (prevVal !== undefined && prevVal !== '') return prevVal
    }

    return fb
  }
  const s = (p: string, v: any) => onChange(set(d, p, v))

  const hasPremium = m && (m.premium_10cm != null || m.premium_20cm != null || m.premium_30cm != null || m.premium_second_board != null)
  const hasStyleFactors = !!(capPref || boardPref || switchSigs?.length || premTrend)

  return (
    <div className="space-y-6">
      {/* 溢价率快照 + 系统推导提示 */}
      {(hasPremium || hasStyleFactors) && (
        <PrefillBanner>
          {hasPremium && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Metric label="10cm首板溢价" value={premMap.new_theme} suffix="%" />
              <Metric label="20cm首板溢价" value={premMap.board_20cm} suffix="%" />
              <Metric label="30cm首板溢价" value={premMap.board_30cm} suffix="%" />
              <Metric label="二板溢价" value={premMap.second_board} suffix="%" />
              {premMap.big_cap != null && <Metric label="容量票溢价" value={premMap.big_cap} suffix="%" />}
              {premMap.first_open != null && <Metric label="一字首次开板溢价" value={premMap.first_open} suffix="%" />}
            </div>
          )}

          {/* 市值/板型偏好摘要 */}
          {(capPref || boardPref) && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-2">
              {capPref && (
                <>
                  <Metric label="市值偏好" value={capPref.relative} />
                  <Metric label="沪深300" value={capPref.csi300_chg} suffix="%" />
                  <Metric label="中证1000" value={capPref.csi1000_chg} suffix="%" />
                  <Metric label="价差" value={capPref.spread} suffix="%" />
                </>
              )}
              {boardPref && !capPref && (
                <>
                  <Metric label="板型主导" value={boardPref.dominant_type} />
                  {boardPref.pct_10cm != null && <Metric label="10cm占比" value={boardPref.pct_10cm} suffix="%" />}
                  {boardPref.pct_20cm != null && <Metric label="20cm占比" value={boardPref.pct_20cm} suffix="%" />}
                  {boardPref.pct_30cm != null && <Metric label="30cm占比" value={boardPref.pct_30cm} suffix="%" />}
                </>
              )}
            </div>
          )}

          {/* 若 capPref 存在，再展示一行 boardPref */}
          {capPref && boardPref && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-2">
              <Metric label="板型主导" value={boardPref.dominant_type} />
              {boardPref.pct_10cm != null && <Metric label="10cm占比" value={boardPref.pct_10cm} suffix="%" />}
              {boardPref.pct_20cm != null && <Metric label="20cm占比" value={boardPref.pct_20cm} suffix="%" />}
              {boardPref.pct_30cm != null && <Metric label="30cm占比" value={boardPref.pct_30cm} suffix="%" />}
            </div>
          )}

          {/* 溢价率趋势方向 */}
          {premTrend && (
            <div className="mt-2 flex items-center gap-4 text-xs text-gray-600">
              <span>溢价趋势：<strong>{premTrend.direction}</strong></span>
              {Array.isArray(premTrend.first_board_median_5d) && premTrend.first_board_median_5d.length > 0 && (
                <span>首板5日中位：{premTrend.first_board_median_5d.join(' → ')}</span>
              )}
            </div>
          )}

          {/* 审美切换信号 */}
          {switchSigs && switchSigs.length > 0 && (
            <div className="mt-2 space-y-0.5">
              {switchSigs.map((sig, i) => (
                <div key={i} className="text-xs text-amber-700">⚡ {sig}</div>
              ))}
            </div>
          )}

          <div className="mt-1.5 text-xs text-amber-600">
            {capPref && '市值偏好已根据价差自动推导；'}
            溢价率与效应方向已从盘后数据自动预填
            {prefill?.prev_review && `；审美偏好参考前日复盘（${prefill.prev_review.date}）`}
          </div>
        </PrefillBanner>
      )}

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
              <div className="w-36 shrink-0 text-sm text-gray-600 py-1.5">{item.label}</div>
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
