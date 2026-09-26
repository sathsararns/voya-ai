import { useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { SendHorizontalIcon, SquareIcon } from 'lucide-react'
import voyaLogo from '../assets/voya-logo.jpg'
import { useAppStore } from '../hooks/useAppStore'

export function Composer({ compact = false }: { compact?: boolean }) {
  const [value, setValue] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const send = useAppStore((s) => s.send)
  const stopGenerating = useAppStore((s) => s.stopGenerating)
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
      className="rounded-2xl border border-line bg-surface/80 p-4 backdrop-blur-xl transition-all duration-200 focus-within:border-2 focus-within:border-ink/35 sm:p-5"
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
        {isResponding ? (
          // Stop button: replaces the send button entirely (same slot, so
          // this isn't a layout change) while a response is in flight,
          // whether it's still waiting for the first token or already
          // streaming — the request is cancellable either way.
          <motion.button
            type="button"
            onClick={stopGenerating}
            whileTap={{ scale: 0.94 }}
            transition={{ duration: 0.12 }}
            aria-label="Stop generating"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent text-zinc-900 transition-opacity duration-150 hover:opacity-90"
          >
            <SquareIcon className="h-4 w-4 fill-current" strokeWidth={2} />
          </motion.button>
        ) : (
          <motion.button
            type="submit"
            whileTap={{ scale: 0.94 }}
            transition={{ duration: 0.12 }}
            disabled={!value.trim()}
            aria-label="Send message"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent text-zinc-900 transition-opacity duration-150 hover:opacity-90 disabled:opacity-40"
          >
            <SendHorizontalIcon className="h-[18px] w-[18px]" strokeWidth={2} />
          </motion.button>
        )}
      </div>
    </form>
  )
}
