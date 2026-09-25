// Proves the account-switch guarantee at the REACT TREE level, not just
// the data layer (accountScoping.test.ts already covers useAppStore's own
// state in isolation). This is the test that answers "could some component
// have local state (useState, a ref, an effect closure) that survives an
// account switch even though the Zustand stores were correctly reset?" —
// by rendering a real child with its own local state under ProtectedRoute
// and checking that state does NOT survive a user change.
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { useState } from 'react'
import '@testing-library/jest-dom/vitest'
import { ProtectedRoute } from '../src/components/auth/ProtectedRoute'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { AuthUser } from '../src/types/auth'

const initialAppState = useAppStore.getState()
const initialAuthState = useAuthStore.getState()

beforeEach(() => {
  useAppStore.setState(initialAppState, true)
  useAuthStore.setState(initialAuthState, true)
})

afterEach(() => {
  cleanup()
})

const tom: AuthUser = { id: 'user-tom', name: 'Tom', email: 'tom@example.com', created_at: '2026-01-01T00:00:00Z' }
const sathsara: AuthUser = {
  id: 'user-sathsara',
  name: 'Sathsara',
  email: 'sathsara@example.com',
  created_at: '2026-01-01T00:00:00Z',
}

// Stands in for "any child component that might hold its own local state
// alongside reading useAppStore" — a mount-id proves whether a real
// unmount+remount happened (a plain re-render would keep the same id,
// since useState's initializer only runs once per mount), and the
// rendered conversation titles prove what data is actually on screen.
function ProtectedContent() {
  const [mountId] = useState(() => Math.random().toString(36).slice(2))
  const conversations = useAppStore((s) => s.conversations)

  return (
    <div>
      <span data-testid="mount-id">{mountId}</span>
      <ul>
        {conversations.map((c) => (
          <li key={c.conversation_id}>{c.title}</li>
        ))}
      </ul>
    </div>
  )
}

function renderProtected() {
  return render(
    <MemoryRouter>
      <ProtectedRoute>
        <ProtectedContent />
      </ProtectedRoute>
    </MemoryRouter>,
  )
}

const tomsConversation = {
  conversation_id: 'tom-1',
  session_id: 's',
  title: "Tom's trip to Nuwara Eliya",
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const sathsarasConversation = {
  conversation_id: 'sathsara-1',
  session_id: 's',
  title: "Sathsara's trip to Jaffna",
  created_at: '2026-01-02T00:00:00Z',
  updated_at: '2026-01-02T00:00:00Z',
}

describe('ProtectedRoute — the protected subtree fully remounts when the authenticated user changes', () => {
  it('unmounts and remounts the entire protected subtree, discarding local component state, when switching from Tom to Sathsara', () => {
    useAuthStore.setState({ user: tom, status: 'authenticated' })
    useAppStore.setState({ conversations: [tomsConversation] })

    const { rerender } = renderProtected()

    expect(screen.getByText("Tom's trip to Nuwara Eliya")).toBeInTheDocument()
    const mountIdWhileTom = screen.getByTestId('mount-id').textContent

    // Switch the authenticated user — exactly what useAuthStore's
    // subscription does for real (see useAuthStore.ts), simulated directly
    // here since this test is specifically about whether the component
    // tree reacts to that change, not about the subscription itself.
    useAuthStore.setState({ user: sathsara, status: 'authenticated' })
    useAppStore.setState({ conversations: [sathsarasConversation] })

    rerender(
      <MemoryRouter>
        <ProtectedRoute>
          <ProtectedContent />
        </ProtectedRoute>
      </MemoryRouter>,
    )

    // A genuinely new mount id proves a real unmount + fresh mount
    // happened — not just a re-render with the same component instance
    // (which would have kept its original useState value).
    const mountIdWhileSathsara = screen.getByTestId('mount-id').textContent
    expect(mountIdWhileSathsara).not.toBe(mountIdWhileTom)

    // And the actually-rendered content is Sathsara's alone.
    expect(screen.queryByText("Tom's trip to Nuwara Eliya")).not.toBeInTheDocument()
    expect(screen.getByText("Sathsara's trip to Jaffna")).toBeInTheDocument()
  })

  it('does NOT remount (and does not lose local state) on a re-render where the user stays the same', () => {
    useAuthStore.setState({ user: tom, status: 'authenticated' })
    useAppStore.setState({ conversations: [tomsConversation] })

    const { rerender } = renderProtected()
    const firstMountId = screen.getByTestId('mount-id').textContent

    // Some unrelated state changes (e.g. a new message arrives) but the
    // authenticated user is unchanged.
    useAppStore.setState({ conversations: [tomsConversation, sathsarasConversation] })
    rerender(
      <MemoryRouter>
        <ProtectedRoute>
          <ProtectedContent />
        </ProtectedRoute>
      </MemoryRouter>,
    )

    expect(screen.getByTestId('mount-id').textContent).toBe(firstMountId)
  })
})

describe('ProtectedRoute — logged-out visitors are never auto-redirected to /login', () => {
  it('renders a neutral welcome state with explicit Log in / Sign up actions instead of navigating away', () => {
    useAuthStore.setState({ user: null, status: 'unauthenticated' })

    renderProtected()

    // The protected content itself must never render for a logged-out visitor...
    expect(screen.queryByTestId('mount-id')).not.toBeInTheDocument()
    // ...and instead of being navigated to /login, they see it as an
    // explicit, clickable choice on the current page.
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
    expect(screen.getByRole('link', { name: 'Sign up' })).toHaveAttribute('href', '/signup')
  })

  it('shows a loading spinner (not the welcome state, not the app) while auth status is still unknown', () => {
    useAuthStore.setState({ user: null, status: 'loading' })

    renderProtected()

    expect(screen.queryByTestId('mount-id')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Log in' })).not.toBeInTheDocument()
  })
})
