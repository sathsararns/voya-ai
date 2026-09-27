// Tests for the Settings page's preferences panel (src/pages/SettingsPage.tsx)
// — proving: preferences load correctly for the logged-in user, can be
// edited and saved (as a full replace), can be cleared, and that switching
// accounts never leaves a previous account's preferences on screen. Mocks
// only `fetch`, exactly like this project's other component tests
// (Composer.test.tsx, ItineraryCard.test.tsx) — real component, real
// rendering, real store.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { SettingsPage } from '../src/pages/SettingsPage'
import { useAppStore } from '../src/hooks/useAppStore'
import { useAuthStore } from '../src/hooks/useAuthStore'
import type { AuthUser } from '../src/types/auth'

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 500) {
  return { ok, status, json: async () => body }
}

// Only fetch calls to this page's own endpoint matter for these tests —
// filtering by URL rather than asserting a raw call count is what keeps
// them robust against useAuthStore.ts's own cross-store subscription (see
// below), an unrelated, pre-existing effect these tests would otherwise
// have to account for exactly.
function preferencesCalls(fetchSpy: ReturnType<typeof vi.fn>) {
  return fetchSpy.mock.calls.filter(([url]) => typeof url === 'string' && url.includes('/api/v1/auth/preferences'))
}

const initialAuthState = useAuthStore.getState()
const initialAppState = useAppStore.getState()

const defaultUser: AuthUser = {
  id: 'user-1',
  name: 'Pref Tester',
  email: 'pref@example.com',
  created_at: '2026-01-01T00:00:00Z',
}

const EMPTY_PREFERENCES = {
  preferred_destinations: [],
  budget_lkr_min: null,
  budget_lkr_max: null,
  typical_trip_duration_days: null,
  hotel_style: null,
  transport_preference: null,
  food_preferences: [],
  activity_preferences: [],
  travel_style: null,
}

const POPULATED_PREFERENCES = {
  preferred_destinations: ['Kandy', 'Galle'],
  budget_lkr_min: 20000,
  budget_lkr_max: 60000,
  typical_trip_duration_days: 5,
  hotel_style: 'Luxury hotel',
  transport_preference: 'Tuk tuk',
  food_preferences: ['Vegetarian'],
  activity_preferences: ['Hiking'],
  travel_style: 'Luxury',
}

beforeEach(async () => {
  useAppStore.setState(initialAppState, true)
  // useAuthStore.ts subscribes to its own state and fires a fire-and-forget
  // initConversations() (a useAppStore action, unrelated to this page) on
  // every account-identity change — this default mock + tick flush is what
  // stops that background call from racing with (and stealing a
  // mockResolvedValueOnce slot from) each test's own, more specific mocks,
  // the same pattern tests/Composer.test.tsx and tests/useAppStore.test.ts
  // already use for the same reason.
  global.fetch = vi.fn().mockResolvedValue(jsonResponse({ conversations: [] })) as unknown as typeof fetch
  useAuthStore.setState({ ...initialAuthState, user: defaultUser, status: 'authenticated' }, true)
  await new Promise((resolve) => setTimeout(resolve, 0))

  try {
    document.cookie = 'voya_csrf_token=test-csrf-token'
  } catch {
    // jsdom always supports document.cookie; defensive only.
  }
})

afterEach(() => {
  cleanup()
})

describe('SettingsPage — loading preferences', () => {
  it("loads and displays the current user's saved preferences", async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(jsonResponse(POPULATED_PREFERENCES)) as unknown as typeof fetch

    render(<SettingsPage />)

    await screen.findByDisplayValue('Kandy, Galle')
    expect(screen.getByDisplayValue('20000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('60000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('5')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Luxury hotel')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Tuk tuk')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Vegetarian')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Hiking')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Luxury')).toBeInTheDocument()
  })

  it('shows an empty form for a brand-new account with nothing saved yet', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES)) as unknown as typeof fetch

    render(<SettingsPage />)

    const hotelInput = (await screen.findByLabelText('Hotel style')) as HTMLInputElement
    expect(hotelInput.value).toBe('')
  })

  it('shows an error message if loading preferences fails, without crashing', async () => {
    global.fetch = vi.fn().mockResolvedValueOnce(
      jsonResponse({ detail: 'A required service is temporarily unavailable.' }, false, 503),
    ) as unknown as typeof fetch

    render(<SettingsPage />)

    await screen.findByText('A required service is temporarily unavailable.')
  })
})

