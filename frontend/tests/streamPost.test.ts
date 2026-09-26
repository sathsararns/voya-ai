// Unit tests for streamPost (see src/lib/api.ts), isolated from the store —
// these prove the SSE client contract itself: token/done dispatch order,
// error-event handling, and non-2xx/network failures, all independent of
// how useAppStore.ts happens to use it (see tests/useAppStore.test.ts for
// that integration).
import { describe, expect, it, vi } from 'vitest'
import { ApiAbortError, ApiNetworkError, ApiRequestError, streamPost } from '../src/lib/api'

function sseBody(blocks: string[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(blocks.join('')))
      controller.close()
    },
  })
}

function tokenBlock(text: string) {
  return `event: token\ndata: ${JSON.stringify({ text })}\n\n`
}

function doneBlock(data: unknown) {
  return `event: done\ndata: ${JSON.stringify(data)}\n\n`
}

function errorBlock(detail: string) {
  return `event: error\ndata: ${JSON.stringify({ detail })}\n\n`
}

describe('streamPost', () => {
  it('calls onToken for each token event in arrival order, then onDone once with the terminal payload', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: sseBody([tokenBlock('Hello'), tokenBlock(' world'), doneBlock({ summary: 'Hello world' })]),
    }) as unknown as typeof fetch

    const tokens: string[] = []
    let done: unknown = null

    await streamPost('/api/v1/chat/stream', { message: 'hi' }, {
      onToken: (text) => tokens.push(text),
      onDone: (data) => {
        done = data
      },
    })

    expect(tokens).toEqual(['Hello', ' world'])
    expect(done).toEqual({ summary: 'Hello world' })
  })

  it('splits SSE events correctly even when a single chunk boundary lands mid-event', async () => {
    // The two "event: done...\n\n" bytes are split across two enqueue()
    // calls, mimicking a real network chunk boundary that doesn't respect
    // SSE's blank-line framing.
    const full = doneBlock({ summary: 'Split across chunks' })
    const splitAt = Math.floor(full.length / 2)
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(full.slice(0, splitAt)))
        controller.enqueue(new TextEncoder().encode(full.slice(splitAt)))
        controller.close()
      },
    })
    global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body }) as unknown as typeof fetch

    let done: unknown = null
    await streamPost('/api/v1/chat/stream', { message: 'hi' }, {
      onDone: (data) => {
        done = data
      },
    })

    expect(done).toEqual({ summary: 'Split across chunks' })
  })

  it('throws ApiRequestError when the stream reports an error event, without calling onDone', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: sseBody([tokenBlock('Working'), errorBlock('A required service is temporarily unavailable.')]),
    }) as unknown as typeof fetch

    const onDone = vi.fn()

    await expect(
      streamPost('/api/v1/chat/stream', { message: 'hi' }, { onDone }),
    ).rejects.toThrow('A required service is temporarily unavailable.')
    expect(onDone).not.toHaveBeenCalled()
  })

  it('throws ApiRequestError with the backend detail on a non-2xx response, without reading the body as a stream', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Session expired, please log in again' }),
    }) as unknown as typeof fetch

    await expect(streamPost('/api/v1/chat/stream', { message: 'hi' }, {})).rejects.toMatchObject({
      message: 'Session expired, please log in again',
      status: 401,
    })
  })

  it('throws ApiNetworkError when fetch itself fails', async () => {
    global.fetch = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch')) as unknown as typeof fetch

    await expect(streamPost('/api/v1/chat/stream', { message: 'hi' }, {})).rejects.toBeInstanceOf(ApiNetworkError)
  })

  it('sends the CSRF header and any extra headers passed in, alongside a JSON body', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: sseBody([doneBlock({ summary: 'ok' })]),
    })
    global.fetch = fetchSpy as unknown as typeof fetch

    try {
      document.cookie = 'voya_csrf_token=test-csrf-token'
    } catch {
      // jsdom always supports document.cookie; this is just defensive.
    }

    await streamPost(
      '/api/v1/chat/stream',
      { message: 'hi' },
      {},
      { headers: { 'X-Client-Expects-Auth': '1' } },
    )

    const [, requestInit] = fetchSpy.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(requestInit.headers)
    expect(headers.get('X-Client-Expects-Auth')).toBe('1')
    expect(headers.get('X-CSRF-Token')).toBe('test-csrf-token')
    expect(requestInit.body).toBe(JSON.stringify({ message: 'hi' }))
    expect(requestInit.credentials).toBe('include')
  })

  it('rejects instead of hanging when the response has no body to stream', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body: null }) as unknown as typeof fetch

    await expect(streamPost('/api/v1/chat/stream', { message: 'hi' }, {})).rejects.toBeInstanceOf(ApiRequestError)
  })

  it('forwards the signal to fetch()', async () => {
    const controller = new AbortController()
    const fetchSpy = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      body: sseBody([doneBlock({ summary: 'ok' })]),
    })
    global.fetch = fetchSpy as unknown as typeof fetch

    await streamPost('/api/v1/chat/stream', { message: 'hi' }, {}, { signal: controller.signal })

    const [, requestInit] = fetchSpy.mock.calls[0] as [string, RequestInit]
    expect(requestInit.signal).toBe(controller.signal)
  })

  it('rejects with ApiAbortError (not ApiNetworkError) when aborted before a response arrives', async () => {
    const controller = new AbortController()
    // Mirrors real fetch()'s contract: a pending request rejects with a
    // DOMException named "AbortError" the moment its signal fires.
    global.fetch = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted.', 'AbortError'))
        })
      })
    }) as unknown as typeof fetch

    const promise = streamPost('/api/v1/chat/stream', { message: 'hi' }, {}, { signal: controller.signal })
    controller.abort()

    await expect(promise).rejects.toBeInstanceOf(ApiAbortError)
  })

  it('rejects with ApiAbortError when aborted mid-stream, after some tokens but before done', async () => {
    const controller = new AbortController()
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        streamController = c
        c.enqueue(new TextEncoder().encode(tokenBlock('Working on it')))
      },
    })
    // Mirrors what a real aborted fetch does to its response body: reading
    // it further rejects with an AbortError.
    controller.signal.addEventListener('abort', () => {
      streamController.error(new DOMException('The operation was aborted.', 'AbortError'))
    })

    global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body }) as unknown as typeof fetch

    const onToken = vi.fn()
    const onDone = vi.fn()

    const promise = streamPost(
      '/api/v1/chat/stream',
      { message: 'hi' },
      { onToken, onDone },
      { signal: controller.signal },
    )

    // Let the first token's read -> decode -> dispatch chain run before
    // aborting, so this genuinely tests a mid-stream abort, not a
    // before-anything-arrived one (already covered above).
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(onToken).toHaveBeenCalledWith('Working on it')

    controller.abort()

    await expect(promise).rejects.toBeInstanceOf(ApiAbortError)
    expect(onDone).not.toHaveBeenCalled()
  })

  it('treats a mid-stream connection drop that is NOT an abort as ApiNetworkError', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        streamController = c
        c.enqueue(new TextEncoder().encode(tokenBlock('Working on it')))
      },
    })
    global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body }) as unknown as typeof fetch

    const promise = streamPost('/api/v1/chat/stream', { message: 'hi' }, {})
    await new Promise((resolve) => setTimeout(resolve, 0))

    // A real network drop, not a cancellation — no AbortSignal involved.
    streamController.error(new TypeError('network error'))

    await expect(promise).rejects.toBeInstanceOf(ApiNetworkError)
  })
})
