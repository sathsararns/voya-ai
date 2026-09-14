import { useRef, useState } from 'react'
import { motion } from 'framer-motion'
import {
  SparklesIcon,
  SendIcon,
  MapPinIcon,
  DollarSignIcon,
  BriefcaseIcon,
  Loader2Icon,
} from 'lucide-react'
import { quickActions } from '../data/suggestions'
import { useAppStore } from '../hooks/useAppStore'

const actionIcons = [SparklesIcon, MapPinIcon, DollarSignIcon, BriefcaseIcon]

export function Composer({ compact = false }: { compact?: boolean }) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const send = useAppStore((s) => s.send)
  const isResponding = useAppStore((s) => s.isResponding)

  const submit = () => {
    if (!value.trim() || isResponding) return
    send(value)
    setValue('')
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
      className="rounded-2xl border border-line bg-surface p-4 shadow-card transition-colors duration-150 focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <SparklesIcon className="mt-1 h-5 w-5 shrink-0 text-accent" strokeWidth={2} />
        <label className="sr-only" htmlFor="composer">
          Message Voya AI
        </label>
        <textarea
          id="composer"
          ref={inputRef}
          rows={compact ? 1 : 2}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          placeholder="Initiate a query or send a command to the AI..."
          className="w-full resize-none bg-transparent text-[16px] leading-6 text-ink outline-none placeholder:text-faint"
        />
      </div>

      <div className="mt-4 flex items-end gap-3">
        <div className="flex min-w-0 flex-1 flex-wrap gap-2">
          {quickActions.map((action, i) => {
            const Icon = actionIcons[i]
            return (
              <button
                key={action.id}
                type="button"
                onClick={() => setValue(action.prompt)}
                className="flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-[14px] font-medium text-muted transition-colors duration-150 hover:border-accent/40 hover:text-ink"
              >
                <Icon className="h-4 w-4 text-accent" strokeWidth={1.9} />
                {action.label}
              </button>
            )
          })}
        </div>

        <motion.button
          type="submit"
          whileTap={{ scale: 0.94 }}
          transition={{ duration: 0.12 }}
          disabled={!value.trim() || isResponding}
          aria-label="Send message"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent text-white transition-opacity duration-150 hover:opacity-90 disabled:opacity-40"
        >
          {isResponding ? (
            <Loader2Icon className="h-5 w-5 animate-spin" />
          ) : (
            <SendIcon className="h-[18px] w-[18px]" strokeWidth={2} />
          )}
        </motion.button>
      </div>
    </form>
  )
}
