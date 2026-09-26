// Store-level tests for Voya AI's most important chat flows. These mock
// `fetch` and call the Zustand store's public actions directly — no DOM
// rendering, no server — so they stay fast and reliable while still
// exercising the real state-transition logic in useAppStore.ts.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { TYPEWRITER_CHARS_PER_TICK, TYPEWRITER_INTERVAL_MS, useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { AuthUser } from '../src/types/auth'

// Zustand stores keep their state across tests unless reset — snapshot the
// store's state once (before any test mutates it) so every test can restore
// a clean baseline in beforeEach.
const initialState = useAppStore.getState()
const initialAuthState = useAuthStore.getState()

// send() only ever runs from the real chat composer, which is only ever
// reachable once ProtectedRoute considers the caller authenticated (see
// components/auth/ProtectedRoute.tsx) — so every send()-flow test below
// defaults to a logged-in user, matching that real precondition. The one
// test specifically covering a logged-out send overrides this explicitly.
const defaultUser: AuthUser = {
  id: 'default-user',
  name: 'Test User',
  email: 'test@example.com',
  created_at: '2026-01-01T00:00:00Z',
}

function jsonResponse(body: unknown, ok = true) {
  return { ok, status: ok ? 200 : 500, json: async () => body }
}

// Builds a fetch-mock-compatible response for streamPost (see
// src/lib/api.ts) — a real ReadableStream over SSE-formatted bytes, so the
// exact same parsing code path send() uses in production runs in these
// tests too, not a hand-rolled substitute for it. `events` defaults to a
// single terminal `done` event, which is all most send() tests need; pass
// explicit token events first to also exercise the live-typing path.
function sseResponse(events: Array<{ event: string; data: unknown }>) {
  const text = events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join('')
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(text))
      controller.close()
    },
  })
  return { ok: true, status: 200, body }
}

function sseDone(data: unknown) {
  return sseResponse([{ event: 'done', data }])
}

