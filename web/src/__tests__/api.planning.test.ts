import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../lib/api'

function mockJson(data: unknown) {
  return vi.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve(data),
  })
}

function mockError(status: number, text = 'error') {
  return vi.fn().mockResolvedValue({
    ok: false,
    status,
    statusText: text,
    text: () => Promise.resolve(text),
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('Planning API methods', () => {
  it('createPlanDraft sends POST /api/plans/drafts with body', async () => {
    globalThis.fetch = mockJson({ draft_id: 'draft_abc', trade_date: '2026-04-10' })
    const result = await api.createPlanDraft({ trade_date: '2026-04-10', market_facts: { bias: '震荡' } })
    expect(globalThis.fetch).toHaveBeenCalledOnce()
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/drafts')
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body)
    expect(body.trade_date).toBe('2026-04-10')
    expect(result.draft_id).toBe('draft_abc')
  })

  it('getPlanDraft sends GET /api/plans/drafts/{draftId}', async () => {
    globalThis.fetch = mockJson({ draft_id: 'draft_xyz', title: '测试草稿' })
    const result = await api.getPlanDraft('draft_xyz')
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/drafts/draft_xyz')
    expect(result.draft_id).toBe('draft_xyz')
  })

  it('confirmPlan sends POST /api/plans/{draftId}/confirm', async () => {
    globalThis.fetch = mockJson({ plan_id: 'plan_001', status: 'confirmed' })
    const result = await api.confirmPlan('draft_xyz', { trade_date: '2026-04-10', input_by: 'web' })
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/draft_xyz/confirm')
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body)
    expect(body.trade_date).toBe('2026-04-10')
    expect(result.plan_id).toBe('plan_001')
  })

  it('getPlan sends GET /api/plans/{planId}', async () => {
    globalThis.fetch = mockJson({ plan_id: 'plan_001', market_bias: '震荡' })
    const result = await api.getPlan('plan_001')
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/plan_001')
    expect(result.market_bias).toBe('震荡')
  })

  it('getPlanDiagnostics sends GET /api/plans/{planId}/diagnostics', async () => {
    globalThis.fetch = mockJson({
      plan_id: 'plan_001',
      fact_check_count: 3,
      missing_data_count: 1,
      items_json: [],
    })
    const result = await api.getPlanDiagnostics('plan_001')
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/plan_001/diagnostics')
    expect(result.fact_check_count).toBe(3)
  })

  it('reviewPlan sends POST /api/plans/{planId}/review', async () => {
    globalThis.fetch = mockJson({ review_id: 'plan_review_001', plan_id: 'plan_001' })
    const result = await api.reviewPlan('plan_001', {
      trade_date: '2026-04-10',
      outcome_summary: '完成',
      input_by: 'web',
    })
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/plans/plan_001/review')
    expect(init.method).toBe('POST')
    expect(result.review_id).toBe('plan_review_001')
  })

  it('throws on non-2xx from confirmPlan', async () => {
    globalThis.fetch = mockError(404, 'draft not found')
    await expect(api.confirmPlan('bad_id', { trade_date: '2026-04-10' })).rejects.toThrow('API 404')
  })
})

describe('Knowledge API methods', () => {
  it('createKnowledgeAsset sends POST /api/knowledge/assets', async () => {
    globalThis.fetch = mockJson({ asset_id: 'asset_abc', title: '机器人' })
    const result = await api.createKnowledgeAsset({
      asset_type: 'manual_note',
      title: '机器人',
      content: '机器人回流',
      tags: ['机器人'],
    })
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/knowledge/assets')
    expect(init.method).toBe('POST')
    const body = JSON.parse(init.body)
    expect(body.title).toBe('机器人')
    expect(result.asset_id).toBe('asset_abc')
  })

  it('listKnowledgeAssets sends query string from params', async () => {
    globalThis.fetch = mockJson([{ asset_id: 'a1' }])
    const result = await api.listKnowledgeAssets({ limit: 50, offset: 0, keyword: '锂电' })
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toContain('/api/knowledge/assets?')
    expect(url).toContain('limit=50')
    expect(url).toContain('keyword=')
    expect(Array.isArray(result)).toBe(true)
  })

  it('listKnowledgeAssets omits empty query when no params', async () => {
    globalThis.fetch = mockJson([])
    await api.listKnowledgeAssets({})
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/knowledge/assets')
  })

  it('draftFromAsset sends POST /api/knowledge/assets/{assetId}/draft', async () => {
    globalThis.fetch = mockJson({
      observation: { observation_id: 'obs_1', source_type: 'knowledge_asset' },
      draft: { draft_id: 'draft_k1', trade_date: '2026-04-10' },
    })
    const result = await api.draftFromAsset('asset_abc', {
      trade_date: '2026-04-10',
      input_by: 'web',
    })
    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(url).toBe('/api/knowledge/assets/asset_abc/draft')
    expect(init.method).toBe('POST')
    expect(result.draft.draft_id).toBe('draft_k1')
  })

  it('throws on non-2xx from draftFromAsset', async () => {
    globalThis.fetch = mockError(404, 'asset not found')
    await expect(api.draftFromAsset('bad_id', {})).rejects.toThrow('API 404')
  })
})
