import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Vite already defaults to not opening a browser, but this makes it an
    // explicit, guaranteed setting rather than an implicit default — `npm
    // run dev` starts the dev server only; open it yourself when you're
    // ready.
    open: false,
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.{ts,tsx}'],
  },
})