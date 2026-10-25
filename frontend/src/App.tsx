import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router'
import { Layout } from './components/Layout'
import { Loading } from './components/ui'

// Each page (and the chart library it uses) is a separate chunk fetched on first visit, so the
// app shell loads fast.
const OverviewPage = lazy(() => import('./pages/OverviewPage').then((m) => ({ default: m.OverviewPage })))
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage').then((m) => ({ default: m.AnalyticsPage })))
const SourcesPage = lazy(() => import('./pages/SourcesPage').then((m) => ({ default: m.SourcesPage })))
const JobsPage = lazy(() => import('./pages/JobsPage').then((m) => ({ default: m.JobsPage })))
const JobDetailPage = lazy(() => import('./pages/JobDetailPage').then((m) => ({ default: m.JobDetailPage })))
const QualityPage = lazy(() => import('./pages/QualityPage').then((m) => ({ default: m.QualityPage })))
const OperationsPage = lazy(() => import('./pages/OperationsPage').then((m) => ({ default: m.OperationsPage })))
const AthenaPage = lazy(() => import('./pages/AthenaPage').then((m) => ({ default: m.AthenaPage })))
const AboutPage = lazy(() => import('./pages/AboutPage').then((m) => ({ default: m.AboutPage })))
const NotFoundPage = lazy(() => import('./pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })))

export function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<OverviewPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="sources" element={<SourcesPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="jobs/:jobId" element={<JobDetailPage />} />
          <Route path="quality" element={<QualityPage />} />
          <Route path="operations" element={<OperationsPage />} />
          <Route path="athena" element={<AthenaPage />} />
          <Route path="about" element={<AboutPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </Suspense>
  )
}
