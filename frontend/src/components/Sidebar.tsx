import React from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  HomeIcon,
  PlaneIcon,
  BookmarkIcon,
  SettingsIcon,
  SparklesIcon,
  CompassIcon,
  MessageSquareIcon,
  MoreVerticalIcon,
  XIcon,
} from 'lucide-react'
import { navItems } from '../data/navigation'
import { recentChats } from '../data/chats'
import { currentUser } from '../data/user'
import { useAppStore } from '../hooks/useAppStore'
import type { NavItem } from '../types'

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
        className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors duration-150 ${
          active
            ? 'font-semibold text-accent'
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
  return (
    <div className="grid h-full w-full grid-cols-1 grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden bg-surface">
      {/* Logo */}
      <div className="flex items-center gap-2.5 px-5 pb-3 pt-5">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent-soft text-accent">
          <CompassIcon className="h-5 w-5" strokeWidth={2.2} />
        </span>
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
            className="text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-70"
          >
            View all
          </button>
        </div>

        <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto pb-4 pr-1">
          {recentChats.map((chat) => (
            <li key={chat.id}>
              <RecentChatButton chatId={chat.id} title={chat.title} timestamp={chat.timestamp} />
            </li>
          ))}
        </ul>
      </div>

      {/* Footer */}
      <div className="border-t border-line px-4 py-4">
        <div className="flex items-center gap-3">
          <img
            src={currentUser.avatar}
            alt=""
            className="h-9 w-9 shrink-0 rounded-full object-cover"
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-ink">{currentUser.name}</p>
            <p className="truncate text-xs text-faint">{currentUser.email}</p>
          </div>
          <button
            type="button"
            aria-label="Account options"
            className="rounded-lg p-1.5 text-faint transition-colors duration-150 hover:bg-canvas hover:text-ink"
          >
            <MoreVerticalIcon className="h-4 w-4" />
          </button>
        </div>

        <button
          type="button"
          className="mt-3 flex w-full items-center justify-center gap-2 rounded-full border border-accent/40 bg-accent-soft px-4 py-2.5 text-sm font-semibold text-accent transition-colors duration-150 hover:bg-accent/15"
        >
          <SparklesIcon className="h-4 w-4" strokeWidth={2.2} />
          Upgrade to Pro
        </button>
      </div>
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
  const activeChatId = useAppStore((s) => s.activeChatId)
  const openChat = useAppStore((s) => s.openChat)

  return (
    <button
      type="button"
      onClick={() => openChat(chatId, title)}
      className={`group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors duration-150 hover:bg-canvas ${
        activeChatId === chatId ? 'bg-canvas' : ''
      }`}
    >
      <MessageSquareIcon className="h-4 w-4 shrink-0 text-faint" strokeWidth={1.8} />
      <span className="min-w-0 flex-1 truncate text-[14px] text-muted group-hover:text-ink">
        {title}
      </span>
      <span className="shrink-0 text-[11px] text-faint">{timestamp}</span>
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