describe('SettingsPage — editing and saving', () => {
  it('lets the user edit a field and save, sending the full preferences payload as a replace', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES)) // initial GET on mount
      .mockResolvedValueOnce(jsonResponse({ ...EMPTY_PREFERENCES, hotel_style: 'Boutique hotel' })) // PUT response
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    const hotelInput = await screen.findByLabelText('Hotel style')
    fireEvent.change(hotelInput, { target: { value: 'Boutique hotel' } })

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await screen.findByText('Preferences saved.')

    expect(fetchSpy).toHaveBeenCalledTimes(2)
    const [putUrl, putInit] = fetchSpy.mock.calls[1] as [string, RequestInit]
    expect(putUrl).toMatch(/\/api\/v1\/auth\/preferences$/)
    expect(putInit.method).toBe('PUT')
    const body = JSON.parse(putInit.body as string)
    expect(body.hotel_style).toBe('Boutique hotel')
    // Full replace: every field is sent, not just the one that changed.
    expect(body.preferred_destinations).toEqual([])
    expect(body.food_preferences).toEqual([])

    // Reflects the server's response (the source of truth) back into the form.
    expect((screen.getByLabelText('Hotel style') as HTMLInputElement).value).toBe('Boutique hotel')
  })

  it('splits comma-separated list fields into an array and numbers into integers before saving', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES))
      .mockResolvedValueOnce(jsonResponse(POPULATED_PREFERENCES))
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    await screen.findByLabelText('Hotel style')

    fireEvent.change(screen.getByLabelText(/preferred destinations/i), { target: { value: 'Kandy, Galle' } })
    fireEvent.change(screen.getByLabelText(/budget range — min/i), { target: { value: '20000' } })
    fireEvent.change(screen.getByLabelText(/budget range — max/i), { target: { value: '60000' } })
    fireEvent.change(screen.getByLabelText(/typical trip duration/i), { target: { value: '5' } })

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await screen.findByText('Preferences saved.')

    const [, putInit] = fetchSpy.mock.calls[1] as [string, RequestInit]
    const body = JSON.parse(putInit.body as string)
    expect(body.preferred_destinations).toEqual(['Kandy', 'Galle'])
    expect(body.budget_lkr_min).toBe(20000)
    expect(body.budget_lkr_max).toBe(60000)
    expect(body.typical_trip_duration_days).toBe(5)
  })

  it('shows an error message if saving fails, and does not wipe the unsaved edit from the form', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES))
      .mockResolvedValueOnce(jsonResponse({ detail: 'Could not save' }, false, 500))
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    const hotelInput = await screen.findByLabelText('Hotel style')
    fireEvent.change(hotelInput, { target: { value: 'Boutique hotel' } })
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await screen.findByText('Could not save')
    expect((screen.getByLabelText('Hotel style') as HTMLInputElement).value).toBe('Boutique hotel')
  })
})

describe('SettingsPage — clearing preferences', () => {
  it('lets the user clear all saved preferences', async () => {
    const fetchSpy = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(POPULATED_PREFERENCES))
      .mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES))
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    await screen.findByDisplayValue('Kandy, Galle')

    fireEvent.click(screen.getByRole('button', { name: /clear all preferences/i }))

    await screen.findByText('Preferences cleared.')

    expect(fetchSpy).toHaveBeenCalledTimes(2)
    const [deleteUrl, deleteInit] = fetchSpy.mock.calls[1] as [string, RequestInit]
    expect(deleteUrl).toMatch(/\/api\/v1\/auth\/preferences$/)
    expect(deleteInit.method).toBe('DELETE')

    expect((screen.getByLabelText('Hotel style') as HTMLInputElement).value).toBe('')
    expect(screen.getByLabelText(/preferred destinations/i)).toHaveValue('')
  })
})

describe('SettingsPage — account scoping', () => {
  it('never shows a client-supplied user id — GET/PUT/DELETE are all scoped by the session cookie alone', async () => {
    const fetchSpy = vi.fn().mockResolvedValueOnce(jsonResponse(EMPTY_PREFERENCES))
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    await screen.findByLabelText('Hotel style')

    const [url] = fetchSpy.mock.calls[0] as [string]
    expect(url).not.toContain(defaultUser.id)
    expect(url).not.toContain('user_id')
  })

  it("re-fetches and shows the new account's own preferences after switching accounts, never the previous account's", async () => {
    const otherUser: AuthUser = {
      id: 'user-2',
      name: 'Other Person',
      email: 'other@example.com',
      created_at: '2026-01-01T00:00:00Z',
    }

    // useAuthStore.ts's own subscription fires an extra, unrelated
    // initConversations() call the instant user.id changes (same as in
    // beforeEach) — and it can land BETWEEN SettingsPage's own two GETs,
    // not just after them, so a plain queue of mockResolvedValueOnce values
    // isn't reliable here. Routing by URL instead — answering the
    // conversations endpoint separately from the preferences endpoint, and
    // counting only the latter — makes the mock immune to exactly when that
    // background call happens to interleave.
    let preferencesCallCount = 0
    const fetchSpy = vi.fn((url: unknown) => {
      if (typeof url === 'string' && url.includes('/api/v1/auth/preferences')) {
        preferencesCallCount += 1
        return Promise.resolve(jsonResponse(preferencesCallCount === 1 ? POPULATED_PREFERENCES : EMPTY_PREFERENCES))
      }
      return Promise.resolve(jsonResponse({ conversations: [] }))
    })
    global.fetch = fetchSpy as unknown as typeof fetch

    render(<SettingsPage />)
    await screen.findByDisplayValue('Kandy, Galle')

    act(() => {
      useAuthStore.setState({ user: otherUser })
    })

    // Polls for the actual target condition (not "the old value is gone",
    // which would also — misleadingly — pass during the brief moment the
    // form unmounts for the loading spinner between the two fetches).
    await waitFor(() => {
      expect((screen.getByLabelText('Hotel style') as HTMLInputElement).value).toBe('')
    })
    expect(screen.queryByDisplayValue('Kandy, Galle')).not.toBeInTheDocument()
    expect(preferencesCalls(fetchSpy)).toHaveLength(2)
  })
})
