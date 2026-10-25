import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import * as fx from './test/fixtures'
import { mockApi, renderAt } from './test/utils'

describe('Overview', () => {
  it('shows the KPIs and latest jobs from the API', async () => {
    mockApi({ '/api/overview': fx.overview })
    renderAt('/')
    expect(await screen.findByText('1,04,415')).toBeInTheDocument()
    expect(screen.getByText('96.03%')).toBeInTheDocument()
    expect(screen.getByText('p95 770 ms')).toBeInTheDocument()
    expect(screen.getByText('dirty_small.csv')).toBeInTheDocument()
    expect(screen.getAllByText('FAILED').length).toBeGreaterThan(0)
  })

  it('shows an error state when the API fails', async () => {
    mockApi({ '/api/overview': 500 })
    renderAt('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })

  it('has navigation to every section', async () => {
    mockApi({ '/api/overview': fx.overview })
    renderAt('/')
    const nav = screen.getAllByRole('navigation', { name: 'Main' })[0]
    for (const label of ['Analytics', 'Sources', 'Jobs', 'Data quality', 'Operations', 'Athena SQL', 'How it works']) {
      expect(within(nav).getByRole('link', { name: label })).toBeInTheDocument()
    }
  })
})

describe('Jobs', () => {
  it('filters by status and opens a job when a row is clicked', async () => {
    mockApi({
      '/api/jobs/eb5bf1873e7c3d7a/rejected': { columns: ['transaction_id', 'reasons'], rows: [['', 'missing_transaction_id']] },
      '/api/jobs/eb5bf1873e7c3d7a': fx.jobDetail,
      '/api/jobs': { jobs: [fx.dirtyJob, fx.poisonJob, fx.populationJob] },
    })
    const user = userEvent.setup()
    renderAt('/jobs')
    expect(await screen.findByText('3 of 3 jobs')).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Failed' }))
    expect(screen.getByText('1 of 3 jobs')).toBeInTheDocument()
    expect(screen.getByText(/missing required columns: unit_price/)).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'All' }))
    await user.click(screen.getByText('dirty_small.csv'))
    expect(await screen.findByRole('heading', { name: 'dirty_small.csv' })).toBeInTheDocument()
    expect(await screen.findByText('missing_transaction_id')).toBeInTheDocument()
  })

  it('searches by file name', async () => {
    mockApi({ '/api/jobs': { jobs: [fx.dirtyJob, fx.poisonJob, fx.populationJob] } })
    const user = userEvent.setup()
    renderAt('/jobs')
    await user.type(await screen.findByRole('searchbox'), 'population')
    expect(screen.getByText('1 of 3 jobs')).toBeInTheDocument()
  })
})

describe('Analytics', () => {
  it('defaults to world population and explains snapshot data', async () => {
    mockApi({
      '/api/datasets': {
        datasets: [
          { name: 'sales', jobs: 3, succeeded: 2, latest: fx.dirtyJob },
          { name: 'world_population', jobs: 1, succeeded: 1, latest: fx.populationJob },
          { name: 'returns', jobs: 0, succeeded: 0, latest: null },
        ],
      },
      '/api/analytics/world_population': fx.populationAnalytics,
    })
    renderAt('/analytics')
    expect(await screen.findByText(/Snapshot dataset\./)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'World population' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.queryByRole('radio', { name: 'Returns' })).not.toBeInTheDocument() // no data yet
    expect(screen.getByText('8.22B')).toBeInTheDocument()
    expect(screen.getByText('World population by year')).toBeInTheDocument()
    expect(screen.getByText(/49 country_code values excluded · top 2 of 217/)).toBeInTheDocument()
  })
})

describe('Operations', () => {
  it('warns when the dead-letter queue is not empty', async () => {
    mockApi({ '/api/operations': fx.operations })
    renderAt('/operations')
    expect(await screen.findByText(/1 message\(s\) in the DLQ/)).toBeInTheDocument()
    expect(screen.getByText('poison.csv')).toBeInTheDocument()
  })
})

describe('Athena', () => {
  it('explains when analytics is disabled', async () => {
    mockApi({ '/api/athena': { enabled: false, queries: [] } })
    renderAt('/athena')
    expect(await screen.findByText(/not enabled on this stack/)).toBeInTheDocument()
  })

  it('runs the selected saved query and shows bytes scanned', async () => {
    mockApi({
      '/api/athena/monthly-revenue': {
        name: 'monthly-revenue', title: 'Monthly revenue', sql: 'SELECT 1', columns: ['month', 'revenue'],
        rows: [['2026-09', '543194.63'], ['2026-10', '183785.39']], bytes_scanned: 6839,
        runtime_ms: 700, ran_at: '2026-10-25T04:00:00Z',
      },
      '/api/athena': { enabled: true, queries: [{ name: 'monthly-revenue', title: 'Monthly revenue', sql: 'SELECT 1' }] },
    })
    renderAt('/athena')
    expect(await screen.findByText('6.7 KB')).toBeInTheDocument()
    expect(screen.getByText('543194.63')).toBeInTheDocument()
  })
})

describe('Routing', () => {
  it('shows a not-found page for unknown URLs', async () => {
    mockApi({})
    renderAt('/does-not-exist')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
