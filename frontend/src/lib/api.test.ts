import { describe, expect, it } from 'vitest'
import { mockApi } from '../test/utils'
import { api, ApiError, getJson } from './api'

describe('api client', () => {
  it('calls same-origin /api paths with query parameters, skipping empty ones', async () => {
    const calls = mockApi({ '/api/jobs': { jobs: [] }, '/api/quality': { reasons: [] } })
    await api.jobs({ status: 'FAILED', dataset: undefined })
    await api.quality()
    expect(calls).toEqual(['/api/jobs?status=FAILED', '/api/quality'])
  })

  it('unwraps list endpoints', async () => {
    mockApi({ '/api/datasets': { datasets: [{ name: 'sales' }] } })
    await expect(api.datasets()).resolves.toEqual([{ name: 'sales' }])
  })

  it('raises ApiError with the server message', async () => {
    mockApi({ '/api/overview': 500 })
    const error = await getJson('/api/overview').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(500)
    expect((error as ApiError).message).toBe('boom')
  })
})
