import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { COLORS } from '../lib/colors'
import { fmtCompact, fmtDay, fmtNumber } from '../lib/format'


const axis = { fontSize: 12, fill: '#64748b' }
const grid = <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
const tooltipStyle = { borderRadius: 8, border: '1px solid #e2e8f0', fontSize: 12 }

type Row = Record<string, string | number>

export function StackedBars({
  data,
  xKey,
  series,
  height = 260,
  xFormat = fmtDay,
}: {
  data: Row[]
  xKey: string
  series: { key: string; label: string; color: string }[]
  height?: number
  xFormat?: (value: string) => string
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey={xKey} tick={axis} tickFormatter={xFormat} />
        <YAxis tick={axis} tickFormatter={(v: number) => fmtCompact(v)} width={48} />
        <Tooltip contentStyle={tooltipStyle} labelFormatter={(v) => xFormat(String(v))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        {series.map((s) => (
          <Bar key={s.key} dataKey={s.key} name={s.label} stackId="a" fill={s.color} maxBarSize={56} isAnimationActive={false} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

export function TrendLine({
  data,
  xKey,
  yKey,
  label,
  height = 280,
  yFormat = (v: number) => fmtCompact(v),
  domain,
}: {
  data: Row[]
  xKey: string
  yKey: string
  label: string
  height?: number
  yFormat?: (value: number) => string
  domain?: [number | 'auto' | 'dataMin', number | 'auto' | 'dataMax']
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        {grid}
        <XAxis dataKey={xKey} tick={axis} minTickGap={24} />
        <YAxis tick={axis} tickFormatter={yFormat} width={56} domain={domain ?? ['auto', 'auto']} />
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v) => [fmtNumber(Number(v), 4), label]}
        />
        <Line
          type="monotone"
          dataKey={yKey}
          name={label}
          stroke={COLORS.brand}
          strokeWidth={2}
          dot={data.length < 40}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  )
}

export function DailyArea({ data, height = 280 }: { data: Row[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="areaFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={COLORS.brand} stopOpacity={0.35} />
            <stop offset="100%" stopColor={COLORS.brand} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        {grid}
        <XAxis dataKey="date" tick={axis} tickFormatter={fmtDay} minTickGap={24} />
        <YAxis tick={axis} tickFormatter={(v: number) => fmtCompact(v)} width={56} />
        <Tooltip
          contentStyle={tooltipStyle}
          labelFormatter={(v) => fmtDay(String(v))}
          formatter={(v, name) => [fmtNumber(Number(v)), name === 'total' ? 'Total' : 'Rows']}
        />
        <Area
          type="monotone"
          dataKey="total"
          stroke={COLORS.brand}
          fill="url(#areaFill)"
          strokeWidth={2}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function HorizontalBars({
  data,
  labelKey,
  valueKey,
  color = COLORS.brand,
  valueFormat = (v: number) => fmtCompact(v),
}: {
  data: Row[]
  labelKey: string
  valueKey: string
  color?: string
  valueFormat?: (value: number) => string
}) {
  const height = Math.max(160, data.length * 30 + 40)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" horizontal={false} />
        <XAxis type="number" tick={axis} tickFormatter={valueFormat} />
        <YAxis
          type="category"
          dataKey={labelKey}
          tick={axis}
          width={170}
          interval={0}
          tickFormatter={(v: string) => (v.length > 26 ? `${v.slice(0, 25)}…` : v)}
        />
        <Tooltip contentStyle={tooltipStyle} formatter={(v) => [fmtNumber(Number(v), 4), '']} />
        <Bar dataKey={valueKey} fill={color} radius={[0, 4, 4, 0]} maxBarSize={22} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}
