import { useEffect, useState } from 'react'
import { Loader2Icon, RotateCcwIcon, SaveIcon } from 'lucide-react'
import { api, ApiRequestError } from '../lib/api'
import { useAuthStore } from '../hooks/useAuthStore'
import type { UserPreferences } from '../types/auth'

const inputClass =
  'w-full rounded-xl border border-line bg-canvas px-3.5 py-2.5 text-[14px] text-ink outline-none transition-colors duration-150 placeholder:text-faint focus:border-ink/35'

// The form's own working shape — list fields as a single comma-separated
// string (a plain text input) and numbers as strings (so an input can be
// genuinely empty, not just "0") — converted to/from UserPreferences only
// at load/save time. Keeps every field a plain, uncontrolled-feeling text
// input without needing a tag-input or number-input edge cases mid-typing.
interface PreferencesFormState {
  preferred_destinations: string
  budget_lkr_min: string
  budget_lkr_max: string
  typical_trip_duration_days: string
  hotel_style: string
  transport_preference: string
  food_preferences: string
  activity_preferences: string
  travel_style: string
}

const EMPTY_FORM: PreferencesFormState = {
  preferred_destinations: '',
  budget_lkr_min: '',
  budget_lkr_max: '',
  typical_trip_duration_days: '',
  hotel_style: '',
  transport_preference: '',
  food_preferences: '',
  activity_preferences: '',
  travel_style: '',
}

function toForm(preferences: UserPreferences): PreferencesFormState {
  return {
    preferred_destinations: preferences.preferred_destinations.join(', '),
    budget_lkr_min: preferences.budget_lkr_min?.toString() ?? '',
    budget_lkr_max: preferences.budget_lkr_max?.toString() ?? '',
    typical_trip_duration_days: preferences.typical_trip_duration_days?.toString() ?? '',
    hotel_style: preferences.hotel_style ?? '',
    transport_preference: preferences.transport_preference ?? '',
    food_preferences: preferences.food_preferences.join(', '),
    activity_preferences: preferences.activity_preferences.join(', '),
    travel_style: preferences.travel_style ?? '',
  }
}

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)
}

function toNumberOrNull(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? Math.trunc(parsed) : null
}

// The exact PUT payload shape backend/schemas/preferences.py's
// UserPreferencesUpdate expects — a full replace (see that model's own
// docstring), so every field is always sent, never just the ones touched.
function toPayload(form: PreferencesFormState) {
  return {
    preferred_destinations: splitList(form.preferred_destinations),
    budget_lkr_min: toNumberOrNull(form.budget_lkr_min),
    budget_lkr_max: toNumberOrNull(form.budget_lkr_max),
    typical_trip_duration_days: toNumberOrNull(form.typical_trip_duration_days),
    hotel_style: form.hotel_style.trim() || null,
    transport_preference: form.transport_preference.trim() || null,
    food_preferences: splitList(form.food_preferences),
    activity_preferences: splitList(form.activity_preferences),
    travel_style: form.travel_style.trim() || null,
  }
}

interface FieldConfig {
  key: keyof PreferencesFormState
  label: string
  placeholder: string
  hint?: string
  type?: 'text' | 'number'
}

const LIST_AND_TEXT_FIELDS: FieldConfig[] = [
  {
    key: 'preferred_destinations',
    label: 'Preferred destinations',
    placeholder: 'Kandy, Galle, Ella',
    hint: 'Comma-separated',
  },
  { key: 'hotel_style', label: 'Hotel style', placeholder: 'e.g. Luxury hotel, Budget hotel, Homestay' },
  { key: 'transport_preference', label: 'Transport preference', placeholder: 'e.g. Tuk tuk, Rental car, Train' },
  {
    key: 'food_preferences',
    label: 'Food preferences',
    placeholder: 'Vegetarian, Seafood',
    hint: 'Comma-separated',
  },
  {
    key: 'activity_preferences',
    label: 'Activity preferences',
    placeholder: 'Hiking, Beach, Cultural sightseeing',
    hint: 'Comma-separated',
  },
  { key: 'travel_style', label: 'Travel style', placeholder: 'e.g. Relaxed, Fast-paced, Luxury, Family, Solo' },
]

