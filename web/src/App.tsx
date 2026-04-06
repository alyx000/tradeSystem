import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Link, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { localDateString } from './lib/date'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const ReviewWorkbench = lazy(() => import('./pages/ReviewWorkbench'))
const MarketOverview = lazy(() => import('./pages/MarketOverview'))
const SearchCenter = lazy(() => import('./pages/SearchCenter'))
const CommandsCenter = lazy(() => import('./pages/CommandsCenter'))
const TeacherNotes = lazy(() => import('./pages/TeacherNotes'))
const Holdings = lazy(() => import('./pages/Holdings'))
const HoldingTasks = lazy(() => import('./pages/HoldingTasks'))
const Watchlist = lazy(() => import('./pages/Watchlist'))
const Calendar = lazy(() => import('./pages/Calendar'))
const IndustryInfo = lazy(() => import('./pages/IndustryInfo'))
const PlanWorkbench = lazy(() => import('./pages/PlanWorkbench'))
const KnowledgeWorkbench = lazy(() => import('./pages/KnowledgeWorkbench'))
const IngestWorkbench = lazy(() => import('./pages/IngestWorkbench'))
const RegulatoryMonitor = lazy(() => import('./pages/RegulatoryMonitor'))

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

const today = localDateString()

const NAV = [
  { to: '/', label: '仪表盘' },
  { to: `/market/${today}`, label: '市场' },
  { to: `/review/${today}`, label: '复盘' },
  { to: `/plans/${today}`, label: '计划' },
  { to: '/knowledge', label: '资料' },
  { to: '/ingest', label: '采集' },
  { to: '/search', label: '查询' },
  { to: '/commands', label: '命令' },
  { to: '/teachers', label: '老师观点' },
  { to: '/holdings', label: '持仓' },
  { to: '/holding-tasks', label: '持仓任务' },
  { to: '/watchlist', label: '关注池' },
  { to: '/regulatory-monitor', label: '异动监管' },
  { to: '/calendar', label: '日历' },
  { to: '/industry', label: '行业信息' },
]

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <div className="min-h-screen bg-gray-50">
          <nav className="bg-white border-b border-gray-200 px-6 py-3 flex gap-4 items-center">
            <span className="font-bold text-lg text-gray-800 mr-4">交易复盘系统</span>
            {NAV.map(n => (
              <Link key={n.to} to={n.to}
                className="text-gray-600 hover:text-blue-600 text-sm transition-colors">
                {n.label}
              </Link>
            ))}
          </nav>
          <main className="max-w-7xl mx-auto px-6 py-6">
            <Suspense fallback={<RouteLoadingFallback />}>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/market/:date" element={<MarketOverview />} />
                <Route path="/review/:date" element={<ReviewWorkbench />} />
                <Route path="/search" element={<SearchCenter />} />
                <Route path="/commands" element={<CommandsCenter />} />
                <Route path="/teachers" element={<TeacherNotes />} />
                <Route path="/holdings" element={<Holdings />} />
                <Route path="/holding-tasks" element={<HoldingTasks />} />
                <Route path="/watchlist" element={<Watchlist />} />
                <Route path="/regulatory-monitor" element={<RegulatoryMonitor />} />
                <Route path="/calendar" element={<Calendar />} />
                <Route path="/industry" element={<IndustryInfo />} />
                <Route path="/plans/:date" element={<PlanWorkbench />} />
                <Route path="/knowledge" element={<KnowledgeWorkbench />} />
                <Route path="/ingest" element={<IngestWorkbench />} />
                <Route path="*" element={<Navigate to="/" />} />
              </Routes>
            </Suspense>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

function RouteLoadingFallback() {
  return (
    <div className="bg-white rounded-lg border border-gray-200 px-4 py-10 text-center text-sm text-gray-400">
      页面加载中...
    </div>
  )
}
