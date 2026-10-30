import { waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { Conversation } from '../src/types'
import type { AuthUser } from '../src/types/auth'

const userA: AuthUser = { id: 'restore-a', name: 'A', email: 'a@example.com', created_at: '2026-01-01T00:00:00Z' }
const userB: AuthUser = { ...userA, id: 'restore-b', name: 'B', email: 'b@example.com' }
const chat = (id: string): Conversation => ({
  conversation_id: id, session_id: 'browser', title: id,
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
})
const json = (body: unknown) => ({ ok: true, status: 200, json: async () => body })
const history = (id: string) => ({ conversation_id: id, messages: [{
  id: 1, user_message: `${id} question`, created_at: '2026-01-01T00:00:00Z',
  assistant_reply: { summary: `${id} answer`, itinerary: [] },
}] })
const key = (user: AuthUser) => `voya_active_conversation:${user.id}`

function doneResponse(data: unknown) {
  return { ok: true, status: 200, body: new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(`event: done\ndata: ${JSON.stringify(data)}\n\n`))
      controller.close()
    },
  }) }
}

function mockApi() {
  return vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith('/auth/login')) return json(userA)
    if (url.endsWith('/auth/logout')) return json({ message: 'Logged out' })
    if (url.endsWith('/conversations')) {
      return json({ conversations: useAuthStore.getState().user?.id === userB.id
        ? [chat('b-new')] : [chat('a-new'), chat('a-old')] })
    }
    const id = url.match(/conversations\/([^/]+)\/messages$/)?.[1]
    if (id) return json(history(id))
    throw new Error(`Unexpected request: ${url}`)
  })
}

async function expectOpen(id: string) {
  await waitFor(() => {
    expect(useAppStore.getState().activeConversationId).toBe(id)
    expect(useAppStore.getState().messages[0]?.content).toBe(`${id} question`)
    expect(useAppStore.getState().isLoadingHistory).toBe(false)
  })
}

beforeEach(() => {
  useAuthStore.setState({ user: null, status: 'unauthenticated' })
  useAppStore.getState().resetConversations()
  window.localStorage.clear()
  global.fetch = mockApi() as unknown as typeof fetch
})

