import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router'
import { Card, ErrorState, Loading, Notice, PageHeader, StatusBadge } from '../components/ui'
import { api } from '../lib/api'
import { describeSchedule, fmtDateTime, fmtInt, fmtPct, humanize } from '../lib/format'

export function SourcesPage() {
  const { data, error, isPending } = useQuery({ queryKey: ['sources'], queryFn: api.sources })
  return (
    <>
      <PageHeader
        title="Sources"
        description="Public CSV datasets the platform ingests automatically. The ingester downloads each one over HTTPS and writes it to S3 only when its content changed (sha256 comparison)."
      />
      {isPending && <Loading />}
      {error && <ErrorState error={error} />}
      {data && (
        <div className="flex flex-col gap-6">
          <Notice>
            <strong>{describeSchedule(data.schedule)}</strong> Amazon EventBridge Scheduler runs
            the ingester. Unchanged sources are skipped, so no new job is created.{' '}
            <span className="text-xs opacity-75">
              (<code>{data.schedule}</code>)
            </span>
          </Notice>
          <div className="grid gap-6 lg:grid-cols-2">
            {data.sources.map((s) => (
              <Card key={s.dataset} title={humanize(s.dataset)} subtitle={s.publisher}>
                {s.description && <p className="mb-4 text-sm text-slate-600">{s.description}</p>}
                <dl className="mb-4 grid gap-2 text-sm">
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-slate-500">License</dt>
                    <dd>{s.license}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-slate-500">Source</dt>
                    <dd className="min-w-0">
                      <a
                        href={s.homepage || s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 break-all text-brand-600 hover:underline"
                      >
                        {s.homepage || s.url} <ExternalLink className="size-3 shrink-0" aria-hidden />
                      </a>
                    </dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-slate-500">Lands in</dt>
                    <dd className="min-w-0 break-all">
                      <code>{s.raw_key}</code>
                    </dd>
                  </div>
                </dl>
                {s.latest ? (
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg bg-slate-50 p-3 text-sm">
                    <StatusBadge status={s.latest.status} />
                    <span className="tabular-nums">{fmtInt(s.latest.rows_total)} rows</span>
                    <span className="tabular-nums">{fmtPct(s.latest.acceptance_rate)} accepted</span>
                    <span className="text-slate-500">{fmtDateTime(s.latest.started_at)}</span>
                    <Link
                      to={`/analytics?dataset=${s.dataset}`}
                      className="ml-auto font-medium text-brand-600 hover:underline"
                    >
                      Explore →
                    </Link>
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">Not ingested yet.</p>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