beforeEach(() => {
  useAppStore.setState(initialState, true)
  useAuthStore.setState(initialAuthState, true)
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
    // Checks the path and the absence of session_id, not the origin —
    // VITE_API_BASE_URL (see frontend/.env.local/.env.example) is
    // deliberately environment-configurable, so asserting a specific
    // hardcoded origin here would make this test fragile to a setting
    // that has nothing to do with what it's actually verifying.
    const [requestedUrl] = fetchSpy.mock.calls[0] as [string]
    expect(requestedUrl).toMatch(/\/api\/v1\/chat\/conversations$/)
    expect(requestedUrl).not.toContain('session_id')
  })

  it('refreshConversations() also requests it with no session_id query param', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce(jsonResponse({ conversations: [] }))
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().refreshConversations()

    const [requestedUrl] = fetchSpy.mock.calls[0] as [string]
    expect(requestedUrl).toMatch(/\/api\/v1\/chat\/conversations$/)
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
  // send() only ever runs from the real chat composer, which is only
  // reachable once ProtectedRoute considers the caller authenticated — set
  // that up here for every test in this block. Setting useAuthStore's user
  // fires its cross-store subscription (see useAuthStore.ts), which kicks
  // off its own fire-and-forget initConversations() call; flushing that
  // with a safe default mock BEFORE each test installs its own fetch mocks
  // is what stops that background call from racing with (and stealing a
  // mockResolvedValueOnce slot from) the test's own assertions.
  beforeEach(async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  it('refuses to send (and never calls fetch) when this tab already knows it is logged out', async () => {
    // The one case get_current_user_optional's 401 fix (see
    // backend/routes/auth.py) cannot cover on its own: a request that
    // carries NO session cookie at all is, by design, indistinguishable
    // there from a genuine anonymous caller. This tab already knowing
    // `user` is null must stop the message from ever leaving as if it
    // were a legitimate authenticated send.
    useAuthStore.setState({ user: null, status: 'unauthenticated' })
    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    expect(fetchSpy).not.toHaveBeenCalled()
    const assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toBe('Your session has ended. Please log in again to keep chatting.')
  })

  it('sends X-Client-Expects-Auth when this tab believes it is logged in — the signal that catches a silently-vanished cookie', async () => {
    // Closes the gap neither the logged-out guard above nor the backend's
    // own cookie-validation fix alone can: useAuthStore.getState().user is
    // in-memory belief, set once at login, never re-verified against the
    // actual browser cookie before this call. If that cookie is ever
    // silently gone by request time (for a reason neither side can
    // directly observe), this header is what tells routes/auth.py's
    // get_current_user_optional to reject it (401) instead of quietly
    // treating it as a genuine anonymous send.
    const fetchSpy = vi.fn().mockResolvedValueOnce(
      sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
    )
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    const [, requestInit] = fetchSpy.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(requestInit.headers)
    expect(headers.get('X-Client-Expects-Auth')).toBe('1')
  })

  it('never sends X-Client-Expects-Auth for a genuinely logged-out sender (the guard above already stops the send, but confirms no stray header either)', async () => {
    useAuthStore.setState({ user: null, status: 'unauthenticated' })
    const fetchSpy = vi.fn()
    global.fetch = fetchSpy as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('adopts the conversation_id the backend assigns on the first message', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
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
        sseDone({ summary: 'Updated plan', itinerary: [], conversation_id: 'existing-conv', kb_sources: [] }),
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
        sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
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
        sseDone({ summary: 'Updated plan', itinerary: [], conversation_id: 'existing-conv', kb_sources: [] }),
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
        sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
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
        sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch // incomplete/stale reconciliation response

    await useAppStore.getState().send('Plan a trip to Ella')

    const conversations = useAppStore.getState().conversations
    expect(conversations).toHaveLength(1)
    expect(conversations[0].conversation_id).toBe('new-conv-1')
  })

  it('a 401 from a stale/revoked session surfaces the real message and re-syncs auth state instead of silently failing', async () => {
    // Mirrors routes/auth.py's get_current_user_optional: a session that
    // was believed valid but is actually revoked/expired now rejects the
    // send with 401 (detail: "Session expired...") instead of silently
    // saving the conversation as anonymous (user_id NULL) — see
    // backend/tests/test_account_scoped_chats.py's
    // TestChatOwnershipNeverSilentlyAnonymous for the backend side.
    //
    // Deliberately does NOT call useAuthStore.setState() again here — the
    // describe block's own beforeEach already established (and flushed) an
    // authenticated user. Re-setting it here would trigger ANOTHER
    // identity transition on useAuthStore's subscription (see
    // useAuthStore.ts), kicking off a second fire-and-forget
    // initConversations() call that could resolve mid-send() and wipe
    // `messages` (its success path does `messages: []`) right after this
    // test's assertion target gets set — a test-authoring hazard, not a
    // real one, since real usage never changes the authenticated user
    // mid-send.
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Session expired, please log in again' }),
      })
      .mockRejectedValueOnce(new Error('GET /me also fails — the session is genuinely gone')) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Ella')

    const assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toBe('Session expired, please log in again')

    // fetchCurrentUser() is fire-and-forget from send()'s point of view
    // (send() doesn't await it), so give its own GET /me call a tick to
    // settle before checking the result.
    await new Promise((resolve) => setTimeout(resolve, 0))

    // Proves fetchCurrentUser() was actually triggered: it re-checked with
    // the backend, that also failed, and auth state correctly flipped to
    // unauthenticated — not left stuck claiming a session that no longer
    // works.
    expect(useAuthStore.getState().status).toBe('unauthenticated')
  })

  it('if the post-send Recent Chats refresh fails, the sent chat still stays visible in Recent Chats', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        sseDone({ summary: 'A plan', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] }),
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

describe('send — streaming: the assistant bubble fills in token-by-token', () => {
  beforeEach(async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  it('the finished message contains all streamed text and the structured plan from the done event', async () => {
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          { event: 'token', data: { text: 'Kandy ' } },
          { event: 'token', data: { text: 'is lovely.' } },
          {
            event: 'done',
            data: { summary: 'Kandy is lovely.', itinerary: [], conversation_id: 'new-conv-1', kb_sources: [] },
          },
        ]),
      )
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Kandy')

    const assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toContain('Kandy is lovely.')
    // Streaming has finished and the full structured reply is attached —
    // this is what lets ChatThread.tsx switch from the live-typing text to
    // the finished ItineraryCard.
    expect(assistantMessage?.streaming).toBe(false)
    expect(assistantMessage?.plan?.conversation_id).toBe('new-conv-1')
  })

  it('stays pending until the typewriter reveals its first character, then marks the message streaming (not pending), before done', async () => {
    // A stream that deliberately pauses after its first token — lets this
    // test observe state strictly BETWEEN the first token and the done
    // event, which a stream that resolves in one go never allows. Fake
    // timers make the typewriter's reveal (see useAppStore.ts's send(),
    // TYPEWRITER_INTERVAL_MS) deterministic instead of racing real time.
    vi.useFakeTimers()
    try {
      let releaseStream!: () => void
      const streamGate = new Promise<void>((resolve) => {
        releaseStream = resolve
      })

      const body = new ReadableStream<Uint8Array>({
        async start(controller) {
          controller.enqueue(new TextEncoder().encode('event: token\ndata: {"text": "Hello"}\n\n'))
          await streamGate
          controller.enqueue(
            new TextEncoder().encode(
              `event: done\ndata: ${JSON.stringify({
                summary: 'Hello there.',
                itinerary: [],
                conversation_id: 'new-conv-1',
                kb_sources: [],
              })}\n\n`,
            ),
          )
          controller.close()
        },
      })

      global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body }) as unknown as typeof fetch

      const sendPromise = useAppStore.getState().send('Hi')
      // Flushes the token event's read -> decode -> dispatch chain (pure
      // microtask work) without advancing the typewriter's own interval at
      // all yet.
      await vi.advanceTimersByTimeAsync(0)

      let assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
      // The token has arrived (targetText is populated, the ticker is
      // scheduled), but it hasn't ticked yet — still shows the typing
      // indicator instead of jumping straight to text.
      expect(assistantMessage?.pending).toBe(true)
      expect(assistantMessage?.content).toBe('')

      // One tick reveals the first TYPEWRITER_CHARS_PER_TICK character(s).
      await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS)
      assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
      expect(assistantMessage?.pending).toBe(false)
      expect(assistantMessage?.streaming).toBe(true)
      expect(assistantMessage?.content).toBe('Hello'.slice(0, TYPEWRITER_CHARS_PER_TICK))
      expect(assistantMessage?.plan).toBeUndefined()

      releaseStream()
      await sendPromise
    } finally {
      vi.useRealTimers()
    }
  })

  it('an error event partway through the stream surfaces as a graceful message instead of leaving the bubble stuck streaming', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(
      sseResponse([
        { event: 'token', data: { text: 'Working on it' } },
        { event: 'error', data: { detail: 'A required service is temporarily unavailable. Please try again shortly.' } },
      ]),
    ) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip to Kandy')

    const state = useAppStore.getState()
    expect(state.isResponding).toBe(false)
    const assistantMessage = state.messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.streaming).toBe(false)
    expect(assistantMessage?.content).toBe('A required service is temporarily unavailable. Please try again shortly.')
  })
})

