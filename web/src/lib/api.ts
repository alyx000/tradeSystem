const BASE = '/api'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${detail}`)
  }
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json()
  return res.text() as unknown as T
}

export const api = {
  // Review
  getReview: (date: string) => request<any>(`/review/${date}`),
  getPrefill: (date: string) => request<any>(`/review/${date}/prefill`),
  saveReview: (date: string, data: any) =>
    request<any>(`/review/${date}`, { method: 'PUT', body: JSON.stringify(data) }),

  // Search
  unifiedSearch: (q: string, params?: Record<string, string>) => {
    const sp = new URLSearchParams({ q, ...params })
    return request<any>(`/search/unified?${sp}`)
  },
  exportSearch: (q: string) => request<string>(`/search/export?q=${encodeURIComponent(q)}`),

  // Style factors
  getStyleFactors: (metrics: string, from: string, to: string) =>
    request<any[]>(`/style-factors/series?metrics=${metrics}&from=${from}&to=${to}`),

  // Teachers
  getTeachers: () => request<any[]>('/teachers'),
  getTeacherTimeline: (id: number) => request<any[]>(`/teachers/${id}/timeline`),

  // Notes
  getNotes: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<any[]>(`/teacher-notes?${sp}`)
  },
  createNote: (data: any) =>
    request<any>('/teacher-notes', { method: 'POST', body: JSON.stringify(data) }),
  deleteNote: (id: number) =>
    request<any>(`/teacher-notes/${id}`, { method: 'DELETE' }),

  // Holdings
  getHoldings: () => request<any[]>('/holdings'),
  createHolding: (data: any) =>
    request<any>('/holdings', { method: 'POST', body: JSON.stringify(data) }),
  deleteHolding: (id: number) =>
    request<any>(`/holdings/${id}`, { method: 'DELETE' }),

  // Watchlist
  getWatchlist: (tier?: string) => {
    const sp = tier ? `?tier=${tier}` : ''
    return request<any[]>(`/watchlist${sp}`)
  },
  createWatchlistItem: (data: any) =>
    request<any>('/watchlist', { method: 'POST', body: JSON.stringify(data) }),

  // Calendar
  getCalendarRange: (from: string, to: string) =>
    request<any[]>(`/calendar/range?from=${from}&to=${to}`),
  createCalendarEvent: (data: any) =>
    request<any>('/calendar', { method: 'POST', body: JSON.stringify(data) }),

  // Market
  getMarket: (date: string) => request<any>(`/market/${date}`),
  getMarketHistory: (days: number = 20) => request<any[]>(`/market/history?days=${days}`),
  getPostMarket: (date: string) => request<any>(`/post-market/${date}`),

  // Trades
  getTrades: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<any[]>(`/trades?${sp}`)
  },
  createTrade: (data: any) =>
    request<any>('/trades', { method: 'POST', body: JSON.stringify(data) }),
}