describe('saved chat restoration across authentication changes', () => {
  it('reopens the selected older chat with messages after logout and login to the same account', async () => {
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-new')
    await useAppStore.getState().selectConversation('a-old')
    await useAuthStore.getState().logout()
    expect(useAppStore.getState().messages).toEqual([])
    expect(window.localStorage.getItem(key(userA))).toBe('a-old')

    await useAuthStore.getState().login(userA.email, 'Password123')
    await expectOpen('a-old')
    expect(window.localStorage.getItem(key(userA))).not.toContain('question')
  })

  it('restores after a fresh store initialization and deduplicates the subsequent app mount', async () => {
    window.localStorage.setItem(key(userA), 'a-old')
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-old')
    const fetchSpy = global.fetch as ReturnType<typeof vi.fn>
    fetchSpy.mockClear()
    await useAppStore.getState().initConversations()
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(useAppStore.getState().messages[0]?.content).toBe('a-old question')

    useAppStore.getState().resetConversations()
    await useAppStore.getState().initConversations()
    await expectOpen('a-old')
  })

  it('keeps an explicit New Chat blank after relogin, with saved chats still in the sidebar', async () => {
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-new')
    useAppStore.getState().newChat()
    await useAuthStore.getState().logout()
    await useAuthStore.getState().login(userA.email, 'Password123')
    await waitFor(() => expect(useAppStore.getState().conversations).toHaveLength(2))
    expect(useAppStore.getState().activeConversationId).toBeNull()
    expect(useAppStore.getState().messages).toEqual([])
  })

  it('keeps account selections separate and never opens an ID absent from the current account list', async () => {
    window.localStorage.setItem(key(userA), 'a-old')
    window.localStorage.setItem(key(userB), 'a-old') // stale/foreign selection
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-old')
    const fetchSpy = global.fetch as ReturnType<typeof vi.fn>
    fetchSpy.mockClear()
    useAuthStore.setState({ user: userB, status: 'authenticated' })
    expect(useAppStore.getState().messages).toEqual([])
    await expectOpen('b-new')
    expect(fetchSpy.mock.calls.some(([url]) => String(url).includes('/a-old/messages'))).toBe(false)
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-old')
  })

  it('ignores an old account list arriving after another account has loaded', async () => {
    let resolveOld!: (value: unknown) => void
    const pending = new Promise((resolve) => { resolveOld = resolve })
    const normal = mockApi()
    global.fetch = vi.fn().mockImplementation((url: string) =>
      url.endsWith('/conversations') && useAuthStore.getState().user?.id === userA.id
        ? pending : normal(url)) as unknown as typeof fetch
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    useAuthStore.setState({ user: userB, status: 'authenticated' })
    await expectOpen('b-new')
    resolveOld(json({ conversations: [chat('a-new')] }))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(useAppStore.getState().conversations).toEqual([chat('b-new')])
    expect(useAppStore.getState().messages[0]?.content).toBe('b-new question')
  })

  it('ignores an old history response arriving after an account switch', async () => {
    let resolveOld!: (value: unknown) => void
    const pending = new Promise((resolve) => { resolveOld = resolve })
    const normal = mockApi()
    global.fetch = vi.fn().mockImplementation((url: string) =>
      url.endsWith('/a-new/messages') ? pending : normal(url)) as unknown as typeof fetch
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await waitFor(() => expect(useAppStore.getState().activeConversationId).toBe('a-new'))
    useAuthStore.setState({ user: userB, status: 'authenticated' })
    await expectOpen('b-new')
    resolveOld(json(history('a-new')))
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(useAppStore.getState().activeConversationId).toBe('b-new')
    expect(useAppStore.getState().messages[0]?.content).toBe('b-new question')
  })

  it('does not replace a newly opened draft when an initialization request finishes late', async () => {
    let resolveList!: (value: unknown) => void
    global.fetch = vi.fn().mockReturnValue(new Promise((resolve) => { resolveList = resolve })) as unknown as typeof fetch
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    useAppStore.getState().newChat()
    resolveList(json({ conversations: [chat('a-new')] }))
    await waitFor(() => expect(useAppStore.getState().isLoadingConversations).toBe(false))
    expect(useAppStore.getState().activeConversationId).toBeNull()
    expect(useAppStore.getState().messages).toEqual([])
  })

  it('remembers a successfully sent chat before logout and does not remember an unsaved error reply', async () => {
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-new')
    useAppStore.getState().newChat()
    const normal = mockApi()
    let failed = false
    global.fetch = vi.fn().mockImplementation((url: string) => url.endsWith('/chat/stream')
      ? doneResponse({ summary: failed ? 'Busy' : 'Saved', itinerary: [], kb_sources: [],
        conversation_id: failed ? 'unsaved' : 'a-sent', error_code: failed ? 'rate_limited' : null })
      : normal(url)) as unknown as typeof fetch
    await useAppStore.getState().send('Hello')
    expect(window.localStorage.getItem(key(userA))).toBe('a-sent')
    useAppStore.getState().newChat()
    failed = true
    await useAppStore.getState().send('Try again')
    expect(window.localStorage.getItem(key(userA))).toBe('')
    expect(useAppStore.getState().conversations.some((c) => c.conversation_id === 'unsaved')).toBe(false)
  })

  it('cannot repopulate another account with a send that finishes after logout', async () => {
    useAuthStore.setState({ user: userA, status: 'authenticated' })
    await expectOpen('a-new')
    useAppStore.getState().newChat()
    const normal = mockApi()
    let resolveStream!: (value: unknown) => void
    const pending = new Promise((resolve) => { resolveStream = resolve })
    global.fetch = vi.fn().mockImplementation((url: string) =>
      url.endsWith('/chat/stream') ? pending : normal(url)) as unknown as typeof fetch
    const sending = useAppStore.getState().send('Hello')
    await useAuthStore.getState().logout()
    useAuthStore.setState({ user: userB, status: 'authenticated' })
    await expectOpen('b-new')
    resolveStream(doneResponse({ summary: 'Late reply', itinerary: [], kb_sources: [], conversation_id: 'a-late' }))
    await sending
    expect(useAppStore.getState().activeConversationId).toBe('b-new')
    expect(useAppStore.getState().messages[0]?.content).toBe('b-new question')
    expect(window.localStorage.getItem(key(userA))).toBe('')
    expect(useAppStore.getState().conversations.some((c) => c.conversation_id === 'a-late')).toBe(false)
  })
})