describe('stopGenerating — cancelling an in-flight stream', () => {
  beforeEach(async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  // Builds a fetch mock whose returned stream reacts to the AbortSignal
  // streamPost forwards to it, exactly like a real fetch()/ReadableStream
  // would — this is what lets stopGenerating() (which only calls
  // AbortController#abort() — see useAppStore.ts) actually be observed
  // taking effect through the real send()/streamPost code path, not a
  // mocked-out shortcut.
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

  it('aborts the in-flight request and marks the message stopped, keeping whatever text had already streamed', async () => {
    global.fetch = abortableStreamFetch('Kandy is a lovely') as unknown as typeof fetch

    const sendPromise = useAppStore.getState().send('Plan a trip to Kandy')
    // Lets the token event arrive (targetText is now populated) without
    // asserting on `content` yet — with the typewriter reveal (see
    // useAppStore.ts's send()), content grows gradually via a timer rather
    // than jumping to the full text the instant a token arrives, so the
    // meaningful assertion is what happens once stopped, below, not this
    // intermediate moment.
    await new Promise((resolve) => setTimeout(resolve, 0))

    useAppStore.getState().stopGenerating()
    await sendPromise

    const state = useAppStore.getState()
    // Leaves streaming mode cleanly: not stuck responding/pending/streaming.
    expect(state.isResponding).toBe(false)
    const assistantMessage = state.messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.pending).toBe(false)
    expect(assistantMessage?.streaming).toBe(false)
    // Everything that had arrived over the network is kept, not discarded —
    // flushed in full on stop regardless of how much the typewriter had
    // visually revealed by that point (see tests further down for that
    // specific guarantee in detail).
    expect(assistantMessage?.content).toBe('Kandy is a lovely')
    expect(assistantMessage?.stopped).toBe(true)
    // Never completed, so there's no structured plan for this message.
    expect(assistantMessage?.plan).toBeUndefined()
  })

  it('stopping before any token has arrived leaves the message empty but clearly marked stopped', async () => {
    global.fetch = abortableStreamFetch() as unknown as typeof fetch

    const sendPromise = useAppStore.getState().send('Plan a trip to Kandy')
    await new Promise((resolve) => setTimeout(resolve, 0))

    useAppStore.getState().stopGenerating()
    await sendPromise

    const assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toBe('')
    expect(assistantMessage?.stopped).toBe(true)
    expect(useAppStore.getState().isResponding).toBe(false)
  })

  it('is a harmless no-op when nothing is in flight', () => {
    expect(() => useAppStore.getState().stopGenerating()).not.toThrow()
    expect(useAppStore.getState().isResponding).toBe(false)
  })

  it('does not resurrect a previous request: stopping an old (already-settled) send has no effect on a new one', async () => {
    // First send completes normally.
    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(sseDone({ summary: 'First reply', itinerary: [], conversation_id: 'conv-1', kb_sources: [] }))
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    await useAppStore.getState().send('First message')
    expect(useAppStore.getState().isResponding).toBe(false)

    // Calling stopGenerating() now (nothing in flight) must not affect a
    // second, currently-in-flight send.
    global.fetch = abortableStreamFetch('Second reply in progress') as unknown as typeof fetch
    const secondSend = useAppStore.getState().send('Second message')
    await new Promise((resolve) => setTimeout(resolve, 0))

    useAppStore.getState().stopGenerating()
    await secondSend

    // The second send WAS stopped (it's the one actually in flight) —
    // this proves stopGenerating() always targets the current request, not
    // a stale reference left over from the first one.
    const messages = useAppStore.getState().messages
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
    expect(lastAssistant?.stopped).toBe(true)
    expect(lastAssistant?.content).toBe('Second reply in progress')
  })

  it('a new message can be sent normally after a previous generation was stopped', async () => {
    global.fetch = abortableStreamFetch() as unknown as typeof fetch
    const firstSend = useAppStore.getState().send('First message')
    await new Promise((resolve) => setTimeout(resolve, 0))
    useAppStore.getState().stopGenerating()
    await firstSend
    expect(useAppStore.getState().isResponding).toBe(false)

    global.fetch = vi
      .fn()
      .mockResolvedValueOnce(sseDone({ summary: 'Second reply', itinerary: [], conversation_id: 'conv-2', kb_sources: [] }))
      .mockResolvedValueOnce(jsonResponse({ conversations: [] })) as unknown as typeof fetch

    await useAppStore.getState().send('Second message')

    const messages = useAppStore.getState().messages
    const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant')
    expect(lastAssistant?.content).toContain('Second reply')
    expect(lastAssistant?.stopped).toBeUndefined()
    expect(useAppStore.getState().isResponding).toBe(false)
  })
})

