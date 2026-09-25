import { Link } from 'react-router-dom'
import { AuthLayout } from '../components/auth/AuthLayout'

// Shown at "/" (and any other unmatched path — see App.tsx's catch-all)
// for a logged-out visitor, in place of an automatic redirect to /login.
// Reuses AuthLayout for the same visual language as the auth pages, with
// no form — just an explicit choice to log in or sign up.
export function WelcomePage() {
  return (
    <AuthLayout title="Welcome to Voya AI" subtitle="Plan trips, save itineraries, and pick up where you left off.">
      <div className="space-y-3">
        <Link
          to="/login"
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90"
        >
          Log in
        </Link>
        <Link
          to="/signup"
          className="flex w-full items-center justify-center gap-2 rounded-full border border-line px-4 py-2.5 text-sm font-semibold text-ink transition-colors duration-150 hover:bg-canvas"
        >
          Sign up
        </Link>
      </div>
    </AuthLayout>
  )
}
