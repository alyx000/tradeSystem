import { describe, it, expect, vi, beforeEach } from 'vitest'
import { api } from '../lib/api'

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('api client', () => {
  it('handles network error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'))
    await expect(api.getTeachers()).rejects.toThrow('Network error')
  })

  it('handles 422 error', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      text: () => Promise.resolve('Invalid date format'),
    })
    await expect(api.getReview('bad')).rejects.toThrow('API 422')
  })

  it('handles successful JSON response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve([{ id: 1, name: 'test' }]),
    })
    const result = await api.getTeachers()
    expect(result).toEqual([{ id: 1, name: 'test' }])
  })

  it('getConcentrationHistory hits concentration endpoint with days param', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      headers: new Headers({ 'content-type': 'application/json' }),
      json: () => Promise.resolve({ requested_days: 30, series: [], sector_keys: [], snapshot: null }),
    })
    globalThis.fetch = fetchMock
    const result = await api.getConcentrationHistory(15)
    expect(fetchMock).toHaveBeenCalledWith('/api/market/concentration/history?days=15', expect.anything())
    expect(result.requested_days).toBe(30)
  })
})