describe('typewriter reveal — streamed text builds up character-by-character', () => {
  beforeEach(async () => {
    global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
    useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
    await new Promise((resolve) => setTimeout(resolve, 0))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('reveals a streamed token a few characters at a time, not all at once', async () => {
    vi.useFakeTimers()

    // A stream that pauses after its token event — without this gate, a
    // mocked response delivers token+done in the same synchronous pass
    // (see the SSE parser in lib/api.ts), leaving no window in which the
    // ticker could visibly be "still catching up". Real Groq responses
    // naturally have that window because generation takes real time; this
    // reproduces it deterministically instead of depending on that timing.
    let releaseStream!: () => void
    const streamGate = new Promise<void>((resolve) => {
      releaseStream = resolve
    })
    const fullText = 'Kandy is a lovely hill-country city with a rich cultural heritage.'

    const body = new ReadableStream<Uint8Array>({
      async start(controller) {
        controller.enqueue(new TextEncoder().encode(`event: token\ndata: ${JSON.stringify({ text: fullText })}\n\n`))
        await streamGate
        controller.enqueue(
          new TextEncoder().encode(
            `event: done\ndata: ${JSON.stringify({
              summary: fullText,
              itinerary: [],
              conversation_id: 'conv-1',
              kb_sources: [],
            })}\n\n`,
          ),
        )
        controller.close()
      },
    })
    global.fetch = vi.fn().mockResolvedValueOnce({ ok: true, status: 200, body }) as unknown as typeof fetch

    const sendPromise = useAppStore.getState().send('Plan a trip to Kandy')
    await vi.advanceTimersByTimeAsync(0) // deliver the token event, schedule the ticker

    await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS * 3)
    const afterThreeTicks = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    const expectedAfterThree = fullText.slice(0, Math.min(fullText.length, TYPEWRITER_CHARS_PER_TICK * 3))
    expect(afterThreeTicks?.content).toBe(expectedAfterThree)
    // The actual point of this test: genuinely partial, not the whole
    // string already — if this ever failed, the test would no longer be
    // proving a gradual reveal at all.
    expect(afterThreeTicks!.content.length).toBeLessThan(fullText.length)

    await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS * 3)
    const afterSixTicks = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    const expectedAfterSix = fullText.slice(0, Math.min(fullText.length, TYPEWRITER_CHARS_PER_TICK * 6))
    expect(afterSixTicks?.content).toBe(expectedAfterSix)
    expect(afterSixTicks!.content.length).toBeGreaterThan(afterThreeTicks!.content.length)

    releaseStream()
    // Enough ticks to finish revealing the rest, plus room for the done
    // event's own microtasks to settle.
    const remainingTicks = Math.ceil((fullText.length - afterSixTicks!.content.length) / TYPEWRITER_CHARS_PER_TICK) + 5
    await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS * remainingTicks)
    await sendPromise

    const finalMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(finalMessage?.content).toContain(fullText)
    expect(finalMessage?.streaming).toBe(false)
  })

  it('snaps immediately to the full authoritative text on done, even with zero typewriter progress', async () => {
    // No fake timers, and none needed: send() awaits streamPost, which
    // resolves once the (ungated, single-chunk) SSE body is fully parsed —
    // proving completion never depends on the reveal ticker ever firing.
    const fullText = 'This is a fairly long sentence that would take many ticks to fully type out.'
    global.fetch = vi.fn().mockResolvedValueOnce(
      sseResponse([
        { event: 'token', data: { text: fullText } },
        { event: 'done', data: { summary: fullText, itinerary: [], conversation_id: 'conv-1', kb_sources: [] } },
      ]),
    ) as unknown as typeof fetch

    await useAppStore.getState().send('Plan a trip')

    const assistantMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(assistantMessage?.content).toContain(fullText)
    expect(assistantMessage?.streaming).toBe(false)
    expect(assistantMessage?.plan?.summary).toBe(fullText)
  })

  it('flushes to everything that had arrived on Stop, even if the typewriter had only revealed part of it', async () => {
    vi.useFakeTimers()

    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const fullText = 'Kandy is a lovely hill-country city with a rich cultural heritage.'
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        streamController = c
        c.enqueue(new TextEncoder().encode(`event: token\ndata: ${JSON.stringify({ text: fullText })}\n\n`))
      },
    })
    global.fetch = vi.fn((_url: string, init?: RequestInit) => {
      init?.signal?.addEventListener('abort', () => {
        streamController.error(new DOMException('The operation was aborted.', 'AbortError'))
      })
      return Promise.resolve({ ok: true, status: 200, body })
    }) as unknown as typeof fetch

    const sendPromise = useAppStore.getState().send('Plan a trip to Kandy')
    await vi.advanceTimersByTimeAsync(0) // deliver the token event
    await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS * 2) // reveal only a couple of characters

    const midway = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    expect(midway!.content.length).toBeGreaterThan(0)
    expect(midway!.content.length).toBeLessThan(fullText.length)

    useAppStore.getState().stopGenerating()
    await sendPromise

    const finalMessage = useAppStore.getState().messages.find((m) => m.role === 'assistant')
    // The whole point: stopping doesn't freeze on the half-typed snapshot —
    // it shows everything that had actually arrived over the network,
    // exactly like stopping without a typewriter effect at all would.
    expect(finalMessage?.content).toBe(fullText)
    expect(finalMessage?.stopped).toBe(true)
    expect(finalMessage?.streaming).toBe(false)
  })

  it('never leaves a dangling reveal timer running after the message finishes', async () => {
    vi.useFakeTimers()

    const fullText = 'Short reply.'
    global.fetch = vi.fn().mockResolvedValueOnce(
      sseResponse([
        { event: 'token', data: { text: fullText } },
        { event: 'done', data: { summary: fullText, itinerary: [], conversation_id: 'conv-1', kb_sources: [] } },
      ]),
    ) as unknown as typeof fetch

    await useAppStore.getState().send('Hi')

    const messagesBefore = useAppStore.getState().messages
    // If a timer were still running, advancing fake time here would trigger
    // more set() calls — asserting the array reference itself is unchanged
    // (Zustand's set() always produces a new array for `messages`) is a
    // direct proof that nothing fired.
    await vi.advanceTimersByTimeAsync(TYPEWRITER_INTERVAL_MS * 20)
    expect(useAppStore.getState().messages).toBe(messagesBefore)
  })
})
