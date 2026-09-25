import React, { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useNavigate } from 'react-router-dom'
import {
  HomeIcon,
  PlaneIcon,
  BookmarkIcon,
  SettingsIcon,
  MessageSquareIcon,
  MoreVerticalIcon,
  LogOutIcon,
  XIcon,
} from 'lucide-react'
import voyaLogo from '../assets/voya-logo.jpg'
import { navItems } from '../data/navigation'
import { useAppStore } from '../hooks/useAppStore'
import { useAuthStore } from '../hooks/useAuthStore'
import type { Conversation, NavItem, RecentChat } from '../types'

const CONVERSATION_TITLE_FALLBACK = 'New conversation'

function toRecentChats(conversations: Conversation[]): RecentChat[] {
  return conversations.map((c) => ({
    id: c.conversation_id,
    title: c.title?.trim() ? c.title : CONVERSATION_TITLE_FALLBACK,
    timestamp: new Date(c.updated_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }))
}

const icons: Record<NavItem['icon'], React.ElementType> = {
  home: HomeIcon,
  trips: PlaneIcon,
  saved: BookmarkIcon,
  settings: SettingsIcon,
}

function NavButton({ item }: { item: NavItem }) {
  const activeNav = useAppStore((s) => s.activeNav)
  const setActiveNav = useAppStore((s) => s.setActiveNav)
  const Icon = icons[item.icon]
  const active = activeNav === item.id

  return (
    <li>
      <button
        type="button"
        onClick={() => setActiveNav(item.id)}
        aria-current={active ? 'page' : undefined}
        className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-[14px] transition-colors duration-150 ${
          active
            ? 'font-semibold text-ink'
            : 'font-medium text-muted hover:bg-canvas hover:text-ink'
        }`}
      >
        {active && (
          <motion.span
            layoutId="nav-active"
            className="absolute inset-0 -z-10 rounded-xl bg-accent-soft"
            transition={{ type: 'spring', stiffness: 420, damping: 34 }}
          />
        )}
        <Icon className="h-[18px] w-[18px] shrink-0" strokeWidth={1.9} />
        <span className="flex-1 truncate text-left">{item.label}</span>
        {!!item.badge && (
          <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-ink px-1.5 text-[11px] font-semibold text-canvas">
            {item.badge}
          </span>
        )}
      </button>
    </li>
  )
}

function SidebarContent({ onClose }: { onClose?: () => void }) {
  const conversations = useAppStore((s) => s.conversations)
  const recentChats = toRecentChats(conversations)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout()
    navigate('/login')
  }

  return (
    <div className="grid h-full w-full grid-cols-1 grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden bg-surface">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 pb-3 pt-5">
        <img src={voyaLogo} alt="Voya AI" className="h-11 w-11 shrink-0 rounded-xl object-cover" />
        <span className="font-display text-[17px] font-bold tracking-tight text-ink">Voya AI</span>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="ml-auto rounded-lg p-1.5 text-muted transition-colors duration-150 hover:bg-canvas hover:text-ink"
          >
            <XIcon className="h-5 w-5" />
          </button>
        )}
      </div>

      {/* Main nav */}
      <nav aria-label="Main" className="px-4 pt-1">
        <ul className="space-y-0.5">
          {navItems.map((item) => (
            <NavButton key={item.id} item={item} />
          ))}
        </ul>
      </nav>

      {/* Recent chats - scroll area */}
      <div className="flex min-h-0 flex-col overflow-hidden px-4 pt-5">
        <div className="flex items-center justify-between px-3 pb-2">
          <h2 className="text-[15px] font-semibold text-ink">Recent Chats</h2>
          <button
            type="button"
            className="text-sm font-medium text-ink transition-opacity duration-150 hover:opacity-70"
          >
            View all
          </button>
        </div>

        {recentChats.length > 0 ? (
          <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-4 pr-1">
            {recentChats.map((chat) => (
              <li key={chat.id}>
                <RecentChatButton chatId={chat.id} title={chat.title} timestamp={chat.timestamp} />
              </li>
            ))}
          </ul>
        ) : (
          <p className="px-3 pb-4 text-sm text-faint">No conversations yet.</p>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-line px-4 py-4">
        <AccountMenu />

        <button
          type="button"
          onClick={handleLogout}
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-full border border-accent/40 bg-accent-soft px-4 py-2.5 text-sm font-semibold text-ink transition-colors duration-150 hover:bg-accent/15"
        >
          <LogOutIcon className="h-4 w-4" strokeWidth={2.2} />
          Log out
        </button>
      </div>
    </div>
  )
}

function AccountMenu() {
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)

  const handleLogout = async () => {
    setOpen(false)
    await logout()
    navigate('/login')
  }

  return (
    <div className="relative flex items-center gap-3">
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-ink">
        {(user?.name ?? '?').charAt(0).toUpperCase()}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-semibold text-ink">{user?.name ?? 'Account'}</p>
        <p className="truncate text-xs text-faint">{user?.email ?? ''}</p>
      </div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Account options"
        aria-expanded={open}
        className="rounded-lg p-1.5 text-faint transition-colors duration-150 hover:bg-canvas hover:text-ink"
      >
        <MoreVerticalIcon className="h-4 w-4" />
      </button>

      {open && (
        <>
          <button
            type="button"
            aria-label="Close account menu"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-10 cursor-default"
          />
          <div className="absolute bottom-full right-0 z-20 mb-2 w-40 overflow-hidden rounded-xl border border-line bg-surface shadow-lift">
            <button
              type="button"
              onClick={handleLogout}
              className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-sm font-medium text-ink transition-colors duration-150 hover:bg-canvas"
            >
              <LogOutIcon className="h-4 w-4" strokeWidth={2} />
              Log out
            </button>
          </div>
        </>
      )}
    </div>
  )
}

function RecentChatButton({
  chatId,
  title,
  timestamp,
}: {
  chatId: string
  title: string
  timestamp: string
}) {
  const activeConversationId = useAppStore((s) => s.activeConversationId)
  const selectConversation = useAppStore((s) => s.selectConversation)
  const active = activeConversationId === chatId

  return (
    <button
      type="button"
      onClick={() => selectConversation(chatId)}
      aria-current={active ? 'true' : undefined}
      className={`group relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors duration-150 ${
        active ? '' : 'hover:bg-canvas'
      }`}
    >
      {active && (
        <motion.span
          layoutId="recent-chat-active"
          className="absolute inset-0 -z-10 rounded-lg border-l-2 border-l-ink bg-ink/6"
          transition={{ type: 'spring', stiffness: 420, damping: 34 }}
        />
      )}
      <MessageSquareIcon
        className={`h-4 w-4 shrink-0 transition-colors duration-150 ${
          active ? 'text-ink' : 'text-faint'
        }`}
        strokeWidth={1.8}
      />
      <span
        className={`min-w-0 flex-1 truncate text-[14px] transition-colors duration-150 ${
          active ? 'font-semibold text-ink' : 'text-muted group-hover:text-ink'
        }`}
      >
        {title}
      </span>
      <span className="shrink-0 text-[11.5px] text-faint">{timestamp}</span>
    </button>
  )
}

export function Sidebar() {
  const open = useAppStore((s) => s.sidebarOpen)
  const setOpen = useAppStore((s) => s.setSidebarOpen)

  return (
    <>
      <aside className="hidden h-full w-[280px] shrink-0 overflow-hidden border-r border-line lg:block">
        <SidebarContent />
      </aside>

      <AnimatePresence>
        {open && (
          <div className="fixed inset-0 z-50 lg:hidden">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
              onClick={() => setOpen(false)}
              className="absolute inset-0 bg-slate-950/40"
            />
            <motion.div
              role="dialog"
              aria-label="Navigation"
              initial={{ x: -300 }}
              animate={{ x: 0 }}
              exit={{ x: -300 }}
              transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
              className="absolute inset-y-0 left-0 h-full w-[280px] max-w-[85vw] overflow-hidden border-r border-line shadow-lift"
            >
              <SidebarContent onClose={() => setOpen(false)} />
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  )
}
