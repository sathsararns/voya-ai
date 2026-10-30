import { create } from 'zustand'
import { api, ApiAbortError, ApiNetworkError, ApiRequestError, streamPost } from '../lib/api'
import { getItineraryRevealText } from '../lib/itineraryReveal'
import { useAuthStore } from './useAuthStore'
import type {
  ChatHistoryResponse,
  ChatResponse,
  Conversation,
  ConversationListResponse,
  ItineraryDay,
  Message,
} from '../types'

type Theme = 'light' | 'dark'

interface AppState {
  theme: Theme
  sidebarOpen: boolean
  activeNav: string
  messages: Message[]
  isResponding: boolean
  isLoadingHistory: boolean
  conversations: Conversation[]
  activeConversationId: string | null
  isLoadingConversations: boolean
  conversationsUserId: string | null
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setSidebarOpen: (open: boolean) => void
  setActiveNav: (id: string) => void
  newChat: () => void
  send: (content: string) => Promise<void>
  stopGenerating: () => void
  initConversations: () => Promise<void>
  selectConversation: (conversationId: string) => Promise<void>
  refreshConversations: () => Promise<void>
  resetConversations: () => void
}

const SESSION_STORAGE_KEY = 'voya_session_id'
let cachedSessionId: string | null = null
let conversationEpoch = 0
let selectionEpoch = 0

// Only the selected ID is stored locally. Messages and ownership come from
// the authenticated API, and each account has its own selection.
function activeChatKey(userId: string): string {
  return `voya_active_conversation:${userId}`
}

function rememberActiveChat(userId: string | null, conversationId: string | null): void {
  if (!userId) return
  try {
    window.localStorage.setItem(activeChatKey(userId), conversationId ?? '')
  } catch {
    // Server history still works when browser storage is unavailable.
  }
}

function restoredChatId(userId: string, conversations: Conversation[]): string | null {
  try {
    const saved = window.localStorage.getItem(activeChatKey(userId))
    if (saved === '') return null // The user deliberately opened New Chat.
    if (saved && conversations.some((chat) => chat.conversation_id === saved)) return saved
  } catch {
    // Fall back to the most recent server-owned conversation.
  }
  return conversations[0]?.conversation_id ?? null
}

function isCurrentAccount(epoch: number, userId: string | null): boolean {
  return epoch === conversationEpoch && (useAuthStore.getState().user?.id ?? null) === userId
}

// Cinematic typewriter reveal for a streaming assistant message — tune
// these together to make the effect faster/slower/pausier without
// touching the reveal logic itself (see send()'s revealTick). Exported so
// tests can advance fake timers by an exact multiple of the base interval,
// or by an exact pause duration, instead of guessing at either.
//
// TYPEWRITER_INTERVAL_MS/TYPEWRITER_CHARS_PER_TICK set the baseline
// cadence (how fast plain text reveals). The three pause constants below
// add extra delay ON TOP of that baseline immediately after certain
// characters are revealed — sentence-ending punctuation pauses longest, a
// comma pauses less, and a plain word boundary (space) pauses only
// slightly — which is what keeps the reveal reading like natural speech
// instead of a constant mechanical drip.
export const TYPEWRITER_INTERVAL_MS = 16
export const TYPEWRITER_CHARS_PER_TICK = 2
export const TYPEWRITER_SENTENCE_PAUSE_MS = 220
export const TYPEWRITER_COMMA_PAUSE_MS = 110
export const TYPEWRITER_WORD_PAUSE_MS = 45

// Extra pause (beyond the baseline interval) to insert after revealing a
// chunk that contains a sentence end, a comma, or a word boundary — checked
// across the WHOLE chunk (not just its last character) since
// TYPEWRITER_CHARS_PER_TICK can reveal more than one character at a time,
// and a pause-worthy character can land anywhere inside that chunk. When a
// chunk contains more than one kind (e.g. ". " — a period then a space),
// the strongest applicable pause wins rather than whichever is checked last.
function extraPauseForChunk(chunk: string): number {
  let pause = 0
  for (const char of chunk) {
    if (char === '.' || char === '!' || char === '?') {
      pause = Math.max(pause, TYPEWRITER_SENTENCE_PAUSE_MS)
    } else if (char === ',') {
      pause = Math.max(pause, TYPEWRITER_COMMA_PAUSE_MS)
    } else if (char === ' ') {
      pause = Math.max(pause, TYPEWRITER_WORD_PAUSE_MS)
    }
  }
  return pause
}

