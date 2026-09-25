// Regression tests for the logout CSRF fix: logout() must never send a
// request that's guaranteed to fail verify_csrf on the backend (see
// routes/auth.py) just because the local `status` was stale relative to
// the browser's actual cookies.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'

const initialAppState = useAppStore.getState()
const initialAuthState = useAuthStore.getState()

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

function setCsrfCookie(value: string) {
  document.cookie = `voya_csrf_token=${value}; path=/`
}

function clearAllCookies() {
  document.cookie.split(';').forEach((cookie) => {
    const name = cookie.split('=')[0].trim()
    if (name) {
      document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
    }
  })
}

beforeEach(() => {
  useAppStore.setState(initialAppState, true)
  useAuthStore.setState(initialAuthState, true)
  clearAllCookies()
})

afterEach(() => {
  clearAllCookies()
})

describe('logout() and the CSRF cookie', () => {
  it('sends the voya_csrf_token cookie value as the X-CSRF-Token header when it exists', async () => {
    setCsrfCookie('real-csrf-token')
    const fetchSpy = vi.fn().mockResolvedValueOnce(jsonResponse({ message: 'Logged out' }))
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAuthStore.getState().logout()

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [, requestInit] = fetchSpy.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(requestInit.headers)
    expect(headers.get('X-CSRF-Token')).toBe('real-csrf-token')

    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().status).toBe('unauthenticated')
  })

  it('skips the backend call entirely when there is no CSRF cookie, and still clears local state', async () => {
    // No setCsrfCookie() call — simulates status being stale 'authenticated'
    // while the browser's actual cookies had already expired/were cleared.
    //
    // Setting `user` here also fires useAuthStore's own subscription (see
    // useAuthStore.ts), which kicks off its own unrelated
    // initConversations() fetch — that's the existing account-switch
    // behavior working as designed, not what this test is about. Let it
    // settle first, then swap in a fresh spy so the assertion below is
    // specifically about what logout() itself does.
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({
      user: { id: 'u1', name: 'Tom', email: 'tom@example.com', created_at: '2026-01-01T00:00:00Z' },
      status: 'authenticated',
    })
    useAppStore.setState({
      conversations: [
        {
          conversation_id: 'c1',
          session_id: 's',
          title: "Tom's trip",
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAuthStore.getState().logout()

    // The whole point: logout() itself never sent a request (nothing to
    // 403 on) once there was no CSRF cookie to send.
    expect(fetchSpy).not.toHaveBeenCalled()

    expect(useAuthStore.getState().user).toBeNull()
    expect(useAuthStore.getState().status).toBe('unauthenticated')
    expect(useAppStore.getState().conversations).toEqual([])
  })
})
