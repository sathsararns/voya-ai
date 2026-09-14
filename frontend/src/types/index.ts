export type Role = 'user' | 'assistant'

export interface Message {
  id: string
  role: Role
  content: string
  createdAt: number
  pending?: boolean
}

export interface RecentChat {
  id: string
  title: string
  timestamp: string
}

export interface NavItem {
  id: string
  label: string
  icon: 'home' | 'history' | 'saved' | 'budget' | 'profile' | 'settings'
}
