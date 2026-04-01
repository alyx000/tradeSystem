import { BrowserRouter, Routes, Route, Link, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Dashboard from './pages/Dashboard'
import ReviewWorkbench from './pages/ReviewWorkbench'
import SearchCenter from './pages/SearchCenter'
import TeacherNotes from './pages/TeacherNotes'
import Holdings from './pages/Holdings'
import Watchlist from './pages/Watchlist'
import Calendar from './pages/Calendar'

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

const NAV = [
  { to: '/', label: '仪表盘' },
  { to: `/review/${new Date().toISOString().slice(0, 10)}`, label: '复盘' },
  { to: '/search', label: '查询' },
  { to: '/teachers', label: '老师观点' },
  { to: '/holdings', label: '持仓' },
  { to: '/watchlist', label: '关注池' },
  { to: '/calendar', label: '日历' },
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
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/review/:date" element={<ReviewWorkbench />} />
              <Route path="/search" element={<SearchCenter />} />
              <Route path="/teachers" element={<TeacherNotes />} />
              <Route path="/holdings" element={<Holdings />} />
              <Route path="/watchlist" element={<Watchlist />} />
              <Route path="/calendar" element={<Calendar />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </main>
        </div>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
