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
