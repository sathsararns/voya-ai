// Tests for ItineraryCard's "Tools used" chips (see src/components/
// ItineraryCard.tsx's TOOL_META) — proving they show up when
// plan.tools_used is populated, stay hidden when it isn't, and that
// rendering the rest of the card (summary, itinerary, kb_sources,
// follow_up_question) is unaffected either way.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { ItineraryCard } from '../src/components/ItineraryCard'
import type { ChatResponse } from '../src/types'
import { getItineraryRevealText } from '../src/lib/itineraryReveal'

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
  it('reveals explicit assumptions before day cards without showing them all at once', () => {
    const plan: ChatResponse = {
      ...basePlan,
      assumptions: ['Starting point: Colombo (assumed; please confirm).', 'Travellers: 1 (assumed).'],
    }
    const { rerender } = render(
      <ItineraryCard plan={plan} fallback={plan.summary} revealCharacters={'Planning assumptions'.length + 8} />,
    )
    const assumptions = screen.getByRole('region', { name: 'Planning assumptions' })
    expect(within(assumptions).getByText('Starting')).toBeInTheDocument()
    expect(screen.queryByText('Day 1')).not.toBeInTheDocument()
    expect(screen.queryByText('Travellers: 1 (assumed).')).not.toBeInTheDocument()

    rerender(<ItineraryCard plan={plan} fallback={plan.summary} />)
    expect(screen.getByText(plan.assumptions![0])).toBeInTheDocument()
    expect(screen.getByText('Day 1')).toBeInTheDocument()
    expect(getItineraryRevealText(plan)).toContain('Planning assumptions' + plan.assumptions!.join(''))
  })

  it.each([
    ['user_provided', 'Based on your stated budget'],
    ['saved_preference', 'Based on your saved budget preference'],
    ['estimate', 'Suggested estimate'],
  ] as const)('shows the %s budget provenance with its explanation', (source, label) => {
    const plan: ChatResponse = {
      ...basePlan, budget_source: source, budget_note: 'Category amounts are planning allowances, not verified quotes.',
      budget_breakdown: {
        accommodation_lkr: 13500, food_lkr: 13500, transport_lkr: 11250,
        activities_lkr: 4500, misc_lkr: 2250, total_lkr: 45000, is_estimate: true,
      },
    }
    const { rerender } = render(<ItineraryCard plan={plan} fallback={plan.summary} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.getByText(plan.budget_note!)).toBeInTheDocument()
    expect(getItineraryRevealText(plan)).toContain(label)

    const daysText = 'Day 1ArrivalCheck inTemple of the Sacred Tooth Relic'
    rerender(<ItineraryCard plan={plan} fallback={plan.summary}
      revealCharacters={daysText.length + 'Estimated budget allocation'.length + label.length} />)
    expect(screen.getByText(label)).toBeInTheDocument()
    expect(screen.queryByText(plan.budget_note!)).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows a factual answer without a trip overview even if destination metadata is present', () => {
    const plan: ChatResponse = {
      destination: 'Kandy', summary: 'Kandy attractions include Kandy Lake.', itinerary: [],
    }
    render(<ItineraryCard plan={plan} fallback={plan.summary} />)
    expect(screen.getByText(plan.summary)).toBeInTheDocument()
    expect(screen.queryByText('Trip overview')).not.toBeInTheDocument()
    expect(screen.queryByText('Day 1')).not.toBeInTheDocument()
  })

  it('shows an explicitly estimated allocation with all categories and the total', () => {
    const plan: ChatResponse = {
      ...basePlan,
      budget_breakdown: {
        accommodation_lkr: 13500, food_lkr: 13500, transport_lkr: 11250,
        activities_lkr: 4500, misc_lkr: 2250, total_lkr: 45000, is_estimate: true,
      },
      fee_notice: 'Entrance fees are unverified. Confirm current charges with each venue.',
    }
    const { rerender } = render(<ItineraryCard plan={plan} fallback={plan.summary} />)
    const table = screen.getByRole('table', { name: 'Estimated budget allocation' })

    expect(within(table).getAllByRole('row')).toHaveLength(6)
    expect(within(table).getByText('Accommodation')).toBeInTheDocument()
    expect(within(table).getByText('Food')).toBeInTheDocument()
    expect(within(table).getByText('11,250 LKR')).toBeInTheDocument()
    expect(within(table).getByText('45,000 LKR')).toBeInTheDocument()
    expect(screen.getByText(/Planning allowances/)).toBeInTheDocument()
    expect(screen.getByText(plan.fee_notice!)).toBeInTheDocument()

    // Saved history from before this feature remains readable.
    rerender(<ItineraryCard plan={basePlan} fallback={basePlan.summary} />)
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('types the budget rows in order before the fee notice and follow-up', () => {
    const plan: ChatResponse = {
      ...basePlan, itinerary: [],
      budget_breakdown: {
        accommodation_lkr: 13500, food_lkr: 13500, transport_lkr: 11250,
        activities_lkr: 4500, misc_lkr: 2250, total_lkr: 45000, is_estimate: true,
      },
      fee_notice: 'Entrance fees are unverified.',
    }
    const limit = 'Estimated budget allocation'.length
      + 'Planning allowances. Confirm actual prices for your dates, travellers and nights.'.length
      + 'Accommodation'.length + 3
    const { rerender } = render(
      <ItineraryCard plan={plan} fallback={plan.summary} revealCharacters={limit} />,
    )

    const table = screen.getByRole('table', { name: 'Estimated budget allocation' })
    expect(within(table).getByText('13,')).toBeInTheDocument()
    expect(within(table).queryByText('Food')).not.toBeInTheDocument()
    expect(screen.queryByText(plan.fee_notice!)).not.toBeInTheDocument()
    expect(screen.queryByText(plan.follow_up_question!)).not.toBeInTheDocument()

    rerender(<ItineraryCard plan={plan} fallback={plan.summary} />)
    expect(screen.getByText(plan.fee_notice!)).toBeInTheDocument()
    expect(screen.getByText(plan.follow_up_question!)).toBeInTheDocument()
  })

  it('reveals titles and bullets in order without showing later days early', () => {
    const plan: ChatResponse = {
      ...basePlan,
      itinerary: [
        { day: 1, title: 'Arrival', items: ['Board the train', 'Check in'] },
        { day: 2, title: 'Explore', items: ['Visit the gardens'] },
      ],
      tools_used: ['get_weather'],
    }
    const { rerender } = render(
      <ItineraryCard plan={plan} fallback={plan.summary} revealCharacters={2} />,
    )

    expect(screen.getByText(plan.summary)).toBeInTheDocument()
    expect(screen.getByText('Da')).toBeInTheDocument()
    expect(screen.queryByText('Arrival')).not.toBeInTheDocument()
    expect(screen.queryByText('Day 2')).not.toBeInTheDocument()

    // Day label + title + only the first three characters of the first bullet.
    rerender(<ItineraryCard plan={plan} fallback={plan.summary} revealCharacters={15} />)
    expect(screen.getByText('Arrival')).toBeInTheDocument()
    expect(screen.getByText('Boa')).toBeInTheDocument()
    expect(screen.queryByText('Board the train')).not.toBeInTheDocument()
    expect(screen.queryByText('Check in')).not.toBeInTheDocument()
    expect(screen.queryByText('Day 2')).not.toBeInTheDocument()
    expect(screen.queryByText(plan.follow_up_question!)).not.toBeInTheDocument()
    expect(screen.queryByText('Tools used')).not.toBeInTheDocument()

    rerender(<ItineraryCard plan={plan} fallback={plan.summary} />)
    expect(screen.getByText('Board the train')).toBeInTheDocument()
    expect(screen.getByText('Check in')).toBeInTheDocument()
    expect(screen.getByText('Day 2')).toBeInTheDocument()
    expect(screen.getByText('Visit the gardens')).toBeInTheDocument()
    expect(screen.getByText(plan.follow_up_question!)).toBeInTheDocument()
    expect(screen.getByText('Tools used')).toBeInTheDocument()
  })

  it('types the follow-up question after all itinerary text', () => {
    const plan: ChatResponse = {
      summary: 'Here is your plan.',
      itinerary: [{ day: 1, title: 'Arrive', items: ['Rest'] }],
      follow_up_question: 'Want more ideas?',
    }
    // "Day 1" (5) + "Arrive" (6) + "Rest" (4) + "Want" (4).
    render(<ItineraryCard plan={plan} fallback={plan.summary} revealCharacters={19} />)

    expect(screen.getByText('Rest')).toBeInTheDocument()
    expect(screen.getByText('Want')).toBeInTheDocument()
    expect(screen.queryByText('Want more ideas?')).not.toBeInTheDocument()
  })

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
    expect(screen.getByRole('region', { name: 'Sources' })).toBeInTheDocument()

    // The new section, alongside them.
    expect(screen.getByText('Tools used')).toBeInTheDocument()
    expect(screen.getByText('Budget')).toBeInTheDocument()
  })

  it('renders normally (no crash, no stray "Tools used" section) for a plain reply with no plan-level extras at all', () => {
    const minimalPlan: ChatResponse = { summary: 'Hi! How can I help you plan your trip?' }

    render(<ItineraryCard plan={minimalPlan} fallback={minimalPlan.summary} />)

    expect(screen.getByText('Hi! How can I help you plan your trip?')).toBeInTheDocument()
    expect(screen.queryByText('Tools used')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Sources' })).not.toBeInTheDocument()
  })
})
