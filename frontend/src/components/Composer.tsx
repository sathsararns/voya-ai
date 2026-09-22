import { useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { SendHorizontalIcon, Loader2Icon } from 'lucide-react'
import voyaLogo from '../assets/voya-logo.jpg'
import { useAppStore } from '../hooks/useAppStore'

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
      className="rounded-2xl border border-line bg-surface/80 p-4 backdrop-blur-xl transition-colors duration-150 focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10 sm:p-5"
    >
      <div className="flex items-start gap-3">
        <img src={voyaLogo} alt="Voya AI" className="mt-1 h-5 w-5 shrink-0 rounded-md object-cover" />
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
          className="w-full resize-none bg-transparent text-[15px] leading-6 text-ink outline-none placeholder:text-faint"
        />
      </div>

      <div className="mt-4 flex items-end justify-end gap-3">
        <motion.button
          type="submit"
          whileTap={{ scale: 0.94 }}
          transition={{ duration: 0.12 }}
          disabled={!value.trim() || isResponding}
          aria-label="Send message"
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent text-zinc-900 transition-opacity duration-150 hover:opacity-90 disabled:opacity-40"
        >
          {isResponding ? (
            <Loader2Icon className="h-5 w-5 animate-spin" />
          ) : (
            <SendHorizontalIcon className="h-[18px] w-[18px]" strokeWidth={2} />
          )}
        </motion.button>
      </div>
    </form>
  )
}
