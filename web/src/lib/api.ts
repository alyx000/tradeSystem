import type {
  CalendarEvent,
  CalendarEventCreateInput,
  CommandIndexPayload,
  Holding,
  HoldingCreateInput,
  HoldingUpdateInput,
  HoldingTaskItem,
  HoldingTaskUpdateInput,
  HoldingSignalsPayload,
  IngestErrorRecord,
  IngestInspectRecord,
  IngestInterfaceRecord,
  IngestRetrySummary,
  IngestReconcileInput,
  IngestReconcileResult,
  IngestRetryRunInput,
  IngestRetryRunResult,
  IngestHealthSummary,
  IngestRunRecord,
  IngestRunInterfaceInput,
  IngestRunInterfaceResult,
  IngestRunStageInput,
  IngestRunStageResult,
  IndustryInfoCreateInput,
  IndustryInfoItem,
  KnowledgeAssetCreateInput,
  KnowledgeAssetRecord,
  KnowledgeDraftInput,
  KnowledgeDraftResult,
  MarketChartItem,
  MarketFullData,
  MainThemeItem,
  PlanConfirmInput,
  PlanDiagnosticsRecord,
  PlanDraftCreateInput,
  PlanDraftRecord,
  PlanObservationRecord,
  PlanObservationUpdateInput,
  PlanRecord,
  PlanReviewInput,
  PlanReviewRecord,
  PlanUpdateInput,
  PlanDraftUpdateInput,
  PostMarketPayload,
  ReviewFormData,
  ReviewPrefillData,
  ReviewRecord,
  StyleFactorSeriesItem,
  UnifiedSearchResult,
  TeacherNote,
  TeacherNoteCreateInput,
  TeacherRecord,
  TeacherTimelineItem,
  TradeCreateInput,
  TradeRecord,
  WatchlistCreateInput,
  WatchlistItem,
} from './types'

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
  // Meta
  getCommandIndex: () => request<CommandIndexPayload>('/meta/commands'),

  // Review
  getReview: (date: string) => request<ReviewRecord>(`/review/${date}`),
  getPrefill: (date: string) => request<ReviewPrefillData>(`/review/${date}/prefill`),
  saveReview: (date: string, data: ReviewFormData) =>
    request<ReviewRecord>(`/review/${date}`, { method: 'PUT', body: JSON.stringify(data) }),

  // Search
  unifiedSearch: (q: string, params?: Record<string, string>) => {
    const sp = new URLSearchParams({ q, ...params })
    return request<UnifiedSearchResult>(`/search/unified?${sp}`)
  },
  exportSearch: (q: string) => request<string>(`/search/export?q=${encodeURIComponent(q)}`),

  // Style factors
  getStyleFactors: (metrics: string, from: string, to: string) =>
    request<StyleFactorSeriesItem[]>(`/style-factors/series?metrics=${metrics}&from=${from}&to=${to}`),

  // Teachers
  getTeachers: () => request<TeacherRecord[]>('/teachers'),
  getTeacherTimeline: (id: number) => request<TeacherTimelineItem[]>(`/teachers/${id}/timeline`),

  // Notes
  getNotes: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<TeacherNote[]>(`/teacher-notes?${sp}`)
  },
  createNote: (data: TeacherNoteCreateInput) =>
    request<TeacherNote>('/teacher-notes', { method: 'POST', body: JSON.stringify(data) }),
  deleteNote: (id: number) =>
    request<{ ok?: boolean }>(`/teacher-notes/${id}`, { method: 'DELETE' }),

  // Holdings
  getHoldings: () => request<Holding[]>('/holdings'),
  getHoldingSignals: (date: string) =>
    request<HoldingSignalsPayload>(`/holdings/signals?date=${encodeURIComponent(date)}`),
  listHoldingTasks: (date?: string, status = 'open') => {
    const sp = new URLSearchParams()
    if (date) sp.set('date', date)
    if (status) sp.set('status', status)
    return request<HoldingTaskItem[]>(`/holdings/tasks?${sp}`)
  },
  updateHoldingTask: (id: number, data: HoldingTaskUpdateInput) =>
    request<{ ok?: boolean }>(`/holdings/tasks/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  createHolding: (data: HoldingCreateInput) =>
    request<Holding>('/holdings', { method: 'POST', body: JSON.stringify(data) }),
  updateHolding: (id: number, data: HoldingUpdateInput) =>
    request<{ ok?: boolean }>(`/holdings/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  deleteHolding: (id: number) =>
    request<{ ok?: boolean }>(`/holdings/${id}`, { method: 'DELETE' }),

  // Watchlist
  getWatchlist: (tier?: string) => {
    const sp = tier ? `?tier=${tier}` : ''
    return request<WatchlistItem[]>(`/watchlist${sp}`)
  },
  createWatchlistItem: (data: WatchlistCreateInput) =>
    request<WatchlistItem>('/watchlist', { method: 'POST', body: JSON.stringify(data) }),
  deleteWatchlistItem: (id: number) =>
    request<{ ok?: boolean }>(`/watchlist/${id}`, { method: 'DELETE' }),

  // Calendar
  getCalendarRange: (from: string, to: string) =>
    request<CalendarEvent[]>(`/calendar/range?from=${from}&to=${to}`),
  createCalendarEvent: (data: CalendarEventCreateInput) =>
    request<CalendarEvent>('/calendar', { method: 'POST', body: JSON.stringify(data) }),

  // Market
  getMarket: (date: string) => request<MarketFullData>(`/market/${date}`),
  getMarketHistory: (days: number = 20) => request<MarketChartItem[]>(`/market/history?days=${days}`),
  getPostMarket: (date: string) => request<PostMarketPayload>(`/post-market/${date}`),
  getMainThemes: () => request<MainThemeItem[]>('/main-themes'),

  // Industry info
  getIndustryInfo: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<IndustryInfoItem[]>(`/industry?${sp}`)
  },
  createIndustryInfo: (data: IndustryInfoCreateInput) =>
    request<IndustryInfoItem>('/industry', { method: 'POST', body: JSON.stringify(data) }),
  deleteIndustryInfo: (id: number) =>
    request<{ ok?: boolean }>(`/industry/${id}`, { method: 'DELETE' }),

  // Trades
  getTrades: (params?: Record<string, string>) => {
    const sp = new URLSearchParams(params || {})
    return request<TradeRecord[]>(`/trades?${sp}`)
  },
  createTrade: (data: TradeCreateInput) =>
    request<TradeRecord>('/trades', { method: 'POST', body: JSON.stringify(data) }),

  // Planning
  listPlanObservations: (date?: string, limit = 20) =>
    request<PlanObservationRecord[]>(`/plans/observations?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  updatePlanObservation: (observationId: string, data: PlanObservationUpdateInput) =>
    request<PlanObservationRecord>(`/plans/observations/${observationId}`, { method: 'PUT', body: JSON.stringify(data) }),
  listPlanDrafts: (date?: string, limit = 20) =>
    request<PlanDraftRecord[]>(`/plans/drafts?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  createPlanDraft: (data: PlanDraftCreateInput) =>
    request<PlanDraftRecord>('/plans/drafts', { method: 'POST', body: JSON.stringify(data) }),
  getPlanDraft: (draftId: string) =>
    request<PlanDraftRecord>(`/plans/drafts/${draftId}`),
  updatePlanDraft: (draftId: string, data: PlanDraftUpdateInput) =>
    request<PlanDraftRecord>(`/plans/drafts/${draftId}`, { method: 'PUT', body: JSON.stringify(data) }),
  confirmPlan: (draftId: string, data: PlanConfirmInput) =>
    request<PlanRecord>(`/plans/${draftId}/confirm`, { method: 'POST', body: JSON.stringify(data) }),
  listPlans: (date?: string, limit = 20) =>
    request<PlanRecord[]>(`/plans?${new URLSearchParams(date ? { date, limit: String(limit) } : { limit: String(limit) })}`),
  getPlan: (planId: string) =>
    request<PlanRecord>(`/plans/${planId}`),
  updatePlan: (planId: string, data: PlanUpdateInput) =>
    request<PlanRecord>(`/plans/${planId}`, { method: 'PUT', body: JSON.stringify(data) }),
  getPlanDiagnostics: (planId: string) =>
    request<PlanDiagnosticsRecord>(`/plans/${planId}/diagnostics`),
  reviewPlan: (planId: string, data: PlanReviewInput) =>
    request<PlanReviewRecord>(`/plans/${planId}/review`, { method: 'POST', body: JSON.stringify(data) }),

  // Ingest
  listIngestInterfaces: () =>
    request<IngestInterfaceRecord[]>('/ingest/interfaces'),
  inspectIngest: (date: string, interfaceName?: string | null, stage?: string | null) => {
    const sp = new URLSearchParams({ date })
    if (interfaceName) sp.set('interface', interfaceName)
    if (stage) sp.set('stage', stage)
    return request<IngestInspectRecord>(`/ingest/inspect?${sp}`)
  },
  listIngestRuns: (date: string, interfaceName?: string | null, stage?: string | null) => {
    const sp = new URLSearchParams({ date })
    if (interfaceName) sp.set('interface', interfaceName)
    if (stage) sp.set('stage', stage)
    return request<IngestRunRecord[]>(`/ingest/runs?${sp}`)
  },
  listIngestErrors: (date: string, interfaceName?: string | null, stage?: string | null) => {
    const sp = new URLSearchParams({ date })
    if (interfaceName) sp.set('interface', interfaceName)
    if (stage) sp.set('stage', stage)
    return request<IngestErrorRecord[]>(`/ingest/errors?${sp}`)
  },
  runIngestStage: (data: IngestRunStageInput) =>
    request<IngestRunStageResult>('/ingest/run', { method: 'POST', body: JSON.stringify(data) }),
  runIngestInterface: (data: IngestRunInterfaceInput) =>
    request<IngestRunInterfaceResult>('/ingest/run-interface', { method: 'POST', body: JSON.stringify(data) }),
  getIngestRetrySummary: (interfaceName?: string | null, stage?: string | null) => {
    const sp = new URLSearchParams()
    if (interfaceName) sp.set('interface', interfaceName)
    if (stage) sp.set('stage', stage)
    return request<IngestRetrySummary>(`/ingest/retry${sp.toString() ? `?${sp}` : ''}`)
  },
  getIngestHealthSummary: (date: string, days = 7, stage?: string | null) => {
    const sp = new URLSearchParams({ date, days: String(days) })
    if (stage) sp.set('stage', stage)
    return request<IngestHealthSummary>(`/ingest/health?${sp}`)
  },
  reconcileIngestRuns: (data: IngestReconcileInput = {}) =>
    request<IngestReconcileResult>('/ingest/reconcile', { method: 'POST', body: JSON.stringify(data) }),
  retryIngestGroups: (data: IngestRetryRunInput = {}) =>
    request<IngestRetryRunResult>('/ingest/retry-run', { method: 'POST', body: JSON.stringify(data) }),

  // Knowledge
  createKnowledgeAsset: (data: KnowledgeAssetCreateInput) =>
    request<KnowledgeAssetRecord>('/knowledge/assets', { method: 'POST', body: JSON.stringify(data) }),
  listKnowledgeAssets: (limit = 20) =>
    request<KnowledgeAssetRecord[]>(`/knowledge/assets?limit=${limit}`),
  draftFromAsset: (assetId: string, data: KnowledgeDraftInput) =>
    request<KnowledgeDraftResult>(`/knowledge/assets/${assetId}/draft`, { method: 'POST', body: JSON.stringify(data) }),
}
