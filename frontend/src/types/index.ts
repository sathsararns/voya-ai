export type Role = 'user' | 'assistant'

export interface ItineraryDay {
  day: number
  title: string
  items?: string[]
}

export interface ChatResponse {
  destination?: string | null
  days?: number | null
  budget_lkr?: number | null
  summary: string
  itinerary?: ItineraryDay[]
  follow_up_question?: string | null
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

export type NavIcon = 'home' | 'trips' | 'saved' | 'settings'

export interface NavItem {
  id: string
  label: string
  icon: NavIcon
  badge?: number
}
