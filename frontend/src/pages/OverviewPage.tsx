import { useQuery } from '@tanstack/react-query'
import { ArrowRight } from 'lucide-react'
import { Link } from 'react-router'
import { StackedBars } from '../components/charts'
import { COLORS } from '../lib/colors'
import { JobsTable } from '../components/JobsTable'
import { Card, ErrorState, Kpi, Loading, PageHeader } from '../components/ui'
import { api } from '../lib/api'
import { fmtInt, fmtMs, fmtPct } from '../lib/format'

const STEPS = ['Scheduler', 'Ingester λ', 'S3 raw/', 'SQS', 'Processor λ', 'S3 + DynamoDB']

export function OverviewPage() {
  const { data, error, isPending } = useQuery({
    queryKey: ['overview'],
    queryFn: api.overview,
    refetchInterval: 30_000,
  })

  return (
    <>
      <PageHeader
        title="Overview"
        description="Public CSV datasets are fetched automatically, validated row by row, cleaned and stored. Every file becomes one job tracked in DynamoDB."
      />
      <ol className="mb-6 flex flex-wrap items-center gap-2 text-xs font-medium text-slate-600">
        {STEPS.map((step, i) => (
          <li key={step} className="flex items-center gap-2">
            <span className="rounded-full bg-white px-3 py-1 ring-1 ring-slate-200">{step}</span>
            {i < STEPS.length - 1 && <ArrowRight className="size-3 text-slate-400" aria-hidden />}
          </li>
        ))}
        <li>
          <Link to="/about" className="ml-1 text-brand-600 hover:underline">
            How it works →
          </Link>
        </li>
      </ol>

      {isPending && <Loading />}
      {error && <ErrorState error={error} />}
      {data && (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <Kpi label="Files processed" value={fmtInt(data.kpis.files_processed)} />
            <Kpi
              label="Failed jobs"
              value={fmtInt(data.kpis.failed_jobs)}
              tone={data.kpis.failed_jobs ? 'bad' : 'good'}
              hint={data.kpis.failed_jobs ? 'see Operations' : 'all healthy'}
            />
            <Kpi label="Rows processed" value={fmtInt(data.kpis.rows_processed)} />
            <Kpi label="Acceptance rate" value={fmtPct(data.kpis.acceptance_rate)} tone="good" />
            <Kpi
              label="Avg job time"
              value={fmtMs(data.kpis.avg_duration_ms)}
              hint={`p95 ${fmtMs(data.kpis.p95_duration_ms)}`}
            />
            <Kpi label="Datasets" value={fmtInt(data.kpis.datasets)} />
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Jobs per day" subtitle="By final status">
              <StackedBars
                data={data.jobs_per_day}
                xKey="day"
                series={[
                  { key: 'SUCCEEDED', label: 'Succeeded', color: COLORS.ok },
                  { key: 'FAILED', label: 'Failed', color: COLORS.bad },
                  { key: 'PROCESSING', label: 'Processing', color: COLORS.warn },
                ]}
              />
            </Card>
            <Card title="Rows per day" subtitle="Accepted vs rejected by validation">
              <StackedBars
                data={data.rows_per_day}
                xKey="day"
                series={[
                  { key: 'accepted', label: 'Accepted', color: COLORS.ok },
                  { key: 'rejected', label: 'Rejected', color: COLORS.bad },
                ]}
              />
            </Card>
          </div>

          <Card
            title="Latest jobs"
            actions={
              <Link to="/jobs" className="text-sm font-medium text-brand-600 hover:underline">
                All jobs →
              </Link>
            }
          >
            <JobsTable jobs={data.latest} />
          </Card>
        </div>
      )}
    </>
  )
}
