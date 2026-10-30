import { afterEach, describe, expect, it, vi } from 'vitest'
import { retireLegacyWorker } from '../src/lib/legacyWorker'

afterEach(() => vi.unstubAllGlobals())

describe('retiring legacy browser workers', () => {
  it('unregisters a controlling root worker and reloads before the app starts', async () => {
    const worker = { scriptURL: new URL('/service-worker.js', window.location.origin).href }
    const unregister = vi.fn().mockResolvedValue(true)
    vi.stubGlobal('navigator', { serviceWorker: {
      controller: worker, getRegistrations: vi.fn().mockResolvedValue([
        { scope: `${window.location.origin}/`, active: worker, unregister },
      ]),
    } })
    const reload = vi.fn()
    expect(await retireLegacyWorker(reload)).toBe(true)
    expect(unregister).toHaveBeenCalledOnce()
    expect(reload).toHaveBeenCalledOnce()
  })

  it('does not reload on later clean page loads', async () => {
    vi.stubGlobal('navigator', { serviceWorker: {
      controller: null, getRegistrations: vi.fn().mockResolvedValue([]),
    } })
    const reload = vi.fn()
    expect(await retireLegacyWorker(reload)).toBe(false)
    expect(reload).not.toHaveBeenCalled()
  })

  it('leaves workers belonging to other paths alone', async () => {
    const unregister = vi.fn()
    vi.stubGlobal('navigator', { serviceWorker: {
      controller: null, getRegistrations: vi.fn().mockResolvedValue([
        { scope: `${window.location.origin}/other/`, active: { scriptURL: `${window.location.origin}/other/service-worker.js` }, unregister },
        { scope: `${window.location.origin}/`, active: { scriptURL: `${window.location.origin}/unrelated.js` }, unregister },
      ]),
    } })
    expect(await retireLegacyWorker(vi.fn())).toBe(false)
    expect(unregister).not.toHaveBeenCalled()
  })

  it('supports browsers without service workers', async () => {
    vi.stubGlobal('navigator', {})
    expect(await retireLegacyWorker(vi.fn())).toBe(false)
  })
})
