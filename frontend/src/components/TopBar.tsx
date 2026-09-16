import { motion } from 'framer-motion'
import { MenuIcon, MoonIcon, PlusIcon, SunIcon } from 'lucide-react'
import { currentUser } from '../data/user'
import { useAppStore } from '../hooks/useAppStore'

export function TopBar() {
  const theme = useAppStore((s) => s.theme)
  const toggleTheme = useAppStore((s) => s.toggleTheme)
  const newChat = useAppStore((s) => s.newChat)
  const setSidebarOpen = useAppStore((s) => s.setSidebarOpen)

  return (
    <header className="fixed inset-x-0 top-0 z-40 flex h-16 items-center gap-3 border-b border-line bg-canvas/85 px-4 backdrop-blur-md sm:px-6 lg:left-[280px]">
      <button
        type="button"
        onClick={() => setSidebarOpen(true)}
        aria-label="Open navigation"
        className="rounded-xl border border-line bg-surface p-2 text-muted transition-colors duration-150 hover:text-ink lg:hidden"
      >
        <MenuIcon className="h-5 w-5" />
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <motion.button
          type="button"
          onClick={newChat}
          whileTap={{ scale: 0.97 }}
          transition={{ duration: 0.12 }}
          className="flex items-center gap-2 rounded-full bg-ink px-3 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90 sm:px-5"
        >
          <PlusIcon className="h-4 w-4" strokeWidth={2.4} />
          <span className="hidden sm:inline">New Chat</span>
        </motion.button>

        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === 'light' ? 'Switch to dark mode' : 'Switch to light mode'}
          className="flex h-10 w-10 items-center justify-center rounded-full border border-line bg-surface text-muted transition-colors duration-150 hover:text-ink"
        >
          {theme === 'light' ? <SunIcon className="h-[18px] w-[18px]" /> : <MoonIcon className="h-[18px] w-[18px]" />}
        </button>

        <span className="relative shrink-0">
          <img
            src={currentUser.avatar}
            alt={currentUser.name}
            className="h-10 w-10 rounded-full object-cover"
          />
          <span className="absolute bottom-0 right-0 h-2.5 w-2.5 rounded-full border-2 border-canvas bg-teal" />
        </span>
      </div>
    </header>
  )
}
