import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useSearchParams } from 'react-router-dom'
import { Loader2Icon } from 'lucide-react'
import { AuthLayout } from '../components/auth/AuthLayout'
import { PasswordInput } from '../components/auth/PasswordInput'
import { resetPasswordSchema, type ResetPasswordFormValues } from '../schemas/auth'
import { useAuthStore } from '../hooks/useAuthStore'

export function ResetPasswordPage() {
  // The reset token lives in the URL (the link mailed by /forgot-password),
  // not typed in by hand — see routes/auth.py's reset_password.
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')

  const resetPassword = useAuthStore((s) => s.resetPassword)
  const [formError, setFormError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ResetPasswordFormValues>({ resolver: zodResolver(resetPasswordSchema) })

  if (!token) {
    return (
      <AuthLayout title="Invalid reset link" subtitle="This password reset link is missing or malformed.">
        <Link
          to="/forgot-password"
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90"
        >
          Request a new link
        </Link>
      </AuthLayout>
    )
  }

  const onSubmit = async (values: ResetPasswordFormValues) => {
    setFormError(null)
    try {
      await resetPassword(token, values.newPassword)
      setDone(true)
    } catch (err) {
      // A used/expired token surfaces as the backend's own "Invalid or
      // expired reset link" — already clear and safe to show as-is.
      setFormError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    }
  }

  if (done) {
    return (
      <AuthLayout title="Password updated" subtitle="You can now log in with your new password.">
        <Link
          to="/login"
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90"
        >
          Go to login
        </Link>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title="Set a new password" subtitle="Choose a new password for your account.">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div>
          <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="newPassword">
            New password
          </label>
          <PasswordInput id="newPassword" autoComplete="new-password" {...register('newPassword')} />
          {errors.newPassword && <p className="mt-1 text-xs text-red-500">{errors.newPassword.message}</p>}
        </div>

        <div>
          <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="confirmPassword">
            Confirm new password
          </label>
          <PasswordInput id="confirmPassword" autoComplete="new-password" {...register('confirmPassword')} />
          {errors.confirmPassword && (
            <p className="mt-1 text-xs text-red-500">{errors.confirmPassword.message}</p>
          )}
        </div>

        {formError && <p className="text-xs text-red-500">{formError}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
        >
          {isSubmitting && <Loader2Icon className="h-4 w-4 animate-spin" />}
          Update password
        </button>
      </form>
    </AuthLayout>
  )
}
