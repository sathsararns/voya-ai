import { Composer } from '../components/Composer'
import { ChatThread } from '../components/ChatThread'
import { currentUser } from '../data/user'
import { useAppStore } from '../hooks/useAppStore'
import { AnimatePresence, motion } from 'framer-motion'

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good Morning'
  if (hour < 18) return 'Good Afternoon'
  return 'Good Evening'
}

export function Home() {
  const hasThread = useAppStore((s) => s.messages.length > 0)

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-1 flex-col px-4 pb-10 pt-6 sm:px-6 sm:pt-10">
      <AnimatePresence mode="wait">
        {hasThread ? (
          <motion.div
            key="thread"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            className="flex flex-1 flex-col"
          >
            <div className="flex-1 pb-6">
              <ChatThread />
            </div>
            <div className="sticky bottom-0 bg-canvas pb-1 pt-3">
              <Composer compact />
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="welcome"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, ease: [0.23, 1, 0.32, 1] }}
          >
            <div className="flex flex-col items-center text-center">
              <motion.div
                aria-hidden="true"
                initial={{ opacity: 0, scale: 0.96 }}
                animate={{ opacity: 1, scale: 1, y: [0, -6, 0] }}
                transition={{
                  opacity: { duration: 0.3, ease: [0.23, 1, 0.32, 1] },
                  scale: { duration: 0.3, ease: [0.23, 1, 0.32, 1] },
                  y: { duration: 5, repeat: Infinity, ease: 'easeInOut' },
                }}
                className="h-20 w-20 rounded-full shadow-lift"
                style={{
                  background:
                    'radial-gradient(circle at 32% 28%, #e9d5ff 0%, #a5b4fc 45%, #6366f1 100%)',
                }}
              />
              <h1 className="mt-7 text-3xl font-bold tracking-tight text-ink sm:text-[34px]">
                {greeting()}, {currentUser.firstName} 👋
              </h1>
              <p className="mt-1 text-3xl font-bold tracking-tight text-ink sm:text-[34px]">
                How Can I <span className="text-accent">Assist You Today?</span>
              </p>
              <p className="mt-4 max-w-xl text-[15px] leading-6 text-muted">
                I can help you plan trips, find places, create{' '}
                <span className="font-semibold text-ink">itineraries</span> and more.
              </p>
            </div>

            <div className="mt-9">
              <Composer />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  )
}