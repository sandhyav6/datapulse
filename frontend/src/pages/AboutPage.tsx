import { Card, PageHeader } from '../components/ui'

const FLOW = [
  {
    step: 'Schedule',
    service: 'EventBridge Scheduler',
    text: 'Every day at 06:30 IST it starts the ingester Lambda.',
  },
  {
    step: 'Ingest',
    service: 'Lambda (dataset-ingester)',
    text: 'Downloads each public CSV over HTTPS, hashes it, and writes it to S3 raw/ only if the content changed.',
  },
  {
    step: 'Queue',
    service: 'S3 → SQS',
    text: 'The new object fires an event into a queue, so bursts wait safely and failures retry.',
  },
  {
    step: 'Validate and transform',
    service: 'Lambda (etl-processor)',
    text: 'Streams the CSV, checks every row against the JSON schema, cleans it, and removes duplicates.',
  },
  {
    step: 'Store',
    service: 'S3 + DynamoDB',
    text: 'Clean rows, rejected rows with reasons, and a report go to S3. Job state goes to DynamoDB.',
  },
  {
    step: 'Observe',
    service: 'CloudWatch + SNS',
    text: 'Logs, metrics, a dashboard and 4 alarms that email the operator.',
  },
  {
    step: 'Explore',
    service: 'CloudFront + API Gateway + Lambda',
    text: 'This website: React on S3 behind CloudFront, reading a read-only JSON API.',
  },
]

const GUARANTEES = [
  ['Idempotent', 'Re-delivering the same file never duplicates data: a conditional DynamoDB claim plus deterministic output keys.'],
  ['Fault tolerant', 'A failing file is retried 3 times, then parked in a dead-letter queue and alarmed, and can be redriven after a fix.'],
  ['Least privilege', 'Every Lambda has its own IAM role scoped to exact S3 prefixes and tables. This site can only read.'],
  ['Config, not code', 'Each dataset is a JSON schema; adding a dataset needs no pipeline code.'],
  ['Infrastructure as code', 'One AWS SAM template creates (and deletes) everything.'],
  ['Free plan', 'Everything runs inside AWS always-free allowances: no servers, NAT gateways or databases to pay for.'],
]

export function AboutPage() {
  return (
    <>
      <PageHeader
        title="How it works"
        description="An event-driven, serverless data pipeline on AWS. From a public CSV on the internet to the charts on this site."
      />
      <div className="flex flex-col gap-6">
        <Card title="The path of one file">
          <ol className="relative space-y-5 border-l-2 border-brand-100 pl-6">
            {FLOW.map((f, i) => (
              <li key={f.step}>
                <span className="absolute -left-[13px] grid size-6 place-items-center rounded-full bg-brand-600 text-xs font-semibold text-white">
                  {i + 1}
                </span>
                <p className="text-sm font-semibold text-slate-900">
                  {f.step} <span className="font-normal text-brand-600">· {f.service}</span>
                </p>
                <p className="mt-0.5 text-sm text-slate-600">{f.text}</p>
              </li>
            ))}
          </ol>
        </Card>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {GUARANTEES.map(([title, text]) => (
            <Card key={title} title={title}>
              <p className="text-sm text-slate-600">{text}</p>
            </Card>
          ))}
        </div>
      </div>
    </>
  )
}
