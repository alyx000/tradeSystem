import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepLeaders from '../components/review/StepLeaders'
import type { ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}) {
  const onChange = vi.fn()
  render(<StepLeaders data={data} onChange={onChange} />)
  return { onChange }
}

describe('StepLeaders', () => {
  it('renders existing top-leader rows and transition fields', () => {
    renderStep({
      top_leaders: [
        {
          stock: '高标A',
          sector: 'AI算力',
          attribute: '最高标',
          clarity: '一眼看出',
          position: '主升',
          is_new: true,
        },
      ],
      transition: {
        old: '旧龙头',
        new: '新龙头',
        reason: '高低切换',
      },
    })

    expect(screen.getByDisplayValue('高标A')).toBeInTheDocument()
    expect(screen.getByDisplayValue('AI算力')).toBeInTheDocument()
    expect(screen.getByLabelText('清晰度')).toHaveValue('一眼看出')
    expect(screen.getByLabelText('当前位置')).toHaveValue('主升')
    expect(screen.getByLabelText('新最')).toBeChecked()
    expect(screen.getByDisplayValue('高低切换')).toBeInTheDocument()
  })

  it('emits top-leaders payload when user edits leader stock', () => {
    const { onChange } = renderStep({
      top_leaders: [
        {
          stock: '高标A',
          sector: 'AI算力',
          attribute: '最高标',
          clarity: '一眼看出',
          position: '主升',
          is_new: false,
        },
      ],
    })

    fireEvent.change(screen.getByLabelText('股票'), { target: { value: '高标B' } })

    expect(onChange).toHaveBeenCalledWith({
      top_leaders: [
        {
          stock: '高标B',
          sector: 'AI算力',
          attribute: '最高标',
          clarity: '一眼看出',
          position: '主升',
          is_new: false,
        },
      ],
    })
  })
})
