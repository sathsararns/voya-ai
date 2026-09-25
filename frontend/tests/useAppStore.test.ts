// Store-level tests for Voya AI's most important chat flows. These mock
// `fetch` and call the Zustand store's public actions directly — no DOM
// rendering, no server — so they stay fast and reliable while still
// exercising the real state-transition logic in useAppStore.ts.
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAppStore } from '../src/hooks/useAppStore'

// Zustand stores keep their state across tests unless reset — snapshot the
// store's state once (before any test mutates it) so every test can restore
// a clean baseline in beforeEach.
const initialState = useAppStore.getState()

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

beforeEach(() => {
  useAppStore.setState(initialState, true)
  window.localStorage.clear()
})

describe('initConversations — flow 1: fresh startup shows blank New Chat', () => {
  it('never restores a previous conversation as active, even when the sidebar has history', async () => {
    const conversations = [
      {
        conversation_id: 'conv-1',
        session_id: 's1',
        title: 'Old trip to Galle',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ]
    global.fetch = vi.fn().mockResolvedValueOnce(jsonResponse({ conversations })) as unknown as typeof fetch

    await useAppStore.getState().initConversations()

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBeNull()
    expect(state.messages).toEqual([])
    // The sidebar list itself is still populated — only the "auto-open" part is gone.
    expect(state.conversations).toEqual(conversations)
  })
})

describe('newChat — flow 2: does not auto-open a recent conversation', () => {
  it('resets to a blank draft locally, without any network call', () => {
    useAppStore.setState({
      activeConversationId: 'conv-1',
      messages: [{ id: 'm1', role: 'user', content: 'hi', createdAt: Date.now() }],
    })
    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    useAppStore.getState().newChat()

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBeNull()
    expect(state.messages).toEqual([])
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})

describe('selectConversation — flow 3: clicking a recent chat loads it correctly', () => {
  it('sets the clicked conversation active and loads its history', async () => {
    const history = {
      conversation_id: 'conv-42',
      messages: [
        {
          id: 1,
          user_message: 'Plan a trip to Galle',
          assistant_reply: { summary: 'Here is a plan', itinerary: [], kb_sources: [] },
          created_at: '2026-01-01T00:00:00Z',
        },
      ],
    }
    global.fetch = vi.fn().mockResolvedValueOnce(jsonResponse(history)) as unknown as typeof fetch

    await useAppStore.getState().selectConversation('conv-42')

    const state = useAppStore.getState()
    expect(state.activeConversationId).toBe('conv-42')
    // One history record expands into a user message + an assistant message.
    expect(state.messages).toHaveLength(2)
    expect(state.messages[0].content).toBe('Plan a trip to Galle')
  })

  it('does nothing (besides closing the sidebar) if the conversation is already active', async () => {
    useAppStore.setState({ activeConversationId: 'conv-42', sidebarOpen: true })
    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().selectConversation('conv-42')

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(useAppStore.getState().sidebarOpen).toBe(false)
  })
})

describe('Recent Chats listing never depends on session_id — user_id (the session cookie) is the only scope', () => {
  it('initConversations() requests /api/v1/chat/conversations with no query string at all', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce(jsonResponse({ conversations: [] }))
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().initConversations()

    expect(fetchSpy).toHaveBeenCalledTimes(1)
    const [requestedUrl] = fetchSpy.mock.calls[0] as [string]
    expect(requestedUrl).toBe('http://127.0.0.1:8000/api/v1/chat/conversations')
    expect(requestedUrl).not.toContain('session_id')
  })

  it('refreshConversations() also requests it with no session_id query param', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce(jsonResponse({ conversations: [] }))
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().refreshConversations()

    const [requestedUrl] = fetchSpy.mock.calls[0] as [string]
    expect(requestedUrl).toBe('http://127.0.0.1:8000/api/v1/chat/conversations')
    expect(requestedUrl).not.toContain('session_id')
  })

  it('selectConversation() (reading a single conversation) never sends session_id either', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce(
      jsonResponse({ conversation_id: 'c1', messages: [] }),
    )
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().selectConversation('c1')

    const [requestedUrl] = fetchSpy.mock.calls[0] as [string]
    expect(requestedUrl).not.toContain('session_id')
  })
})

