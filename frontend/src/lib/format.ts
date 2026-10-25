// Display formatting. Times arrive as UTC ISO strings and are shown in the viewer's time zone.

const dash = '—'

export function fmtInt(value: number | null | undefined): string {
  return value == null || Number.isNaN(value) ? dash : Math.round(value).toLocaleString('en-IN')
}

export function fmtNumber(value: number | null | undefined, maxDigits = 2): string {
  if (value == null || Number.isNaN(value)) return dash
  if (Number.isInteger(value)) return value.toLocaleString('en-IN')
  return value.toLocaleString('en-IN', { maximumFractionDigits: maxDigits })
}

/** 8215424893 -> "8.22B", 1450935791 -> "1.45B", 726980.02 -> "727K" */
export function fmtCompact(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return dash
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 }).format(
    value,
  )
}

export function fmtPct(value: number | null | undefined, digits = 2): string {
  return value == null || Number.isNaN(value) ? dash : `${value.toFixed(digits)}%`
}

export function fmtMs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return dash
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`
}

export function fmtBytes(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return dash
  const units = ['B', 'KB', 'MB', 'GB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return dash
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return dash
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function fmtDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`)
  return Number.isNaN(date.getTime())
    ? day
    : date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', timeZone: 'UTC' })
}

/** "world_population_by_year" -> "World population by year"; keeps units like °C as written. */
export function humanize(name: string): string {
  const text = name.replace(/_/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** "negative_quantity" -> "Negative quantity" */
export const reasonLabel = humanize

/** "cron(30 6 * * ? *) (Asia/Kolkata)" -> "Every day at 06:30 (Asia/Kolkata)." */
export function describeSchedule(schedule: string): string {
  const daily = /^cron\((\d{1,2}) (\d{1,2}) \* \* \? \*\)\s*(?:\((.+)\))?$/.exec(schedule.trim())
  if (daily) {
    const [, minute, hour, zone] = daily
    const time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
    return `Every day at ${time}${zone ? ` (${zone})` : ''}.`
  }
  const rate = /^rate\((\d+) (\w+)\)/.exec(schedule.trim())
  if (rate) return `Every ${rate[1]} ${rate[2]}.`
  return 'On a schedule.'
}
