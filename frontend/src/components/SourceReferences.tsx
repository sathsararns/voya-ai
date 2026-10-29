import { BookOpenIcon } from 'lucide-react'
import type { KBSource } from '../types'

const DOCUMENT_TITLES: Record<string, string> = {
  'guide-book-sri-lanka.pdf': 'Sri Lanka Travel Guide',
  'train-timetable.pdf': 'Sri Lanka Train Timetable',
}

function sourceTitle(source: KBSource): string {
  const filename = source.document_name.trim().split(/[\\/]/).pop() ?? ''
  const knownTitle = DOCUMENT_TITLES[filename.toLowerCase()]
  if (knownTitle) return knownTitle
  const title = source.title.trim()
  return ((title && title.toLowerCase() !== 'source' ? title : filename)
    .replace(/\.(?:pdf|txt|md)$/i, '')
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()) || 'Travel reference'
}

export function SourceReferences({ sources }: { sources: KBSource[] }) {
  const documents = new Map<string, string>()
  for (const source of sources) {
    const title = sourceTitle(source)
    const key = (source.document_name.trim() || title).toLowerCase()
    if (!documents.has(key)) documents.set(key, title)
  }
  if (documents.size === 0) return null

  return (
    <section aria-label="Sources" className="border-t border-line pt-3.5">
      <h3 className="mb-2 text-[12px] font-semibold text-muted">Sources</h3>
      <ul className="space-y-2">
        {[...documents].map(([key, title]) => (
          <li key={key} className="flex items-center gap-2.5 rounded-xl border border-line bg-canvas/50 px-3 py-2.5 text-[13px] font-medium text-ink">
            <BookOpenIcon aria-hidden="true" className="h-4 w-4 shrink-0 text-muted" />
            <span className="min-w-0 break-words">{title}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
