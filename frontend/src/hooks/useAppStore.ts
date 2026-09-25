import { create } from 'zustand'
import { api } from '../lib/api'
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
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setSidebarOpen: (open: boolean) => void
  setActiveNav: (id: string) => void
  newChat: () => void
  send: (content: string) => Promise<void>
  initConversations: () => Promise<void>
  selectConversation: (conversationId: string) => Promise<void>
  refreshConversations: () => Promise<void>
  resetConversations: () => void
}

const SESSION_STORAGE_KEY = 'voya_session_id'
let cachedSessionId: string | null = null

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
// /api/v1/chat/), where it has a different, unrelated job: Pinecone
// conversational-memory continuity (see backend/pinecone_memory.py) and
// the anonymous-caller fallback the backend still supports for direct API
// callers with no session cookie (see routes/chat.py's
// get_current_user_optional) — not Recent Chats ownership. Falls back to
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
  const data = await api.get<ConversationListResponse>('/api/v1/chat/conversations')
  return data.conversations
}

async function apiGetConversationMessages(conversationId: string): Promise<ChatHistoryResponse> {
  return api.get<ChatHistoryResponse>(`/api/v1/chat/conversations/${conversationId}/messages`)
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

  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set({ theme: get().theme === 'light' ? 'dark' : 'light' }),
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
  setActiveNav: (activeNav) => set({ activeNav, sidebarOpen: false }),

  // Runs once on app start (and again every time ChatApp mounts — i.e.
  // every fresh login, since navigating to /login and back necessarily
  // unmounts/remounts it) to load this browser's conversation list for the
  // sidebar. NEVER auto-restores a previous conversation as active — app
  // start/refresh always lands on the blank welcome/composer state. A
  // conversation only ever becomes active when the user explicitly clicks
  // one in Recent Chats (selectConversation) or sends a first message
  // (send(), which lazily creates one).
  //
  // Clears conversations/messages/activeConversationId SYNCHRONOUSLY,
  // before the fetch even starts — this is what stops one account's Recent
  // Chats from being visible, even briefly, right after switching to a
  // different account in the same browser tab. Without this, the previous
  // account's list would stay in this store (a separate Zustand store from
  // useAuthStore, so logging out never touched it) until the new fetch
  // resolved — or indefinitely, if that fetch ever failed, since the old
  // code's catch block only cleared loading flags, not the stale list
  // itself.
  //
  // Guards against a concurrent call with an early return — useAuthStore's
  // subscription (see useAuthStore.ts) and ChatApp's own mount effect can
  // both call this within the same tick right after a login, and running
  // the fetch twice in parallel is wasted work, not a correctness issue,
  // but worth skipping.
  initConversations: async () => {
    if (get().isLoadingConversations) return

    set({
      conversations: [],
      messages: [],
      activeConversationId: null,
      isLoadingHistory: true,
      isLoadingConversations: true,
    })

    try {
      const conversations = await apiListConversations()
      set({
        conversations,
        isLoadingConversations: false,
        activeConversationId: null,
        messages: [],
        isLoadingHistory: false,
      })
    } catch {
      // Backend unreachable — stay on the empty state already set above
      // (never fall back to a stale, possibly-another-account's list) so
      // the composer still works locally.
      set({ isLoadingHistory: false, isLoadingConversations: false })
    }
  },

  refreshConversations: async () => {
    try {
      const conversations = await apiListConversations()
      set({ conversations })
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
    set({
      conversations: [],
      messages: [],
      activeConversationId: null,
      isLoadingConversations: false,
      isLoadingHistory: false,
    })
  },

  selectConversation: async (conversationId) => {
    if (get().activeConversationId === conversationId) {
      set({ sidebarOpen: false })
      return
    }

    set({ activeConversationId: conversationId, sidebarOpen: false, isLoadingHistory: true })

    try {
      const history = await apiGetConversationMessages(conversationId)
      set({ messages: toMessages(history), isLoadingHistory: false })
    } catch {
      set({ messages: [], isLoadingHistory: false })
    }
  },

  // Purely local: clears the thread and detaches from any active
  // conversation. No backend call — nothing is persisted until the user
  // actually sends a message (send() lazily creates the conversation then).
  newChat: () => {
    set({
      isResponding: false,
      sidebarOpen: false,
      activeNav: 'home',
      messages: [],
      activeConversationId: null,
    })
  },

  send: async (content) => {
    const text = content.trim()
    if (!text || get().isResponding) return

    const stamp = Date.now()
    const sessionId = getOrCreateSessionId()
    let conversationId = get().activeConversationId

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
      // credentials are sent by `api` — when logged in, this conversation
      // is created/continued under the caller's account (see
      // routes/chat.py), not just this browser's session_id.
      const data = await api.post<ChatResponse>('/api/v1/chat/', {
        message: text,
        session_id: sessionId,
        conversation_id: conversationId,
      })
      const assistantText = formatAssistantReply(data)

      // Covers the rare case where no conversation existed yet and the
      // backend created one on the fly.
      if (data.conversation_id && data.conversation_id !== conversationId) {
        conversationId = data.conversation_id
        set({ activeConversationId: conversationId })
      }

      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}` ? { ...m, pending: false, content: assistantText, plan: data } : m,
        ),
        // Insert/bump Recent Chats immediately, from data already in
        // hand — this is the actual guarantee that a sent chat appears,
        // not the network refresh below. See
        // upsertConversationOptimistically's own comment for why.
        conversations: conversationId
          ? upsertConversationOptimistically(state.conversations, conversationId, sessionId, text)
          : state.conversations,
      }))

      // Reconciles with the server's exact state afterward (the real
      // title text vs. this file's mirrored truncation, the precise
      // updated_at, etc.) — awaited so it's still a deterministic part of
      // send() completing, but its failure no longer means the sidebar
      // shows nothing: the optimistic update above already guarantees
      // that. isResponding is already false by this point, so none of
      // this delays the message itself appearing "sent" to the user.
      await get().refreshConversations()

      // refreshConversations() does a full replace (set({ conversations })),
      // not a merge — if that response ever comes back without the
      // conversation just sent (a delayed/incomplete/wrong response, for
      // any reason, including ones no test here can reproduce), it would
      // silently erase the optimistic entry above instead of just failing
      // to improve on it. This is the actual guarantee: whatever the
      // reconciliation step returns, the just-sent conversation can never
      // regress to "not there" as a result of it.
      if (conversationId && !get().conversations.some((c) => c.conversation_id === conversationId)) {
        set((state) => ({
          conversations: upsertConversationOptimistically(state.conversations, conversationId!, sessionId, text),
        }))
      }
    } catch {
      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}`
            ? { ...m, pending: false, content: 'Something went wrong. Please try again.' }
            : m,
        ),
      }))
    }
  },
}))