// The in-flight send()'s AbortController, if any — module-level rather than
// store state because it's plumbing for cancellation, not UI-facing data
// (mirrors cachedSessionId's own reasoning above). send() guards against a
// second concurrent call (`if (get().isResponding) return`), so there is
// only ever at most one of these active at a time; stopGenerating() just
// aborts whichever one that is.
let activeAbortController: AbortController | null = null

// The in-flight send()'s "finish the reveal right now" hook, if any — set
// only once the backend's authoritative `done` payload has arrived but the
// typewriter hasn't finished animating it yet (see send()'s finalizeMessage
// below). isResponding — and therefore the Composer's Stop button — stays
// true for that entire animation tail, not just until the network response
// itself completes, so Stop has to remain able to do SOMETHING the whole
// time: abort the request if generation is still genuinely in flight, or,
// if generation already finished and only the on-screen animation is still
// catching up, jump straight to the finished result instead of silently
// doing nothing.
let activeRevealSkip: (() => void) | null = null

function createId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

// Stable per-browser id, persisted so it survives reloads — but NOT per
// account: it deliberately stays the same across login/logout/switching
// accounts in the same browser. This app only ever loads/reads Recent
// Chats from inside ProtectedRoute, i.e. once a real user is already known
// (see components/auth/ProtectedRoute.tsx) — so it is NEVER sent for
// listing or reading conversations (see apiListConversations /
// apiGetConversationMessages below); user_id, from the httpOnly session
// cookie every authenticated request already sends, is the only thing
// that ever scopes Recent Chats. It's still sent on send() (POST
// /api/v1/chat/), where it scopes the anonymous-caller fallback for direct API
// callers with no session cookie (see routes/chat.py's
// get_current_user_optional). Pinecone memory uses the server-authenticated
// account identity, independently of this browser id. Falls back to
// an in-memory id (and skips persistence) if localStorage is unavailable,
// e.g. private browsing — the session is still consistent for the current
// tab.
function getOrCreateSessionId(): string {
  if (cachedSessionId) return cachedSessionId

  try {
    const stored = window.localStorage.getItem(SESSION_STORAGE_KEY)
    if (stored) {
      cachedSessionId = stored
      return cachedSessionId
    }
  } catch {
    // localStorage unavailable — fall through and generate an in-memory id
  }

  const id = createId()
  cachedSessionId = id

  try {
    window.localStorage.setItem(SESSION_STORAGE_KEY, id)
  } catch {
    // best effort only
  }

  return id
}

// Sent on every request this store makes while useAuthStore believes a
// user is logged in — a signal the backend cannot otherwise derive, and
// the missing piece that let an authenticated send still land as
// anonymous: useAuthStore.getState().user is in-memory belief, set once at
// login and not re-verified against the actual browser cookie before every
// call. If that httpOnly cookie is ever silently gone by the time a
// request actually goes out — expired without any 401 ever having fired
// to correct the local belief, cleared by devtools or an extension,
// anything — the request would otherwise arrive with no cookie at all,
// which is indistinguishable at the backend from a genuine anonymous
// caller (see routes/auth.py's get_current_user_optional, which must keep
// that path working for this project's real anonymous/API traffic). This
// header tells it "this specific request expects a session regardless" —
// so a present header with no cookie is treated as a broken session (401),
// never silently downgraded to anonymous. A genuine anonymous caller
// (direct API access, this project's own integration tests) never sends
// it, so that path is completely unaffected.
function authExpectationHeaders(): Record<string, string> | undefined {
  return useAuthStore.getState().user ? { 'X-Client-Expects-Auth': '1' } : undefined
}

