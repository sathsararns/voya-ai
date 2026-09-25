// Flow 8: sidebar recent-chat highlighting. Renders the real Sidebar
// component and checks that only the active conversation's button carries
// aria-current, both when one is selected and when none is (fresh startup).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import '@testing-library/jest-dom/vitest'
import { Sidebar } from '../src/components/Sidebar'
import { useAppStore } from '../src/hooks/useAppStore'

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

// Sidebar's account menu uses useNavigate() (for the post-logout redirect),
// which needs a Router context even though these tests never trigger it.
function renderSidebar() {
  return render(
    <MemoryRouter>
      <Sidebar />
    </MemoryRouter>,
  )
}

const initialState = useAppStore.getState()

const conversations = [
  {
    conversation_id: 'conv-1',
    session_id: 's1',
    title: 'Trip to Galle',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
  },
  {
    conversation_id: 'conv-2',
    session_id: 's1',
    title: 'Trip to Kandy',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  },
]

beforeEach(() => {
  useAppStore.setState(initialState, true)
})

afterEach(() => {
  cleanup()
})

describe('Sidebar recent chat highlighting', () => {
  it('marks only the active conversation as current', () => {
    useAppStore.setState({ conversations, activeConversationId: 'conv-2' })

    renderSidebar()

    const activeButton = screen.getByText('Trip to Kandy').closest('button')
    const inactiveButton = screen.getByText('Trip to Galle').closest('button')

    expect(activeButton).toHaveAttribute('aria-current', 'true')
    expect(inactiveButton).not.toHaveAttribute('aria-current')
  })

  it('highlights nothing when no conversation is active, e.g. on fresh startup', () => {
    useAppStore.setState({ conversations, activeConversationId: null })

    renderSidebar()

    const buttons = screen.getAllByText(/Trip to/).map((el) => el.closest('button'))
    for (const button of buttons) {
      expect(button).not.toHaveAttribute('aria-current')
    }
  })

  it('moves the highlight when a different conversation becomes active', () => {
    useAppStore.setState({ conversations, activeConversationId: 'conv-1' })
    const { rerender } = renderSidebar()

    expect(screen.getByText('Trip to Galle').closest('button')).toHaveAttribute('aria-current', 'true')

    useAppStore.setState({ activeConversationId: 'conv-2' })
    rerender(
      <MemoryRouter>
        <Sidebar />
      </MemoryRouter>,
    )

    expect(screen.getByText('Trip to Galle').closest('button')).not.toHaveAttribute('aria-current')
    expect(screen.getByText('Trip to Kandy').closest('button')).toHaveAttribute('aria-current', 'true')
  })
})

describe('Sidebar reflects a sent chat immediately — real store, real render, real send()', () => {
  // End-to-end proof through the actual chain a user experiences: the real
  // Sidebar component, subscribed to the real useAppStore, driven by the
  // real send() action (only fetch is mocked) — not just asserting on
  // store state in isolation (see tests/useAppStore.test.ts for that).
  it('a brand-new conversation appears in the rendered sidebar right after send() resolves', async () => {
    useAppStore.setState({ conversations: [] })
    renderSidebar()

    expect(screen.queryByText('Plan a trip to Ella')).not.toBeInTheDocument()

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          conversations: [
            {
              conversation_id: 'new-conv-1',
              session_id: 's',
              title: 'Plan a trip to Ella',
              created_at: '2026-01-01T00:00:00Z',
              updated_at: '2026-01-01T00:00:05Z',
            },
          ],
        }),
      ) as unknown as typeof fetch

    await act(async () => {
      await useAppStore.getState().send('Plan a trip to Ella')
    })

    expect(screen.getByText('Plan a trip to Ella')).toBeInTheDocument()
  })

  it('a follow-up message keeps the conversation visible and does not duplicate it in the sidebar', async () => {
    const existing = {
      conversation_id: 'existing-conv',
      session_id: 's',
      title: 'Original title',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    useAppStore.setState({ conversations: [existing], activeConversationId: 'existing-conv' })
    renderSidebar()

    expect(screen.getByText('Original title')).toBeInTheDocument()

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'Updated plan', itinerary: [], conversation_id: 'existing-conv', kb_sources: [] }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ conversations: [{ ...existing, updated_at: '2026-01-01T00:05:00Z' }] }),
      ) as unknown as typeof fetch

    await act(async () => {
      await useAppStore.getState().send('Make it shorter')
    })

    // Still visible, still exactly one entry — never duplicated.
    expect(screen.getAllByText('Original title')).toHaveLength(1)
  })
})
