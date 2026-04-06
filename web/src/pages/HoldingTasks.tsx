import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import type { HoldingTaskItem } from '../lib/types'

const today = localDateString()

type HoldingTaskFilter = 'open' | 'done' | 'ignored'

const FILTER_OPTIONS: Array<{ value: HoldingTaskFilter; label: string; hint: string }> = [
  { value: 'open', label: '未完成', hint: '需要今天继续跟踪或处理的计划。' },
  { value: 'done', label: '已完成', hint: '已经执行或确认结束的计划。' },
  { value: 'ignored', label: '已忽略', hint: '已决定不继续跟踪的计划。' },
]

function taskStatusLabel(status: HoldingTaskItem['status']): string {
  if (status === 'done') return '已完成'
  if (status === 'ignored') return '已忽略'
  return '未完成'
}

function taskStatusClasses(status: HoldingTaskItem['status']): string {
  if (status === 'done') return 'bg-green-100 text-green-700'
  if (status === 'ignored') return 'bg-gray-100 text-gray-600'
  return 'bg-amber-100 text-amber-700'
}

export default function HoldingTasks() {
  const queryClient = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const initialDate = searchParams.get('date') || today
  const initialStatus = searchParams.get('status')
  const [taskDate, setTaskDate] = useState(initialDate)
  const [taskFilter, setTaskFilter] = useState<HoldingTaskFilter>(
    initialStatus === 'done' || initialStatus === 'ignored' || initialStatus === 'open' ? initialStatus : 'open'
  )

  const { data: tasks, isLoading } = useQuery({
    queryKey: ['holding-tasks', taskDate, taskFilter],
    queryFn: () => api.listHoldingTasks(taskDate, taskFilter),
  })

  const taskMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'done' | 'ignored' }) => api.updateHoldingTask(id, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['holding-tasks'] })
      queryClient.invalidateQueries({ queryKey: ['holding-signals'] })
    },
  })

  const currentOption = FILTER_OPTIONS.find((option) => option.value === taskFilter) || FILTER_OPTIONS[0]

  function updateFilters(nextDate: string, nextStatus: HoldingTaskFilter) {
    setTaskDate(nextDate)
    setTaskFilter(nextStatus)
    const nextParams = new URLSearchParams()
    if (nextDate && nextDate !== today) nextParams.set('date', nextDate)
    if (nextStatus !== 'open') nextParams.set('status', nextStatus)
    setSearchParams(nextParams)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">持仓任务</h1>
          <p className="mt-1 text-sm text-gray-500">
            集中查看第 7 步复盘写入的持仓计划，并跟踪完成状态。
          </p>
        </div>
        <Link to="/holdings" className="text-sm text-blue-600 hover:underline">
          返回持仓页 →
        </Link>
      </div>

      <div className="rounded-lg bg-white p-4 shadow space-y-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="text-sm font-medium text-gray-700">当前视角：{currentOption.label}</div>
            <div className="mt-1 text-xs text-gray-500">
              {taskDate} · {currentOption.hint}
            </div>
          </div>
          <div className="flex flex-col gap-3 md:items-end">
            <div className="flex items-center gap-2">
              <label htmlFor="holding-task-date" className="text-xs text-gray-500">
                计划日期
              </label>
              <input
                id="holding-task-date"
                type="date"
                value={taskDate}
                onChange={(event) => updateFilters(event.target.value || today, taskFilter)}
                className="rounded border border-gray-200 px-2 py-1 text-xs text-gray-700"
              />
              <button
                type="button"
                onClick={() => updateFilters(today, taskFilter)}
                className="text-xs text-blue-600 hover:underline"
              >
                回到今天
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
            {FILTER_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => updateFilters(taskDate, option.value)}
                className={`rounded-full px-3 py-1 text-xs ${
                  taskFilter === option.value
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {option.label}
              </button>
            ))}
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="rounded border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400">
            加载中...
          </div>
        ) : !tasks?.length ? (
          <div className="rounded border border-dashed border-gray-200 px-4 py-8 text-center text-sm text-gray-400">
            当前筛选下暂无持仓任务
          </div>
        ) : (
          <div className="space-y-3">
            {tasks.map((task) => (
              <div key={`${task.id || task.stock_code}-${task.trade_date}-${task.action_plan}`} className="rounded-lg border border-gray-100 px-4 py-3">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-gray-800">{task.stock_name || task.stock_code}</span>
                      <span className="font-mono text-xs text-gray-500">{task.stock_code}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${taskStatusClasses(task.status)}`}>
                        {taskStatusLabel(task.status)}
                      </span>
                    </div>
                    <div className="text-xs text-gray-500">{task.trade_date} · {task.source || 'review_step7'}</div>
                    <div className="text-sm text-gray-700">{task.action_plan}</div>
                  </div>
                  {task.id && task.status === 'open' ? (
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => taskMut.mutate({ id: task.id!, status: 'done' })}
                        className="rounded px-2.5 py-1 text-xs text-green-700 hover:bg-green-50"
                      >
                        标记完成
                      </button>
                      <button
                        type="button"
                        onClick={() => taskMut.mutate({ id: task.id!, status: 'ignored' })}
                        className="rounded px-2.5 py-1 text-xs text-gray-500 hover:bg-gray-50"
                      >
                        忽略
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
