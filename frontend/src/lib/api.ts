// Shared fetch wrapper for the auth API. Always sends credentials (so the
// httpOnly session cookie goes with every request) and, for any mutating
// request, echoes the readable CSRF cookie back as a header — the
// "double-submit cookie" half of routes/auth.py's verify_csrf dependency.
// The JWT itself is never read here or stored anywhere in JS — it lives
// only in the httpOnly cookie the browser manages automatically.

// Configurable via VITE_API_BASE_URL (see frontend/.env.example) so the
// backend's address can be changed without editing source — e.g. when the
// default port is already taken by something else on the machine. Falls
// back to the project's conventional local dev address.
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

// The server responded, but with a non-2xx status — .message is the
// backend's own `detail` text (e.g. "Incorrect email or password"),
// already safe to show directly to the user.
export class ApiRequestError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// fetch() itself never got a response to read a status from. Common causes:
// the backend isn't running, a stale VITE_API_BASE_URL in .env.local is
// pointing at a port nothing is listening on anymore (e.g. left over from
// working around an earlier port conflict — check that file first if this
// used to work), or something ELSE is already listening on the target port
// and rejecting the request/CORS preflight, e.g. another local project's
// dev server. These used to surface as a raw, uninformative TypeError that
// every page's catch block discarded in favor of a generic "Something went
// wrong" — this gives them a specific, actionable message instead, naming
// the exact URL that couldn't be reached so it's obvious where to look.
export class ApiNetworkError extends Error {
  constructor(url: string) {
    super(
      `Could not reach the backend at ${url}. Check that it's running there — if this used ` +
        `to work, frontend/.env.local may have a stale VITE_API_BASE_URL pointing at the ` +
        `wrong port. Otherwise, make sure nothing else on your machine (another project's ` +
        `server, a stale process) is already using that port.`,
    )
  }
}

// Exported so callers can check for a real session before attempting a
// request that needs one — see useAuthStore.ts's logout(), which skips the
// backend call entirely rather than sending one doomed to fail verify_csrf.
export function getCsrfToken(): string | null {
  const match = document.cookie.match(/(?:^|; )voya_csrf_token=([^;]*)/)
  return match ? decodeURIComponent(match[1]) : null
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = (options.method ?? 'GET').toUpperCase()
  const headers = new Headers(options.headers)
  headers.set('Content-Type', 'application/json')

  if (method !== 'GET') {
    const csrfToken = getCsrfToken()
    if (csrfToken) {
      headers.set('X-CSRF-Token', csrfToken)
    }
  }

  const url = `${API_BASE}${path}`

  let response: Response
  try {
    response = await fetch(url, {
      ...options,
      method,
      headers,
      credentials: 'include',
    })
  } catch {
    throw new ApiNetworkError(url)
  }

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    const message = data && typeof data.detail === 'string' ? data.detail : 'Something went wrong'
    throw new ApiRequestError(response.status, message)
  }

  return data as T
}

export const api = {
  get: <T>(path: string) => request<T>(path, { method: 'GET' }),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
}
