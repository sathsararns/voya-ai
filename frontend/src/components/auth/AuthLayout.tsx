import type { ReactNode } from 'react'
import voyaLogo from '../../assets/voya-logo.jpg'

export function AuthLayout({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-canvas px-4 font-sans text-ink">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <img src={voyaLogo} alt="Voya AI" className="h-12 w-12 rounded-xl object-cover" />
          <h1 className="mt-4 font-display text-2xl font-bold tracking-tight text-ink">{title}</h1>
          {subtitle && <p className="mt-1.5 text-sm text-muted">{subtitle}</p>}
        </div>
        <div className="rounded-2xl border border-line bg-surface p-6">{children}</div>
      </div>
    </div>
  )
}
