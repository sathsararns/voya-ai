import { create } from 'zustand'
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
  newChat: () => Promise<void>
  send: (content: string) => Promise<void>
  initConversations: () => Promise<void>
  selectConversation: (conversationId: string) => Promise<void>
  refreshConversations: () => Promise<void>
}

const API_BASE = 'http://127.0.0.1:8000'

const SESSION_STORAGE_KEY = 'voya_session_id'
let cachedSessionId: string | null = null

function createId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `id-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

// Stable per-browser id, persisted so it survives reloads. Falls back to an
// in-memory id (and skips persistence) if localStorage is unavailable, e.g.
// private browsing — the session is still consistent for the current tab.
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

const ACTIVE_CONVERSATION_STORAGE_KEY = 'voya_active_conversation_id'

function getStoredConversationId(): string | null {
  try {
    return window.localStorage.getItem(ACTIVE_CONVERSATION_STORAGE_KEY)
  } catch {
    return null
  }
}

function setStoredConversationId(conversationId: string): void {
  try {
    window.localStorage.setItem(ACTIVE_CONVERSATION_STORAGE_KEY, conversationId)
  } catch {
    // best effort only
  }
}

async function apiCreateConversation(sessionId: string): Promise<Conversation> {
  const response = await fetch(`${API_BASE}/api/v1/chat/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  })
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`)
  }
  return response.json()
}

async function apiListConversations(sessionId: string): Promise<Conversation[]> {
  const response = await fetch(
    `${API_BASE}/api/v1/chat/conversations?session_id=${encodeURIComponent(sessionId)}`,
  )
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`)
  }
  const data: ConversationListResponse = await response.json()
  return data.conversations
}

async function apiGetConversationMessages(conversationId: string): Promise<ChatHistoryResponse> {
  const response = await fetch(`${API_BASE}/api/v1/chat/conversations/${conversationId}/messages`)
  if (!response.ok) {
    throw new Error(`Backend error: ${response.status}`)
  }
  return response.json()
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

  // Runs once on app start: loads this browser's conversation list, picks
  // (or creates) the active one, and loads its messages.
  initConversations: async () => {
    set({ isLoadingHistory: true, isLoadingConversations: true })
    const sessionId = getOrCreateSessionId()

    try {
      const conversations = await apiListConversations(sessionId)
      const storedId = getStoredConversationId()
      const active = conversations.find((c) => c.conversation_id === storedId) ?? conversations[0] ?? null

      set({ conversations, isLoadingConversations: false })

      if (active) {
        setStoredConversationId(active.conversation_id)
        const history = await apiGetConversationMessages(active.conversation_id)
        set({
          activeConversationId: active.conversation_id,
          messages: toMessages(history),
          isLoadingHistory: false,
        })
        return
      }

      // Brand new browser/session — nothing exists yet, start one conversation.
      const created = await apiCreateConversation(sessionId)
      setStoredConversationId(created.conversation_id)
      set({
        activeConversationId: created.conversation_id,
        conversations: [created],
        messages: [],
        isLoadingHistory: false,
      })
    } catch {
      // Backend unreachable — start with an empty, unsaved thread so the
      // composer still works locally.
      set({ isLoadingHistory: false, isLoadingConversations: false })
    }
  },

  refreshConversations: async () => {
    try {
      const conversations = await apiListConversations(getOrCreateSessionId())
      set({ conversations })
    } catch {
      // best effort only — sidebar just keeps its last known list
    }
  },

  selectConversation: async (conversationId) => {
    if (get().activeConversationId === conversationId) {
      set({ sidebarOpen: false })
      return
    }

    setStoredConversationId(conversationId)
    set({ activeConversationId: conversationId, sidebarOpen: false, isLoadingHistory: true })

    try {
      const history = await apiGetConversationMessages(conversationId)
      set({ messages: toMessages(history), isLoadingHistory: false })
    } catch {
      set({ messages: [], isLoadingHistory: false })
    }
  },

  newChat: async () => {
    const { activeConversationId, messages } = get()
    set({ isResponding: false, sidebarOpen: false, activeNav: 'home' })

    // Already on a fresh, empty conversation — nothing to create.
    if (activeConversationId && messages.length === 0) {
      return
    }

    try {
      const created = await apiCreateConversation(getOrCreateSessionId())
      setStoredConversationId(created.conversation_id)
      set((state) => ({
        activeConversationId: created.conversation_id,
        conversations: [created, ...state.conversations],
        messages: [],
      }))
    } catch {
      // Backend unreachable — clear the visible thread locally. The next
      // successful send() will get (or create) a conversation on its own.
      set({ messages: [], activeConversationId: null })
    }
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
      const response = await fetch(`${API_BASE}/api/v1/chat/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: text, session_id: sessionId, conversation_id: conversationId }),
      })

      if (!response.ok) {
        throw new Error(`Backend error: ${response.status}`)
      }

      const data: ChatResponse = await response.json()
      const assistantText = formatAssistantReply(data)

      // Covers the rare case where no conversation existed yet and the
      // backend created one on the fly.
      if (data.conversation_id && data.conversation_id !== conversationId) {
        conversationId = data.conversation_id
        setStoredConversationId(conversationId)
        set({ activeConversationId: conversationId })
      }

      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}` ? { ...m, pending: false, content: assistantText, plan: data } : m,
        ),
      }))

      // Title/ordering may have changed server-side (touch_conversation) —
      // refresh in the background so the sidebar reflects it.
      get().refreshConversations()
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
