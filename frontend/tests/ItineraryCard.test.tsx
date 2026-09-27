// Tests for ItineraryCard's "Tools used" chips (see src/components/
// ItineraryCard.tsx's TOOL_META) — proving they show up when
// plan.tools_used is populated, stay hidden when it isn't, and that
// rendering the rest of the card (summary, itinerary, kb_sources,
// follow_up_question) is unaffected either way.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { ItineraryCard } from '../src/components/ItineraryCard'
import type { ChatResponse } from '../src/types'

afterEach(() => {
  cleanup()
})

const basePlan: ChatResponse = {
  destination: 'Kandy',
  days: 3,
  budget_lkr: 45000,
  summary: 'A relaxed 3 day trip to Kandy.',
  itinerary: [{ day: 1, title: 'Arrival', items: ['Check in', 'Temple of the Sacred Tooth Relic'] }],
  follow_up_question: 'Would you like hotel recommendations too?',
  kb_sources: [],
}

describe('ItineraryCard — Tools used chips', () => {
  it('shows a "Tools used" section with a labeled, icon chip for each tool when tools_used is populated', () => {
    const plan: ChatResponse = {
      ...basePlan,
      tools_used: ['get_weather', 'calculate_budget', 'search_places', 'generate_itinerary'],
    }

    render(<ItineraryCard plan={plan} fallback={plan.summary} />)

    expect(screen.getByText('Tools used')).toBeInTheDocument()
    expect(screen.getByText('Weather')).toBeInTheDocument()
    expect(screen.getByText('Budget')).toBeInTheDocument()
    expect(screen.getByText('Places')).toBeInTheDocument()
    expect(screen.getByText('Itinerary')).toBeInTheDocument()
  })

  it('falls back to the raw tool name for a tool it does not have a label/icon mapping for', () => {
    const plan: ChatResponse = { ...basePlan, tools_used: ['some_future_tool'] }

    render(<ItineraryCard plan={plan} fallback={plan.summary} />)

    expect(screen.getByText('Tools used')).toBeInTheDocument()
    expect(screen.getByText('some_future_tool')).toBeInTheDocument()
  })

  it('hides the "Tools used" section entirely when tools_used is an empty array', () => {
    const plan: ChatResponse = { ...basePlan, tools_used: [] }

    render(<ItineraryCard plan={plan} fallback={plan.summary} />)

    expect(screen.queryByText('Tools used')).not.toBeInTheDocument()
  })

  it('hides the "Tools used" section when tools_used is not present at all (older saved history)', () => {
    // basePlan itself has no `tools_used` key at all — mirrors a message
    // saved before this field existed, whose stored assistant_reply JSON
    // simply won't have it.
    render(<ItineraryCard plan={basePlan} fallback={basePlan.summary} />)

    expect(screen.queryByText('Tools used')).not.toBeInTheDocument()
  })

  it('still renders the rest of the card normally alongside the tools-used chips', () => {
    const plan: ChatResponse = {
      ...basePlan,
      tools_used: ['calculate_budget'],
      kb_sources: [
        {
          title: 'Sri Lanka Guide',
          document_name: 'guide.pdf',
          chunk_index: 2,
          score: 0.87,
          snippet: 'Kandy is home to the Temple of the Sacred Tooth Relic.',
        },
      ],
    }

    render(<ItineraryCard plan={plan} fallback={plan.summary} />)

    // Existing sections (unaffected by the new one being added).
    expect(screen.getByText('Trip overview')).toBeInTheDocument()
    expect(screen.getByText('Kandy')).toBeInTheDocument()
    expect(screen.getByText('A relaxed 3 day trip to Kandy.')).toBeInTheDocument()
    expect(screen.getByText('Day 1')).toBeInTheDocument()
    expect(screen.getByText('Would you like hotel recommendations too?')).toBeInTheDocument()
    expect(screen.getByText('Travel knowledge used')).toBeInTheDocument()

    // The new section, alongside them.
    expect(screen.getByText('Tools used')).toBeInTheDocument()
    expect(screen.getByText('Budget')).toBeInTheDocument()
  })

  it('renders normally (no crash, no stray "Tools used" section) for a plain reply with no plan-level extras at all', () => {
    const minimalPlan: ChatResponse = { summary: 'Hi! How can I help you plan your trip?' }

    render(<ItineraryCard plan={minimalPlan} fallback={minimalPlan.summary} />)

    expect(screen.getByText('Hi! How can I help you plan your trip?')).toBeInTheDocument()
    expect(screen.queryByText('Tools used')).not.toBeInTheDocument()
    expect(screen.queryByText('Travel knowledge used')).not.toBeInTheDocument()
  })
})
