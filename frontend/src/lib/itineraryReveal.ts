import type { ChatResponse } from '../types'

function getSections(plan: ChatResponse) {
  const budget = plan.budget_breakdown
  const budgetRows = budget ? [
    { id: 'accommodation', label: 'Accommodation', value: budget.accommodation_lkr },
    { id: 'food', label: 'Food', value: budget.food_lkr },
    { id: 'transport', label: 'Transport', value: budget.transport_lkr },
    { id: 'activities', label: 'Activities', value: budget.activities_lkr },
    { id: 'buffer', label: 'Buffer', value: budget.misc_lkr },
    { id: 'total', label: 'Total', value: budget.total_lkr },
  ].map((row) => ({ ...row, value: `${row.value.toLocaleString()} LKR` })) : []
  return {
    days: (Array.isArray(plan.itinerary) ? plan.itinerary : []).map((day) => ({
      day: day.day,
      label: `Day ${day.day}`,
      title: day.title,
      items: Array.isArray(day.items) ? day.items : [],
    })),
    budgetHeading: budget ? 'Estimated budget allocation' : '',
    budgetNote: budget ? 'Planning allowances. Confirm actual prices for your dates, travellers and nights.' : '',
    budgetRows,
    feeNotice: plan.fee_notice ?? '',
    followUpQuestion: plan.follow_up_question ?? '',
  }
}

// The timer and the card must count text in the same display order.
export function getItineraryRevealText(plan: ChatResponse): string {
  const sections = getSections(plan)
  return sections.days.flatMap((day) => [day.label, day.title, ...day.items])
    .concat(sections.budgetHeading, sections.budgetNote)
    .concat(sections.budgetRows.flatMap((row) => [row.label, row.value]))
    .concat(sections.feeNotice)
    .concat(sections.followUpQuestion)
    .join('')
}

export function getVisibleItinerary(plan: ChatResponse, characterLimit = Infinity) {
  let remaining = Math.max(0, characterLimit)
  const take = (text: string) => {
    const visible = text.slice(0, remaining)
    remaining = Math.max(0, remaining - text.length)
    return visible
  }
  const sections = getSections(plan)

  return {
    days: sections.days.map((day) => ({
      day: day.day,
      label: take(day.label),
      title: take(day.title),
      items: day.items.map(take).filter(Boolean),
    })).filter((day) => day.label.length > 0),
    budgetHeading: take(sections.budgetHeading),
    budgetNote: take(sections.budgetNote),
    budgetRows: sections.budgetRows.map((row) => ({
      id: row.id, label: take(row.label), value: take(row.value),
    })).filter((row) => row.label.length > 0),
    feeNotice: take(sections.feeNotice),
    followUpQuestion: take(sections.followUpQuestion),
  }
}
