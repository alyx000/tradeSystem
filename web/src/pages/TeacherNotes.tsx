import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

function safeParseJson(v: any): any {
  if (v == null) return null
  if (typeof v !== 'string') return v
  try { return JSON.parse(v) } catch { return null }
}

/** 将数组元素（可能是字符串或对象）统一转为可显示字符串 */
function toStr(item: any): string {
  if (item == null) return ''
  if (typeof item === 'string') return item
  if (typeof item === 'object') {
    return item.name ?? item.view ?? item.label ?? item.text ?? JSON.stringify(item)
  }
  return String(item)
}

/** 按换行符拆分文本为多行，过滤空行 */
function splitLines(text: string): string[] {
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
  return lines.length > 1 ? lines : [text]
}

/** 检测行首是否为序号，返回去掉序号后的正文 */
function stripLeadingNumber(line: string): { isNumbered: boolean; num: number; text: string } {
  // 匹配行首：1. / 1、/ 1。/ （1）/ ① 等
  const m = line.match(/^(\d+)[.、。]\s*(.*)$/)
  if (m) return { isNumbered: true, num: parseInt(m[1]), text: m[2] }
  const m2 = line.match(/^[（(](\d+)[)）]\s*(.*)$/)
  if (m2) return { isNumbered: true, num: parseInt(m2[1]), text: m2[2] }
  const CIRCLE = '①②③④⑤⑥⑦⑧⑨⑩'
  const idx = CIRCLE.indexOf(line[0])
  if (idx >= 0) return { isNumbered: true, num: idx + 1, text: line.slice(1).trimStart() }
  return { isNumbered: false, num: 0, text: line }
}

export default function TeacherNotes() {
  const queryClient = useQueryClient()
  const [keyword, setKeyword] = useState('')
  const [selectedTeacher, setSelectedTeacher] = useState('')

  const params: Record<string, string> = {}
  if (keyword) params.keyword = keyword
  if (selectedTeacher) params.teacher = selectedTeacher

  const { data: notes, isLoading } = useQuery({
    queryKey: ['teacher-notes', params],
    queryFn: () => api.getNotes(Object.keys(params).length > 0 ? params : undefined),
  })

  const { data: teachers } = useQuery({
    queryKey: ['teachers'],
    queryFn: api.getTeachers,
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteNote(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['teacher-notes'] }),
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-bold text-gray-800">老师观点</h1>
        <div className="flex gap-2 flex-wrap">
          {teachers && teachers.length > 0 && (
            <select
              value={selectedTeacher}
              onChange={e => setSelectedTeacher(e.target.value)}
              className="border rounded px-3 py-1.5 text-sm text-gray-700"
            >
              <option value="">全部老师</option>
              {(teachers as any[]).map((t: any) => (
                <option key={t.id} value={t.name}>{t.name}{t.platform ? ` (${t.platform})` : ''}</option>
              ))}
            </select>
          )}
          <input type="text" value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="搜索关键词…"
            className="border rounded px-3 py-1.5 text-sm" />
        </div>
      </div>

      {isLoading ? (
        <div className="text-gray-500 text-sm">加载中...</div>
      ) : (
        <div className="space-y-3">
          {notes?.map((note: any) => (
            <NoteCard key={note.id} note={note}
              onDelete={() => {
                if (window.confirm(`确认删除「${note.title}」？此操作不可撤销。`)) {
                  deleteMutation.mutate(note.id)
                }
              }}
              deleting={deleteMutation.isPending && deleteMutation.variables === note.id}
            />
          ))}
          {notes?.length === 0 && (
            <div className="text-gray-400 text-sm py-4 text-center">暂无数据</div>
          )}
        </div>
      )}
    </div>
  )
}

