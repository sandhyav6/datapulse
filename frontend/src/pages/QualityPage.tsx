import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { HorizontalBars, TrendLine } from '../components/charts'
import { COLORS } from '../lib/colors'
import { Card, Empty, ErrorState, Kpi, Loading, PageHeader, Segmented } from '../components/ui'
import { api } from '../lib/api'
import { fmtDateTime, fmtInt, fmtPct, reasonLabel } from '../lib/format'
import { useState } from 'react'

export function QualityPage() {
  const [dataset, setDataset] = useState('')
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: api.datasets })
  const quality = useQuery({ queryKey: ['quality', dataset], queryFn: () => api.quality(dataset) })
  const options = [
    { value: '', label: 'All datasets' },
    ...(datasets.data ?? [])
      .filter((d) => d.succeeded > 0)
      .map((d) => ({ value: d.name, label: d.name })),
  ]
  const data = quality.data
  const trend = (data?.per_job ?? []).map((j, i) => ({
    job: `#${i + 1}`,
    acceptance: j.acceptance_rate ?? 0,
  }))
  const withRejections = (data?.per_job ?? []).filter((j) => j.rows_rejected).reverse()

  return (
    <>
      <PageHeader
        title="Data quality"
        description="Why rows are rejected. Every reason is a machine-readable code produced by the schema rules (required, type, range, duplicate)."
      />
      <div className="mb-6">
        <Segmented label="Dataset" value={dataset} onChange={setDataset} options={options} />
      </div>
      {quality.isPending && <Loading />}
      {quality.error && <ErrorState error={quality.error} />}
      {data && (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Kpi label="Rows checked" value={fmtInt(data.rows_checked)} />
            <Kpi label="Rows rejected" value={fmtInt(data.rows_rejected)} tone={data.rows_rejected ? 'bad' : 'good'} />
            <Kpi label="Acceptance rate" value={fmtPct(data.acceptance_rate)} tone="good" />
            <Kpi label="Issue types" value={fmtInt(data.reasons.length)} />
          </div>
          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Rejections by reason">
              {data.reasons.length ? (
                <HorizontalBars
                  data={data.reasons.map((r) => ({ reason: reasonLabel(r.reason), count: r.count }))}
                  labelKey="reason"
                  valueKey="count"
                  color={COLORS.bad}
                  valueFormat={(v) => fmtInt(v)}
                />
              ) : (
                <Empty>No rejected rows. Every row passed validation.</Empty>
              )}
            </Card>
            <Card title="Acceptance rate per job" subtitle="Oldest to newest">
              {trend.length ? (
                <TrendLine
                  data={trend}
                  xKey="job"
                  yKey="acceptance"
                  label="Acceptance %"
                  yFormat={(v) => `${v}%`}
                  domain={[0, 100]}
                />
              ) : (
                <Empty>No jobs yet.</Empty>
              )}
            </Card>
          </div>
          <Card title="Jobs with rejected rows" subtitle="Open a job to inspect its rejected.csv">
            {withRejections.length ? (
              <ul className="divide-y divide-slate-100">
                {withRejections.map((j) => (
                  <li key={j.job_id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
                    <Link to={`/jobs/${j.job_id}`} className="font-medium text-brand-600 hover:underline">
                      {j.file}
                    </Link>
                    <span className="text-slate-500">{j.dataset}</span>
                    <span className="text-slate-500">{fmtDateTime(j.started_at)}</span>
                    <span className="tabular-nums text-red-700">{fmtInt(j.rows_rejected)} rejected</span>
                    <span className="text-xs text-slate-500">
                      {Object.entries(j.reasons)
                        .map(([r, c]) => `${reasonLabel(r)} ${c}`)
                        .join(' · ')}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <Empty>No job has rejected rows.</Empty>
            )}
          </Card>
        </div>
      )}
    </>
  )
}
