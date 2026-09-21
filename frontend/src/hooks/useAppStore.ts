import { create } from 'zustand'
import type { ChatResponse, ItineraryDay, Message } from '../types'

type Theme = 'light' | 'dark'

interface AppState {
  theme: Theme
  sidebarOpen: boolean
  activeNav: string
  activeChatId: string | null
  messages: Message[]
  isResponding: boolean
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
  setSidebarOpen: (open: boolean) => void
  setActiveNav: (id: string) => void
  openChat: (id: string, title: string) => Promise<void>
  newChat: () => void
  send: (content: string) => Promise<void>
}

const API_BASE = 'http://127.0.0.1:8000'

const SESSION_STORAGE_KEY = 'voya_session_id'
let cachedSessionId: string | null = null

function createSessionId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `sess-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
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

  const id = createSessionId()
  cachedSessionId = id

  try {
    window.localStorage.setItem(SESSION_STORAGE_KEY, id)
  } catch {
    // best effort only
  }

  return id
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

export const useAppStore = create<AppState>((set, get) => ({
  theme: 'dark',
  sidebarOpen: false,
  activeNav: 'home',
  activeChatId: null,
  messages: [],
  isResponding: false,

  setTheme: (theme) => set({ theme }),
  toggleTheme: () => set({ theme: get().theme === 'light' ? 'dark' : 'light' }),
  setSidebarOpen: (sidebarOpen) => set({ sidebarOpen }),
  setActiveNav: (activeNav) => set({ activeNav, sidebarOpen: false }),

  openChat: async (id, title) => {
    set({
      activeChatId: id,
      sidebarOpen: false,
      messages: [],
      isResponding: false,
      activeNav: 'home',
    })

    await get().send(title)
  },

  newChat: () => {
    set({
      messages: [],
      activeChatId: null,
      isResponding: false,
      sidebarOpen: false,
      activeNav: 'home',
    })
  },

  send: async (content) => {
    const text = content.trim()
    if (!text || get().isResponding) return

    const stamp = Date.now()

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
        body: JSON.stringify({ message: text, session_id: getOrCreateSessionId() }),
      })

      if (!response.ok) {
        throw new Error(`Backend error: ${response.status}`)
      }

      const data: ChatResponse = await response.json()
      const assistantText = formatAssistantReply(data)

      set((state) => ({
        isResponding: false,
        messages: state.messages.map((m) =>
          m.id === `a-${stamp}` ? { ...m, pending: false, content: assistantText, plan: data } : m,
        ),
      }))
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