// Regression tests for account-scoped Recent Chats on the client side.
// useAppStore (conversations/messages) and useAuthStore (the logged-in
// user) are separate Zustand stores — these tests exercise the real
// cross-store interaction (logout() reaching into useAppStore) and the
// initConversations() synchronous-clear behavior directly, without a DOM
// or a server, the same pattern useAppStore.test.ts already uses.
import { waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { AuthUser } from '../src/types/auth'

const initialAppState = useAppStore.getState()
const initialAuthState = useAuthStore.getState()

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

beforeEach(() => {
  useAppStore.setState(initialAppState, true)
  useAuthStore.setState(initialAuthState, true)
  window.localStorage.clear()
})

const aConversation = {
  conversation_id: 'a-1',
  session_id: 'shared-session',
  title: "Account A's trip",
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const bConversation = {
  conversation_id: 'b-1',
  session_id: 'shared-session',
  title: "Account B's trip",
  created_at: '2026-01-02T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

const tom: AuthUser = { id: 'user-tom', name: 'Tom', email: 'tom@example.com', created_at: '2026-01-01T00:00:00Z' }
const sathsara: AuthUser = {
  id: 'user-sathsara',
  name: 'Sathsara',
  email: 'sathsara@example.com',
  created_at: '2026-01-01T00:00:00Z',
}

describe('account switching — Recent Chats never leak between accounts', () => {
  it('logout() clears the previous account\'s conversations/messages immediately', async () => {
    useAppStore.setState({
      conversations: [aConversation],
      messages: [{ id: 'm1', role: 'user', content: 'hi', createdAt: Date.now() }],
      activeConversationId: 'a-1',
    })

    global.fetch = vi.fn().mockResolvedValueOnce(
      jsonResponse({ message: 'Logged out' }),
    ) as unknown as typeof fetch

    await useAuthStore.getState().logout()

    const appState = useAppStore.getState()
    expect(appState.conversations).toEqual([])
    expect(appState.messages).toEqual([])
    expect(appState.activeConversationId).toBeNull()
  })

  it('never renders the previous account\'s list while the next account\'s fetch is still in flight', async () => {
    useAppStore.setState({ conversations: [aConversation] })

    let resolveFetch!: (value: unknown) => void
    const pending = new Promise((resolve) => {
      resolveFetch = resolve
    })
    global.fetch = vi.fn().mockReturnValueOnce(pending) as unknown as typeof fetch

    const loadPromise = useAppStore.getState().initConversations()

    // Synchronously, before the network call resolves, A's old list must
    // already be gone — this is the exact window that used to leak.
    expect(useAppStore.getState().conversations).toEqual([])

    resolveFetch(jsonResponse({ conversations: [bConversation] }))
    await loadPromise

    expect(useAppStore.getState().conversations).toEqual([bConversation])
  })

  it('stays empty, not stuck on stale data, if the new account\'s conversation fetch fails', async () => {
    useAppStore.setState({ conversations: [aConversation] })

    global.fetch = vi.fn().mockRejectedValueOnce(new Error('network down')) as unknown as typeof fetch

    await useAppStore.getState().initConversations()

    expect(useAppStore.getState().conversations).toEqual([])
  })

  it('resetConversations() clears everything on its own (defense-in-depth path)', () => {
    useAppStore.setState({
      conversations: [aConversation],
      messages: [{ id: 'm1', role: 'user', content: 'hi', createdAt: Date.now() }],
      activeConversationId: 'a-1',
      isLoadingConversations: true,
      isLoadingHistory: true,
    })

    useAppStore.getState().resetConversations()

    const state = useAppStore.getState()
    expect(state.conversations).toEqual([])
    expect(state.messages).toEqual([])
    expect(state.activeConversationId).toBeNull()
    expect(state.isLoadingConversations).toBe(false)
    expect(state.isLoadingHistory).toBe(false)
  })
})

describe('structural auth-change subscription — works even without any component mounting/unmounting', () => {
  it('logging in as a different account (same tab, nothing unmounts) clears Tom\'s chats and loads Sathsara\'s', async () => {
    // Tom is already logged in with his chats cached, as if ChatApp had
    // been sitting mounted this whole time.
    useAuthStore.setState({ user: tom, status: 'authenticated' })
    useAppStore.setState({ conversations: [aConversation] })

    // Sathsara logs in — in a real app this is a form submit, not a
    // navigation; nothing unmounts or remounts.
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(sathsara)) // POST /login
      .mockResolvedValueOnce(jsonResponse({ conversations: [bConversation] })) as unknown as typeof fetch // GET /conversations, fired by the subscription

    await useAuthStore.getState().login(sathsara.email, 'Password123')

    // The subscription's initConversations() call is fire-and-forget from
    // login()'s point of view, so it may not have resolved the instant
    // login() itself returns — wait for the end state instead of a fixed
    // number of ticks.
    await waitFor(() => {
      expect(useAppStore.getState().conversations).toEqual([bConversation])
    })

    // At no point should Tom's conversation have still been present.
    expect(useAppStore.getState().conversations).not.toContainEqual(aConversation)
  })

  it('a storage event from another tab (a different account logging in there) re-hydrates and reloads this tab', async () => {
    // This tab still thinks Tom is logged in and shows his chats — it was
    // never touched by whatever just happened in the other tab.
    useAuthStore.setState({ user: tom, status: 'authenticated' })
    useAppStore.setState({ conversations: [aConversation] })

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(sathsara)) // GET /me, re-hydrating from the now-changed session cookie
      .mockResolvedValueOnce(jsonResponse({ conversations: [bConversation] })) as unknown as typeof fetch // GET /conversations

    // Simulate the other tab's login() having called notifyOtherTabs() —
    // the real browser fires this event in every OTHER same-origin tab
    // automatically; here we fire it directly since jsdom is a single tab.
    window.dispatchEvent(new StorageEvent('storage', { key: 'voya_auth_sync', newValue: String(Date.now()) }))

    await waitFor(() => {
      expect(useAuthStore.getState().user?.id).toBe(sathsara.id)
    })
    await waitFor(() => {
      expect(useAppStore.getState().conversations).toEqual([bConversation])
    })
  })

  it('a storage event with an unrelated key is ignored', async () => {
    useAuthStore.setState({ user: tom, status: 'authenticated' })
    useAppStore.setState({ conversations: [aConversation] })

    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    window.dispatchEvent(new StorageEvent('storage', { key: 'some_other_apps_key', newValue: 'x' }))

    // Give any (incorrect) reaction a chance to happen before asserting nothing did.
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(useAppStore.getState().conversations).toEqual([aConversation])
  })
})
