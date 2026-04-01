import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'

export default function TeacherNotes() {
  const queryClient = useQueryClient()
  const [keyword, setKeyword] = useState('')

  const params: Record<string, string> = {}
  if (keyword) params.keyword = keyword

  const { data: notes, isLoading } = useQuery({
    queryKey: ['teacher-notes', params],
    queryFn: () => api.getNotes(Object.keys(params).length > 0 ? params : undefined),
  })

  const { data: teachers } = useQuery({
    queryKey: ['teachers'],
    queryFn: api.getTeachers,
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-800">老师观点</h1>
        <div className="flex gap-2">
          <input type="text" value={keyword}
            onChange={e => setKeyword(e.target.value)}
            placeholder="搜索关键词…"
            className="border rounded px-3 py-1.5 text-sm" />
        </div>
      </div>

      {teachers && teachers.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {teachers.map((t: any) => (
            <span key={t.id} className="bg-blue-50 text-blue-700 px-2 py-1 rounded text-xs">
              {t.name} {t.platform && `(${t.platform})`}
            </span>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="text-gray-500 text-sm">加载中...</div>
      ) : (
        <div className="space-y-3">
          {notes?.map((note: any) => (
            <div key={note.id} className="bg-white rounded-lg shadow p-4">
              <div className="flex justify-between items-start mb-2">
                <div>
                  <span className="font-medium text-gray-800">{note.title}</span>
                  <span className="text-xs text-blue-600 ml-2">{note.teacher_name}</span>
                </div>
                <span className="text-xs text-gray-400">{note.date}</span>
              </div>
              {note.core_view && (
                <p className="text-sm text-gray-600">{note.core_view}</p>
              )}
              {note.tags && (
                <div className="flex gap-1 mt-2">
                  {JSON.parse(note.tags).map((tag: string) => (
                    <span key={tag} className="bg-gray-100 text-gray-600 px-2 py-0.5 rounded text-xs">
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
          {notes?.length === 0 && (
            <div className="text-gray-400 text-sm py-4 text-center">暂无数据</div>
          )}
        </div>
      )}
    </div>
  )
}
