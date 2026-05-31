import { describe, it, expect, vi, afterEach } from 'vitest'
import { getImageUrl, fetchProperties, fetchFeedback } from './api'

describe('getImageUrl', () => {
  it('builds an encoded image URL', () => {
    expect(getImageUrl('123', 'a b.jpg')).toBe('/api/images/123/a%20b.jpg')
  })

  it('encodes path separators in both segments', () => {
    expect(getImageUrl('a/b', 'x/y.jpg')).toBe('/api/images/a%2Fb/x%2Fy.jpg')
  })
})

describe('fetchProperties', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('returns parsed JSON when the response is ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, json: async () => [{ property_id: 'P1' }],
    }))
    await expect(fetchProperties()).resolves.toEqual([{ property_id: 'P1' }])
  })

  it('throws the server error message when not ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 500, json: async () => ({ error: 'boom' }),
    }))
    await expect(fetchProperties()).rejects.toThrow('boom')
  })
})

describe('fetchFeedback', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('returns [] on a failed request instead of throwing', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('network down')))
    await expect(fetchFeedback()).resolves.toEqual([])
  })
})
