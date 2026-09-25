import { useState } from 'react'
import { useForm } from 'react-hook-form'
import { zodResolver } from '@hookform/resolvers/zod'
import { Link, useNavigate } from 'react-router-dom'
import { Loader2Icon } from 'lucide-react'
import { AuthLayout } from '../components/auth/AuthLayout'
import { PasswordInput } from '../components/auth/PasswordInput'
import { loginSchema, type LoginFormValues } from '../schemas/auth'
import { useAuthStore } from '../hooks/useAuthStore'

const inputClass =
  'w-full rounded-xl border border-line bg-canvas px-3.5 py-2.5 text-[14px] text-ink outline-none transition-colors duration-150 placeholder:text-faint focus:border-ink/35'

export function LoginPage() {
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const [formError, setFormError] = useState<string | null>(null)

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginFormValues>({ resolver: zodResolver(loginSchema) })

  const onSubmit = async (values: LoginFormValues) => {
    setFormError(null)
    try {
      await login(values.email, values.password)
      navigate('/')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : 'Something went wrong. Please try again.')
    }
  }

  return (
    <AuthLayout title="Welcome back" subtitle="Log in to continue planning your trip.">
      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div>
          <label className="mb-1.5 block text-[13px] font-medium text-muted" htmlFor="email">
            Email
          </label>
          <input id="email" type="email" autoComplete="email" className={inputClass} {...register('email')} />
          {errors.email && <p className="mt-1 text-xs text-red-500">{errors.email.message}</p>}
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="block text-[13px] font-medium text-muted" htmlFor="password">
              Password
            </label>
            <Link to="/forgot-password" className="text-[12.5px] font-medium text-ink hover:underline">
              Forgot password?
            </Link>
          </div>
          <PasswordInput id="password" autoComplete="current-password" {...register('password')} />
          {errors.password && <p className="mt-1 text-xs text-red-500">{errors.password.message}</p>}
        </div>

        {formError && <p className="text-xs text-red-500">{formError}</p>}

        <button
          type="submit"
          disabled={isSubmitting}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-ink px-4 py-2.5 text-sm font-semibold text-canvas transition-opacity duration-150 hover:opacity-90 disabled:opacity-50"
        >
          {isSubmitting && <Loader2Icon className="h-4 w-4 animate-spin" />}
          Log in
        </button>
      </form>

      <p className="mt-5 text-center text-[13px] text-muted">
        Don&apos;t have an account?{' '}
        <Link to="/signup" className="font-medium text-ink hover:underline">
          Sign up
        </Link>
      </p>
    </AuthLayout>
  )
}