async function apiListConversations(): Promise<Conversation[]> {
  // No session_id in this request, deliberately — this call only ever
  // happens for an authenticated caller (see initConversations/
  // refreshConversations below, both only reachable from inside
  // ProtectedRoute), and `api` (see lib/api.ts) already sends the httpOnly
  // session cookie with every request. The backend scopes the result by
  // that cookie's user_id alone — see routes/chat.py's get_conversations
  // and db/crud.py's list_conversations, which never even reads session_id
  // once a caller is authenticated. Sending it here would be inert at
  // best; omitting it entirely is what makes that guarantee visible and
  // unambiguous at the call site, not just at the backend.
  const data = await api.get<ConversationListResponse>(
    '/api/v1/chat/conversations',
    authExpectationHeaders(),
  )
  return data.conversations
}

async function apiGetConversationMessages(conversationId: string): Promise<ChatHistoryResponse> {
  return api.get<ChatHistoryResponse>(
    `/api/v1/chat/conversations/${conversationId}/messages`,
    authExpectationHeaders(),
  )
}

// Mirrors backend/routes/chat.py's CONVERSATION_TITLE_MAX_LENGTH /
// _default_title() exactly, so the optimistic entry send() inserts below
// looks identical to what the next real refresh will show — this value is
// only ever used for that one optimistic frame, never persisted or sent
// anywhere.
const CONVERSATION_TITLE_MAX_LENGTH = 60

function defaultTitle(message: string): string {
  const text = message.trim()
  return text.length > CONVERSATION_TITLE_MAX_LENGTH
    ? `${text.slice(0, CONVERSATION_TITLE_MAX_LENGTH)}…`
    : text
}

// Inserts or bumps a conversation in the sidebar list immediately, from
// data already in hand — no network round-trip required. This is what
// makes "a sent chat appears in Recent Chats" NOT depend on a second
// request (refreshConversations) ever succeeding: that second request
// still runs afterward to reconcile with the server's exact state, but by
// the time it fires the entry is already visible. Mirrors the backend's
// own rules exactly (see db/crud.py's touch_conversation): a brand-new
// conversation gets a title derived from this first message; an existing
// one keeps its original title and only its updated_at (and therefore its
// position — list_conversations orders by updated_at desc) changes.
function upsertConversationOptimistically(
  conversations: Conversation[],
  conversationId: string,
  sessionId: string,
  firstMessage: string,
): Conversation[] {
  const now = new Date().toISOString()
  const existingIndex = conversations.findIndex((c) => c.conversation_id === conversationId)

  if (existingIndex === -1) {
    const optimisticEntry: Conversation = {
      conversation_id: conversationId,
      session_id: sessionId,
      title: defaultTitle(firstMessage),
      created_at: now,
      updated_at: now,
    }
    return [optimisticEntry, ...conversations]
  }

  const touched = { ...conversations[existingIndex], updated_at: now }
  const rest = conversations.filter((_, i) => i !== existingIndex)
  return [touched, ...rest]
}

function formatAssistantReply(data: ChatResponse): string {
  const lines: string[] = []

  if (data.destination) {
    lines.push(`Destination: ${data.destination}`)
  }

  if (typeof data.days === 'number') {
    lines.push(`Duration: ${data.days} days`)
  }

  if (typeof data.budget_lkr === 'number') {
    lines.push(`Budget: ${data.budget_lkr.toLocaleString()} LKR`)
  }

  if (data.summary) {
    lines.push('')
    lines.push(data.summary)
  }

  if (Array.isArray(data.itinerary) && data.itinerary.length > 0) {
    lines.push('')
    lines.push('Itinerary:')
    data.itinerary.slice(0, 3).forEach((day: ItineraryDay) => {
      lines.push(`Day ${day.day}: ${day.title}`)
      if (Array.isArray(day.items)) {
        day.items.slice(0, 3).forEach((item: string) => {
          lines.push(`- ${item}`)
        })
      }
    })
  }

  if (data.follow_up_question) {
    lines.push('')
    lines.push(data.follow_up_question)
  }

  return lines.join('\n')
}

