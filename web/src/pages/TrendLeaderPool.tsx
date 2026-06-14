import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { TrendLeaderRow } from '../lib/types'

// 红线提示：与后端 renderer._REDLINE 同款文案（盘后只读 [判断]）。
const REDLINE =
  '盘后只读观察清单 · 全部为 [判断] · 不构成买卖建议、不含价位、不预测点位、不写交易计划层。'

// 信号 chip 数据驱动：镜像后端 renderer._SIGNAL_LABELS 的键与文案。
// 加一个信号 = 这里加一行 + 后端 signal_hits 多一个 case（页面骨架不动）。
// caution=true 的信号用琥珀色「提示性」，其余中性蓝；一律不带买卖动作词、不出价位。
const SIGNAL_LABELS: Array<{ key: string; label: string; caution?: boolean }> = [
  { key: 'shrink_pullback_buy', label: '缩量阴线回踩' },
  { key: 'near_ma5', label: '贴MA5' },
  { key: 'overheat', label: '远离MA5(乖离过大)', caution: true },
]

/** 申万二级 + 概念分支标注（branch 非空即标，如实显示该票经哪条概念分支入主线）。 */
function sectorLabel(row: TrendLeaderRow): string {
  if (row.branch_concepts.length > 0) {
    return `${row.sw_l2}·分支:${row.branch_concepts.join('/')}`
  }
  return row.sw_l2
}

function TriggerBadge({ trigger }: { trigger: string | null }) {
  const t = trigger || '涨停'
  const cls =
    t === '涨停'
      ? 'bg-red-50 text-red-700'
      : 'bg-orange-50 text-orange-700'
  return <span className={`px-1.5 py-0.5 rounded text-xs ${cls}`}>{t}</span>
}

/** 信号链：命中=实心、未命中=ghost、Pass1 未维护=「待维护」灰条。中性色不暗示买卖。 */
function SignalChips({ hits }: { hits: TrendLeaderRow['signal_hits'] }) {
  if (hits == null) {
    return <span className="text-xs text-gray-400 italic">待维护</span>
  }
  return (
    <div className="flex flex-wrap gap-1">
      {SIGNAL_LABELS.map(({ key, label, caution }) => {
        const hit = hits[key] === true
        let cls: string
        if (!hit) cls = 'border border-gray-200 text-gray-300'
        else if (caution) cls = 'bg-amber-100 text-amber-700'
        else cls = 'bg-blue-100 text-blue-700'
        // a11y：命中/未命中此前仅靠填充与颜色传达，屏幕阅读器与色盲不可辨。
        // aria-label/title 补「命中/未命中」非视觉通道；caution 命中再加 ⚠ 非纯色彩标记。
        const name = `${label}（${hit ? '命中' : '未命中'}）`
        return (
          <span key={key} className={`px-1.5 py-0.5 rounded text-xs ${cls}`}
                title={name} aria-label={name}>
            {caution && hit ? '⚠ ' : ''}{label}
          </span>
        )
      })}
    </div>
  )
}

function EmptyCard({ text }: { text: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 px-4 py-10 text-center text-sm text-gray-400">
      {text}
    </div>
  )
}

function ActiveTable({ rows }: { rows: TrendLeaderRow[] }) {
  if (rows.length === 0) return <EmptyCard text="观察池为空" />
  return (
    <div className="bg-white rounded-lg shadow p-4 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2 pr-4">代码 / 名称</th>
            <th className="py-2 pr-4">申万二级·分支</th>
            <th className="py-2 pr-4">触发</th>
            <th className="py-2 pr-4">入池日</th>
            <th className="py-2 pr-4 text-right">在池天数</th>
            <th className="py-2 pr-0">信号链 [判断]</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code} className="border-b border-gray-50 hover:bg-gray-50 align-top">
              <td className="py-2 pr-4">
                <div className="font-medium text-gray-800">{row.name || '-'}</div>
                <div className="text-xs text-gray-400">{row.code}</div>
              </td>
              <td className="py-2 pr-4 text-gray-700">{sectorLabel(row)}</td>
              <td className="py-2 pr-4"><TriggerBadge trigger={row.entry_trigger} /></td>
              <td className="py-2 pr-4 text-gray-700">
                <div>{row.entered_date}</div>
                <div className="text-xs text-gray-400">首次加速 {row.first_limit_date}</div>
              </td>
              <td className="py-2 pr-4 text-right text-gray-700">{row.days_in_pool}</td>
              <td className="py-2 pr-0"><SignalChips hits={row.signal_hits} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ExitedTable({ rows }: { rows: TrendLeaderRow[] }) {
  if (rows.length === 0) return <EmptyCard text="暂无退池记录" />
  return (
    <div className="bg-white rounded-lg shadow p-4 overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-gray-500">
            <th className="py-2 pr-4">代码 / 名称</th>
            <th className="py-2 pr-4">申万二级</th>
            <th className="py-2 pr-4">入池日</th>
            <th className="py-2 pr-4">退池日</th>
            <th className="py-2 pr-4 text-right">在池天数</th>
            <th className="py-2 pr-0">退出原因</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code} className="border-b border-gray-50 hover:bg-gray-50 text-gray-500">
              <td className="py-2 pr-4">
                <div className="font-medium text-gray-700">{row.name || '-'}</div>
                <div className="text-xs text-gray-400">{row.code}</div>
              </td>
              <td className="py-2 pr-4">{row.sw_l2}</td>
              <td className="py-2 pr-4">{row.entered_date}</td>
              <td className="py-2 pr-4">{row.exit_date || '-'}</td>
              <td className="py-2 pr-4 text-right">{row.days_in_pool}</td>
              <td className="py-2 pr-0">
                <span className="px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-600">
                  {row.exit_reason || '—'}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function TrendLeaderPool() {
  const [tab, setTab] = useState<'active' | 'exited'>('active')
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['trend-leaders'],
    queryFn: () => api.getTrendLeaders(),
  })

  const { active, exited } = useMemo(() => {
    const rows = data ?? []
    const activeRows = rows
      .filter((r) => r.status === 'active')
      .sort((a, b) => b.days_in_pool - a.days_in_pool) // 默认在池天数降序
    const exitedRows = rows
      .filter((r) => r.status === 'exited')
      .sort((a, b) => (b.exit_date || '').localeCompare(a.exit_date || '')) // 退池日倒序
    return { active: activeRows, exited: exitedRows }
  }, [data])

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-bold text-gray-800">趋势主升观察池</h1>
        <span className="px-1.5 py-0.5 rounded text-xs bg-gray-100 text-gray-500">[判断]</span>
      </div>
      <div className="bg-amber-50 border border-amber-100 text-amber-800 text-xs rounded px-3 py-2">
        {REDLINE}
      </div>

      <div className="flex gap-1" role="tablist" aria-label="趋势池视图">
        {([['active', `在池 (${active.length})`], ['exited', `历史退池 (${exited.length})`]] as const).map(
          ([key, label]) => (
            <button
              key={key}
              role="tab"
              aria-selected={tab === key}
              onClick={() => setTab(key)}
              className={`px-3 py-1 text-xs rounded-full transition-colors ${
                tab === key ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label}
            </button>
          )
        )}
      </div>

      {isLoading && <EmptyCard text="加载中..." />}
      {isError && <EmptyCard text={`加载失败：${(error as Error)?.message ?? '未知错误'}`} />}
      {!isLoading && !isError && (
        <div role="tabpanel">
          {tab === 'active' ? <ActiveTable rows={active} /> : <ExitedTable rows={exited} />}
        </div>
      )}
    </div>
  )
}
