import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import {
  getIngestHealthStatus,
  getIngestHealthStatusClasses,
  getIngestHealthStatusReason,
} from '../lib/ingestHealthStatus'
import type { CalendarEvent, CommandDocItem, HoldingTaskItem, IngestHealthSummary } from '../lib/types'

const today = localDateString()
const DASHBOARD_QUICKSTART: CommandDocItem[] = [
  { command: 'make bootstrap', description: '首次安装依赖并启用本地 hooks' },
  { command: 'make today-close', description: '执行今日盘后流程' },
  { command: 'make today-open', description: '执行今日盘前流程' },
  { command: 'make check', description: '执行完整检查' },
  { command: 'make dev', description: '启动开发环境' },
  { command: 'make market-open DATE=YYYY-MM-DD', description: '打开市场看板' },
  { command: 'make today-ingest-health', description: '查看今日采集健康摘要' },
]

function fmtPct(v: number | null | undefined) {
  if (v == null) return '-'
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

export default function Dashboard() {
  const [loadHoldingTasks, setLoadHoldingTasks] = useState(false)
  const [loadIngestHealth, setLoadIngestHealth] = useState(false)
  const holdingTasksRef = useRef<HTMLDivElement | null>(null)
  const ingestHealthRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.IntersectionObserver === 'undefined') {
      queueMicrotask(() => {
        setLoadHoldingTasks(true)
        setLoadIngestHealth(true)
      })
      return
    }

    const observer = new window.IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue
          if (entry.target === holdingTasksRef.current) setLoadHoldingTasks(true)
          if (entry.target === ingestHealthRef.current) setLoadIngestHealth(true)
        }
      },
      { rootMargin: '240px 0px' }
    )

    if (holdingTasksRef.current) observer.observe(holdingTasksRef.current)
    if (ingestHealthRef.current) observer.observe(ingestHealthRef.current)

    return () => observer.disconnect()
  }, [])

  const { data: review } = useQuery({ queryKey: ['review', today], queryFn: () => api.getReview(today) })
  const { data: holdings } = useQuery({ queryKey: ['holdings'], queryFn: api.getHoldings })
  const { data: calendar } = useQuery({
    queryKey: ['calendar', today],
    queryFn: () => api.getCalendarRange(today, today),
  })
  const { data: market } = useQuery({
    queryKey: ['market', today],
    queryFn: () => api.getMarket(today),
  })
  const { data: holdingTasks } = useQuery({
    queryKey: ['holding-tasks', today, 'open'],
    queryFn: () => api.listHoldingTasks(today, 'open'),
    enabled: loadHoldingTasks,
  })
  const { data: ingestHealth } = useQuery({
    queryKey: ['ingest-health-dashboard', today],
    queryFn: () => api.getIngestDashboardHealthSummary(today, 7),
    enabled: loadIngestHealth,
  })
  const ingestHealthCore = ingestHealth?.core ?? null
  const ingestHealthExtended = ingestHealth?.extended ?? null

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-gray-800">仪表盘 - {today}</h1>

      {/* 市场摘要卡片 */}
      {market?.available && (
        <Link to={`/market/${today}`} className="block">
          <div className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-medium text-gray-500">今日市场</h2>
              <span className="text-xs text-blue-500">查看详情 &rarr;</span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
              <div>
                <div className="text-xs text-gray-400">上证</div>
                <div className={`text-sm font-semibold ${market.sh_index_change_pct == null ? 'text-gray-500' : market.sh_index_change_pct >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {fmtPct(market.sh_index_change_pct)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">深证</div>
                <div className={`text-sm font-semibold ${market.sz_index_change_pct == null ? 'text-gray-500' : market.sz_index_change_pct >= 0 ? 'text-red-600' : 'text-green-600'}`}>
                  {fmtPct(market.sz_index_change_pct)}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">成交额</div>
                <div className="text-sm font-semibold text-gray-800">
                  {market.total_amount != null
                    ? market.total_amount >= 10000
                      ? `${(market.total_amount / 10000).toFixed(2)}万亿`
                      : `${market.total_amount.toFixed(0)}亿`
                    : '-'}
                </div>
              </div>
              <div>
                <div className="text-xs text-gray-400">涨停</div>
                <div className="text-sm font-semibold text-red-600">{market.limit_up_count ?? '-'}</div>
              </div>
              <div>
                <div className="text-xs text-gray-400">跌停</div>
                <div className="text-sm font-semibold text-green-600">{market.limit_down_count ?? '-'}</div>
              </div>
            </div>
          </div>
        </Link>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">复盘状态</h2>
          <div className="text-lg font-semibold">
            {review?.exists ? (
              <span className="text-green-600">已完成</span>
            ) : (
              <Link to={`/review/${today}`} className="text-amber-600 hover:underline">
                待复盘 →
              </Link>
            )}
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">持仓数量</h2>
          <div className="text-lg font-semibold text-gray-800">
            {holdings?.length ?? 0} 只
          </div>
        </div>

        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-2">今日日历事件</h2>
          <div className="text-lg font-semibold text-gray-800">
            {calendar?.length ?? 0} 条
          </div>
        </div>
      </div>

      <div ref={holdingTasksRef}>
        {holdingTasks && holdingTasks.length > 0 && (
          <div className="bg-white rounded-lg shadow p-4">
            <div className="flex items-center justify-between mb-3 gap-3">
              <div>
                <h2 className="text-sm font-medium text-gray-500">未完成持仓计划</h2>
                <p className="text-xs text-gray-400 mt-1">来自上一交易日第 7 步复盘的持仓操作计划。</p>
              </div>
              <Link to={`/holding-tasks?date=${today}&status=open`} className="text-xs text-blue-500 hover:underline">
                打开任务页 →
              </Link>
            </div>
            <div className="space-y-2">
              {holdingTasks.slice(0, 5).map((task: HoldingTaskItem) => (
                <div key={task.id || `${task.trade_date}-${task.stock_code}`} className="rounded border border-gray-200 bg-gray-50 px-3 py-2">
                  <div className="text-sm text-gray-800">{task.stock_name || task.stock_code}</div>
                  <div className="text-xs text-gray-500 mt-1">{task.trade_date} · {task.action_plan}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div ref={ingestHealthRef}>
        {(ingestHealthCore || ingestHealthExtended) && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {ingestHealthCore && (
              <IngestHealthCard
                title="采集健康 · 盘后核心"
                description="近 7 天 post_core 视角，优先看主链路是否稳定。"
                health={ingestHealthCore}
                href={`/ingest?date=${today}&health_sort=streak`}
              />
            )}
            {ingestHealthExtended && (
              <IngestHealthCard
                title="采集健康 · 盘后扩展"
                description="近 7 天 post_extended 视角，适合排查扩展事实层接口。"
                health={ingestHealthExtended}
                href={`/ingest?date=${today}&stage=post_extended&health_sort=streak`}
              />
            )}
          </div>
        )}
      </div>

      {calendar && calendar.length > 0 && (
        <div className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-3">今日投资日历</h2>
          <ul className="space-y-2">
            {calendar.map((e: CalendarEvent) => (
              <li key={e.id} className="flex items-center gap-2 text-sm">
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${
                  e.impact === 'high' ? 'bg-red-100 text-red-700' :
                  e.impact === 'medium' ? 'bg-amber-100 text-amber-700' :
                  'bg-gray-100 text-gray-600'
                }`}>
                  {e.impact || '一般'}
                </span>
                <span className="text-gray-800">{e.event}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex items-center justify-between mb-3 gap-3">
          <div>
            <h2 className="text-sm font-medium text-gray-500">命令速查</h2>
            <p className="text-xs text-gray-400 mt-1">
              基于仓库统一入口维护的高频摘要，完整列表见 <code className="text-gray-600">docs/commands.md</code>
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400">每日高频</span>
            <Link
              to={`/ingest?date=${today}`}
              className="text-xs text-blue-500 hover:underline"
            >
              打开健康视图 →
            </Link>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {DASHBOARD_QUICKSTART.map((item: CommandDocItem) => (
            <div key={item.command} className="rounded border border-gray-200 bg-gray-50 px-3 py-2">
              <div className="font-mono text-xs text-gray-800">{item.command}</div>
              <div className="text-xs text-gray-500 mt-1">{item.description}</div>
            </div>
          ))}
        </div>
      </div>

    </div>
  )
}

