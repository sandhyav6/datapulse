import type {
  Analytics,
  AthenaQuery,
  AthenaResult,
  DatasetInfo,
  JobDetail,
  JobStatus,
  JobSummary,
  Operations,
  Overview,
  Quality,
  Source,
  TableSample,
} from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** GET a JSON endpoint on the same origin (CloudFront routes /api/* to API Gateway). */
export async function getJson<T>(path: string, params?: Record<string, string | undefined>) {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value) query.set(key, value)
  }
  const url = query.size ? `${path}?${query}` : path
  const response = await fetch(url, { headers: { accept: 'application/json' } })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { error?: string }
      if (body.error) message = body.error
    } catch {
      /* non-JSON error page */
    }
    throw new ApiError(response.status, message)
  }
  return (await response.json()) as T
}

export const api = {
  overview: () => getJson<Overview>('/api/overview'),
  jobs: (filters: { status?: JobStatus; dataset?: string } = {}) =>
    getJson<{ jobs: JobSummary[] }>('/api/jobs', filters).then((r) => r.jobs),
  job: (jobId: string) => getJson<JobDetail>(`/api/jobs/${jobId}`),
  rejected: (jobId: string) => getJson<TableSample>(`/api/jobs/${jobId}/rejected`),
  datasets: () => getJson<{ datasets: DatasetInfo[] }>('/api/datasets').then((r) => r.datasets),
  analytics: (dataset: string) => getJson<Analytics>(`/api/analytics/${dataset}`),
  quality: (dataset?: string) => getJson<Quality>('/api/quality', { dataset }),
  sources: () => getJson<{ schedule: string; sources: Source[] }>('/api/sources'),
  operations: () => getJson<Operations>('/api/operations'),
  athenaQueries: () => getJson<{ enabled: boolean; queries: AthenaQuery[] }>('/api/athena'),
  athena: (name: string) => getJson<AthenaResult>(`/api/athena/${name}`),
}
