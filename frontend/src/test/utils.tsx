import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import { App } from '../App'

/** Mock fetch: each key is a path prefix (longest match wins) mapped to a JSON body or status. */
export function mockApi(routes: Record<string, unknown>) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      const path = url.split('?')[0]
      const key = Object.keys(routes)
        .filter((k) => path === k || path.startsWith(`${k}/`) || path.startsWith(k))
        .sort((a, b) => b.length - a.length)[0]
      if (key === undefined) {
        return new Response(JSON.stringify({ error: `no mock for ${url}` }), { status: 404 })
      }
      const body = routes[key]
      if (typeof body === 'number') {
        return new Response(JSON.stringify({ error: 'boom' }), { status: body })
      }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }),
  )
  return calls
}

export function renderAt(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}
