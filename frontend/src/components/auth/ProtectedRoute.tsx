import { Fragment, type ReactNode } from 'react'
import { Loader2Icon } from 'lucide-react'
import { useAuthStore } from '../../hooks/useAuthStore'
import { WelcomePage } from '../../pages/WelcomePage'

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const status = useAuthStore((s) => s.status)
  const userId = useAuthStore((s) => s.user?.id)

  // 'idle'/'loading' covers the brief window while the initial GET /me
  // (see useAuthStore.fetchCurrentUser, called once from App.tsx) is still
  // in flight — showing a spinner here instead of redirecting avoids a
  // flash of the login page for someone who actually has a valid session.
  if (status === 'idle' || status === 'loading') {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-canvas">
        <Loader2Icon className="h-6 w-6 animate-spin text-faint" />
      </div>
    )
  }

  if (status === 'unauthenticated') {
    // No automatic redirect to /login — a logged-out visitor sees a
    // neutral welcome state instead, with explicit Log in / Sign up
    // actions. /login itself is still fully reachable, just never forced.
    return <WelcomePage />
  }

  // Keying the whole protected subtree by the authenticated user's id is a
  // structural guarantee, not just a data-layer one (see useAuthStore.ts's
  // subscription, which resets useAppStore's chat data on every account
  // change): changing a React `key` forces a full unmount + fresh mount of
  // everything below it, discarding ANY component-local state — not just
  // Zustand state — the instant the authenticated identity changes. This
  // is what protects against a leak from a source this file doesn't even
  // know about (some future child component's own useState, a ref, an
  // effect closure) without needing every one of them individually audited
  // and wired into the reset logic by hand. It also means ChatApp's own
  // mount effect (which calls initConversations()) naturally re-runs on
  // every account switch, on top of (not instead of) useAuthStore's own
  // subscription-driven reset.
  return <Fragment key={userId ?? 'no-user'}>{children}</Fragment>
}
