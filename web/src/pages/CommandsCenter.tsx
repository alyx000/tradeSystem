import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

export default function CommandsCenter() {
  const [query, setQuery] = useState('')
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null)
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({})
  const today = new Date().toISOString().slice(0, 10)
  const { data, isLoading, error } = useQuery({
    queryKey: ['command-index'],
    queryFn: api.getCommandIndex,
  })

  if (isLoading) {
    return <div className="text-sm text-gray-500 py-8 text-center">命令索引加载中...</div>
  }

  if (error) {
    return <div className="text-sm text-red-600 py-8 text-center">命令索引加载失败</div>
  }

  const keyword = query.trim().toLowerCase()
  const webShortcuts = [
    {
      title: '盘后核心诊断',
      href: `/ingest?date=${today}`,
      description: '打开今天的盘后核心采集诊断视图。',
    },
    {
      title: '盘后扩展诊断',
      href: `/ingest?date=${today}&stage=post_extended`,
      description: '直接检查 post_extended 阶段的接口运行和错误。',
    },
    {
      title: '连续失败视图',
      href: `/ingest?date=${today}&health_sort=streak`,
      description: '优先查看连续失败多天的接口。',
    },
  ].filter(item => {
    if (!keyword) return true
    return item.title.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword) || item.href.toLowerCase().includes(keyword)
  })
  const quickstart = (data?.daily_quickstart ?? []).filter(item => {
    if (!keyword) return true
    return item.command.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword)
  })
  const sections = (data?.sections ?? [])
    .map(section => ({
      ...section,
      items: section.items.filter(item => {
        if (!keyword) return true
        return item.command.toLowerCase().includes(keyword) || item.description.toLowerCase().includes(keyword)
      }),
    }))
    .filter(section => section.items.length > 0)

  const hasSections = sections.length > 0

  const handleCopy = async (command: string) => {
    try {
      await navigator.clipboard.writeText(command)
      setCopiedCommand(command)
      window.setTimeout(() => {
        setCopiedCommand(current => (current === command ? null : current))
      }, 1500)
    } catch {
      setCopiedCommand(null)
    }
  }

  const toggleSection = (title: string) => {
    setCollapsedSections(current => ({
      ...current,
      [title]: !current[title],
    }))
  }

  const setAllSectionsCollapsed = (collapsed: boolean) => {
    setCollapsedSections(
      Object.fromEntries(sections.map(section => [section.title, collapsed]))
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-800">命令中心</h1>
        <p className="text-sm text-gray-500 mt-1">
          统一入口优先使用仓库根目录的 <code className="text-gray-700">make</code> 目标。
        </p>
      </div>

      <div className="bg-white rounded-lg shadow p-4">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[280px]">
            <label className="block text-sm font-medium text-gray-500 mb-2" htmlFor="commands-filter">
              关键词过滤
            </label>
            <input
              id="commands-filter"
              type="text"
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="输入 make、plan、ingest、review 等关键词"
              className="w-full border rounded px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 outline-none"
            />
          </div>
          {hasSections && (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setAllSectionsCollapsed(false)}
                className="border border-gray-300 rounded px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                全部展开
              </button>
              <button
                type="button"
                onClick={() => setAllSectionsCollapsed(true)}
                className="border border-gray-300 rounded px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                全部折叠
              </button>
            </div>
          )}
        </div>
      </div>

      {quickstart.length > 0 && (
        <section className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-3">每日高频</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {quickstart.map(item => (
              <div key={item.command} className="rounded border border-gray-200 bg-gray-50 px-3 py-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="font-mono text-xs text-gray-800">{item.command}</div>
                  <button
                    type="button"
                    onClick={() => handleCopy(item.command)}
                    className="text-xs text-blue-600 hover:text-blue-700 shrink-0"
                  >
                    {copiedCommand === item.command ? '已复制' : '复制'}
                  </button>
                </div>
                <div className="text-xs text-gray-500 mt-1">{item.description}</div>
              </div>
            ))}
          </div>
        </section>
      )}

      {webShortcuts.length > 0 && (
        <section className="bg-white rounded-lg shadow p-4">
          <h2 className="text-sm font-medium text-gray-500 mb-3">Web 入口速查</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {webShortcuts.map(item => (
              <Link
                key={item.href}
                to={item.href}
                className="rounded border border-gray-200 bg-gray-50 px-3 py-3 hover:bg-gray-100 transition-colors"
              >
                <div className="text-sm font-medium text-gray-800">{item.title}</div>
                <div className="text-xs text-gray-500 mt-1">{item.description}</div>
                <div className="mt-2 font-mono text-xs text-blue-600">{item.href}</div>
              </Link>
            ))}
          </div>
        </section>
      )}

      {sections.map(section => (
        <section key={section.title} className="bg-white rounded-lg shadow">
          <div className="px-4 py-3 border-b bg-gray-50 rounded-t-lg flex items-center justify-between gap-3">
            <h2 className="text-sm font-medium text-gray-700">{section.title}</h2>
            <button
              type="button"
              onClick={() => toggleSection(section.title)}
              className="text-xs text-blue-600 hover:text-blue-700"
            >
              {collapsedSections[section.title] ? '展开' : '折叠'}
            </button>
          </div>
          {!collapsedSections[section.title] && (
            <div className="divide-y">
              {section.items.map(item => (
                <div key={item.target} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="font-mono text-sm text-gray-800">{item.command}</div>
                    <button
                      type="button"
                      onClick={() => handleCopy(item.command)}
                      className="text-xs text-blue-600 hover:text-blue-700 shrink-0"
                    >
                      {copiedCommand === item.command ? '已复制' : '复制'}
                    </button>
                  </div>
                  <div className="text-sm text-gray-500 mt-1">{item.description}</div>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}

      {quickstart.length === 0 && sections.length === 0 && (
        <div className="bg-white rounded-lg shadow p-8 text-sm text-center text-gray-400">
          未找到匹配的命令
        </div>
      )}
    </div>
  )
}