export function SettingsPage() {
  const user = useAuthStore((s) => s.user)

  const [form, setForm] = useState<PreferencesFormState>(EMPTY_FORM)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [isClearing, setIsClearing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  // Loads the CURRENT account's preferences from the server — never a
  // client-supplied user id anywhere in this request (see
  // backend/routes/auth.py's get_preferences), so there's no way for this
  // to accidentally show a different account's data even in principle.
  // Re-runs if the logged-in account itself changes (see the `user?.id`
  // dependency) — mostly academic, since ProtectedRoute already remounts
  // this whole page on any account switch (see components/auth/
  // ProtectedRoute.tsx's key={userId}), but makes the dependency explicit
  // rather than relying solely on that outer remount.
  useEffect(() => {
    let cancelled = false

    setIsLoading(true)
    setError(null)

    api
      .get<UserPreferences>('/api/v1/auth/preferences')
      .then((preferences) => {
        if (!cancelled) setForm(toForm(preferences))
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiRequestError ? err.message : 'Could not load your preferences.')
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [user?.id])

  const updateField = (key: keyof PreferencesFormState, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setStatusMessage(null)
  }

  const handleSave = async () => {
    setIsSaving(true)
    setError(null)
    setStatusMessage(null)
    try {
      const saved = await api.put<UserPreferences>('/api/v1/auth/preferences', toPayload(form))
      // The backend may normalize the input (trim/cap list length, swap a
      // reversed budget min/max — see UserPreferencesUpdate) — reflecting
      // its response back into the form keeps what's on screen exactly in
      // sync with what's actually stored, not just an optimistic echo of
      // what was submitted.
      setForm(toForm(saved))
      setStatusMessage('Preferences saved.')
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not save your preferences. Please try again.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleClear = async () => {
    setIsClearing(true)
    setError(null)
    setStatusMessage(null)
    try {
      const cleared = await api.del<UserPreferences>('/api/v1/auth/preferences')
      setForm(toForm(cleared))
      setStatusMessage('Preferences cleared.')
    } catch (err) {
      setError(err instanceof ApiRequestError ? err.message : 'Could not clear your preferences. Please try again.')
    } finally {
      setIsClearing(false)
    }
  }

  const busy = isSaving || isClearing

  return (
    <main className="mx-auto flex min-h-0 w-full max-w-4xl flex-1 flex-col overflow-y-auto px-4 pt-6 sm:px-6 sm:pt-10">
      <div className="pb-10">
        <h1 className="font-display text-[26px] font-bold tracking-tight text-ink sm:text-[32px]">
          Travel preferences
        </h1>
        <p className="mt-2 max-w-xl text-[14px] leading-6 text-muted">
          What Voya AI remembers about {user?.name ?? 'you'} across conversations, and uses to personalize trip
          suggestions. Only visible to your own account.
        </p>

        <div className="mt-6 rounded-2xl border border-line bg-surface p-5 sm:p-6">
          {isLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2Icon className="h-6 w-6 animate-spin text-faint" />
            </div>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                handleSave()
              }}
              className="space-y-4"
            >
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="budget_lkr_min">
                    Budget range — min (LKR)
                  </label>
                  <input
                    id="budget_lkr_min"
                    type="number"
                    min={0}
                    inputMode="numeric"
                    placeholder="e.g. 20000"
                    className={inputClass}
                    value={form.budget_lkr_min}
                    onChange={(e) => updateField('budget_lkr_min', e.target.value)}
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="budget_lkr_max">
                    Budget range — max (LKR)
                  </label>
                  <input
                    id="budget_lkr_max"
                    type="number"
                    min={0}
                    inputMode="numeric"
                    placeholder="e.g. 60000"
                    className={inputClass}
                    value={form.budget_lkr_max}
                    onChange={(e) => updateField('budget_lkr_max', e.target.value)}
                  />
                </div>
              </div>

              <div>
                <label
                  className="mb-1.5 block text-[13px] font-medium text-muted"
                  htmlFor="typical_trip_duration_days"
                >
                  Typical trip duration (days)
                </label>
                <input
                  id="typical_trip_duration_days"
                  type="number"
                  min={1}
                  max={365}
                  inputMode="numeric"
                  placeholder="e.g. 5"
                  className={`${inputClass} max-w-[160px]`}
                  value={form.typical_trip_duration_days}
                  onChange={(e) => updateField('typical_trip_duration_days', e.target.value)}
                />
              </div>

              {LIST_AND_TEXT_FIELDS.map((field) => (
                <div key={field.key}>
                  <label className="mb-1.5 flex items-baseline gap-2 text-[13px] font-medium text-muted" htmlFor={field.key}>
                    {field.label}
                    {field.hint && <span className="text-[11.5px] font-normal text-faint">({field.hint})</span>}
                  </label>
                  <input
                    id={field.key}
                    type="text"
                    placeholder={field.placeholder}
                    className={inputClass}
                    value={form[field.key]}
                    onChange={(e) => updateField(field.key, e.target.value)}
                  />
                </div>
              ))}

              {error && <p className="text-[13px] text-red-500">{error}</p>}
              {statusMessage && !error && <p className="text-[13px] text-teal">{statusMessage}</p>}

              <div className="flex flex-wrap items-center gap-3 pt-2">
                <button
                  type="submit"
                  disabled={busy}
                  className="flex items-center gap-2 rounded-full bg-ink px-5 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
                >
                  {isSaving ? (
                    <Loader2Icon className="h-4 w-4 animate-spin" />
                  ) : (
                    <SaveIcon className="h-4 w-4" strokeWidth={2.2} />
                  )}
                  Save changes
                </button>

                <button
                  type="button"
                  onClick={handleClear}
                  disabled={busy}
                  className="flex items-center gap-2 rounded-full border border-line bg-surface px-5 py-2.5 text-sm font-semibold text-muted transition-colors duration-150 hover:bg-canvas hover:text-ink disabled:opacity-50"
                >
                  {isClearing ? (
                    <Loader2Icon className="h-4 w-4 animate-spin" />
                  ) : (
                    <RotateCcwIcon className="h-4 w-4" strokeWidth={2.2} />
                  )}
                  Clear all preferences
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </main>
  )
}
