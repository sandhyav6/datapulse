import clsx from 'clsx'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { fmtDateTime, fmtInt, fmtMs, fmtPct } from '../lib/format'
import type { JobSummary } from '../lib/types'
import { Empty, StatusBadge } from './ui'

type SortKey = 'started_at' | 'rows_total' | 'acceptance_rate' | 'duration_ms' | 'attempts'

const COLUMNS: { key: SortKey | null; label: string; align?: 'right' }[] = [
  { key: null, label: 'Status' },
  { key: 'started_at', label: 'Started' },
  { key: null, label: 'Dataset' },
  { key: null, label: 'File' },
  { key: 'rows_total', label: 'Rows', align: 'right' },
  { key: 'acceptance_rate', label: 'Accepted', align: 'right' },
  { key: 'duration_ms', label: 'Duration', align: 'right' },
  { key: 'attempts', label: 'Attempts', align: 'right' },
]

/** Sortable job list; clicking a row opens the job's detail page. */
export function JobsTable({ jobs, showError = false }: { jobs: JobSummary[]; showError?: boolean }) {
  const navigate = useNavigate()
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({
    key: 'started_at',
    desc: true,
  })

  const sorted = useMemo(() => {
    const copy = [...jobs]
    copy.sort((a, b) => {
      const av = a[sort.key] ?? ''
      const bv = b[sort.key] ?? ''
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sort.desc ? -cmp : cmp
    })
    return copy
  }, [jobs, sort])

  if (!jobs.length) return <Empty>No jobs match.</Empty>

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead className="bg-slate-50">
          <tr>
            {COLUMNS.map((col) => (
              <th
                key={col.label}
                scope="col"
                className={clsx(
                  'whitespace-nowrap px-3 py-2 text-xs font-semibold uppercase tracking-wide text-slate-500',
                  col.align === 'right' ? 'text-right' : 'text-left',
                )}
              >
                {col.key ? (
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 uppercase hover:text-slate-800"
                    onClick={() =>
                      setSort((s) => ({
                        key: col.key as SortKey,
                        desc: s.key === col.key ? !s.desc : true,
                      }))
                    }
                  >
                    {col.label}
                    {sort.key === col.key &&
                      (sort.desc ? (
                        <ArrowDown className="size-3" aria-label="descending" />
                      ) : (
                        <ArrowUp className="size-3" aria-label="ascending" />
                      ))}
                  </button>
                ) : (
                  col.label
                )}
              </th>
            ))}
            {showError && (
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                Error
              </th>
            )}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {sorted.map((job) => (
            <tr
              key={job.job_id}
              onClick={() => navigate(`/jobs/${job.job_id}`)}
              className="cursor-pointer hover:bg-brand-50/60"
            >
              <td className="px-3 py-2">
                <StatusBadge status={job.status} />
              </td>
              <td className="whitespace-nowrap px-3 py-2 text-slate-600">
                {fmtDateTime(job.started_at)}
              </td>
              <td className="px-3 py-2 text-slate-600">{job.dataset ?? '—'}</td>
              <td className="max-w-56 truncate px-3 py-2 font-medium text-slate-800" title={job.file}>
                {job.file}
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(job.rows_total)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtPct(job.acceptance_rate, 1)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtMs(job.duration_ms)}</td>
              <td className="px-3 py-2 text-right tabular-nums">{fmtInt(job.attempts)}</td>
              {showError && (
                <td className="max-w-md truncate px-3 py-2 text-red-700" title={job.error_message}>
                  {job.error_type ? `${job.error_type}: ${job.error_message ?? ''}` : '—'}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
