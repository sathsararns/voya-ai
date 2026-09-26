// Real component, real store, mocked fetch only — proves the actual button
// a user clicks (not just the store action behind it, see
// tests/useAppStore.test.ts's stopGenerating describe block for that) shows
// up while generating and, clicked, actually stops it.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { Composer } from '../src/components/Composer'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { AuthUser } from '../src/types/auth'

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

// Wires the mocked stream's body to the AbortSignal streamPost forwards to
// fetch() — mirrors what a real fetch()/ReadableStream does when aborted,
// so clicking Stop exercises the real cancellation path end to end instead
// of a shortcut that assumes it works.
function abortableStreamFetch(initialTokenText?: string) {
  let streamController!: ReadableStreamDefaultController<Uint8Array>
  return vi.fn((_url: string, init?: RequestInit) => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        streamController = c
        if (initialTokenText) {
          c.enqueue(new TextEncoder().encode(`event: token\ndata: ${JSON.stringify({ text: initialTokenText })}\n\n`))
        }
      },
    })
    init?.signal?.addEventListener('abort', () => {
      streamController.error(new DOMException('The operation was aborted.', 'AbortError'))
    })
    return Promise.resolve({ ok: true, status: 200, body })
  })
}

const initialState = useAppStore.getState()
const initialAuthState = useAuthStore.getState()

const defaultUser: AuthUser = {
  id: 'default-user',
  name: 'Test User',
  email: 'test@example.com',
  created_at: '2026-01-01T00:00:00Z',
}

afterEach(() => {
  cleanup()
})

describe('Composer — Stop generating button', () => {
  beforeEach(async () => {
    useAppStore.setState(initialState, true)
    useAuthStore.setState(initialAuthState, true)
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  it('shows Send by default and switches to Stop generating once a message is sent', async () => {
    global.fetch = abortableStreamFetch() as unknown as typeof fetch
    render(<Composer />)

    expect(screen.getByLabelText('Send message')).toBeInTheDocument()
    expect(screen.queryByLabelText('Stop generating')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Message Voya AI'), { target: { value: 'Plan a trip to Kandy' } })
    fireEvent.click(screen.getByLabelText('Send message'))

    await screen.findByLabelText('Stop generating')
    expect(screen.queryByLabelText('Send message')).not.toBeInTheDocument()
  })

  it('clicking Stop generating aborts the request, keeps the partial reply, and returns the composer to Send', async () => {
    global.fetch = abortableStreamFetch('Kandy is') as unknown as typeof fetch
    render(<Composer />)

    fireEvent.change(screen.getByLabelText('Message Voya AI'), { target: { value: 'Plan a trip to Kandy' } })
    fireEvent.click(screen.getByLabelText('Send message'))

    const stopButton = await screen.findByLabelText('Stop generating')

    await act(async () => {
      fireEvent.click(stopButton)
      // Flushes send()'s catch/finally after the abort rejects streamPost.
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    // The composer itself leaves loading mode cleanly — back to Send, not
    // stuck showing Stop for a request that's no longer running.
    await screen.findByLabelText('Send message')
    expect(screen.queryByLabelText('Stop generating')).not.toBeInTheDocument()

    // And the click really did cancel the request (not just flip a local
    // Composer flag): the store's message reflects a genuinely stopped
    // generation with its partial text intact.
    const state = useAppStore.getState()
    expect(state.isResponding).toBe(false)
    const assistantMessage = state.messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.stopped).toBe(true)
    expect(assistantMessage?.content).toBe('Kandy is')
  })

  it('the composer is usable again immediately after a stop — a follow-up message can be typed and sent', async () => {
    global.fetch = abortableStreamFetch() as unknown as typeof fetch
    render(<Composer />)

    fireEvent.change(screen.getByLabelText('Message Voya AI'), { target: { value: 'First message' } })
    fireEvent.click(screen.getByLabelText('Send message'))
    const stopButton = await screen.findByLabelText('Stop generating')

    await act(async () => {
      fireEvent.click(stopButton)
      await new Promise((resolve) => setTimeout(resolve, 0))
    })

    const sendButton = await screen.findByLabelText('Send message')
    expect(sendButton).toBeDisabled() // textarea was cleared on the first submit

    fireEvent.change(screen.getByLabelText('Message Voya AI'), { target: { value: 'Second message' } })
    expect(screen.getByLabelText('Send message')).not.toBeDisabled()
  })
})
