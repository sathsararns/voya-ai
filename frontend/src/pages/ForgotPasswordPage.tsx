import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link } from 'react-router-dom'
import { Loader2Icon } from 'lucide-react'
import { AuthLayout } from '../components/auth/AuthLayout'
import { forgotPasswordSchema, type ForgotPasswordFormValues } from '../schemas/auth'
import { useAuthStore } from '../hooks/useAuthStore'

const inputClass =
  'w-full rounded-xl border border-line bg-canvas px-3.5 py-2.5 text-[14px] text-ink outline-none transition-colors duration-150 placeholder:text-faint focus:border-ink/35'

export function ForgotPasswordPage() {
  const forgotPassword = useAuthStore((s) => s.forgotPassword)
  const [formError, setFormError] = useState<string | null>(null)
  const [sent, setSent] = useState(false)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordFormValues>({ resolver: zodResolver(forgotPasswordSchema) })

  const onSubmit = async (values: ForgotPasswordFormValues) => {
    setFormError(null)
    try {
      await forgotPassword(values.email)
      // The backend never reveals whether the email exists, and the reset
      // itself now happens by clicking a link mailed out asynchronously —
      // there's no code to collect here, so this is journey's end for this
      // page either way.
      setSent(true)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    }
  }

  if (sent) {
    return (
      <AuthLayout title="Check your email" subtitle="If that account exists, we've sent a link to reset your password.">
        <p className="text-center text-[13px] text-muted">
          The link expires in 30 minutes. You can close this tab.
        </p>
        <Link
          to="/login"
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90"
        >
          Back to login
        </Link>
      </AuthLayout>
    )
  }

  return (
    <AuthLayout title="Reset your password" subtitle="Enter your email and we'll send you a reset link.">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div>
          <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="email">
            Email
          </label>
          <input id="email" type="email" autoComplete="email" className={inputClass} {...register('email')} />
          {errors.email && <p className="mt-1 text-xs text-red-500">{errors.email.message}</p>}
        </div>

        {formError && <p className="text-xs text-red-500">{formError}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
        >
          {isSubmitting && <Loader2Icon className="h-4 w-4 animate-spin" />}
          Send reset link
        </button>
      </form>

      <p className="mt-5 text-center text-[13px] text-muted">
        Remembered your password?{' '}
        <Link to="/login" className="font-medium text-ink hover:underline">
          Log in
        </Link>
      </p>
    </AuthLayout>
  )
}
