import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { addDaysLocal, localDateString } from '../lib/date'
import type { KnowledgeAssetRecord, TeacherNote } from '../lib/types'

const ASSET_TYPES = [
  { value: 'teacher_note', label: '老师观点' },
  { value: 'news_note', label: '新闻资讯' },
  { value: 'manual_note', label: '手动笔记' },
]

/** 工作台不提供课程笔记新建；保留标签供列表展示历史 course_note 资产 */
const ASSET_TYPE_LABELS: Record<string, string> = {
  ...Object.fromEntries(ASSET_TYPES.map(t => [t.value, t.label])),
  course_note: '课程笔记',
}

type WorkbenchRow =
  | { kind: 'teacher_note'; note: TeacherNote }
  | { kind: 'asset'; asset: KnowledgeAssetRecord }

/** 合并列表按时间倒序；缺省字段用稳定副键，避免同键下顺序抖动 */
function defaultListDateFrom(): string {
  return localDateString(addDaysLocal(new Date(), -90))
}

function rowSortKey(row: WorkbenchRow): string {
  const epoch = '1970-01-01T00:00:00'
  if (row.kind === 'teacher_note') {
    const n = row.note
    const primary = ((n.created_at || n.date || '') as string).trim() || epoch
    const tie = String(n.id).padStart(12, '0')
    return `${primary}\t${tie}`
  }
  const a = row.asset
  const primary = ((a.created_at || '') as string).trim() || epoch
  const tie = (a.asset_id || a.title || '').toString()
  return `${primary}\t${tie}`
}

