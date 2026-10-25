import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router'
import { HorizontalBars } from '../components/charts'
import { COLORS } from '../lib/colors'
import {
  Card,
  DataTable,
  Empty,
  ErrorState,
  Kpi,
  Loading,
  Notice,
  PageHeader,
  StatusBadge,
} from '../components/ui'
import { api } from '../lib/api'
import { fmtDateTime, fmtInt, fmtMs, fmtNumber, fmtPct, humanize } from '../lib/format'

export function JobDetailPage() {
  const { jobId = '' } = useParams()
  const detail = useQuery({ queryKey: ['job', jobId], queryFn: () => api.job(jobId) })
  const rejected = useQuery({
    queryKey: ['rejected', jobId],
    queryFn: () => api.rejected(jobId),
    enabled: Boolean(detail.data?.job.rows_rejected),
  })

  const back = (
    <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-brand-600 hover:underline">
      <ArrowLeft className="size-4" aria-hidden /> All jobs
    </Link>
  )
  if (detail.isPending) return <Loading />
  if (detail.error) return <>{back}<div className="mt-4"><ErrorState error={detail.error} /></div></>

  const { job, report } = detail.data
  const reasons = Object.entries(job.rejection_reasons ?? {})
    .map(([reason, count]) => ({ reason: humanize(reason), count }))
    .sort((a, b) => b.count - a.count)

  return (
    <>
      {back}
      <PageHeader
        title={job.file}
        description={
          <>
            Job <code>{job.job_id}</code> · dataset <code>{job.dataset}</code> · started{' '}
            {fmtDateTime(job.started_at)}
          </>
        }
        actions={<StatusBadge status={job.status} />}
      />

      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
          <Kpi label="Rows" value={fmtInt(job.rows_total)} />
          <Kpi label="Accepted" value={fmtInt(job.rows_accepted)} tone="good" />
          <Kpi label="Rejected" value={fmtInt(job.rows_rejected)} tone={job.rows_rejected ? 'bad' : 'default'} />
          <Kpi label="Duration" value={fmtMs(job.duration_ms)} />
          <Kpi
            label="Attempts"
            value={fmtInt(job.attempts)}
            hint={job.attempts && job.attempts > 1 ? 'retried or redriven' : 'first try'}
          />
        </div>

        {job.error_type && (
          <Notice tone="bad">
            <strong>{job.error_type}</strong>: {job.error_message}
          </Notice>
        )}

        <div className="grid gap-6 lg:grid-cols-2">
          <Card title="Rejection reasons" subtitle="Machine-readable codes from the dataset schema">
            {reasons.length ? (
              <HorizontalBars
                data={reasons}
                labelKey="reason"
                valueKey="count"
                color={COLORS.bad}
                valueFormat={(v) => fmtInt(v)}
              />
            ) : (
              <Empty>No rejected rows.</Empty>
            )}
          </Card>
          <Card title="Metrics and outputs">
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-slate-500">Acceptance</dt>
                <dd className="font-medium">{fmtPct(job.acceptance_rate)}</dd>
              </div>
              {Object.entries(job.metrics ?? {}).map(([name, value]) => (
                <div key={name}>
                  <dt className="text-slate-500">{humanize(name)}</dt>
                  <dd className="font-medium tabular-nums">{fmtNumber(value, 4)}</dd>
                </div>
              ))}
            </dl>
            <ul className="mt-4 space-y-1 text-xs">
              {Object.entries(job.outputs ?? {}).map(([name, uri]) => (
                <li key={name} className="truncate">
                  <span className="font-semibold text-slate-600">{name}</span>{' '}
                  <code title={uri}>{uri}</code>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        {Boolean(job.rows_rejected) && (
          <Card title="Rejected rows" subtitle="First 50 rows of rejected.csv: original values plus the reasons column">
            {rejected.isPending && <Loading />}
            {rejected.error && <ErrorState error={rejected.error} />}
            {rejected.data && <DataTable columns={rejected.data.columns} rows={rejected.data.rows} />}
          </Card>
        )}

        {report && (
          <Card title="Full report" subtitle="reports/ JSON written by the processor Lambda">
            <details>
              <summary className="cursor-pointer text-sm font-medium text-brand-600">Show JSON</summary>
              <pre className="mt-3 max-h-[32rem] overflow-auto rounded-lg bg-slate-900 p-4 text-xs text-slate-100">
                {JSON.stringify(report, null, 2)}
              </pre>
            </details>
          </Card>
        )}
      </div>
    </>
  )
}
