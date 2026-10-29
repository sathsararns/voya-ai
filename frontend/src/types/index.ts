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

export interface BudgetBreakdown {
  accommodation_lkr: number
  food_lkr: number
  transport_lkr: number
  activities_lkr: number
  misc_lkr: number
  total_lkr: number
  is_estimate: boolean
}

export interface ChatResponse {
  destination?: string | null
  days?: number | null
  budget_lkr?: number | null
  budget_cap_lkr?: number | null
  budget_breakdown?: BudgetBreakdown | null
  fee_notice?: string | null
  summary: string
  itinerary?: ItineraryDay[]
  follow_up_question?: string | null
  conversation_id?: string | null
  kb_sources?: KBSource[]
  // Names of backend tools (weather/places/budget/itinerary — see
  // backend/services/tools.py) the assistant actually called for this
  // reply, e.g. ["get_weather", "calculate_budget"]. The tool results
  // themselves are already woven into `summary`/`itinerary` server-side —
  // this is only the list of which ones ran, rendered as small "Tools
  // used" chips in ItineraryCard.tsx (see its TOOL_META map for the
  // label/icon per tool name).
  tools_used?: string[]
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
  // has arrived at all. Also true while the itinerary is being revealed.
  streaming?: boolean
  // Present only while typing a new plan's details after its summary.
  // Omitted for completed replies and loaded history, which render in full.
  planRevealCharacters?: number
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