function NoteCard({ note, onDelete, deleting }: {
  note: any
  onDelete: () => void
  deleting?: boolean
}) {
  const tags = safeParseJson(note.tags)
  const keyPoints = safeParseJson(note.key_points)
  const sectors = safeParseJson(note.sectors)

  return (
    <details className="bg-white rounded-lg shadow group" open={false}>
      <summary className="cursor-pointer px-4 py-3 flex justify-between items-start list-none select-none">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="font-medium text-gray-800">{note.title}</span>
            <span className="text-xs text-blue-600 shrink-0">{note.teacher_name}</span>
          </div>
          {note.core_view && (() => {
            const lines = splitLines(note.core_view)
            const parsed = lines.map(l => stripLeadingNumber(l))
            const allNumbered = lines.length > 1 && parsed.every(p => p.isNumbered)
            if (lines.length <= 1) {
              return <p className="text-sm text-gray-600 mt-0.5 line-clamp-2 group-open:line-clamp-none">{note.core_view}</p>
            }
            if (allNumbered) {
              return (
                <ol className="mt-1.5 space-y-1">
                  {parsed.map((p, i) => (
                    <li key={i} className="flex gap-2 items-start line-clamp-1 group-open:line-clamp-none">
                      <span className="shrink-0 mt-0.5 w-5 h-5 rounded-full bg-blue-100 text-blue-700 text-xs font-semibold flex items-center justify-center leading-none">
                        {p.num}
                      </span>
                      <span className="text-sm text-gray-700 leading-5">{p.text}</span>
                    </li>
                  ))}
                </ol>
              )
            }
            return (
              <ul className="mt-1 space-y-0.5">
                {lines.map((line, i) => (
                  <li key={i} className="text-sm text-gray-600 leading-snug line-clamp-1 group-open:line-clamp-none">{line}</li>
                ))}
              </ul>
            )
          })()}
          {tags && Array.isArray(tags) && tags.length > 0 && (
            <div className="flex gap-1 mt-1.5 flex-wrap">
              {tags.map((tag: any, i: number) => (
                <span key={i} className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded text-xs">{toStr(tag)}</span>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 ml-3 shrink-0">
          <span className="text-xs text-gray-400">{note.date}</span>
          <button
            type="button"
            onClick={e => { e.preventDefault(); e.stopPropagation(); onDelete() }}
            disabled={deleting}
            className="p-1 rounded text-gray-300 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-40"
            title="删除此观点"
          >
            {deleting ? (
              <svg className="w-4 h-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
              </svg>
            ) : (
              <svg className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm5-1a1 1 0 00-1 1v6a1 1 0 102 0V8a1 1 0 00-1-1z" clipRule="evenodd"/>
              </svg>
            )}
          </button>
        </div>
      </summary>

      <div className="px-4 pb-4 border-t border-gray-100 pt-3">
        {/* 要点 — 最突出，放最前面 */}
        {keyPoints && Array.isArray(keyPoints) && keyPoints.length > 0 && (
          <div className="mb-3">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">核心要点</div>
            <ol className="space-y-1.5">
              {keyPoints.map((pt: any, i: number) => (
                <li key={i} className="flex gap-2.5 items-start">
                  <span className="shrink-0 mt-0.5 w-5 h-5 rounded-full bg-blue-100 text-blue-700 text-xs font-semibold flex items-center justify-center leading-none">
                    {i + 1}
                  </span>
                  <span className="text-sm text-gray-800 leading-5">{toStr(pt)}</span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* 仓位建议 — 醒目 */}
        {note.position_advice && (
          <div className="mb-3 flex items-start gap-2 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            <span className="shrink-0 text-amber-500 mt-0.5">⚖</span>
            <div>
              <div className="text-xs font-semibold text-amber-700 mb-0.5">仓位建议</div>
              <p className="text-sm text-amber-900">{note.position_advice}</p>
            </div>
          </div>
        )}

        {/* 涉及板块 */}
        {sectors && Array.isArray(sectors) && sectors.length > 0 && (
          <div className="mb-3">
            <div className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1.5">涉及板块</div>
            <div className="flex gap-1.5 flex-wrap">
              {sectors.map((s: any, i: number) => (
                <span key={i} className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full text-xs font-medium">{toStr(s)}</span>
              ))}
            </div>
          </div>
        )}

        {/* 原始全文 — 折叠显示，避免撑开卡片 */}
        {note.raw_content && (
          <details className="group/raw">
            <summary className="cursor-pointer text-xs text-gray-400 hover:text-gray-600 select-none list-none flex items-center gap-1 mb-1">
              <svg className="w-3 h-3 transition-transform group-open/raw:rotate-90" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd"/>
              </svg>
              原始观点全文
            </summary>
            <p className="text-sm text-gray-600 whitespace-pre-wrap mt-1 pl-4 border-l-2 border-gray-100">{note.raw_content}</p>
          </details>
        )}

        {note.attachments && note.attachments.length > 0 && (
          <div>
            <div className="text-xs font-semibold text-gray-500 mb-2">附件</div>
            <div className="space-y-2">
              {note.attachments.map((att: any, i: number) => {
                const fname = att.file_path?.split('/').pop() || '附件'
                const ext = fname.split('.').pop()?.toLowerCase() || ''
                const isImage = /^(jpg|jpeg|png|gif|webp|bmp)$/.test(ext) || /image/i.test(att.file_type || '')
                const isPdf = ext === 'pdf' || /pdf/i.test(att.file_type || '')
                const isDoc = /^(doc|docx)$/.test(ext)

                if (isImage) {
                  return (
                    <div key={i} className="rounded overflow-hidden border border-gray-100">
                      <img
                        src={att.url}
                        alt={att.description || `附件${i + 1}`}
                        className="w-full object-contain max-h-80 bg-gray-50 cursor-pointer"
                        onClick={() => window.open(att.url, '_blank')}
                        onError={e => {
                          const el = e.currentTarget
                          el.style.display = 'none'
                          el.nextElementSibling?.removeAttribute('hidden')
                        }}
                      />
                      <span hidden className="block text-xs text-gray-400 px-2 py-1">
                        图片加载失败：{att.file_path}
                      </span>
                      {att.description && (
                        <div className="text-xs text-gray-500 px-2 py-1 border-t border-gray-100">{att.description}</div>
                      )}
                    </div>
                  )
                }

                if (isPdf) {
                  return (
                    <div key={i} className="rounded border border-gray-200 overflow-hidden">
                      <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-100">
                        <div className="flex items-center gap-2 text-xs text-gray-600 min-w-0">
                          <span className="text-red-500 shrink-0">📄</span>
                          <span className="truncate font-medium">{fname}</span>
                          {att.description && <span className="text-gray-400 shrink-0">— {att.description}</span>}
                        </div>
                        <a href={att.url} download={fname} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-blue-500 hover:text-blue-700 shrink-0 ml-2">下载</a>
                      </div>
                      <iframe
                        src={att.url}
                        title={fname}
                        className="w-full border-0"
                        style={{ height: '480px' }}
                      />
                    </div>
                  )
                }

                if (isDoc) {
                  return (
                    <div key={i} className="flex items-center gap-3 px-3 py-2.5 rounded border border-gray-200 bg-gray-50">
                      <span className="text-blue-500 text-lg shrink-0">📝</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-medium text-gray-700 truncate">{fname}</div>
                        {att.description && <div className="text-xs text-gray-400">{att.description}</div>}
                        <div className="text-xs text-gray-400 mt-0.5">浏览器无法直接预览 Word 文件</div>
                      </div>
                      <a href={att.url} download={fname}
                        className="shrink-0 px-3 py-1 text-xs bg-blue-600 text-white rounded hover:bg-blue-700">
                        下载
                      </a>
                    </div>
                  )
                }

                // 其他文件类型
                return (
                  <a key={i} href={att.url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 px-3 py-2 rounded border border-gray-100 text-xs text-blue-600 hover:bg-gray-50">
                    <span>📎</span>
                    <span className="truncate">{fname}</span>
                    {att.description && <span className="text-gray-400">— {att.description}</span>}
                  </a>
                )
              })}
            </div>
          </div>
        )}
      </div>
    </details>
  )
}
