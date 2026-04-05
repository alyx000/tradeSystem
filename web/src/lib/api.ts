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
  deleteWatchlistItem: (id: number) =>
    request<any>(`/watchlist/${id}`, { method: 'DELETE' }),

  // Calendar
  getCalendarRange: (from: string, to: string) =>
    request<any[]>(`/calendar/range?from=${from}&to=${to}`),
  createCalendarEvent: (data: any) =>
    request<any>('/calendar', { method: 'POST', body: JSON.stringify(data) }),

  // Market
  getMarket: (date: string) => request<any>(`/market/${date}`),
  getMarketHistory: (days: number = 20) => request<any[]>(`/market/history?days=${days}`),
  getPostMarket: (date: string) => request<any>(`/post-market/${date}`),
  getMainThemes: () => request<any[]>('/main-themes'),

  // Industry info
  getIndustryInfo: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<any[]>(`/industry?${sp}`)
  },
  createIndustryInfo: (data: any) =>
    request<any>('/industry', { method: 'POST', body: JSON.stringify(data) }),
  deleteIndustryInfo: (id: number) =>
    request<any>(`/industry/${id}`, { method: 'DELETE' }),

  // Trades
  getTrades: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<any[]>(`/trades?${sp}`)
  },
  createTrade: (data: any) =>
    request<any>('/trades', { method: 'POST', body: JSON.stringify(data) }),

  // Planning
  listPlanObservations: (date?: string, limit = 20) =>
    request<any[]>(`/plans/observations?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  updatePlanObservation: (observationId: string, data: any) =>
    request<any>(`/plans/observations/${observationId}`, { method: 'PUT', body: JSON.stringify(data) }),
  listPlanDrafts: (date?: string, limit = 20) =>
    request<any[]>(`/plans/drafts?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  createPlanDraft: (data: any) =>
    request<any>('/plans/drafts', { method: 'POST', body: JSON.stringify(data) }),
  getPlanDraft: (draftId: string) =>
    request<any>(`/plans/drafts/${draftId}`),
  updatePlanDraft: (draftId: string, data: any) =>
    request<any>(`/plans/drafts/${draftId}`, { method: 'PUT', body: JSON.stringify(data) }),
  confirmPlan: (draftId: string, data: any) =>
    request<any>(`/plans/${draftId}/confirm`, { method: 'POST', body: JSON.stringify(data) }),
  listPlans: (date?: string, limit = 20) =>
    request<any[]>(`/plans?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  getPlan: (planId: string) =>
    request<any>(`/plans/${planId}`),
  updatePlan: (planId: string, data: any) =>
    request<any>(`/plans/${planId}`, { method: 'PUT', body: JSON.stringify(data) }),
  getPlanDiagnostics: (planId: string) =>
    request<any>(`/plans/${planId}/diagnostics`),
  reviewPlan: (planId: string, data: any) =>
    request<any>(`/plans/${planId}/review`, { method: 'POST', body: JSON.stringify(data) }),

  // Ingest
  listIngestInterfaces: () =>
    request<any[]>('/ingest/interfaces'),
  inspectIngest: (date: string) =>
    request<any>(`/ingest/inspect?date=${encodeURIComponent(date)}`),
  listIngestRuns: (date: string) =>
    request<any[]>(`/ingest/runs?date=${encodeURIComponent(date)}`),
  listIngestErrors: (date: string) =>
    request<any[]>(`/ingest/errors?date=${encodeURIComponent(date)}`),
  runIngestStage: (data: any) =>
    request<any>('/ingest/run', { method: 'POST', body: JSON.stringify(data) }),
  runIngestInterface: (data: any) =>
    request<any>('/ingest/run-interface', { method: 'POST', body: JSON.stringify(data) }),
  getIngestRetrySummary: () =>
    request<any>('/ingest/retry'),

  // Knowledge
  createKnowledgeAsset: (data: any) =>
    request<any>('/knowledge/assets', { method: 'POST', body: JSON.stringify(data) }),
  listKnowledgeAssets: (limit = 20) =>
    request<any[]>(`/knowledge/assets?limit=${limit}`),
  draftFromAsset: (assetId: string, data: any) =>
    request<any>(`/knowledge/assets/${assetId}/draft`, { method: 'POST', body: JSON.stringify(data) }),
}
