import { describe, it, expect, vi, beforeEach } from 'vitest'
import { listCognitions, listInstances, listReviews, getCognitionById, getCognitionReview } from '../lib/api'

beforeEach(() => {
  vi.restoreAllMocks()
})

function mockOkJson(body: unknown) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve(body),
  })
}

describe('cognition api client', () => {
  it('listCognitions builds query string with filters', async () => {
    mockOkJson({ total: 0, cognitions: [] })
    await listCognitions({ category: 'signal', status: 'active', limit: 50, offset: 0 })
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toContain('/api/cognition/cognitions?')
    expect(url).toContain('category=signal')
    expect(url).toContain('status=active')
    expect(url).toContain('limit=50')
    expect(url).toContain('offset=0')
  })

  it('listCognitions omits empty filters', async () => {
    mockOkJson({ total: 0, cognitions: [] })
    await listCognitions()
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toBe('/api/cognition/cognitions')
  })

  it('listInstances includes date range filters', async () => {
    mockOkJson({ total: 0, instances: [] })
    await listInstances({ cognition_id: 'cog_x', date_from: '2026-04-01', date_to: '2026-04-15' })
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toContain('cognition_id=cog_x')
    expect(url).toContain('date_from=2026-04-01')
    expect(url).toContain('date_to=2026-04-15')
  })

  it('listReviews passes period_type', async () => {
    mockOkJson({ total: 0, reviews: [] })
    await listReviews({ period_type: 'weekly', status: 'confirmed' })
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toContain('period_type=weekly')
    expect(url).toContain('status=confirmed')
  })

  it('getCognitionById encodes id in path', async () => {
    mockOkJson({ cognition: { cognition_id: 'x' } })
    await getCognitionById('cog/with space')
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toBe('/api/cognition/cognitions/cog%2Fwith%20space')
  })

  it('getCognitionReview encodes id and hits reviews endpoint', async () => {
    mockOkJson({ review: { review_id: 'rev_x' } })
    await getCognitionReview('rev_x')
    const url = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[0] as string
    expect(url).toBe('/api/cognition/reviews/rev_x')
  })
})
