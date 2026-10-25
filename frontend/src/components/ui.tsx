import clsx from 'clsx'
import { AlertTriangle, Info, Loader2 } from 'lucide-react'
import type { ReactNode } from 'react'
import type { JobStatus } from '../lib/types'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-slate-500">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  )
}

export function Card({
  title,
  subtitle,
  children,
  className,
  actions,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  children: ReactNode
  className?: string
  actions?: ReactNode
}) {
  return (
    <section
      className={clsx('rounded-xl border border-slate-200 bg-white p-5 shadow-sm', className)}
    >
      {(title || actions) && (
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            {title && <h2 className="text-sm font-semibold text-slate-900">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

export function Kpi({
  label,
  value,
  hint,
  tone = 'default',
}: {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'good' | 'bad' | 'warn'
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p
        className={clsx('mt-1 text-2xl font-semibold tabular-nums', {
          'text-slate-900': tone === 'default',
          'text-ok': tone === 'good',
          'text-bad': tone === 'bad',
          'text-amber-600': tone === 'warn',
        })}
      >
        {value}
      </p>
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

const STATUS_STYLES: Record<JobStatus, string> = {
  SUCCEEDED: 'bg-emerald-50 text-emerald-700 ring-emerald-600/20',
  FAILED: 'bg-red-50 text-red-700 ring-red-600/20',
  PROCESSING: 'bg-amber-50 text-amber-700 ring-amber-600/20',
}

export function StatusBadge({ status }: { status: JobStatus | string | undefined }) {
  const style = STATUS_STYLES[status as JobStatus] ?? 'bg-slate-100 text-slate-600 ring-slate-500/20'
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        style,
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {status ?? 'UNKNOWN'}
    </span>
  )
}

export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-slate-500" role="status">
      <Loader2 className="size-4 animate-spin" aria-hidden /> {label}
    </div>
  )
}

export function ErrorState({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error)
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div>
        <p className="font-medium">Could not load this data.</p>
        <p className="mt-0.5 text-red-700">{message}</p>
      </div>
    </div>
  )
}

export function Notice({
  children,
  tone = 'info',
}: {
  children: ReactNode
  tone?: 'info' | 'warn' | 'bad' | 'good'
}) {
  return (
    <div
      className={clsx('flex items-start gap-3 rounded-xl border p-4 text-sm', {
        'border-brand-100 bg-brand-50 text-brand-700': tone === 'info',
        'border-amber-200 bg-amber-50 text-amber-800': tone === 'warn',
        'border-red-200 bg-red-50 text-red-800': tone === 'bad',
        'border-emerald-200 bg-emerald-50 text-emerald-800': tone === 'good',
      })}
    >
      {tone === 'bad' || tone === 'warn' ? (
        <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
      ) : (
        <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
      )}
      <div>{children}</div>
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
      {children}
    </p>
  )
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
  label: string
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex flex-wrap gap-1 rounded-lg bg-slate-100 p-1"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          onClick={() => onChange(option.value)}
          className={clsx(
            'rounded-md px-3 py-1.5 text-sm font-medium transition',
            option.value === value
              ? 'bg-white text-slate-900 shadow-sm'
              : 'text-slate-600 hover:text-slate-900',
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function DataTable({
  columns,
  rows,
  caption,
}: {
  columns: string[]
  rows: (string | number | null)[][]
  caption?: string
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="bg-slate-50">
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                scope="col"
                className="whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-slate-500"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 bg-white">
          {rows.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j} className="whitespace-nowrap px-3 py-1.5 text-slate-700">
                  {cell ?? '—'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
