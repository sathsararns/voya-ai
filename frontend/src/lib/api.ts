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

// The request was deliberately cancelled (see streamPost's `signal` param
// and useAppStore.ts's stopGenerating) — never a failure, so callers should
// treat it as its own outcome rather than lumping it in with a genuine
// network problem or backend error.
export class ApiAbortError extends Error {
  constructor() {
    super('The request was stopped')
    this.name = 'ApiAbortError'
  }
}

function isAbortError(err: unknown): boolean {
  // Covers both a real DOMException (what fetch/AbortController normally
  // throw) and any environment/polyfill that instead throws a plain object
  // with the same `.name` — checking the string is more portable than an
  // `instanceof DOMException`, which isn't guaranteed to be the same
  // constructor across every runtime this code might run in (browser vs.
  // Node/test environment).
  return typeof err === 'object' && err !== null && (err as { name?: unknown }).name === 'AbortError'
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
  get: <T>(path: string, headers?: Record<string, string>) => request<T>(path, { method: 'GET', headers }),
  post: <T>(path: string, body?: unknown, headers?: Record<string, string>) =>
    request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
      headers,
    }),
}

// --- Server-Sent Events (streaming chat) ------------------------------------
//
// The browser's native EventSource can only make GET requests with no
// custom body or headers, which rules it out here: sending a chat message
// needs a POST body plus the same CSRF/auth headers as every other mutating
// request (see request() above). fetch() + a manual line-based SSE parser
// over the response body's ReadableStream is the standard workaround for
// exactly this — same credentials/headers path as the rest of this file,
// just read incrementally instead of awaiting response.json() all at once.

export interface SSEHandlers {
  onToken?: (text: string) => void
  onDone?: (data: unknown) => void
}

// Splits a raw `text/event-stream` block (one blank-line-separated chunk)
// into its event name and joined data payload, and hands it to the matching
// handler. `error` events are deliberately NOT a handler here: the backend
// only ever sends one when no reply could be produced or saved at all (see
// routes/chat.py's chat_stream) — treated as a hard failure by streamPost
// throwing, exactly like a non-2xx status, so callers have one single error
// path instead of two.
function dispatchSSEBlock(block: string, handlers: SSEHandlers): { error?: string } {
  let eventType = 'message'
  const dataLines: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }

  if (dataLines.length === 0) return {}

  let data: unknown
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return {}
  }

  if (eventType === 'token') {
    handlers.onToken?.((data as { text?: string }).text ?? '')
  } else if (eventType === 'done') {
    handlers.onDone?.(data)
  } else if (eventType === 'error') {
    const detail = (data as { detail?: string }).detail
    return { error: typeof detail === 'string' ? detail : 'Something went wrong' }
  }

  return {}
}

export interface StreamPostOptions {
  headers?: Record<string, string>
  // Wired to fetch()'s own `signal` — aborting it (see useAppStore.ts's
  // stopGenerating) cancels both an in-flight request and an already-open
  // stream read, surfacing here as an ApiAbortError either way.
  signal?: AbortSignal
}

// POSTs `body` to `path` and streams the `text/event-stream` response,
// calling `handlers.onToken` for each token event and `handlers.onDone`
// once with the terminal done event's payload. Resolves after the done
// event has been dispatched (or the stream ends); rejects with
// ApiAbortError (cancelled via `options.signal`), ApiRequestError (a
// non-2xx response, or the stream's own `error` event), or ApiNetworkError
// (fetch failed, or the connection dropped partway through) — callers
// don't need a separate error-handling path for streaming vs. not.
export async function streamPost(
  path: string,
  body: unknown,
  handlers: SSEHandlers,
  options: StreamPostOptions = {},
): Promise<void> {
  const { headers, signal } = options
  const requestHeaders = new Headers(headers)
  requestHeaders.set('Content-Type', 'application/json')
  const csrfToken = getCsrfToken()
  if (csrfToken) {
    requestHeaders.set('X-CSRF-Token', csrfToken)
  }

  const url = `${API_BASE}${path}`

  let response: Response
  try {
    response = await fetch(url, {
      method: 'POST',
      headers: requestHeaders,
      credentials: 'include',
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (isAbortError(err)) throw new ApiAbortError()
    throw new ApiNetworkError(url)
  }

  if (!response.ok) {
    const data = await response.json().catch(() => null)
    const message = data && typeof data.detail === 'string' ? data.detail : 'Something went wrong'
    throw new ApiRequestError(response.status, message)
  }

  if (!response.body) {
    // No streaming body support in this environment (shouldn't happen in
    // any real browser) — treat it the same as any other failed request
    // rather than silently doing nothing.
    throw new ApiRequestError(response.status, 'Streaming is not supported in this environment')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      let boundary = buffer.indexOf('\n\n')
      while (boundary !== -1) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)

        const { error } = dispatchSSEBlock(block, handlers)
        if (error) throw new ApiRequestError(response.status, error)

        boundary = buffer.indexOf('\n\n')
      }
    }
  } catch (err) {
    // An `error` event (above) already threw the right type — pass it
    // through as-is rather than reclassifying it as a network failure.
    if (err instanceof ApiRequestError) throw err
    if (isAbortError(err)) throw new ApiAbortError()
    // Aborting mid-read and a genuine dropped connection surface the same
    // way here (reader.read() rejecting) — anything that isn't the abort
    // signal is a real connection failure.
    throw new ApiNetworkError(url)
  }
}
