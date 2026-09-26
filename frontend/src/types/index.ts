export type Role = 'user' | 'assistant'

export interface ItineraryDay {
  day: number
  title: string
  items?: string[]
}

export interface KBSource {
  title: string
  document_name: string
  chunk_index?: number | null
  score?: number | null
  snippet: string
}

export interface ChatResponse {
  destination?: string | null
  days?: number | null
  budget_lkr?: number | null
  summary: string
  itinerary?: ItineraryDay[]
  follow_up_question?: string | null
  conversation_id?: string | null
  kb_sources?: KBSource[]
}

export interface Message {
  id: string
  role: Role
  content: string
  createdAt: number
  pending?: boolean
  // True while an assistant message's text is still arriving token-by-token
  // (see useAppStore.ts's send() and lib/api.ts's streamPost) — distinct
  // from `pending`, which covers the earlier window before the first token
  // has arrived at all. Never true at the same time as `plan` being set:
  // `plan` only appears once the full reply (and therefore streaming) is done.
  streaming?: boolean
  // Set once, permanently, when the user clicked Stop mid-generation (see
  // useAppStore.ts's stopGenerating/send()). `content` is left exactly as
  // it was at that moment — whatever text had already streamed in, or none
  // at all — and `plan` never gets set for this message, since the reply
  // was never completed or saved. ChatThread.tsx uses this only to show a
  // small "Generation stopped" label alongside that leftover content.
  stopped?: boolean
  plan?: ChatResponse
}

export interface RecentChat {
  id: string
  title: string
  timestamp: string
}

export interface ChatHistoryItem {
  id: number
  user_message: string
  assistant_reply: ChatResponse
  created_at: string
}

export interface ChatHistoryResponse {
  conversation_id: string
  messages: ChatHistoryItem[]
}

export interface Conversation {
  conversation_id: string
  session_id: string
  title: string | null
  created_at: string
  updated_at: string
}

export interface ConversationListResponse {
  conversations: Conversation[]
}

export type NavIcon = 'home' | 'trips' | 'saved' | 'settings'

export interface NavItem {
  id: string
  label: string
  icon: NavIcon
  badge?: number
}
