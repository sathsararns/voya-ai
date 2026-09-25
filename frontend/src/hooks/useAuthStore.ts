import { create } from 'zustand'
import { api, ApiRequestError, getCsrfToken } from '../lib/api'
import { useAppStore } from './useAppStore'
import type { AuthUser } from '../types/auth'

export { ApiRequestError }

type AuthStatus = 'idle' | 'loading' | 'authenticated' | 'unauthenticated'

interface AuthState {
  user: AuthUser | null
  status: AuthStatus
  fetchCurrentUser: () => Promise<void>
  signup: (name: string, email: string, password: string) => Promise<void>
  login: (email: string, password: string) => Promise<void>
  forgotPassword: (email: string) => Promise<void>
  resetPassword: (token: string, newPassword: string) => Promise<void>
  logout: () => Promise<void>
}

// Broadcasts an auth change to every OTHER tab open on this origin (the
// browser's `storage` event never fires in the tab that made the write,
// only in other tabs — that's exactly the asymmetry needed here). Without
// this, a tab left open from before an account switch has no way to learn
// the switch happened at all: Zustand state is per-tab, so nothing in that
// tab's memory would ever change on its own, even though the shared,
// per-origin session cookie underneath it already has (logging in
// elsewhere overwrites the one httpOnly cookie every tab sends).
const AUTH_SYNC_KEY = 'voya_auth_sync'

function notifyOtherTabs(): void {
  try {
    window.localStorage.setItem(AUTH_SYNC_KEY, String(Date.now()))
  } catch {
    // best effort only — worst case, a stale second tab just doesn't
    // self-heal until it's next reloaded or navigated
  }
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  status: 'idle',

  // Called once on app boot (see App.tsx) to hydrate auth state from the
  // httpOnly cookie — the frontend never stores the token itself, so this
  // GET /me round trip is the only way it learns whether the browser
  // already has a valid session. Also re-called (see the storage listener
  // below) whenever another tab signals that the session cookie changed.
  fetchCurrentUser: async () => {
    set({ status: 'loading' })
    try {
      const user = await api.get<AuthUser>('/api/v1/auth/me')
      set({ user, status: 'authenticated' })
    } catch {
      set({ user: null, status: 'unauthenticated' })
    }
  },

  // Signup creates the account and signs the caller in immediately — no
  // email-verification step, so the response is already a logged-in user.
  signup: async (name, email, password) => {
    const user = await api.post<AuthUser>('/api/v1/auth/signup', { name, email, password })
    set({ user, status: 'authenticated' })
    notifyOtherTabs()
  },

  login: async (email, password) => {
    const user = await api.post<AuthUser>('/api/v1/auth/login', { email, password })
    set({ user, status: 'authenticated' })
    notifyOtherTabs()
  },

  forgotPassword: async (email) => {
    await api.post('/api/v1/auth/forgot-password', { email })
  },

  resetPassword: async (token, newPassword) => {
    await api.post('/api/v1/auth/reset-password', { token, new_password: newPassword })
  },

  logout: async () => {
    try {
      // No CSRF cookie means there's no real session cookie pair to revoke
      // in the first place (verify_csrf on the backend would reject the
      // request outright anyway — see routes/auth.py) — most commonly this
      // local `status` was stale relative to the browser's actual cookies
      // (e.g. they'd already expired or been cleared). Skip the network
      // call entirely rather than sending one that's guaranteed to 403,
      // and just clear local state below.
      if (getCsrfToken()) {
        await api.post('/api/v1/auth/logout')
      }
    } finally {
      // Clear local state even if the network call fails — the user
      // clicked logout, so the UI should reflect "logged out" regardless.
      set({ user: null, status: 'unauthenticated' })
      notifyOtherTabs()
      // Explicit, unconditional reset — not just relying on the
      // subscription below. If logout() is ever called while `user` was
      // already null (a stale/redundant call), there's no actual identity
      // transition for the subscription to react to, so it wouldn't fire
      // on its own; logout() clicked at all should still guarantee a clean
      // chat store regardless of what state things were already in.
      useAppStore.getState().resetConversations()
    }
  },
}))

// Single source of truth for "the authenticated identity changed, so
// useAppStore's cached chats must be thrown away" — a subscription on the
// store itself, not a call scattered into login/signup/logout/
// fetchCurrentUser individually. This is what makes the guarantee
// structural rather than something every future auth code path has to
// remember to do correctly:
//   - Fires synchronously on every set() to this store, so useAppStore is
//     already clean by the time any caller's `await login(...)` resolves.
//   - Covers login, signup, logout, AND fetchCurrentUser uniformly (the
//     last one matters for the cross-tab case below — re-hydrating this
//     tab's auth state from a changed cookie goes through the exact same
//     path as a real login).
//   - Doesn't depend on ChatApp mounting/unmounting at all, so it still
//     works in a tab that was already open and never navigated away.
// On a genuine account switch it also kicks off initConversations() for
// the new user immediately, rather than leaving the sidebar empty until
// something else happens to trigger a fetch (initConversations() itself
// still runs on every ChatApp mount too — the isLoadingConversations guard
// added there deduplicates the two when both fire close together).
let lastSeenUserId: string | null = null
useAuthStore.subscribe((state) => {
  const currentUserId = state.user?.id ?? null
  if (currentUserId === lastSeenUserId) return
  lastSeenUserId = currentUserId

  const appStore = useAppStore.getState()
  appStore.resetConversations()
  if (currentUserId) {
    appStore.initConversations()
  }
})

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === AUTH_SYNC_KEY) {
      // Another tab logged in, signed up, or logged out — this tab's own
      // `user` may now be wrong relative to the shared session cookie.
      // Re-hydrating from the cookie (not just clearing) is what lets this
      // tab pick up an account switch made elsewhere and end up correctly
      // showing the NEW account's chats, not just an empty sidebar.
      useAuthStore.getState().fetchCurrentUser()
    }
  })
}
