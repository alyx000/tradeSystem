import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { KnowledgeAssetRecord } from '../lib/types'

const ASSET_TYPES = [
  { value: 'teacher_note', label: '老师观点' },
  { value: 'news_note', label: '新闻资讯' },
  { value: 'course_note', label: '课程笔记' },
  { value: 'manual_note', label: '手动笔记' },
]

const ASSET_TYPE_LABELS: Record<string, string> = Object.fromEntries(
  ASSET_TYPES.map(t => [t.value, t.label])
)

function CreateAssetForm({ onCreated }: { onCreated: () => void }) {
  const [assetType, setAssetType] = useState('manual_note')
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [source, setSource] = useState('')
  const [tags, setTags] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  const mutation = useMutation({
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
      setTitle('')
      setContent('')
      setSource('')
      setTags('')
      onCreated()
      setTimeout(() => setSuccess(false), 3000)
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 space-y-4">
      <h2 className="text-base font-semibold text-gray-800">新建资料</h2>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">类型</label>
        <select
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
          资料已录入
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
          setError(null)
          mutation.mutate()
        }}
        disabled={mutation.isPending}
        className="w-full px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 disabled:opacity-50"
      >
        {mutation.isPending ? '提交中...' : '录入资料'}
      </button>
    </div>
  )
}

function DraftModal({
  asset,
  onClose,
}: {
  asset: KnowledgeAssetRecord
  onClose: () => void
}) {
  const today = new Date().toISOString().slice(0, 10)
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

function AssetList() {
  const { data: assets, isLoading } = useQuery({
    queryKey: ['knowledge-assets'],
    queryFn: () => api.listKnowledgeAssets(50),
  })
  const [draftTarget, setDraftTarget] = useState<KnowledgeAssetRecord | null>(null)

  if (isLoading) return <p className="text-sm text-gray-500">加载中...</p>

  if (!assets || assets.length === 0) {
    return <p className="text-sm text-gray-500 py-4 text-center">暂无资料</p>
  }

  return (
    <>
      <ul className="space-y-3">
        {assets.map((asset: KnowledgeAssetRecord) => (
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
              <button
                onClick={() => setDraftTarget(asset)}
                className="shrink-0 px-3 py-1.5 text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-md hover:bg-indigo-100 font-medium"
              >
                生成草稿
              </button>
            </div>
            {asset.content && (
              <p className="text-xs text-gray-600 line-clamp-2 border-t pt-2">
                {asset.content}
              </p>
            )}
          </li>
        ))}
      </ul>

      {draftTarget && (
        <DraftModal asset={draftTarget} onClose={() => setDraftTarget(null)} />
      )}
    </>
  )
}

export default function KnowledgeWorkbench() {
  const queryClient = useQueryClient()

  function handleCreated() {
    queryClient.invalidateQueries({ queryKey: ['knowledge-assets'] })
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
