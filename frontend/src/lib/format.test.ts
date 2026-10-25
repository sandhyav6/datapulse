import { describe, expect, it } from 'vitest'
import {
  describeSchedule,
  fmtBytes,
  fmtCompact,
  fmtInt,
  fmtMs,
  fmtNumber,
  fmtPct,
  humanize,
} from './format'

describe('format', () => {
  it('formats integers with Indian digit grouping and handles missing values', () => {
    expect(fmtInt(104415)).toBe('1,04,415')
    expect(fmtInt(null)).toBe('—')
    expect(fmtInt(undefined)).toBe('—')
  })

  it('formats compact numbers for big populations', () => {
    expect(fmtCompact(8215424893)).toBe('8.22B')
    expect(fmtCompact(726980.02)).toBe('726.98K')
  })

  it('keeps up to the requested decimals', () => {
    expect(fmtNumber(1.3068, 4)).toBe('1.3068')
    expect(fmtNumber(17394)).toBe('17,394')
  })

  it('formats percentages, durations and bytes', () => {
    expect(fmtPct(96.031)).toBe('96.03%')
    expect(fmtMs(770)).toBe('770 ms')
    expect(fmtMs(3816)).toBe('3.8 s')
    expect(fmtBytes(51520)).toBe('50.3 KB')
    expect(fmtBytes(0)).toBe('0 B')
  })

  it('humanizes snake_case names but keeps units', () => {
    expect(humanize('world_population_by_year')).toBe('World population by year')
    expect(humanize('warmest_month_°C')).toBe('Warmest month °C')
  })

  it('describes the ingestion schedule in plain words', () => {
    expect(describeSchedule('cron(30 6 * * ? *) (Asia/Kolkata)')).toBe(
      'Every day at 06:30 (Asia/Kolkata).',
    )
    expect(describeSchedule('rate(1 day)')).toBe('Every 1 day.')
    expect(describeSchedule('something else')).toBe('On a schedule.')
  })
})
