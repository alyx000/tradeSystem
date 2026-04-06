import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import ThemeRhythmPanel from '../components/market/ThemeRhythmPanel'

describe('ThemeRhythmPanel', () => {
  it('renders active theme rows with phase, duration, key stocks and note', () => {
    render(
      <ThemeRhythmPanel
        activeThemes={[
          {
            theme_name: 'AI算力',
            phase: '主升',
            duration_days: 6,
            key_stocks: JSON.stringify(['高标A', '中军B', '容量C']),
            note: '量能配合较强',
            status: 'active',
          },
        ]}
      />
    )

    expect(screen.getByText('主线板块节奏')).toBeInTheDocument()
    expect(screen.getByText('AI算力')).toBeInTheDocument()
    expect(screen.getByText('主升')).toBeInTheDocument()
    expect(screen.getByText('6天')).toBeInTheDocument()
    expect(screen.getByText('高标A')).toBeInTheDocument()
    expect(screen.getByText('量能配合较强')).toBeInTheDocument()
  })

  it('renders nothing when there are no active themes', () => {
    const { container } = render(<ThemeRhythmPanel activeThemes={[]} />)

    expect(container).toBeEmptyDOMElement()
  })
})
