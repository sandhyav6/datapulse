// Shapes returned by the read-only API (backend/src/api). Timestamps are ISO-8601 UTC strings.

export type JobStatus = 'SUCCEEDED' | 'FAILED' | 'PROCESSING'

export interface JobSummary {
  job_id: string
  status: JobStatus
  dataset?: string
  file: string
  source_key?: string
  started_at?: string
  finished_at?: string
  duration_ms?: number
  attempts?: number
  rows_total?: number
  rows_accepted?: number
  rows_rejected?: number
  acceptance_rate?: number
  error_type?: string
  error_message?: string
  rejection_reasons?: Record<string, number>
  metrics?: Record<string, number>
}

export interface Overview {
  kpis: {
    files_processed: number
    failed_jobs: number
    processing_jobs: number
    rows_processed: number
    acceptance_rate: number | null
    avg_duration_ms: number | null
    p95_duration_ms: number | null
    datasets: number
  }
  jobs_per_day: { day: string; SUCCEEDED: number; FAILED: number; PROCESSING: number }[]
  rows_per_day: { day: string; accepted: number; rejected: number }[]
  latest: JobSummary[]
}

export interface Report {
  counts: { total: number; accepted: number; rejected: number }
  acceptance_rate: number
  rejection_reasons: Record<string, number>
  metrics: Record<string, number>
  rules_applied?: string[]
  [key: string]: unknown
}

export interface JobDetail {
  job: JobSummary & { outputs: Record<string, string>; ingest_date?: string }
  report: Report | null
}

export interface TableSample {
  columns: string[]
  rows: string[][]
}

export interface DatasetInfo {
  name: string
  description?: string
  load_type?: 'snapshot' | 'incremental'
  jobs: number
  succeeded: number
  latest: JobSummary | null
}

export type ChartType = 'daily' | 'yearly' | 'group'

export interface Chart {
  name: string
  type: ChartType
  func: string
  where: Record<string, string>
  exclude: Record<string, number>
  points: Record<string, string | number>[]
  size: number
}

export interface Analytics {
  dataset: string
  description?: string
  load_type?: string
  mode: 'snapshot' | 'incremental'
  measure?: string
  source: { job_id: string; started_at?: string; files: number; rows: number }
  metrics: { name: string; value: number | null; func?: string | null }[]
  charts: Chart[]
}

export interface Quality {
  dataset: string | null
  rows_checked: number
  rows_rejected: number
  acceptance_rate: number | null
  reasons: { reason: string; count: number }[]
  per_job: {
    job_id: string
    file: string
    dataset?: string
    started_at?: string
    rows_total?: number
    rows_rejected?: number
    acceptance_rate?: number
    reasons: Record<string, number>
  }[]
}

export interface Source {
  dataset: string
  url: string
  homepage?: string
  publisher?: string
  license?: string
  description?: string
  load_type?: string
  raw_key: string
  latest: JobSummary | null
}

export interface Operations {
  queues: { waiting: number | null; in_flight: number | null; dlq: number | null }
  failed: JobSummary[]
  retrying: JobSummary[]
}

export interface AthenaQuery {
  name: string
  title: string
  sql: string
}

export interface AthenaResult extends AthenaQuery {
  columns: string[]
  rows: (string | null)[][]
  bytes_scanned: number
  runtime_ms: number
  ran_at: string
}