function CreateAssetForm({ onCreated }: { onCreated: () => void }) {
  const [assetType, setAssetType] = useState('manual_note')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [source, setSource] = useState('')
  const [tags, setTags] = useState('')
  const [teacherName, setTeacherName] = useState('')
  const [noteDate, setNoteDate] = useState(() => localDateString())
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const knowledgeMutation = useMutation({
    mutationFn: () =>
      api.createKnowledgeAsset({
        asset_type: assetType,
        title: title.trim(),
        content: content.trim(),
        source: source.trim() || undefined,
        tags: tags
          .split(/[,，]/)
          .map(s => s.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      setError(null)
      setSuccess(true)
      resetForm()
      onCreated()
      setTimeout(() => setSuccess(false), 3000)
    },
    onError: (e: Error) => setError(e.message),
  })

  const teacherMutation = useMutation({
    mutationFn: () =>
      api.createTeacherNote({
        teacher_name: teacherName.trim(),
        date: noteDate,
        title: title.trim(),
        raw_content: content.trim(),
        tags: tags
          .split(/[,，]/)
          .map(s => s.trim())
          .filter(Boolean),
        source_type: 'text',
        input_by: 'web',
      }),
    onSuccess: () => {
      setError(null)
      setSuccess(true)
      resetForm()
      onCreated()
      setTimeout(() => setSuccess(false), 3000)
    },
    onError: (e: Error) => setError(e.message),
  })

  function resetForm() {
    setTitle('')
    setContent('')
    setSource('')
    setTags('')
    setTeacherName('')
    setNoteDate(localDateString())
  }

  const isTeacher = assetType === 'teacher_note'
  const pending = knowledgeMutation.isPending || teacherMutation.isPending

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
      <h2 className="text-base font-semibold text-gray-800">新建资料</h2>
      <p className="text-xs text-gray-500">
        老师观点仅写入「老师观点」库（teacher_notes），与其它资料（knowledge_assets）分表存储。
      </p>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">类型</label>
        <select
          aria-label="新建资料类型"
          value={assetType}
          onChange={e => setAssetType(e.target.value)}
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {ASSET_TYPES.map(t => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </div>

      {isTeacher && (
        <>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              老师姓名 <span className="text-red-500">*</span>
            </label>
            <input
              type="text"
              value={teacherName}
              onChange={e => setTeacherName(e.target.value)}
              placeholder="如：小鲍"
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              笔记日期 <span className="text-red-500">*</span>
            </label>
            <input
              type="date"
              value={noteDate}
              onChange={e => setNoteDate(e.target.value)}
              className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
        </>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          标题 <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          value={title}
          onChange={e => setTitle(e.target.value)}
          placeholder="资料标题"
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">
          正文 <span className="text-red-500">*</span>
        </label>
        <textarea
          value={content}
          onChange={e => setContent(e.target.value)}
          rows={5}
          placeholder="观点、笔记、资讯内容..."
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-y"
        />
      </div>

      {!isTeacher && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">来源</label>
          <input
            type="text"
            value={source}
            onChange={e => setSource(e.target.value)}
            placeholder="如：小鲍直播、财联社"
            className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>
      )}

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">标签（逗号分隔）</label>
        <input
          type="text"
          value={tags}
          onChange={e => setTags(e.target.value)}
          placeholder="如：AI算力, 机器人"
          className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>

      {error && <p className="text-red-600 text-sm">{error}</p>}
      {success && (
        <p className="text-green-700 text-sm bg-green-50 border border-green-200 rounded px-3 py-2">
          已录入
        </p>
      )}

      <button
        onClick={() => {
          if (!title.trim()) {
            setError('标题不能为空')
            return
          }
          if (!content.trim()) {
            setError('正文不能为空')
            return
          }
          if (isTeacher && !teacherName.trim()) {
            setError('老师姓名不能为空')
            return
          }
          setError(null)
          if (isTeacher) {
            teacherMutation.mutate()
          } else {
            knowledgeMutation.mutate()
          }
        }}
        disabled={pending}
        className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {pending ? '提交中...' : '录入资料'}
      </button>
    </div>
  )
}

function DraftModalFromAsset({
  asset,
  onClose,
}: {
  asset: KnowledgeAssetRecord
  onClose: () => void
}) {
  const today = localDateString()
  const [tradeDate, setTradeDate] = useState(today)
  const [result, setResult] = useState<{ draft_id: string; trade_date: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      api.draftFromAsset(asset.asset_id, {
        trade_date: tradeDate,
        input_by: 'web',
      }),
    onSuccess: (payload) => {
      setError(null)
      setResult({
        draft_id: payload.draft?.draft_id ?? '',
        trade_date: payload.draft?.trade_date ?? tradeDate,
      })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-lg w-full max-w-sm p-6 space-y-4">
        <h3 className="font-semibold text-gray-800">从资料生成草稿</h3>
        <p className="text-sm text-gray-600 line-clamp-2">{asset.title}</p>

        {!result ? (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">交易日</label>
              <input
                type="date"
                value={tradeDate}
                onChange={e => setTradeDate(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <div className="flex gap-2">
              <button
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {mutation.isPending ? '生成中...' : '生成草稿'}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="bg-green-50 border border-green-200 rounded p-3 space-y-2">
              <p className="text-sm text-green-800 font-medium">草稿已生成</p>
              <p className="text-xs text-green-700">
                Draft ID：<code className="bg-white px-1 py-0.5 rounded border text-xs">{result.draft_id}</code>
              </p>
            </div>
            <div className="flex gap-2">
              <Link
                to={`/plans/${result.trade_date}`}
                className="flex-1 text-center px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700"
                onClick={onClose}
              >
                进入计划工作台
              </Link>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50"
              >
                关闭
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function DraftModalFromTeacherNote({
  note,
  onClose,
}: {
  note: TeacherNote
  onClose: () => void
}) {
  const today = localDateString()
  const [tradeDate, setTradeDate] = useState(today)
  const [result, setResult] = useState<{ draft_id: string; trade_date: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () =>
      api.draftFromTeacherNote(note.id, {
        trade_date: tradeDate,
        input_by: 'web',
      }),
    onSuccess: (payload) => {
      setError(null)
      setResult({
        draft_id: payload.draft?.draft_id ?? '',
        trade_date: payload.draft?.trade_date ?? tradeDate,
      })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-lg w-full max-w-sm p-6 space-y-4">
        <h3 className="font-semibold text-gray-800">从老师笔记生成草稿</h3>
        <p className="text-sm text-gray-600 line-clamp-2">{note.title}</p>

        {!result ? (
          <>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">交易日</label>
              <input
                type="date"
                value={tradeDate}
                onChange={e => setTradeDate(e.target.value)}
                className="w-full border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            {error && <p className="text-red-600 text-sm">{error}</p>}
            <div className="flex gap-2">
              <button
                onClick={() => mutation.mutate()}
                disabled={mutation.isPending}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
              >
                {mutation.isPending ? '生成中...' : '生成草稿'}
              </button>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50"
              >
                取消
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="bg-green-50 border border-green-200 rounded p-3 space-y-2">
              <p className="text-sm text-green-800 font-medium">草稿已生成</p>
              <p className="text-xs text-green-700">
                Draft ID：<code className="bg-white px-1 py-0.5 rounded border text-xs">{result.draft_id}</code>
              </p>
            </div>
            <div className="flex gap-2">
              <Link
                to={`/plans/${result.trade_date}`}
                className="flex-1 text-center px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700"
                onClick={onClose}
              >
                进入计划工作台
              </Link>
              <button
                onClick={onClose}
                className="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-sm hover:bg-gray-50"
              >
                关闭
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

type DraftTarget =
  | { type: 'asset'; asset: KnowledgeAssetRecord }
  | { type: 'teacher'; note: TeacherNote }

function AssetList() {
  const queryClient = useQueryClient()
  const [dateFrom, setDateFrom] = useState(() => defaultListDateFrom())
  const [dateTo, setDateTo] = useState(() => localDateString())
  const [teacher, setTeacher] = useState('')
  const [keyword, setKeyword] = useState('')
  const [assetTypeFilter, setAssetTypeFilter] = useState('')

  const { data: teachers } = useQuery({
    queryKey: ['teachers'],
    queryFn: () => api.getTeachers(),
  })

  const noteQueryParams = useMemo((): Record<string, string> => {
    const p: Record<string, string> = { limit: '200', offset: '0' }
    if (dateFrom.trim()) p.from = dateFrom.trim()
    if (dateTo.trim()) p.to = dateTo.trim()
    const kw = keyword.trim()
    if (kw) p.keyword = kw
    if (teacher.trim()) p.teacher = teacher.trim()
    return p
  }, [dateFrom, dateTo, keyword, teacher])

  const assetQueryParams = useMemo(
    () => ({
      limit: 200,
      offset: 0,
      created_from: dateFrom.trim() || undefined,
      created_to: dateTo.trim() || undefined,
      keyword: keyword.trim() || undefined,
      asset_type: assetTypeFilter.trim() || undefined,
    }),
    [dateFrom, dateTo, keyword, assetTypeFilter],
  )

  const { data: assets, isLoading: loadingAssets } = useQuery({
    queryKey: ['knowledge-assets', assetQueryParams],
    queryFn: () => api.listKnowledgeAssets(assetQueryParams),
  })
  const { data: notes, isLoading: loadingNotes } = useQuery({
    queryKey: ['kw-teacher-notes', noteQueryParams],
    queryFn: () => api.getNotes(noteQueryParams),
  })

  const [draftTarget, setDraftTarget] = useState<DraftTarget | null>(null)

  const rows = useMemo((): WorkbenchRow[] => {
    const nt: WorkbenchRow[] = (notes || []).map((note) => ({ kind: 'teacher_note', note }))
    const ast: WorkbenchRow[] = (assets || []).map((asset) => ({ kind: 'asset', asset }))
    return [...nt, ...ast].sort((a, b) => rowSortKey(b).localeCompare(rowSortKey(a)))
  }, [notes, assets])

  const deleteTeacherMutation = useMutation({
    mutationFn: (id: number) => api.deleteNote(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['kw-teacher-notes'] })
    },
  })

  const deleteAssetMutation = useMutation({
    mutationFn: (assetId: string) => api.deleteKnowledgeAsset(assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['knowledge-assets'] })
    },
  })

  const loading = loadingAssets || loadingNotes

  function resetFiltersTo90Days() {
    setDateFrom(defaultListDateFrom())
    setDateTo(localDateString())
    setTeacher('')
    setKeyword('')
    setAssetTypeFilter('')
  }

  const filterBar = (
    <div className="bg-white border border-gray-200 rounded-lg p-4 mb-4 space-y-3 text-sm">
      <p className="text-xs text-gray-500">
        默认展示近 90 天：老师笔记按笔记日期、资料按创建日期筛选；各最多返回 200 条。
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">日期从</label>
          <input
            type="date"
            value={dateFrom}
            onChange={e => setDateFrom(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">日期至</label>
          <input
            type="date"
            value={dateTo}
            onChange={e => setDateTo(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">老师</label>
          <select
            aria-label="列表筛选老师"
            value={teacher}
            onChange={e => setTeacher(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          >
            <option value="">全部</option>
            {(teachers || []).map(t => (
              <option key={t.id} value={t.name}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <div className="sm:col-span-2 lg:col-span-1">
          <label className="block text-xs font-medium text-gray-600 mb-1">关键词</label>
          <input
            type="search"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="标题/正文（老师笔记与资料同时筛选）"
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">仅资料类型</label>
          <select
            aria-label="列表筛选资料类型"
            value={assetTypeFilter}
            onChange={e => setAssetTypeFilter(e.target.value)}
            className="w-full border border-gray-300 rounded-md px-2 py-1.5 text-sm"
          >
            <option value="">全部资料</option>
            <option value="news_note">新闻资讯</option>
            <option value="manual_note">手动笔记</option>
          </select>
        </div>
      </div>
      <button
        type="button"
        onClick={resetFiltersTo90Days}
        className="text-xs text-blue-600 hover:text-blue-800 underline"
      >
        重置为近 90 天并清空筛选
      </button>
    </div>
  )

  if (loading) {
    return (
      <>
        {filterBar}
        <p className="text-sm text-gray-500">加载中...</p>
      </>
    )
  }

  if (rows.length === 0) {
    return (
      <>
        {filterBar}
        <p className="text-sm text-gray-500 py-4 text-center">暂无资料</p>
      </>
    )
  }

  function confirmDeleteTeacher(note: TeacherNote) {
    const ok = window.confirm(
      `确定删除老师笔记「${note.title}」？\n将同步影响复盘预填、老师观点页等引用该笔记的地方，且不可恢复。`
    )
    if (ok) deleteTeacherMutation.mutate(note.id)
  }

  function confirmDeleteAsset(asset: KnowledgeAssetRecord) {
    const ok = window.confirm(`确定删除资料「${asset.title || asset.asset_id}」？不可恢复。`)
    if (ok) deleteAssetMutation.mutate(asset.asset_id)
  }

  return (
    <>
      {filterBar}
      <ul className="space-y-3">
        {rows.map((row) => {
          if (row.kind === 'teacher_note') {
            const note = row.note
            const preview =
              (note.raw_content || note.core_view || '').slice(0, 200) ||
              (typeof note.key_points === 'string' ? note.key_points : '') ||
              ''
            return (
              <li
                key={`tn-${note.id}`}
                className="bg-white border border-gray-200 rounded-lg p-4 space-y-2"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1 flex-wrap">
                      <span className="text-xs px-2 py-0.5 bg-emerald-50 text-emerald-800 rounded font-medium">
                        老师观点
                      </span>
                      {note.teacher_name && (
                        <span className="text-xs text-gray-600">{note.teacher_name}</span>
                      )}
                      <span className="text-xs text-gray-400">{note.date}</span>
                    </div>
                    <p className="text-sm font-medium text-gray-800 truncate">{note.title}</p>
                    <p className="text-xs text-gray-400 mt-1">
                      {note.created_at?.slice(0, 16).replace('T', ' ')}
                    </p>
                  </div>
                  <div className="flex flex-col gap-1.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => setDraftTarget({ type: 'teacher', note })}
                      className="px-3 py-1.5 text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-md hover:bg-indigo-100 font-medium"
                    >
                      生成草稿
                    </button>
                    <button
                      type="button"
                      onClick={() => confirmDeleteTeacher(note)}
                      disabled={deleteTeacherMutation.isPending}
                      className="px-3 py-1.5 text-xs text-red-700 border border-red-200 rounded-md hover:bg-red-50 font-medium disabled:opacity-50"
                    >
                      删除
                    </button>
                  </div>
                </div>
                {preview ? (
                  <p className="text-xs text-gray-600 line-clamp-2 border-t pt-2">{preview}</p>
                ) : null}
              </li>
            )
          }

          const asset = row.asset
          return (
            <li
              key={asset.asset_id}
              className="bg-white border border-gray-200 rounded-lg p-4 space-y-2"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-xs px-2 py-0.5 bg-blue-50 text-blue-700 rounded font-medium">
                      {ASSET_TYPE_LABELS[asset.asset_type ?? ''] || asset.asset_type || '未分类'}
                    </span>
                    {asset.tags && (
                      <span className="text-xs text-gray-400">
                        {typeof asset.tags === 'string'
                          ? asset.tags
                          : (asset.tags as string[]).join(', ')}
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-gray-800 truncate">{asset.title}</p>
                  {asset.source && (
                    <p className="text-xs text-gray-500">来源：{asset.source}</p>
                  )}
                  <p className="text-xs text-gray-400 mt-1">
                    {asset.created_at?.slice(0, 16).replace('T', ' ')}
                  </p>
                </div>
                <div className="flex flex-col gap-1.5 shrink-0">
                  <button
                    type="button"
                    onClick={() => setDraftTarget({ type: 'asset', asset })}
                    className="px-3 py-1.5 text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-md hover:bg-indigo-100 font-medium"
                  >
                    生成草稿
                  </button>
                  <button
                    type="button"
                    onClick={() => confirmDeleteAsset(asset)}
                    disabled={deleteAssetMutation.isPending}
                    className="px-3 py-1.5 text-xs text-red-700 border border-red-200 rounded-md hover:bg-red-50 font-medium disabled:opacity-50"
                  >
                    删除
                  </button>
                </div>
              </div>
              {asset.content && (
                <p className="text-xs text-gray-600 line-clamp-2 border-t pt-2">
                  {asset.content}
                </p>
              )}
            </li>
          )
        })}
      </ul>

      {draftTarget?.type === 'asset' && (
        <DraftModalFromAsset asset={draftTarget.asset} onClose={() => setDraftTarget(null)} />
      )}
      {draftTarget?.type === 'teacher' && (
        <DraftModalFromTeacherNote note={draftTarget.note} onClose={() => setDraftTarget(null)} />
      )}
    </>
  )
}

export default function KnowledgeWorkbench() {
  const queryClient = useQueryClient()

  function handleCreated() {
    queryClient.invalidateQueries({ queryKey: ['knowledge-assets'] })
    queryClient.invalidateQueries({ queryKey: ['kw-teacher-notes'] })
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-6">资料工作台</h1>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <CreateAssetForm onCreated={handleCreated} />
        </div>
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-base font-semibold text-gray-800">资料列表</h2>
          </div>
          <AssetList />
        </div>
      </div>
    </div>
  )
}
