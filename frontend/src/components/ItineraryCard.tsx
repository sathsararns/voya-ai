import { CalendarIcon, MapPinIcon, MessageCircleQuestionIcon, WalletIcon } from 'lucide-react'
import type { ChatResponse } from '../types'

export function ItineraryCard({ plan, fallback }: { plan: ChatResponse; fallback: string }) {
  const hasMeta = Boolean(plan.destination || typeof plan.days === 'number' || typeof plan.budget_lkr === 'number')
  const days = Array.isArray(plan.itinerary) ? plan.itinerary : []

  return (
    <div className="space-y-4">
      {hasMeta && (
        <div className="flex flex-wrap gap-2">
          {plan.destination && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-accent-soft px-3 py-1 text-[13px] font-semibold text-accent">
              <MapPinIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
              {plan.destination}
            </span>
          )}
          {typeof plan.days === 'number' && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-teal-soft px-3 py-1 text-[13px] font-semibold text-teal">
              <CalendarIcon className="h-3.5 w-3.5" strokeWidth={2.2} />
              {plan.days} {plan.days === 1 ? 'day' : 'days'}
            </span>
          )}
          {typeof plan.budget_lkr === 'number' && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[13px] font-semibold text-ink">
              <WalletIcon className="h-3.5 w-3.5 text-accent" strokeWidth={2.2} />
              {plan.budget_lkr.toLocaleString()} LKR
            </span>
          )}
        </div>
      )}

      <p className="whitespace-pre-wrap text-[15px] leading-6 text-ink">{plan.summary || fallback}</p>

      {days.length > 0 && (
        <ol className="space-y-2.5">
          {days.map((day) => (
            <li key={day.day} className="rounded-xl border border-line bg-canvas/70 p-3.5">
              <p className="text-[12px] font-bold uppercase tracking-wider text-accent">Day {day.day}</p>
              <p className="mt-0.5 text-[14.5px] font-semibold text-ink">{day.title}</p>
              {Array.isArray(day.items) && day.items.length > 0 && (
                <ul className="mt-2 space-y-1.5">
                  {day.items.map((item, i) => (
                    <li key={i} className="flex gap-2 text-[14px] leading-5 text-muted">
                      <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-faint" />
                      {item}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}

      {plan.follow_up_question && (
        <p className="flex items-start gap-2 rounded-xl bg-teal-soft px-3.5 py-2.5 text-[14px] font-medium text-teal">
          <MessageCircleQuestionIcon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2.2} />
          {plan.follow_up_question}
        </p>
      )}
    </div>
  )
}
