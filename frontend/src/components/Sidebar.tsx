import React from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import {
  HomeIcon,
  ClockIcon,
  BookmarkIcon,
  WalletIcon,
  CircleUserIcon,
  SettingsIcon,
  SearchIcon,
  MessageSquareIcon,
  MoreVerticalIcon,
  Compass,
  XIcon,
} from 'lucide-react'
import { navItems } from '../data/navigation'
import { recentChats } from '../data/chats'
import { currentUser } from '../data/user'
import { useAppStore } from '../hooks/useAppStore'
import type { NavItem } from '../types'

const icons: Record<NavItem['icon'], React.ElementType> = {
  home: HomeIcon,
  history: ClockIcon,
  saved: BookmarkIcon,
  budget: WalletIcon,
  profile: CircleUserIcon,
  settings: SettingsIcon,
}

function SidebarContent({ onClose }: { onClose?: () => void }) {
  const activeNav = useAppStore((s) => s.activeNav)
  const setActiveNav = useAppStore((s) => s.setActiveNav)
  const activeChatId = useAppStore((s) => s.activeChatId)
  const openChat = useAppStore((s) => s.openChat)

  return (
    <div className="grid h-full w-full grid-rows-[auto_auto_auto_minmax(0,1fr)_auto] overflow-hidden bg-surface">
      {/* Header */}
      <div className="flex items-start justify-between px-6 pb-5 pt-6">
        <div className="flex items-center gap-3">
          <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <Compass className="h-5 w-5" strokeWidth={2.2} />
          </span>
          <div>
            <h1 className="text-lg font-bold leading-tight text-ink">Voya AI</h1>
            <p className="text-sm text-faint">Your Travel Assistant</p>
          </div>
        </div>

        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="rounded-lg p-1.5 text-muted transition-colors duration-150 hover:bg-canvas hover:text-ink lg:hidden"
          >
            <XIcon className="h-5 w-5" />
          </button>
        )}
      </div>

      {/* Search */}
      <div className="px-4">
        <label className="relative block">
          <span className="sr-only">Search</span>
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" />
          <input
            type="search"
            placeholder="Search..."
            className="h-11 w-full rounded-xl border border-line bg-surface pl-9 pr-12 text-sm text-ink outline-none transition-colors duration-150 placeholder:text-faint focus:border-accent focus:ring-2 focus:ring-accent/20"
          />
          <kbd className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[11px] font-medium text-faint">
            ⌘K
          </kbd>
        </label>
      </div>

      {/* Nav */}
      <nav aria-label="Main" className="mt-4 px-4">
        <ul className="space-y-0.5">
          {navItems.map((item) => {
            const Icon = icons[item.icon]
            const active = activeNav === item.id
            return (
              <li key={item.id}>
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
                  <span className="truncate">{item.label}</span>
                </button>
              </li>
            )
          })}
        </ul>
      </nav>

      {/* Recent chats - scroll area */}
      <div className="min-h-0 px-4 pt-6">
        <div className="flex items-center justify-between px-3 pb-2">
          <h2 className="text-[15px] font-semibold text-ink">Recent Chats</h2>
          <button
            type="button"
            className="text-sm font-medium text-accent transition-opacity duration-150 hover:opacity-70"
          >
            View all
          </button>
        </div>

        <ul className="h-full space-y-0.5 overflow-y-auto pb-4 pr-1">
          {recentChats.map((chat) => (
            <li key={chat.id}>
              <button
                type="button"
                onClick={() => openChat(chat.id, chat.title)}
                className={`group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left transition-colors duration-150 hover:bg-canvas ${
                  activeChatId === chat.id ? 'bg-canvas' : ''
                }`}
              >
                <MessageSquareIcon className="h-4 w-4 shrink-0 text-faint" strokeWidth={1.8} />
                <span className="min-w-0 flex-1 truncate text-[14px] text-muted group-hover:text-ink">
                  {chat.title}
                </span>
                <span className="shrink-0 text-[11px] text-faint">{chat.timestamp}</span>
              </button>
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
      </div>
    </div>
  )
}

export function Sidebar() {
  const open = useAppStore((s) => s.sidebarOpen)
  const setOpen = useAppStore((s) => s.setSidebarOpen)

  return (
    <>
      <aside className="sticky top-0 hidden h-screen w-[280px] shrink-0 overflow-hidden border-r border-line lg:block">
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