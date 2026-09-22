import type { ReactNode } from 'react'
import { BookOpenIcon, CalendarIcon, MapPinIcon, MessageCircleQuestionIcon, WalletIcon } from 'lucide-react'
import type { ChatResponse } from '../types'

export function ItineraryCard({ plan, fallback }: { plan: ChatResponse; fallback: string }) {
  const days = Array.isArray(plan.itinerary) ? plan.itinerary : []

  // Built as a list (rather than adjacent JSX chips) so a real separator can
  // be placed *between* entries. CSS `gap` alone only creates visual space —
  // it inserts no actual character between sibling elements, so selecting or
  // copying the rendered text (or anything else reading plain text instead
  // of pixels) collapses them together, e.g. "Kandy5 days60,000 LKR".
  const metaChips: Array<{ key: string; icon: ReactNode; label: string }> = []

  if (plan.destination) {
    metaChips.push({
      key: 'destination',
      icon: <MapPinIcon className="h-3.5 w-3.5 text-ink" strokeWidth={2.2} />,
      label: plan.destination,
    })
  }
  if (typeof plan.days === 'number') {
    metaChips.push({
      key: 'days',
      icon: <CalendarIcon className="h-3.5 w-3.5 text-ink" strokeWidth={2.2} />,
      label: `${plan.days} ${plan.days === 1 ? 'day' : 'days'}`,
    })
  }
  if (typeof plan.budget_lkr === 'number') {
    metaChips.push({
      key: 'budget',
      icon: <WalletIcon className="h-3.5 w-3.5 text-ink" strokeWidth={2.2} />,
      label: `${plan.budget_lkr.toLocaleString()} LKR`,
    })
  }

  return (
    <div className="space-y-4">
      {metaChips.length > 0 && (
        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
            Trip overview
          </p>
          <div className="flex flex-wrap items-center gap-2.5">
            {metaChips.map((chip, index) => (
              <span key={chip.key} className="inline-flex items-center gap-2.5">
                <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-[12.5px] font-semibold text-ink">
                  {chip.icon}
                  {chip.label}
                </span>
                {index < metaChips.length - 1 && (
                  <span aria-hidden="true" className="text-faint">
                    ·
                  </span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      <p className="whitespace-pre-wrap text-[15px] leading-6 text-ink">{plan.summary || fallback}</p>

      {days.length > 0 && (
        <ol className="space-y-2.5">
          {days.map((day) => (
            <li key={day.day} className="rounded-xl border border-line bg-canvas/70 p-3.5">
              <p className="text-[12px] font-bold uppercase tracking-wider text-ink">Day {day.day}</p>
              <p className="mt-0.5 text-[14px] font-semibold text-ink">{day.title}</p>
              {Array.isArray(day.items) && day.items.length > 0 && (
                <ul className="mt-2 space-y-1.5">
                  {day.items.map((item, i) => (
                    <li key={i} className="flex gap-2 text-[13.5px] leading-5 text-muted">
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
        <p className="flex items-start gap-2 rounded-xl bg-teal-soft px-3.5 py-2.5 text-[13.5px] font-medium text-teal">
          <MessageCircleQuestionIcon className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={2.2} />
          {plan.follow_up_question}
        </p>
      )}

      {plan.kb_sources && plan.kb_sources.length > 0 && (
        <div className="border-t border-line pt-3.5">
          <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-faint">
            <BookOpenIcon className="h-3 w-3" strokeWidth={2.2} />
            Travel knowledge used
          </p>
          <ul className="space-y-1">
            {plan.kb_sources.map((source, i) => (
              <li
                key={`${source.document_name}-${source.chunk_index ?? i}`}
                title={source.snippet}
                className="truncate text-[12.5px] text-muted"
              >
                <span className="font-medium text-ink">{source.title}</span>
                {typeof source.chunk_index === 'number' && (
                  <span className="text-faint"> · chunk {source.chunk_index}</span>
                )}
                {typeof source.score === 'number' && (
                  <span className="text-faint"> · {Math.round(source.score * 100)}% match</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