function toMessages(history: ChatHistoryResponse): Message[] {
  return history.messages.flatMap((item) => {
    const createdAt = new Date(item.created_at).getTime()
    return [
      {
        id: `h-u-${item.id}`,
        role: 'user' as const,
        content: item.user_message,
        createdAt,
      },
      {
        id: `h-a-${item.id}`,
        role: 'assistant' as const,
        content: formatAssistantReply(item.assistant_reply),
        createdAt,
        plan: item.assistant_reply,
      },
    ]
  })
}

export const useAppStore = create<AppState>((set, get) => ({
  theme: 'light',
  sidebarOpen: false,
  activeNav: 'home',
  messages: [],
  isResponding: false,
  isLoadingHistory: false,
  conversations: [],
  activeConversationId: null,
  isLoadingConversations: false,
  conversationsUserId: null,

  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set({ theme: get().theme === 'light' ? 'dark' : 'light' }),
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
  setActiveNav: (activeNav) => set({ activeNav, sidebarOpen: false }),

  // Login/reload restores this account's selection after fetching its own
  // server history. Auth subscription + ChatApp mount share this initialization.
  initConversations: async () => {
    const userId = useAuthStore.getState().user?.id ?? null
    if (get().isLoadingConversations || (userId && get().conversationsUserId === userId)) return
    const epoch = conversationEpoch
    const selection = selectionEpoch

    set({
      conversations: [],
      messages: [],
      activeConversationId: null,
      isLoadingHistory: true,
      isLoadingConversations: true,
    })

    try {
      const conversations = await apiListConversations()
      if (!isCurrentAccount(epoch, userId)) return
      set({
        conversations,
        conversationsUserId: userId,
        isLoadingConversations: false,
        isLoadingHistory: false,
      })
      if (userId && selection === selectionEpoch) {
        const restoreId = restoredChatId(userId, conversations)
        if (restoreId) await get().selectConversation(restoreId)
      }
    } catch {
      // Backend unreachable — stay on the empty state already set above
      // (never fall back to a stale, possibly-another-account's list) so
      // the composer still works locally.
      if (isCurrentAccount(epoch, userId)) {
        set({ isLoadingHistory: false, isLoadingConversations: false })
      }
    }
  },

  refreshConversations: async () => {
    const epoch = conversationEpoch
    const userId = useAuthStore.getState().user?.id ?? null
    try {
      const conversations = await apiListConversations()
      if (isCurrentAccount(epoch, userId)) set({ conversations })
    } catch (error) {
      // Best effort — the sidebar just keeps its last known list. This
      // used to swallow the error completely, which made a genuine
      // backend/network failure here indistinguishable from "nothing went
      // wrong" from outside a debugger — logging it costs nothing and
      // makes a real failure diagnosable instead of invisible.
      console.error('[useAppStore] refreshConversations failed:', error)
    }
  },

  // Called from useAuthStore's logout() — this store is entirely separate
  // from auth state, so logging out never touches it on its own. Without
  // this, the window between logout and the next account's ChatApp
  // remounting (which re-triggers initConversations) would still have this
  // store holding the previous account's conversations/messages.
  resetConversations: () => {
    get().stopGenerating()
    conversationEpoch += 1
    selectionEpoch += 1
    set({
      conversations: [],
      messages: [],
      activeConversationId: null,
      isLoadingConversations: false,
      isLoadingHistory: false,
      isResponding: false,
      conversationsUserId: null,
    })
  },

  selectConversation: async (conversationId) => {
    if (get().activeConversationId === conversationId) {
      set({ sidebarOpen: false })
      return
    }

    get().stopGenerating()
    const epoch = conversationEpoch
    const selection = ++selectionEpoch
    const userId = useAuthStore.getState().user?.id ?? null
    const isCurrent = () => isCurrentAccount(epoch, userId) && selection === selectionEpoch
    set({ activeConversationId: conversationId, messages: [], sidebarOpen: false, isLoadingHistory: true, isResponding: false })

    try {
      const history = await apiGetConversationMessages(conversationId)
      if (!isCurrent()) return
      set({ messages: toMessages(history), isLoadingHistory: false })
      rememberActiveChat(userId, conversationId)
    } catch {
      if (isCurrent()) set({ activeConversationId: null, messages: [], isLoadingHistory: false })
    }
  },

  // Purely local: clears the thread and detaches from any active
  // conversation. No backend call — nothing is persisted until the user
  // actually sends a message (send() lazily creates the conversation then).
  newChat: () => {
    get().stopGenerating()
    selectionEpoch += 1
    rememberActiveChat(useAuthStore.getState().user?.id ?? null, null)
    set({
      isResponding: false,
      sidebarOpen: false,
      activeNav: 'home',
      messages: [],
      activeConversationId: null,
      isLoadingHistory: false,
    })
  },

  send: async (content) => {
    const text = content.trim()
    if (!text || get().isResponding) return

    // The composer is only ever reachable once ProtectedRoute considers the
    // caller authenticated (see components/auth/ProtectedRoute.tsx) — if
    // useAuthStore's own record of who's logged in is already gone by the
    // moment of sending (a cross-tab logout, a session this tab already
    // detected as expired), refuse to submit as if this were a legitimate
    // authenticated message. The backend's get_current_user_optional (see
    // routes/auth.py) correctly rejects a present-but-broken cookie with
    // 401 — but a request that carries NO cookie at all is, by design,
    // indistinguishable there from a genuine anonymous caller (this
    // project's own real anonymous/API path still needs that to work).
    // That distinction can only be made here, before the request ever
    // leaves this tab.
    if (!useAuthStore.getState().user) {
      set((state) => ({
        messages: [
          ...state.messages,
          {
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: 'Your session has ended. Please log in again to keep chatting.',
            createdAt: Date.now(),
          },
        ],
      }))
      return
    }

    const stamp = Date.now()
    const epoch = conversationEpoch
    const selection = selectionEpoch
    const userId = useAuthStore.getState().user?.id ?? null
    const isCurrent = () => isCurrentAccount(epoch, userId) && selection === selectionEpoch
    const sessionId = getOrCreateSessionId()
    let conversationId = get().activeConversationId

    const controller = new AbortController()
    activeAbortController = controller

    // Typewriter reveal state for this send() call — declared here (not
    // inside the try block below) so the catch block can also reach
    // stopTypewriter()/targetText when the stream is aborted or fails, to
    // stop the ticker and settle on the right final text either way. See
    // TYPEWRITER_INTERVAL_MS/TYPEWRITER_CHARS_PER_TICK/the pause constants
    // above for the actual speed and pacing. `targetText` is what the
    // ticker is currently typing toward; revealTick grows the bubble's
    // visible `content` a fixed few characters at a time, independent of
    // however bursty Groq's own network chunking happens to be. Unlike a
    // fixed setInterval, each next tick is scheduled individually via
    // setTimeout with its OWN delay — the baseline interval plus, when the
    // chunk just revealed contained a sentence end, comma, or space, an
    // extra pause on top (see extraPauseForChunk) — which is what gives the
    // reveal its natural, cinematic pacing instead of a constant drip.
    //
    // Finishing is deliberately NOT "the terminal `done` event immediately
    // snaps content to the full result". `done`'s payload is the full
    // structured plan — destination/duration/budget/itinerary lines that
    // were never part of the streamed text at all, only ever known once
    // generation completes — so setting `content` to it outright (as this
    // used to do) doesn't extend whatever prose the typewriter had reached;
    // it REPLACES it with a longer, differently-shaped string in one frame,
    // which reads as the reply suddenly finishing itself mid-animation.
    // After `done`, the target includes both summary and itinerary details.
    // Once the summary is visible, the same timer reveals the structured
    // card in display order, finishing only after every detail is shown.
    let targetText = ''
    let revealedLength = 0
    let typewriterTimer: ReturnType<typeof setTimeout> | null = null
    let doneData: ChatResponse | null = null
    let savedReply = false
    let finalized = false
    let resolveRevealComplete: () => void = () => {}
    const revealComplete = new Promise<void>((resolve) => {
      resolveRevealComplete = resolve
    })

    const stopTypewriter = () => {
      if (typewriterTimer !== null) {
        clearTimeout(typewriterTimer)
        typewriterTimer = null
      }
    }

    // Completes the structured plan and flips the message out of "streaming"
    // — the ONE place that happens, so it's impossible for it to run twice
    // (guarded by `finalized`) regardless of which of revealTick/onDone/
    // stopGenerating's skip-to-end path gets there first.
    const finalizeMessage = (data: ChatResponse) => {
      if (finalized) return
      finalized = true
      stopTypewriter()
      if (!isCurrent()) {
        resolveRevealComplete()
        return
      }
      const assistantText = formatAssistantReply(data)
      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}`
            ? { ...m, pending: false, streaming: false, content: assistantText, plan: data, planRevealCharacters: undefined }
            : m,
        ),
      }))
      resolveRevealComplete()
    }

    const revealTick = () => {
      typewriterTimer = null
      if (!isCurrent()) {
        resolveRevealComplete()
        return
      }
      if (revealedLength >= targetText.length) {
        // Caught up with everything that has arrived so far. If the
        // authoritative `done` payload is already in hand, that means the
        // reveal has now genuinely reached the end of it — finish for real.
        // Otherwise just stop scheduling; onToken/onDone restart this the
        // next time more text comes in.
        if (doneData) finalizeMessage(doneData)
        return
      }
      const nextLength = Math.min(targetText.length, revealedLength + TYPEWRITER_CHARS_PER_TICK)
      const revealedChunk = targetText.slice(revealedLength, nextLength)
      revealedLength = nextLength
      const summaryLength = doneData?.summary.length ?? targetText.length
      const visibleText = targetText.slice(0, Math.min(revealedLength, summaryLength))
      const detailsVisible = doneData !== null && revealedLength > summaryLength
      const plan = detailsVisible ? (doneData ?? undefined) : undefined
      set((state) => ({
        messages: state.messages.map((m) =>
          // Only ever flips pending -> false on the FIRST tick that
          // actually has something to show — this is what keeps TypingDots
          // visible for the brief instant between "a token event arrived"
          // and "the typewriter has revealed its first character", instead
          // of a blank frame in between.
          m.id === `a-${stamp}` ? {
            ...m,
            pending: false,
            streaming: true,
            content: visibleText,
            plan,
            planRevealCharacters: detailsVisible ? revealedLength - summaryLength : undefined,
          } : m,
        ),
      }))

      if (revealedLength < targetText.length) {
        typewriterTimer = setTimeout(revealTick, TYPEWRITER_INTERVAL_MS + extraPauseForChunk(revealedChunk))
      } else if (doneData) {
        finalizeMessage(doneData)
      }
    }

    // Registered so stopGenerating() can reach it (see activeRevealSkip's
    // own comment) — jumps straight to the finished result if generation
    // has already completed and only the on-screen animation is still
    // catching up; a no-op if `done` hasn't arrived yet (there's nothing
    // finished to jump to — aborting the request, which stopGenerating()
    // always does first, is what actually stops that case). Kept as its
    // own named function (not just assigned inline) so the `finally` block
    // below can check it's still THIS call's hook before clearing it.
    const skipToEndOfReveal = () => {
      if (doneData) {
        revealedLength = targetText.length
        finalizeMessage(doneData)
      }
    }
    activeRevealSkip = skipToEndOfReveal

    set((state) => ({
      isResponding: true,
      activeNav: 'home',
      messages: [
        ...state.messages,
        { id: `u-${stamp}`, role: 'user', content: text, createdAt: stamp },
        { id: `a-${stamp}`, role: 'assistant', content: '', createdAt: stamp, pending: true },
      ],
    }))

    try {
      // credentials are sent by streamPost (see lib/api.ts) — when logged
      // in, this conversation is created/continued under the caller's
      // account (see routes/chat.py's chat_stream), not just this
      // browser's session_id. The extra header (see
      // authExpectationHeaders' own comment) is what makes a
      // silently-vanished cookie fail loudly instead of quietly landing as
      // an anonymous, unowned conversation.
      await streamPost(
        '/api/v1/chat/stream',
        {
          message: text,
          session_id: sessionId,
          conversation_id: conversationId,
        },
        {
          onToken: (chunk) => {
            if (!isCurrent()) return
            targetText += chunk
            if (typewriterTimer === null) {
              typewriterTimer = setTimeout(revealTick, TYPEWRITER_INTERVAL_MS)
            }
          },
          onDone: (rawData) => {
            if (!isCurrent()) return
            const data = rawData as ChatResponse
            doneData = data
            savedReply = !data.error_code

            // Covers the rare case where no conversation existed yet and
            // the backend created one on the fly.
            if (data.conversation_id && data.conversation_id !== conversationId) {
              conversationId = data.conversation_id
              set({ activeConversationId: conversationId })
            }

            // Insert/bump Recent Chats right now, from data already in hand
            // — this is the actual guarantee that a sent chat appears, not
            // the network refresh further below, and it must not wait on
            // the typewriter animation to finish catching up (that can take
            // a while longer for a long reply). See
            // upsertConversationOptimistically's own comment for why.
            if (conversationId && !data.error_code) {
              rememberActiveChat(userId, conversationId)
              const currentConversationId = conversationId
              set((state) => ({
                conversations: upsertConversationOptimistically(state.conversations, currentConversationId, sessionId, text),
              }))
            }

            // Continue from streamed prose into each day and bullet using
            // one character budget. No itinerary text appears ahead of it.
            revealedLength = Math.min(revealedLength, data.summary.length)
            targetText = data.summary + getItineraryRevealText(data)

            if (revealedLength >= targetText.length) {
              // Already fully typed out (e.g. an empty summary, or the
              // ticker had genuinely caught all the way up already) —
              // finish now rather than waiting on a tick that will never
              // fire.
              finalizeMessage(data)
            } else if (typewriterTimer === null) {
              // Covers streams that send their entire reply as `done` with
              // no `token` events at all — the ticker was never started.
              typewriterTimer = setTimeout(revealTick, TYPEWRITER_INTERVAL_MS)
            }
            // Otherwise a tick is already scheduled and will keep typing
            // toward the (now possibly-updated) targetText on its own;
            // revealTick itself calls finalizeMessage once it actually
            // reaches the end.
          },
        },
        { headers: authExpectationHeaders(), signal: controller.signal },
      )

      if (!isCurrent()) return
      if (doneData) {
        // The network side is done, but the on-screen animation may still
        // be mid-reveal — wait for it to actually finish (finalizeMessage)
        // before treating send() itself as complete, so isResponding/plan
        // aren't left stale while text is still visibly typing.
        await revealComplete
      } else if (get().isResponding) {
        // Defensive only: streamPost throws on a reported `error` event or
        // a non-2xx/network failure (caught below, same as before), and the
        // backend's chat_stream always sends a terminal `done`. The one gap
        // neither of those covers is a connection that just ends without
        // either — this stops the composer from being stuck "responding"
        // forever if that ever happens, instead of only reacting to
        // failures the stream actually reported.
        stopTypewriter()
        set((state) => ({
          isResponding: false,
          messages: state.messages.map((m) =>
            m.id === `a-${stamp}`
              ? { ...m, pending: false, streaming: false, content: 'Please try again' }
              : m,
          ),
        }))
      }

      if (!isCurrent()) return
      // Reconciles with the server's exact state afterward (the real
      // title text vs. this file's mirrored truncation, the precise
      // updated_at, etc.) — awaited so it's still a deterministic part of
      // send() completing, but its failure no longer means the sidebar
      // shows nothing: the optimistic update above already guarantees
      // that. isResponding is already false by this point, so none of
      // this delays the message itself appearing "sent" to the user.
      await get().refreshConversations()
      if (!isCurrent()) return

      // refreshConversations() does a full replace (set({ conversations })),
      // not a merge — if that response ever comes back without the
      // conversation just sent (a delayed/incomplete/wrong response, for
      // any reason, including ones no test here can reproduce), it would
      // silently erase the optimistic entry above instead of just failing
      // to improve on it. This is the actual guarantee: whatever the
      // reconciliation step returns, the just-sent conversation can never
      // regress to "not there" as a result of it.
      if (conversationId && savedReply && !get().conversations.some((c) => c.conversation_id === conversationId)) {
        set((state) => ({
          conversations: upsertConversationOptimistically(state.conversations, conversationId!, sessionId, text),
        }))
      }
    } catch (error) {
      // Stops the reveal ticker unconditionally, on every failure branch
      // below — without this, a tick already scheduled before the failure
      // could fire afterward and overwrite whatever this catch block is
      // about to set `content` to with a stale, partially-revealed value.
      stopTypewriter()
      if (!isCurrent()) return

      if (error instanceof ApiAbortError) {
        // The user clicked Stop (see stopGenerating below) — not a
        // failure, so it gets its own outcome instead of an error message.
        // Flushes `content` to `targetText` (everything that had actually
        // arrived over the network) rather than freezing at whatever the
        // typewriter had visually revealed so far — the same "keep
        // whatever was already streamed" guarantee as before the typewriter
        // effect existed, just now needing to say so explicitly: revealing
        // is deliberately slower than arrival, but stopping shouldn't
        // discard text that already arrived just because the animation
        // hadn't caught up to it yet. `stopped` just lets ChatThread.tsx add
        // a small, explicit "Generation stopped" label alongside whatever is
        // there (including nothing, if it was stopped before the first
        // token).
        set((state) => ({
          isResponding: false,
          messages: state.messages.map((m) =>
            m.id === `a-${stamp}` ? { ...m, pending: false, streaming: false, content: targetText, stopped: true } : m,
          ),
        }))
        return
      }

      // Surface the backend's actual reason when there is one (e.g. "Session
      // expired, please log in again" — see routes/auth.py's
      // get_current_user_optional) instead of a generic message that would
      // hide exactly the kind of failure that used to silently orphan a
      // message as anonymous instead of erroring at all. A network-level
      // failure (fetch never got a response, or the connection dropped
      // mid-stream) has no such backend detail to show, so it gets its own
      // plain-language message instead of ApiNetworkError's own
      // developer-facing troubleshooting text.
      const errorMessage =
        error instanceof ApiNetworkError
          ? 'Connection lost'
          : error instanceof ApiRequestError
            ? error.message
            : 'Please try again'

      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}` ? { ...m, pending: false, streaming: false, content: errorMessage } : m,
        ),
      }))

      // A 401 here specifically means the session was believed valid (the
      // composer is only ever reachable once ProtectedRoute considers the
      // caller authenticated) but the backend rejected it. Re-syncing auth
      // state now — rather than leaving `status` stuck on a now-false
      // 'authenticated' — is what makes ProtectedRoute correctly show the
      // logged-out welcome state instead of a composer that will keep
      // failing the same way on every next message.
      if (error instanceof ApiRequestError && error.status === 401) {
        useAuthStore.getState().fetchCurrentUser()
      }
    } finally {
      // Only clear it if it's still this call's controller/skip hook —
      // irrelevant in practice (send() refuses a second concurrent call
      // while isResponding is true) but keeps this from ever clobbering a
      // different, newer call's.
      if (activeAbortController === controller) {
        activeAbortController = null
      }
      if (activeRevealSkip === skipToEndOfReveal) {
        activeRevealSkip = null
      }
    }
  },

  // Cancels the in-flight send(), if any (see the Composer's Stop button).
  // Two distinct things can be in flight when this is clicked, and both
  // need handling: generation itself might still be running (abort the
  // fetch — send()'s own try/catch, the ApiAbortError branch above, turns
  // that into clean UI state) — or generation might have already finished
  // and only the typewriter's on-screen animation is still catching up
  // (nothing left to abort; activeRevealSkip jumps straight to the
  // finished result instead of leaving Stop looking unresponsive for
  // however long the remaining animation would otherwise take). A stray
  // call with nothing in flight is a harmless no-op either way.
  stopGenerating: () => {
    activeAbortController?.abort()
    activeRevealSkip?.()
  },
}))
