import { useState } from 'react'
import { Link } from 'react-router-dom'
import type { CognitionSummary } from '../../lib/types'

const EVIDENCE_STYLE: Record<string, string> = {
  hypothesis: 'bg-gray-100 text-gray-600',
  anecdotal: 'bg-gray-100 text-gray-600',
  supported: 'bg-indigo-100 text-indigo-700',
  strong: 'bg-indigo-200 text-indigo-800',
  validated: 'bg-emerald-100 text-emerald-700',
}

const CATEGORY_LABEL: Record<string, string> = {
  structure: '结构',
  signal: '信号',
  execution: '执行',
  sizing: '仓位',
  position: '位置',
  sentiment: '情绪',
  cycle: '周期',
  macro: '宏观',
  synthesis: '综合',
  fundamental: '基本面',
  valuation: '估值',
}

function confidenceColor(value: number): string {
  if (value >= 0.75) return 'text-indigo-700'
  if (value >= 0.5) return 'text-indigo-500'
  return 'text-gray-400'
}

interface CognitionPanelProps {
  cognitions?: CognitionSummary[]
  /** 透传给 Link 的来源标记（`?from=<stepKey>`）。
   * **当前仅透传 URL**，`/cognition` 目标页暂未消费该参数；后续若要做
   * telemetry / 按 step_key 默认筛选，需要在 `CognitionWorkbench` 中读
   * `useSearchParams().get('from')` 再补实现。 */
  stepKey?: string
  /** 默认折叠；置为 `true` 表示初始展开。 */
  defaultExpanded?: boolean
}

export default function CognitionPanel({
  cognitions,
  stepKey,
  defaultExpanded = false,
}: CognitionPanelProps) {
  const [collapsed, setCollapsed] = useState(!defaultExpanded)
  if (!cognitions?.length) return null

  const linkBase = '/cognition'
  const linkQuery = stepKey ? `?from=${encodeURIComponent(stepKey)}` : ''

  return (
    <div
      data-testid="cognition-panel"
      className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 text-sm"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium text-indigo-700">
          相关底层认知 · {cognitions.length} 条
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(c => !c)}
          className="text-xs text-indigo-500 hover:text-indigo-700"
        >
          {collapsed ? '展开' : '收起'}
        </button>
      </div>
      {!collapsed && (
        <ul className="space-y-2">
          {cognitions.map(c => {
            const categoryLabel = CATEGORY_LABEL[c.category] ?? c.category
            const evidenceClass =
              EVIDENCE_STYLE[c.evidence_level] ?? 'bg-gray-100 text-gray-600'
            const confClass = confidenceColor(c.confidence)
            return (
              <li
                key={c.cognition_id}
                className="border-l-2 border-indigo-300 pl-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <Link
                    to={`${linkBase}${linkQuery}`}
                    className="font-medium text-gray-800 hover:text-indigo-700 hover:underline text-xs"
                    title={`跳转到认知工作台查看 ${c.cognition_id}`}
                  >
                    {c.title}
                  </Link>
                  <span className={`text-xs ${confClass}`}>
                    {(c.confidence * 100).toFixed(0)}%
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11px]">
                  <span className="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700">
                    {categoryLabel}
                  </span>
                  {c.sub_category && (
                    <span className="px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-600">
                      {c.sub_category}
                    </span>
                  )}
                  <span className={`px-1.5 py-0.5 rounded ${evidenceClass}`}>
                    {c.evidence_level}
                  </span>
                  <span className="text-gray-500">
                    实例 {c.instance_count}（验证 {c.validated_count}/推翻 {c.invalidated_count}）
                  </span>
                  {c.conflict_group && (
                    <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700">
                      冲突组 {c.conflict_group}
                    </span>
                  )}
                </div>
                {c.pattern && (
                  <div className="mt-1 text-[11px] text-gray-500 line-clamp-2">
                    {c.pattern}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
