import clsx from 'clsx'
import {
  Activity,
  BarChart3,
  Database,
  Globe,
  LayoutDashboard,
  ListChecks,
  Menu,
  Search,
  ShieldCheck,
  Workflow,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'

const NAV = [
  { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/sources', label: 'Sources', icon: Globe },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/quality', label: 'Data quality', icon: ShieldCheck },
  { to: '/operations', label: 'Operations', icon: Activity },
  { to: '/athena', label: 'Athena SQL', icon: Search },
  { to: '/about', label: 'How it works', icon: Workflow },
]

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          onClick={onNavigate}
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition',
              isActive
                ? 'bg-brand-600 text-white shadow-sm'
                : 'text-slate-300 hover:bg-white/10 hover:text-white',
            )
          }
        >
          <Icon className="size-4" aria-hidden />
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span className="grid size-9 place-items-center rounded-lg bg-brand-500 text-white">
        <Database className="size-5" aria-hidden />
      </span>
      <div className="leading-tight">
        <p className="text-sm font-semibold text-white">ETL Data Platform</p>
        <p className="text-xs text-slate-400">Serverless on AWS</p>
      </div>
    </div>
  )
}

export function Layout() {
  const [open, setOpen] = useState(false)
  return (
    <div className="min-h-screen lg:pl-64">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col gap-8 bg-slate-900 px-4 py-6 lg:flex">
        <Brand />
        <NavItems />
        <p className="mt-auto text-xs leading-relaxed text-slate-500">
          S3 · SQS · Lambda · DynamoDB · EventBridge · CloudWatch · API Gateway · CloudFront ·
          Athena
        </p>
      </aside>

      <header className="sticky top-0 z-20 flex items-center justify-between bg-slate-900 px-4 py-3 lg:hidden">
        <Brand />
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="rounded-md p-2 text-slate-300 hover:bg-white/10"
          aria-label={open ? 'Close menu' : 'Open menu'}
          aria-expanded={open}
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </header>
      {open && (
        <div className="bg-slate-900 px-4 pb-4 lg:hidden">
          <NavItems onNavigate={() => setOpen(false)} />
        </div>
      )}

      <main className="mx-auto max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
        <Outlet />
      </main>
    </div>
  )
}
