import { useQuery } from '@tanstack/react-query'
import { JobsTable } from '../components/JobsTable'
import { Card, Empty, ErrorState, Kpi, Loading, Notice, PageHeader } from '../components/ui'
import { api } from '../lib/api'
import { fmtInt } from '../lib/format'

export function OperationsPage() {
  const { data, error, isPending, dataUpdatedAt } = useQuery({
    queryKey: ['operations'],
    queryFn: api.operations,
    refetchInterval: 15_000,
  })
  return (
    <>
      <PageHeader
        title="Operations"
        description="Queue health and failures. A file that fails three times moves to the dead-letter queue (DLQ) and raises a CloudWatch alarm that emails the operator."
      />
      {isPending && <Loading />}
      {error && <ErrorState error={error} />}
      {data && (
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <Kpi label="Waiting in queue" value={fmtInt(data.queues.waiting)} />
            <Kpi label="Being processed" value={fmtInt(data.queues.in_flight)} />
            <Kpi
              label="Dead-letter queue"
              value={fmtInt(data.queues.dlq)}
              tone={data.queues.dlq ? 'bad' : 'good'}
            />
            <Kpi label="Failed jobs" value={fmtInt(data.failed.length)} tone={data.failed.length ? 'bad' : 'good'} />
          </div>
          {data.queues.dlq ? (
            <Notice tone="bad">
              {data.queues.dlq} message(s) in the DLQ: the <code>dlq-not-empty</code> alarm is
              firing. Fix the cause (schema, code or file), then redrive the DLQ to reprocess the
              same job.
            </Notice>
          ) : (
            <Notice tone="good">The dead-letter queue is empty.</Notice>
          )}
          <Card title="Failed jobs" subtitle="Status FAILED after exhausting retries, with the error recorded">
            {data.failed.length ? <JobsTable jobs={data.failed} showError /> : <Empty>No failed jobs.</Empty>}
          </Card>
          {data.retrying.length > 0 && (
            <Card title="Retrying" subtitle="Failed an attempt; SQS will deliver them again">
              <JobsTable jobs={data.retrying} showError />
            </Card>
          )}
          <p className="text-xs text-slate-400">
            Refreshes every 15 s · last update {new Date(dataUpdatedAt).toLocaleTimeString()}
          </p>
        </div>
      )}
    </>
  )
}
