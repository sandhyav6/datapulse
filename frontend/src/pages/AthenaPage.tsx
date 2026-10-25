import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { HorizontalBars } from '../components/charts'
import {
  Card,
  DataTable,
  ErrorState,
  Kpi,
  Loading,
  Notice,
  PageHeader,
  Segmented,
} from '../components/ui'
import { api } from '../lib/api'
import { fmtBytes, fmtDateTime, fmtInt, fmtMs } from '../lib/format'

export function AthenaPage() {
  const list = useQuery({ queryKey: ['athena'], queryFn: api.athenaQueries })
  const [name, setName] = useState('monthly-revenue')
  const result = useQuery({
    queryKey: ['athena', name],
    queryFn: () => api.athena(name),
    enabled: Boolean(list.data?.enabled),
    staleTime: 10 * 60_000,
  })

  const data = result.data
  const numericCol = data?.columns.findIndex((_, i) =>
    data.rows.every((r) => r[i] == null || !Number.isNaN(Number(r[i]))),
  )
  const chartable =
    data && numericCol != null && numericCol > 0
      ? data.rows.slice(0, 15).map((r) => ({
          label: r.slice(0, numericCol).join(' · '),
          value: Number(r[numericCol]),
        }))
      : []

  return (
    <>
      <PageHeader
        title="Athena SQL"
        description="Serverless SQL over the curated Parquet data lake (AWS Glue table with partition projection, no crawler). The public site can only run these saved, read-only queries."
      />
      {list.isPending && <Loading />}
      {list.error && <ErrorState error={list.error} />}
      {list.data && !list.data.enabled && (
        <Notice>Analytics (V2) is not enabled on this stack. Deploy with EnableAnalytics=true.</Notice>
      )}
      {list.data?.enabled && (
        <div className="flex flex-col gap-6">
          <Segmented
            label="Saved query"
            value={name}
            onChange={setName}
            options={list.data.queries.map((q) => ({ value: q.name, label: q.title }))}
          />
          <Card title="SQL">
            <pre className="overflow-x-auto rounded-lg bg-slate-900 p-4 text-xs text-slate-100">
              {list.data.queries.find((q) => q.name === name)?.sql}
            </pre>
          </Card>
          {result.isPending && <Loading label="Running on Athena…" />}
          {result.error && <ErrorState error={result.error} />}
          {data && (
            <>
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                <Kpi label="Rows" value={fmtInt(data.rows.length)} />
                <Kpi label="Data scanned" value={fmtBytes(data.bytes_scanned)} hint="billed per byte scanned" />
                <Kpi label="Run time" value={fmtMs(data.runtime_ms)} />
                <Kpi label="Ran at" value={<span className="text-base">{fmtDateTime(data.ran_at)}</span>} hint="cached 15 min" />
              </div>
              {chartable.length > 1 && (
                <Card title={data.title}>
                  <HorizontalBars data={chartable} labelKey="label" valueKey="value" />
                </Card>
              )}
              <Card title="Result">
                <DataTable columns={data.columns} rows={data.rows} />
              </Card>
            </>
          )}
        </div>
      )}
    </>
  )
}