describe('send — flows 4 & 5: first message creates a conversation, follow-ups stay in it', () => {
  it('adopts the conversation_id the backend assigns on the first message', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch // refreshConversations()

    await useAppStore.getState().send('Plan a trip to Ella')

    expect(useAppStore.getState().activeConversationId).toBe('new-conv-1')
  })

  it('sends the existing conversation_id on a follow-up instead of starting a new one', async () => {
    useAppStore.setState({ activeConversationId: 'existing-conv' })
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'Updated plan', itinerary: [], conversation_id: 'existing-conv', kb_sources: [] }),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) // refreshConversations()
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().send('Make it shorter')

    const [, requestInit] = fetchSpy.mock.calls[0] as [string, RequestInit]
    const body = JSON.parse(requestInit.body as string)
    expect(body.conversation_id).toBe('existing-conv')
    expect(useAppStore.getState().activeConversationId).toBe('existing-conv')
  })

  it('a brand-new conversation appears in Recent Chats immediately after the first message', async () => {
    const newConversation = {
      conversation_id: 'new-conv-1',
      session_id: 's',
      title: 'Plan a trip to Ella',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:05Z',
    }
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      // refreshConversations() — the backend already created + titled the
      // conversation server-side (see routes/chat.py's touch_conversation)
      // by the time this second call runs.
      .mockResolvedValueOnce(jsonResponse({ conversations: [newConversation] })) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    // The whole point: by the time send() has settled, Recent Chats
    // already reflects the new conversation — not just activeConversationId.
    expect(useAppStore.getState().conversations).toEqual([newConversation])
  })

  it('an existing conversation stays visible (and reflects updated ordering/title) after a follow-up message', async () => {
    const existing = {
      conversation_id: 'existing-conv',
      session_id: 's',
      title: 'Original title',
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
    }
    useAppStore.setState({ activeConversationId: 'existing-conv', conversations: [existing] })

    const touched = { ...existing, updated_at: '2026-01-01T00:05:00Z' }
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'Updated plan', itinerary: [], conversation_id: 'existing-conv', kb_sources: [] }),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [touched] })) as unknown as typeof fetch

    await useAppStore.getState().send('Make it shorter')

    expect(useAppStore.getState().conversations).toEqual([touched])
  })

  it('the new conversation is already in Recent Chats even while the reconciling refresh is still in flight', async () => {
    let resolveRefresh!: (value: unknown) => void
    const pendingRefresh = new Promise((resolve) => {
      resolveRefresh = resolve
    })
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockReturnValueOnce(pendingRefresh) as unknown as typeof fetch

    const sendPromise = useAppStore.getState().send('Plan a trip to Ella')

    // Flush the POST /chat/ mock's promise chain (fetch -> .json() -> the
    // api.ts/api.post async wrapper layers) and let the optimistic set()
    // run, without waiting for the still-pending refreshConversations()
    // call — a macrotask tick flushes any number of microtask hops, so
    // this doesn't depend on counting exactly how many there are.
    await new Promise((resolve) => setTimeout(resolve, 0))

    const conversations = useAppStore.getState().conversations
    expect(conversations).toHaveLength(1)
    expect(conversations[0].conversation_id).toBe('new-conv-1')
    expect(conversations[0].title).toBe('Plan a trip to Ella')

    resolveRefresh(jsonResponse({ conversations }))
    await sendPromise
  })

  it('survives a reconciling refresh that returns 200 OK but an incomplete list missing the conversation just sent', async () => {
    // The exact reported failure mode: both requests succeed at the
    // network level (200 OK), but the second one's body doesn't (yet, or
    // for any reason) include the conversation just created. Without a
    // safeguard, refreshConversations()'s plain `set({ conversations })`
    // would silently erase the sidebar entry the optimistic update above
    // already guaranteed.
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch // incomplete/stale reconciliation response

    await useAppStore.getState().send('Plan a trip to Ella')

    const conversations = useAppStore.getState().conversations
    expect(conversations).toHaveLength(1)
    expect(conversations[0].conversation_id).toBe('new-conv-1')
  })

  it('if the post-send Recent Chats refresh fails, the sent chat still stays visible in Recent Chats', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockRejectedValueOnce(new Error('network blip')) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    const state = useAppStore.getState()
    expect(state.isResponding).toBe(false)
    expect(state.activeConversationId).toBe('new-conv-1')
    // The whole point of the optimistic update: this survives the failed
    // reconciliation request below — the sidebar never goes back to empty.
    expect(state.conversations).toHaveLength(1)
    expect(state.conversations[0].conversation_id).toBe('new-conv-1')
    const assistantMessage = state.messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toContain('A plan')
  })
})
