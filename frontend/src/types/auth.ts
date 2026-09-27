export interface AuthUser {
  id: string
  name: string
  email: string
  created_at: string
}

// Mirrors backend/schemas/preferences.py's UserPreferences exactly — the
// account-scoped travel-preferences summary the assistant both reads (to
// personalize replies, see backend/services/user_preferences.py) and
// writes to automatically from chat. Fetched/edited from the Settings page
// (see pages/SettingsPage.tsx) via GET/PUT/DELETE /api/v1/auth/preferences.
export interface UserPreferences {
  preferred_destinations: string[]
  budget_lkr_min: number | null
  budget_lkr_max: number | null
  typical_trip_duration_days: number | null
  hotel_style: string | null
  transport_preference: string | null
  food_preferences: string[]
  activity_preferences: string[]
  travel_style: string | null
}
