import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { DailyArea, HorizontalBars, TrendLine } from '../components/charts'
import { Card, Empty, ErrorState, Kpi, Loading, Notice, PageHeader, Segmented } from '../components/ui'
import { api } from '../lib/api'
import { fmtCompact, fmtDateTime, fmtInt, fmtNumber, humanize } from '../lib/format'
import type { Chart } from '../lib/types'

function chartSubtitle(chart: Chart): string {
  const parts = [chart.func]
  for (const [column, value] of Object.entries(chart.where)) parts.push(`${column} = ${value}`)
  for (const [column, count] of Object.entries(chart.exclude))
    parts.push(`${count} ${column} values excluded`)
  if (chart.type === 'group' && chart.size > chart.points.length)
    parts.push(`top ${chart.points.length} of ${chart.size}`)
  return parts.join(' · ')
}

function ChartCard({ chart }: { chart: Chart }) {
  const body = !chart.points.length ? (
    <Empty>No data for this aggregate.</Empty>
  ) : chart.type === 'daily' ? (
    <DailyArea data={chart.points} />
  ) : chart.type === 'yearly' ? (
    <TrendLine data={chart.points} xKey="year" yKey="value" label={humanize(chart.name)} />
  ) : (
    <HorizontalBars data={chart.points} labelKey="key" valueKey="value" />
  )
  return (
    <Card title={humanize(chart.name)} subtitle={chartSubtitle(chart)}>
      {body}
    </Card>
  )
}

export function AnalyticsPage() {
  const [params, setParams] = useSearchParams()
  const datasets = useQuery({ queryKey: ['datasets'], queryFn: api.datasets })
  const available = (datasets.data ?? []).filter((d) => d.succeeded > 0)
  const fallback =
    available.find((d) => d.name === 'world_population')?.name ?? available[0]?.name ?? ''
  const selected = params.get('dataset') ?? fallback
  const analytics = useQuery({
    queryKey: ['analytics', selected],
    queryFn: () => api.analytics(selected),
    enabled: Boolean(selected),
  })

  const data = analytics.data
  const lines = data?.charts.filter((c) => c.type !== 'group') ?? []
  const groups = data?.charts.filter((c) => c.type === 'group') ?? []

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Explore each dataset through the aggregates its schema defines. Computed by the pipeline as it processes each file. No table scans."
      />
      {datasets.isPending && <Loading />}
      {datasets.error && <ErrorState error={datasets.error} />}
      {datasets.data && !available.length && <Empty>No dataset has been processed yet.</Empty>}
      {available.length > 0 && (
        <div className="mb-6">
          <Segmented
            label="Dataset"
            value={selected}
            onChange={(value) => setParams({ dataset: value })}
            options={available.map((d) => ({ value: d.name, label: humanize(d.name) }))}
          />
        </div>
      )}

      {analytics.isPending && selected && <Loading />}
      {analytics.error && <ErrorState error={analytics.error} />}
      {data && (
        <div className="flex flex-col gap-6">
          {data.description && <p className="max-w-3xl text-sm text-slate-600">{data.description}</p>}
          <Notice>
            {data.mode === 'snapshot' ? (
              <>
                <strong>Snapshot dataset.</strong> Each download replaces the previous one, so this
                shows the latest version, processed {fmtDateTime(data.source.started_at)} (
                {fmtInt(data.source.rows)} clean rows, job <code>{data.source.job_id}</code>).
                Older versions stay in S3.
              </>
            ) : (
              <>
                <strong>Incremental dataset.</strong> Each file adds new rows, so these totals
                combine all {fmtInt(data.source.files)} processed files.
              </>
            )}
          </Notice>

          {data.metrics.length > 0 && (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
              {data.metrics.map((m) => (
                <Kpi
                  key={m.name}
                  label={humanize(m.name)}
                  value={
                    m.value != null && Math.abs(m.value) >= 1e6
                      ? fmtCompact(m.value)
                      : fmtNumber(m.value, 4)
                  }
                  hint={m.func && data.measure ? `${m.func} of ${data.measure}` : undefined}
                />
              ))}
            </div>
          )}

          {lines.map((chart) => (
            <ChartCard key={chart.name} chart={chart} />
          ))}
          {groups.length > 0 && (
            <div className="grid gap-6 xl:grid-cols-2">
              {groups.map((chart) => (
                <ChartCard key={chart.name} chart={chart} />
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}
