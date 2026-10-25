import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { JobsTable } from '../components/JobsTable'
import { Card, ErrorState, Loading, PageHeader, Segmented } from '../components/ui'
import { api } from '../lib/api'
import type { JobStatus } from '../lib/types'

type StatusFilter = 'ALL' | JobStatus

export function JobsPage() {
  const [status, setStatus] = useState<StatusFilter>('ALL')
  const [dataset, setDataset] = useState('')
  const [search, setSearch] = useState('')
  const { data, error, isPending } = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.jobs(),
    refetchInterval: 30_000,
  })

  const datasets = useMemo(
    () => [...new Set((data ?? []).map((j) => j.dataset).filter(Boolean))].sort() as string[],
    [data],
  )
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (data ?? []).filter(
      (j) =>
        (status === 'ALL' || j.status === status) &&
        (!dataset || j.dataset === dataset) &&
        (!needle || j.file.toLowerCase().includes(needle) || j.job_id.includes(needle)),
    )
  }, [data, status, dataset, search])

  return (
    <>
      <PageHeader
        title="Jobs"
        description="Every file the pipeline has processed, read from the DynamoDB jobs table. Click a job for its full report."
      />
      <Card>
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center">
          <Segmented
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'ALL', label: 'All' },
              { value: 'SUCCEEDED', label: 'Succeeded' },
              { value: 'FAILED', label: 'Failed' },
              { value: 'PROCESSING', label: 'Processing' },
            ]}
          />
          <select
            aria-label="Dataset"
            value={dataset}
            onChange={(e) => setDataset(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">All datasets</option>
            {datasets.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
          <input
            type="search"
            aria-label="Search file or job id"
            placeholder="Search file or job id"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm lg:w-72"
          />
          {data && (
            <p className="text-sm text-slate-500 lg:ml-auto">
              {filtered.length} of {data.length} jobs
            </p>
          )}
        </div>
        {isPending && <Loading />}
        {error && <ErrorState error={error} />}
        {data && <JobsTable jobs={filtered} showError={status === 'FAILED'} />}
      </Card>
    </>
  )
}
