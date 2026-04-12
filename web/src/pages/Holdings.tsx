import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { type SetStateAction, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { localDateString } from '../lib/date'
import type { Holding, HoldingCreateInput, HoldingSignalItem, HoldingTaskItem, HoldingUpdateInput } from '../lib/types'

const today = localDateString()

function normStockCode(code: string): string {
  return String(code || '').trim().toUpperCase().replace(/\.(SZ|SH|BJ)$/i, '')
}

function formatFloatPnl(entry: number | null | undefined, cur: number | null | undefined): string {
  if (entry == null || cur == null || !Number.isFinite(entry) || entry === 0) return '—'
  const pct = ((cur - entry) / entry) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

type HoldingTaskFilter = 'open' | 'done' | 'ignored'

const TASK_FILTER_OPTIONS: Array<{ value: HoldingTaskFilter; label: string }> = [
  { value: 'open', label: '未完成' },
  { value: 'done', label: '已完成' },
  { value: 'ignored', label: '已忽略' },
]

function taskStatusLabel(status: HoldingTaskItem['status']): string {
  if (status === 'done') return '已完成'
  if (status === 'ignored') return '已忽略'
  return '未完成'
}

function parseNullableText(value: string): string | null {
  const t = String(value ?? '').trim()
  return t === '' ? null : t
}

/** 与表单解析一致地将 API 可能返回的 number / string 规范为 number | null，避免基线 `!==` 误判。 */
function coerceHoldingNumeric(raw: unknown): number | null {
  if (raw == null || raw === '') return null
  const n = typeof raw === 'number' ? raw : Number(String(raw).trim())
  if (!Number.isFinite(n) || n < 0) return null
  return n
}

/** 进入行内编辑时的快照，用于只提交相对基线有变化的字段，避免误覆盖未编辑的列。 */
function holdingToEditBaseline(h: Holding): {
  stop_loss: number | null
  target_price: number | null
  position_ratio: number | null
  entry_reason: string | null
  note: string | null
} {
  return {
    stop_loss: coerceHoldingNumeric(h.stop_loss),
    target_price: coerceHoldingNumeric(h.target_price),
    position_ratio: coerceHoldingNumeric(h.position_ratio),
    entry_reason: parseNullableText(h.entry_reason ?? ''),
    note: parseNullableText(h.note ?? ''),
  }
}

const emptyEditForm = {
  stop_loss: '',
  target_price: '',
  position_ratio: '',
  entry_reason: '',
  note: '',
}

const EDIT_NUMERIC_VALIDATION_MSG =
  '止损、止盈、仓位须为非负数字，无法解析时请清空或改正后再保存。'

export default function Holdings() {
  const queryClient = useQueryClient()
  const [showForm, setShowForm] = useState(false)
  const [taskFilter, setTaskFilter] = useState<HoldingTaskFilter>('open')
  const [editingId, setEditingId] = useState<number | null>(null)
  const editBaselineRef = useRef<ReturnType<typeof holdingToEditBaseline> | null>(null)
  const [editForm, setEditForm] = useState({ ...emptyEditForm })
  const [editValidationError, setEditValidationError] = useState<string | null>(null)
  const [form, setForm] = useState({
    stock_code: '',
    stock_name: '',
    entry_price: '',
    shares: '',
    sector: '',
    stop_loss: '',
    target_price: '',
    position_ratio: '',
    entry_reason: '',
    note: '',
  })

  const { data: holdings, isLoading } = useQuery({
    queryKey: ['holdings'],
    queryFn: api.getHoldings,
    // 行内编辑时避免窗口聚焦/重连触发 refetch，降低「基线快照 vs 已刷新列表」不一致概率
    refetchOnWindowFocus: editingId === null,
    refetchOnReconnect: editingId === null,
  })
  const { data: holdingSignals } = useQuery({
    queryKey: ['holding-signals', today],
    queryFn: () => api.getHoldingSignals(today),
  })
  const { data: holdingTasks } = useQuery({
    queryKey: ['holding-tasks', today, taskFilter],
    queryFn: () => api.listHoldingTasks(today, taskFilter),
  })

  const signalMap = new Map(
    (holdingSignals?.items || []).map((item: HoldingSignalItem) => [normStockCode(item.stock_code), item])
  )

  const createMut = useMutation({
    mutationFn: (data: HoldingCreateInput) => api.createHolding(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['holdings'] })
      setShowForm(false)
      setForm({
        stock_code: '',
        stock_name: '',
        entry_price: '',
        shares: '',
        sector: '',
        stop_loss: '',
        target_price: '',
        position_ratio: '',
        entry_reason: '',
        note: '',
      })
    },
  })

  const deleteMut = useMutation({
    mutationFn: (id: number) => api.deleteHolding(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['holdings'] }),
  })

  const updateMut = useMutation({
    mutationFn: ({ id, data }: { id: number; data: HoldingUpdateInput }) => api.updateHolding(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['holdings'] })
      setEditingId(null)
      setEditForm({ ...emptyEditForm })
      editBaselineRef.current = null
      setEditValidationError(null)
    },
  })

  function patchEditForm(next: SetStateAction<typeof emptyEditForm>) {
    setEditValidationError(null)
    setEditForm(next)
  }

  const taskMut = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'done' | 'ignored' }) => api.updateHoldingTask(id, { status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['holding-signals', today] })
      queryClient.invalidateQueries({ queryKey: ['holding-tasks'] })
    },
  })

  function startEditing(h: Holding) {
    setEditingId(h.id)
    setEditValidationError(null)
    editBaselineRef.current = holdingToEditBaseline(h)
    setEditForm({
      stop_loss: h.stop_loss != null ? String(h.stop_loss) : '',
      target_price: h.target_price != null ? String(h.target_price) : '',
      position_ratio: h.position_ratio != null ? String(h.position_ratio) : '',
      entry_reason: h.entry_reason ?? '',
      note: h.note ?? '',
    })
  }

  function cancelEditing() {
    setEditingId(null)
    editBaselineRef.current = null
    setEditForm({ ...emptyEditForm })
    setEditValidationError(null)
  }

  function parseNullableNumber(value: string): number | null {
    const trimmed = String(value || '').trim()
    if (!trimmed) return null
    const num = Number(trimmed)
    if (!Number.isFinite(num) || num < 0) return Number.NaN
    return num
  }

  function saveEditing(hid: number) {
    const baseline = editBaselineRef.current
    if (!baseline) return

    const stopLoss = parseNullableNumber(editForm.stop_loss)
    const targetPrice = parseNullableNumber(editForm.target_price)
    const positionRatio = parseNullableNumber(editForm.position_ratio)
    if ([stopLoss, targetPrice, positionRatio].some((value) => Number.isNaN(value))) {
      setEditValidationError(EDIT_NUMERIC_VALIDATION_MSG)
      return
    }
    setEditValidationError(null)

    const entryReason = parseNullableText(editForm.entry_reason)
    const note = parseNullableText(editForm.note)

    const data: HoldingUpdateInput = {}
    if (stopLoss !== baseline.stop_loss) data.stop_loss = stopLoss
    if (targetPrice !== baseline.target_price) data.target_price = targetPrice
    if (positionRatio !== baseline.position_ratio) data.position_ratio = positionRatio
    if (entryReason !== baseline.entry_reason) data.entry_reason = entryReason
    if (note !== baseline.note) data.note = note

    if (Object.keys(data).length === 0) {
      setEditingId(null)
      editBaselineRef.current = null
      setEditForm({ ...emptyEditForm })
      setEditValidationError(null)
      return
    }

    updateMut.mutate({ id: hid, data })
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-800">持仓池</h1>
          <p className="text-xs text-gray-500 mt-0.5">
            现价由 <code className="bg-gray-100 px-1 rounded">main.py post</code> 盘后任务从当日收盘价写入数据库；未跑过盘前则显示为「—」
          </p>
        </div>
        <button onClick={() => setShowForm(!showForm)}
          className="bg-blue-600 text-white px-3 py-1.5 rounded text-sm hover:bg-blue-700 shrink-0 self-start sm:self-center">
          {showForm ? '取消' : '添加持仓'}
        </button>
      </div>

      {showForm && (
        <div className="bg-white rounded-lg shadow p-4 grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-3">
          <input placeholder="代码" value={form.stock_code}
            onChange={e => setForm(p => ({ ...p, stock_code: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="名称" value={form.stock_name}
            onChange={e => setForm(p => ({ ...p, stock_name: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="成本价" type="number" value={form.entry_price}
            onChange={e => setForm(p => ({ ...p, entry_price: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="数量" type="number" value={form.shares}
            onChange={e => setForm(p => ({ ...p, shares: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="止损价" type="number" value={form.stop_loss}
            onChange={e => setForm(p => ({ ...p, stop_loss: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="止盈价" type="number" value={form.target_price}
            onChange={e => setForm(p => ({ ...p, target_price: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="仓位占比%" type="number" value={form.position_ratio}
            onChange={e => setForm(p => ({ ...p, position_ratio: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm" />
          <input placeholder="买入原因" value={form.entry_reason}
            onChange={e => setForm(p => ({ ...p, entry_reason: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm col-span-2 xl:col-span-4" />
          <input placeholder="备注" value={form.note}
            onChange={e => setForm(p => ({ ...p, note: e.target.value }))}
            className="border rounded px-2 py-1.5 text-sm col-span-2 xl:col-span-3" />
          <button onClick={() => createMut.mutate({
            stock_code: form.stock_code, stock_name: form.stock_name,
            entry_price: parseFloat(form.entry_price) || undefined,
            shares: parseInt(form.shares) || undefined,
            sector: form.sector || undefined,
            stop_loss: parseFloat(form.stop_loss) || undefined,
            target_price: parseFloat(form.target_price) || undefined,
            position_ratio: parseFloat(form.position_ratio) || undefined,
            entry_reason: form.entry_reason || undefined,
            note: form.note || undefined,
          })}
            className="bg-green-600 text-white rounded px-3 py-1.5 text-sm hover:bg-green-700">
            确认
          </button>
        </div>
      )}

      <div className="bg-white rounded-lg shadow p-4 space-y-4">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="text-base font-semibold text-gray-800">计划任务</h2>
            <p className="text-xs text-gray-500 mt-1">显示第 7 步复盘写入的持仓计划，便于次日跟踪与处理。</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {TASK_FILTER_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => setTaskFilter(option.value)}
                className={`rounded-full px-3 py-1 text-xs ${
                  taskFilter === option.value
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {option.label}
              </button>
            ))}
            <Link to="/holding-tasks" className="text-xs text-blue-600 hover:underline ml-1">
              打开任务页 →
            </Link>
          </div>
        </div>
        {!holdingTasks?.length ? (
          <div className="rounded border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-400">
            当前筛选下暂无计划任务
          </div>
        ) : (
          <div className="space-y-3">
            {holdingTasks.map((task) => (
              <div key={`${task.id || task.stock_code}-${task.trade_date}-${task.action_plan}`} className="rounded-lg border border-gray-100 px-4 py-3">
                <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                  <div className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium text-gray-800">{task.stock_name || task.stock_code}</span>
                      <span className="font-mono text-xs text-gray-500">{task.stock_code}</span>
                      <span className={`rounded-full px-2 py-0.5 text-xs ${
                        task.status === 'done'
                          ? 'bg-green-100 text-green-700'
                          : task.status === 'ignored'
                            ? 'bg-gray-100 text-gray-600'
                            : 'bg-amber-100 text-amber-700'
                      }`}>
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

      <div className="bg-white rounded-lg shadow overflow-hidden">
        {editingId != null && editValidationError ? (
          <div
            role="alert"
            className="px-4 py-2 text-xs text-red-700 bg-red-50 border-b border-red-100"
          >
            {editValidationError}
          </div>
        ) : null}
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="px-4 py-3 text-left">代码</th>
              <th className="px-4 py-3 text-left">名称</th>
              <th className="px-4 py-3 text-right">成本价</th>
              <th className="px-4 py-3 text-right">现价（盘后收盘）</th>
              <th className="px-4 py-3 text-right">浮动盈亏</th>
              <th className="px-4 py-3 text-right">数量</th>
              <th className="px-4 py-3 text-right">止损 / 止盈</th>
              <th className="px-4 py-3 text-right">仓位</th>
              <th className="px-4 py-3 text-left">风险</th>
              <th className="px-4 py-3 text-left">公告 / 披露</th>
              <th className="px-4 py-3 text-left">信息面</th>
              <th className="px-4 py-3 text-left">主线归属</th>
              <th className="px-4 py-3 text-left">技术位</th>
              <th className="px-4 py-3 text-left">昨日计划</th>
              <th className="px-4 py-3 text-left">买入原因 / 备注</th>
              <th className="px-4 py-3 text-left">状态</th>
              <th className="px-4 py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {isLoading ? (
              <tr><td colSpan={17} className="px-4 py-8 text-center text-gray-400">加载中...</td></tr>
            ) : holdings?.length === 0 ? (
              <tr><td colSpan={17} className="px-4 py-8 text-center text-gray-400">暂无持仓</td></tr>
            ) : (
              holdings?.map((h: Holding) => (
                <tr key={h.id} className="hover:bg-gray-50 align-top">
                  <td className="px-4 py-3 font-mono">{h.stock_code}</td>
                  <td className="px-4 py-3">{h.stock_name}</td>
                  <td className="px-4 py-3 text-right">{h.entry_price ?? '-'}</td>
                  <td className="px-4 py-3 text-right font-mono">{h.current_price ?? '—'}</td>
                  <td className={`px-4 py-3 text-right font-mono ${
                    h.current_price != null && h.entry_price
                      ? (h.current_price - h.entry_price) >= 0 ? 'text-red-600' : 'text-green-600'
                      : 'text-gray-400'
                  }`}>
                    {formatFloatPnl(h.entry_price, h.current_price)}
                  </td>
                  <td className="px-4 py-3 text-right">{h.shares ?? '-'}</td>
                  <td className="px-4 py-3 text-right text-xs text-gray-600">
                    {editingId === h.id ? (
                      <div className="space-y-2">
                        <input
                          aria-label={`止损价-${h.stock_code}`}
                          type="number"
                          min="0"
                          value={editForm.stop_loss}
                          onChange={(e) => patchEditForm((prev) => ({ ...prev, stop_loss: e.target.value }))}
                          className="w-24 rounded border px-2 py-1 text-right text-xs"
                        />
                        <input
                          aria-label={`止盈价-${h.stock_code}`}
                          type="number"
                          min="0"
                          value={editForm.target_price}
                          onChange={(e) => patchEditForm((prev) => ({ ...prev, target_price: e.target.value }))}
                          className="w-24 rounded border px-2 py-1 text-right text-xs"
                        />
                      </div>
                    ) : (
                      <>
                        <div>{h.stop_loss != null ? `止损 ${h.stop_loss}` : '止损 —'}</div>
                        <div>{h.target_price != null ? `止盈 ${h.target_price}` : '止盈 —'}</div>
                      </>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right text-xs text-gray-600">
                    {editingId === h.id ? (
                      <input
                        aria-label={`仓位占比-${h.stock_code}`}
                        type="number"
                        min="0"
                        value={editForm.position_ratio}
                        onChange={(e) => patchEditForm((prev) => ({ ...prev, position_ratio: e.target.value }))}
                        className="w-20 rounded border px-2 py-1 text-right text-xs"
                      />
                    ) : (
                      h.position_ratio != null ? `${h.position_ratio}%` : '—'
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {(() => {
                        const signal = signalMap.get(normStockCode(h.stock_code))
                        if (!signal?.risk_flags?.length) return <span className="text-xs text-gray-400">—</span>
                        return signal.risk_flags.map((flag) => (
                          <span
                            key={`${flag.level}-${flag.label}`}
                            className={`px-2 py-0.5 rounded text-xs ${
                              flag.level === 'high'
                                ? 'bg-red-100 text-red-700'
                                : flag.level === 'medium'
                                  ? 'bg-yellow-100 text-yellow-700'
                                  : 'bg-green-100 text-green-700'
                            }`}
                            title={flag.reason}
                          >
                            {flag.label}
                          </span>
                        ))
                      })()}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {(() => {
                      const signal = signalMap.get(normStockCode(h.stock_code))
                      if (!signal) return '—'
                      const announcements = signal.event_signals.recent_announcements || []
                      const disclosures = signal.event_signals.disclosure_dates || []
                      if (!announcements.length && !disclosures.length) return '—'
                      return (
                        <div className="space-y-1">
                          {announcements.slice(0, 2).map((item) => (
                            <div key={`ann-${item.ann_date}-${item.title}`}>
                              公告：{item.title || '—'}
                              {item.ann_date ? `（${item.ann_date}）` : ''}
                            </div>
                          ))}
                          {disclosures.slice(0, 2).map((item) => (
                            <div key={`disc-${item.ann_date}-${item.report_end}`}>
                              披露：{item.ann_date || '—'}
                              {item.report_end ? ` · ${item.report_end}` : ''}
                            </div>
                          ))}
                        </div>
                      )
                    })()}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {(() => {
                      const signal = signalMap.get(normStockCode(h.stock_code))
                      if (!signal?.info_signals) return '—'
                      const { investor_qa, research_reports, news } = signal.info_signals
                      if (!investor_qa?.length && !research_reports?.length && !news?.length) return '—'
                      return (
                        <div className="space-y-1">
                          {investor_qa?.slice(0, 1).map((qa, i) => (
                            <div key={`qa-${i}`}>互动易：{qa.question ? `${qa.question.slice(0, 30)}${qa.question.length > 30 ? '…' : ''}` : '—'}</div>
                          ))}
                          {research_reports?.slice(0, 1).map((rr, i) => (
                            <div key={`rr-${i}`}>研报：{rr.institution || ''}{rr.rating ? `「${rr.rating}」` : ''}{rr.target_price ? ` ${rr.target_price}` : ''}</div>
                          ))}
                          {news?.slice(0, 1).map((n, i) => (
                            <div key={`news-${i}`}>新闻：{n.title ? `${n.title.slice(0, 30)}${n.title.length > 30 ? '…' : ''}` : '—'}</div>
                          ))}
                        </div>
                      )
                    })()}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {(() => {
                      const signal = signalMap.get(normStockCode(h.stock_code))
                      if (!signal) return '—'
                      const lines = [
                        signal.theme_signals.is_main_theme ? `主线: ${signal.theme_signals.main_theme_name || '是'}` : null,
                        signal.theme_signals.is_strongest_sector ? `最强板块: ${signal.theme_signals.strongest_sector_name || '是'}` : null,
                        signal.theme_signals.sector_flow_confirmed ? `资金确认: ${String(signal.theme_signals.sector_flow_source || '').toUpperCase()}` : null,
                      ].filter(Boolean)
                      return lines.length ? lines.map((line) => <div key={line}>{line}</div>) : '—'
                    })()}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {(() => {
                      const signal = signalMap.get(normStockCode(h.stock_code))
                      if (!signal) return '—'
                      const lines = [
                        signal.technical_signals.above_ma5 === true ? '站上 MA5' : signal.technical_signals.above_ma5 === false ? '跌破 MA5' : null,
                        signal.technical_signals.above_ma10 === true ? '站上 MA10' : signal.technical_signals.above_ma10 === false ? '跌破 MA10' : null,
                        signal.technical_signals.volume_vs_ma5 ? `量在均量${signal.technical_signals.volume_vs_ma5}` : null,
                        signal.technical_signals.turnover_rate != null
                          ? `换手 ${signal.technical_signals.turnover_rate.toFixed(2)}%${signal.technical_signals.turnover_status ? `（${signal.technical_signals.turnover_status}）` : ''}`
                          : null,
                      ].filter(Boolean)
                      return lines.length ? lines.map((line) => <div key={line}>{line}</div>) : '—'
                    })()}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600">
                    {(() => {
                      const signal = signalMap.get(normStockCode(h.stock_code))
                      if (!signal?.latest_task?.action_plan) return '—'
                      return (
                        <div className="space-y-2">
                          <div>{signal.latest_task.action_plan}</div>
                          {signal.latest_task.id ? (
                            <div className="flex gap-2">
                              <button
                                type="button"
                                onClick={() => taskMut.mutate({ id: signal.latest_task!.id!, status: 'done' })}
                                className="text-xs text-green-700 hover:text-green-900"
                              >
                                标记完成
                              </button>
                              <button
                                type="button"
                                onClick={() => taskMut.mutate({ id: signal.latest_task!.id!, status: 'ignored' })}
                                className="text-xs text-gray-500 hover:text-gray-700"
                              >
                                忽略
                              </button>
                            </div>
                          ) : null}
                        </div>
                      )
                    })()}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-600 max-w-[160px] min-w-0">
                    {editingId === h.id ? (
                      <div className="space-y-2 min-w-0">
                        <input
                          aria-label={`买入原因-${h.stock_code}`}
                          value={editForm.entry_reason}
                          onChange={(e) => patchEditForm((prev) => ({ ...prev, entry_reason: e.target.value }))}
                          placeholder="买入原因"
                          className="w-full min-w-0 rounded border px-2 py-1 text-xs"
                        />
                        <input
                          aria-label={`备注-${h.stock_code}`}
                          value={editForm.note}
                          onChange={(e) => patchEditForm((prev) => ({ ...prev, note: e.target.value }))}
                          placeholder="备注"
                          className="w-full min-w-0 rounded border px-2 py-1 text-xs"
                        />
                      </div>
                    ) : (
                      <>
                        {h.entry_reason && (
                          <div className="mb-1" title={h.entry_reason}>
                            <span className="text-gray-400">原因 </span>
                            <span className="truncate block">{h.entry_reason}</span>
                          </div>
                        )}
                        {h.note && (
                          <div title={h.note}>
                            <span className="text-gray-400">备注 </span>
                            <span className="truncate block">{h.note}</span>
                          </div>
                        )}
                        {!h.entry_reason && !h.note && <span className="text-gray-300">—</span>}
                      </>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      h.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'
                    }`}>{h.status}</span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex justify-end gap-2">
                      {editingId === h.id ? (
                        <>
                          <button
                            type="button"
                            onClick={() => saveEditing(h.id)}
                            className="text-blue-600 hover:text-blue-800 text-xs"
                          >
                            保存
                          </button>
                          <button
                            type="button"
                            onClick={cancelEditing}
                            className="text-gray-500 hover:text-gray-700 text-xs"
                          >
                            取消
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => startEditing(h)}
                          className="text-blue-600 hover:text-blue-800 text-xs"
                        >
                          编辑
                        </button>
                      )}
                      <button onClick={() => deleteMut.mutate(h.id)}
                        className="text-red-500 hover:text-red-700 text-xs">删除</button>
                    </div>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
