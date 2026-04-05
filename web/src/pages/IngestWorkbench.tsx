import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

const STAGES = ['pre_core', 'post_core', 'post_extended', 'watchlist', 'backfill']

function todayString() {
  return new Date().toISOString().slice(0, 10)
}

export default function IngestWorkbench() {
  const queryClient = useQueryClient()
  const [date, setDate] = useState(todayString())
  const [stage, setStage] = useState('post_core')
  const [selectedInterface, setSelectedInterface] = useState('')
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { data: interfaces = [], isLoading: interfacesLoading } = useQuery({
    queryKey: ['ingest-interfaces'],
    queryFn: () => api.listIngestInterfaces(),
  })

  const { data: inspectData, isLoading: inspectLoading } = useQuery({
    queryKey: ['ingest-inspect', date],
    queryFn: () => api.inspectIngest(date),
  })

  const { data: retrySummary } = useQuery({
    queryKey: ['ingest-retry'],
    queryFn: () => api.getIngestRetrySummary(),
  })

  const filteredInterfaces = useMemo(
    () => interfaces.filter((item: any) => item.stage === stage),
    [interfaces, stage]
  )

  const runStageMutation = useMutation({
    mutationFn: () => api.runIngestStage({ stage, date, input_by: 'web' }),
    onSuccess: (result: any) => {
      setError(null)
      setFeedback(`已执行 stage：${result.stage}，记录 ${result.recorded_runs} 条 run`)
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect', date] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const runInterfaceMutation = useMutation({
    mutationFn: () => api.runIngestInterface({ name: selectedInterface, date, input_by: 'web' }),
    onSuccess: (result: any) => {
      setError(null)
      setFeedback(`已执行接口：${result.name}，状态 ${result.run.status}`)
      queryClient.invalidateQueries({ queryKey: ['ingest-inspect', date] })
      queryClient.invalidateQueries({ queryKey: ['ingest-retry'] })
    },
    onError: (e: Error) => {
      setFeedback(null)
      setError(e.message)
    },
  })

  const runs = inspectData?.runs || []
  const errors = inspectData?.errors || []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">采集诊断工作台</h1>
          <p className="text-sm text-gray-500 mt-1">
            查看接口注册表、执行采集、检查 run / error / retry 摘要。
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <h2 className="text-base font-semibold text-gray-800">运行采集</h2>
          <div>
            <label htmlFor="ingest-date" className="block text-sm font-medium text-gray-700 mb-1">日期</label>
            <input
              id="ingest-date"
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="ingest-stage" className="block text-sm font-medium text-gray-700 mb-1">Stage</label>
            <select
              id="ingest-stage"
              value={stage}
              onChange={(e) => {
                setStage(e.target.value)
                setSelectedInterface('')
              }}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              {STAGES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => runStageMutation.mutate()}
            disabled={runStageMutation.isPending}
            className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
          >
            {runStageMutation.isPending ? '执行中...' : '执行 Stage'}
          </button>

          <div className="pt-2 border-t border-gray-100">
            <label htmlFor="ingest-interface" className="block text-sm font-medium text-gray-700 mb-1">单接口</label>
            <select
              id="ingest-interface"
              value={selectedInterface}
              onChange={(e) => setSelectedInterface(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm"
            >
              <option value="">请选择接口</option>
              {filteredInterfaces.map((item: any) => (
                <option key={item.interface_name} value={item.interface_name}>
                  {item.interface_name}
                </option>
              ))}
            </select>
            <button
              onClick={() => runInterfaceMutation.mutate()}
              disabled={!selectedInterface || runInterfaceMutation.isPending}
              className="mt-3 w-full px-4 py-2 bg-green-600 text-white rounded-md text-sm font-medium hover:bg-green-700 disabled:opacity-50"
            >
              {runInterfaceMutation.isPending ? '执行中...' : '执行单接口'}
            </button>
          </div>

          {feedback && <p className="text-sm text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">{feedback}</p>}
          {error && <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">{error}</p>}
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-base font-semibold text-gray-800 mb-3">重试摘要</h2>
          <p className="text-3xl font-bold text-orange-600">
            {retrySummary?.retryable_count ?? 0}
          </p>
          <p className="text-sm text-gray-500 mt-1">未解决可重试错误数</p>
          <div className="mt-3 space-y-2">
            {(retrySummary?.groups || []).slice(0, 5).map((item: any) => (
              <div key={`${item.biz_date}-${item.interface_name}`} className="text-sm bg-gray-50 rounded px-3 py-2">
                <span className="font-medium">{item.interface_name}</span>
                <span className="text-gray-500 ml-2">{item.biz_date}</span>
                <span className="text-orange-600 ml-2">× {item.error_count}</span>
              </div>
            ))}
            {(!retrySummary?.groups || retrySummary.groups.length === 0) && (
              <p className="text-sm text-gray-500">暂无待重试项</p>
            )}
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <h2 className="text-base font-semibold text-gray-800 mb-3">接口注册表</h2>
          {interfacesLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : (
            <div className="space-y-2 max-h-80 overflow-y-auto">
              {interfaces.map((item: any) => (
                <div key={item.interface_name} className="border border-gray-100 rounded px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <code className="text-sm font-medium text-gray-800">{item.interface_name}</code>
                    <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-600">
                      {item.stage}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">{item.provider_method}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-800">运行记录</h2>
            <span className="text-xs text-gray-500">{date}</span>
          </div>
          {inspectLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : runs.length === 0 ? (
            <p className="text-sm text-gray-500">该日期暂无采集记录</p>
          ) : (
            <div className="space-y-2 max-h-[28rem] overflow-y-auto">
              {runs.map((run: any) => (
                <div key={run.run_id} className="border border-gray-100 rounded px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <code className="text-sm font-medium text-gray-800">{run.interface_name}</code>
                    <span
                      className={`text-xs px-2 py-0.5 rounded ${
                        run.status === 'success'
                          ? 'bg-green-100 text-green-700'
                          : run.status === 'failed'
                          ? 'bg-red-100 text-red-700'
                          : 'bg-gray-100 text-gray-600'
                      }`}
                    >
                      {run.status}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    {run.stage} · {run.provider} · rows {run.row_count}
                  </p>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-800">错误记录</h2>
            <span className="text-xs text-gray-500">{date}</span>
          </div>
          {inspectLoading ? (
            <p className="text-sm text-gray-500">加载中...</p>
          ) : errors.length === 0 ? (
            <p className="text-sm text-gray-500">该日期暂无错误</p>
          ) : (
            <div className="space-y-2 max-h-[28rem] overflow-y-auto">
              {errors.map((item: any) => (
                <div key={`${item.run_id}-${item.id}`} className="border border-red-100 rounded px-3 py-2 bg-red-50/40">
                  <div className="flex items-center justify-between gap-3">
                    <code className="text-sm font-medium text-gray-800">{item.interface_name}</code>
                    <span className="text-xs px-2 py-0.5 rounded bg-red-100 text-red-700">
                      {item.error_type}
                    </span>
                  </div>
                  <p className="text-sm text-red-700 mt-1">{item.error_message}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
