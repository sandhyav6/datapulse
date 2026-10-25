// API responses shaped exactly like backend/src/api (see backend/tests/api/test_api.py).
import type { Analytics, JobDetail, JobSummary, Operations, Overview } from '../lib/types'

export const dirtyJob: JobSummary = {
  job_id: 'eb5bf1873e7c3d7a',
  status: 'SUCCEEDED',
  dataset: 'sales',
  file: 'dirty_small.csv',
  started_at: '2026-10-24T09:00:51Z',
  duration_ms: 272,
  attempts: 1,
  rows_total: 1000,
  rows_accepted: 950,
  rows_rejected: 50,
  acceptance_rate: 95,
  rejection_reasons: { duplicate_id: 10, negative_quantity: 10, invalid_unit_price: 10 },
  metrics: { total_sales: 726980.02 },
}

export const poisonJob: JobSummary = {
  job_id: '603022fde2c40966',
  status: 'FAILED',
  dataset: 'sales',
  file: 'poison.csv',
  started_at: '2026-10-25T03:53:36Z',
  attempts: 3,
  error_type: 'HeaderValidationError',
  error_message: 'missing required columns: unit_price',
}

export const populationJob: JobSummary = {
  job_id: '9c4359bde50d261f',
  status: 'SUCCEEDED',
  dataset: 'world_population',
  file: 'world_population.csv',
  started_at: '2026-10-24T08:59:00Z',
  duration_ms: 1013,
  attempts: 1,
  rows_total: 17394,
  rows_accepted: 17394,
  rows_rejected: 0,
  acceptance_rate: 100,
}

export const overview: Overview = {
  kpis: {
    files_processed: 29,
    failed_jobs: 1,
    processing_jobs: 0,
    rows_processed: 104415,
    acceptance_rate: 96.03,
    avg_duration_ms: 412,
    p95_duration_ms: 770,
    datasets: 4,
  },
  jobs_per_day: [{ day: '2026-10-24', SUCCEEDED: 29, FAILED: 1, PROCESSING: 0 }],
  rows_per_day: [{ day: '2026-10-24', accepted: 100000, rejected: 4415 }],
  latest: [poisonJob, dirtyJob, populationJob],
}

export const jobDetail: JobDetail = {
  job: { ...dirtyJob, outputs: { processed: 's3://b/processed/x.csv', rejected: 's3://b/rejected/x.csv' } },
  report: {
    counts: { total: 1000, accepted: 950, rejected: 50 },
    acceptance_rate: 95,
    rejection_reasons: dirtyJob.rejection_reasons ?? {},
    metrics: { total_sales: 726980.02 },
  },
}

export const populationAnalytics: Analytics = {
  dataset: 'world_population',
  description: 'Total population per country and per World Bank aggregate.',
  load_type: 'snapshot',
  mode: 'snapshot',
  measure: 'value',
  source: { job_id: '9c4359bde50d261f', started_at: '2026-10-24T08:59:00Z', files: 1, rows: 17394 },
  metrics: [
    { name: 'largest_population', value: 8215424893, func: 'max' },
    { name: 'rows', value: 17394, func: 'count' },
  ],
  charts: [
    {
      name: 'world_population_by_year',
      type: 'yearly',
      func: 'sum',
      where: { country_code: 'WLD' },
      exclude: {},
      points: [
        { year: 2024, value: 8140897523 },
        { year: 2025, value: 8215424893 },
      ],
      size: 2,
    },
    {
      name: 'peak_population_by_country',
      type: 'group',
      func: 'max',
      where: {},
      exclude: { country_code: 49 },
      points: [
        { key: 'India', value: 1463865525 },
        { key: 'China', value: 1408975000 },
      ],
      size: 217,
    },
  ],
}

export const operations: Operations = {
  queues: { waiting: 0, in_flight: 0, dlq: 1 },
  failed: [poisonJob],
  retrying: [],
}
