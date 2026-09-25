import { forwardRef, useState, type InputHTMLAttributes } from 'react'
import { EyeIcon, EyeOffIcon } from 'lucide-react'

const passwordInputClass =
  'w-full rounded-xl border border-line bg-canvas px-3.5 py-2.5 pr-10 text-[14px] text-ink outline-none transition-colors duration-150 placeholder:text-faint focus:border-ink/35'

// Shared password field for every auth form (signup, login, reset) — hidden
// by default, with an eye icon that toggles plain-text visibility. Forwards
// its ref so it still works as an uncontrolled input under React Hook
// Form's {...register('password')} spread.
export const PasswordInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function PasswordInput(props, ref) {
    const [visible, setVisible] = useState(false)

    return (
      <div className="relative">
        <input ref={ref} type={visible ? 'text' : 'password'} className={passwordInputClass} {...props} />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? 'Hide password' : 'Show password'}
          aria-pressed={visible}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-faint transition-colors duration-150 hover:text-ink"
        >
          {visible ? (
            <EyeOffIcon className="h-4 w-4" strokeWidth={1.8} />
          ) : (
            <EyeIcon className="h-4 w-4" strokeWidth={1.8} />
          )}
        </button>
      </div>
    )
  },
)
