import { useEffect, useRef } from 'react'
import { motion } from 'framer-motion'
import voyaLogo from '../assets/voya-logo.jpg'
import { currentUser } from '../data/user'
import { useAppStore } from '../hooks/useAppStore'
import { ItineraryCard } from './ItineraryCard'

function TypingDots() {
  return (
    <span className="flex items-center gap-1.5 py-2" role="status" aria-label="Voya AI is typing">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-2 w-2 rounded-full bg-faint"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{ duration: 1, repeat: Infinity, delay: i * 0.15, ease: 'linear' }}
        />
      ))}
    </span>
  )
}

export function ChatThread() {
  const messages = useAppStore((s) => s.messages)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  return (
    <div className="space-y-6" role="log" aria-live="polite">
      {messages.map((message) =>
        message.role === 'user' ? (
          <motion.div
            key={message.id}
            id={message.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            className="flex justify-end gap-3"
          >
            <p className="max-w-[80%] whitespace-pre-wrap rounded-2xl rounded-tr-md bg-accent px-4 py-3 text-[15px] leading-6 text-zinc-900">
              {message.content}
            </p>
            <img src={currentUser.avatar} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" />
          </motion.div>
        ) : (
          <motion.div
            key={message.id}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.22, ease: [0.23, 1, 0.32, 1] }}
            className="flex gap-3"
          >
            <img src={voyaLogo} alt="Voya AI" className="h-8 w-8 shrink-0 rounded-full object-cover" />
            <div className="max-w-[80%] rounded-2xl rounded-tl-md border border-line bg-surface px-4 py-3.5">
              {message.pending ? (
                <TypingDots />
              ) : message.plan ? (
                <ItineraryCard plan={message.plan} fallback={message.content} />
              ) : (
                <p className="whitespace-pre-wrap text-[15px] leading-6 text-ink">{message.content}</p>
              )}
            </div>
          </motion.div>
        ),
      )}
      <div ref={endRef} />
    </div>
  )
}