function topIngestRiskLabel(health: IngestHealthSummary) {
  const top = health.top_failed_interfaces?.[0]
  if (!top) return '暂无异常'
  const label = top.interface_label || top.interface_name || '未知接口'
  if ((top.consecutive_failure_days ?? 0) > 1) {
    return `${label} · 连续失败 ${top.consecutive_failure_days} 天`
  }
  return `${label} · 失败 ${top.failure_count ?? 0} 次`
}

function IngestHealthCard({
  title,
  description,
  health,
  href,
}: {
  title: string
  description: string
  health: IngestHealthSummary
  href: string
}) {
  const statusLabel = getIngestHealthStatus(health)
  const statusReason = getIngestHealthStatusReason(health)

  return (
    <Link to={href} className="block">
      <div className="bg-white rounded-lg shadow p-4 hover:shadow-md transition-shadow">
        <div className="flex items-center justify-between mb-3 gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-medium text-gray-500">{title}</h2>
              <span className={`inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium ${getIngestHealthStatusClasses(statusLabel)}`}>
                {statusLabel}
              </span>
            </div>
            <p className="text-xs text-gray-400 mt-1">{description}</p>
            <p className="text-xs text-gray-500 mt-1">{statusReason}</p>
          </div>
          <span className="text-xs text-blue-500">查看详情 &rarr;</span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <div className="text-xs text-gray-400">未解决失败</div>
            <div className={`text-sm font-semibold ${(health.unresolved_failures ?? 0) > 0 ? 'text-orange-600' : 'text-green-600'}`}>
              {health.unresolved_failures ?? 0}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400">失败接口数</div>
            <div className="text-sm font-semibold text-gray-800">{health.failed_interface_count ?? 0}</div>
          </div>
          <div>
            <div className="text-xs text-gray-400">最需关注</div>
            <div className="text-sm font-semibold text-gray-800">{topIngestRiskLabel(health)}</div>
          </div>
        </div>
      </div>
    </Link>
  )
}
