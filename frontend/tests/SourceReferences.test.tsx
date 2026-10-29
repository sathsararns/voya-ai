import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { SourceReferences } from '../src/components/SourceReferences'
import type { KBSource } from '../src/types'

afterEach(cleanup)

const guide: KBSource = {
  title: 'Guide-Book-Sri-Lanka.pdf', document_name: 'Guide-Book-Sri-Lanka.pdf',
  chunk_index: 35, score: 0.33, snippet: 'Galle Fort is a cultural attraction on the southern coast.',
}

describe('SourceReferences', () => {
  it('groups the four retrieved passages from the screenshot into one named document', () => {
    const sources = [35, 43, 9, 42].map((chunk_index) => ({
      ...guide, chunk_index, snippet: `Reference passage ${chunk_index}.`,
    }))
    const { container } = render(<SourceReferences sources={sources} />)

    expect(screen.getByText('Sri Lanka Travel Guide')).toBeInTheDocument()
    expect(screen.getAllByRole('listitem')).toHaveLength(1)
    expect(container.textContent).not.toContain('Guide-Book-Sri-Lanka.pdf')
    expect(container.textContent).not.toContain('chunk')
    expect(container.textContent).not.toContain('% match')
  })

  it('shows a static source label without dropdown controls or excerpts', () => {
    const { container } = render(<SourceReferences sources={[guide]} />)

    expect(screen.getByText('Sri Lanka Travel Guide')).toBeInTheDocument()
    expect(container.querySelector('details, summary, button')).toBeNull()
    expect(screen.queryByText(guide.snippet)).not.toBeInTheDocument()
  })

  it('lists each document once without merging different documents', () => {
    render(<SourceReferences sources={[
      guide, { ...guide, chunk_index: 43, snippet: guide.snippet.replaceAll(' ', '  ') },
      { ...guide, title: 'Train-Timetable.pdf', document_name: 'Train-Timetable.pdf', snippet: 'Train reference.' },
    ]} />)

    expect(screen.getAllByRole('listitem')).toHaveLength(2)
    expect(screen.getByText('Sri Lanka Train Timetable')).toBeInTheDocument()
  })

  it('preserves a descriptive title for other documents and handles older sources without excerpts', () => {
    render(<SourceReferences sources={[
      { title: 'Sri Lanka Visa Guide', document_name: 'visa.pdf', snippet: '' },
      { title: 'food_preferences.md', document_name: 'food_preferences.md', snippet: '' },
    ]} />)

    expect(screen.getByText('Sri Lanka Visa Guide')).toBeInTheDocument()
    expect(screen.getByText('food preferences')).toBeInTheDocument()
    expect(screen.queryByText('chunk')).not.toBeInTheDocument()
  })

  it('renders no source section when there are no references', () => {
    const { container } = render(<SourceReferences sources={[]} />)
    expect(container).toBeEmptyDOMElement()
  })
})
