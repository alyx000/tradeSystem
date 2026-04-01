import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import SearchCenter from '../pages/SearchCenter'

beforeAll(() => {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: () => Promise.resolve({ teacher_notes: [], industry_info: [], macro_info: [] }),
    text: () => Promise.resolve(''),
  })
})

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  )
}

describe('SearchCenter', () => {
  it('renders search input and button', () => {
    renderWithProviders(<SearchCenter />)
    expect(screen.getByPlaceholderText(/输入关键词/)).toBeInTheDocument()
    expect(screen.getByText('搜索')).toBeInTheDocument()
  })

  it('shows hint when no query', () => {
    renderWithProviders(<SearchCenter />)
    expect(screen.getByText(/输入关键词开始搜索/)).toBeInTheDocument()
  })
})
