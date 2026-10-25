import { Link } from 'react-router'
import { PageHeader } from '../components/ui'

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="That page does not exist." />
      <Link to="/" className="text-sm font-medium text-brand-600 hover:underline">
        ← Back to the overview
      </Link>
    </>
  )
}
