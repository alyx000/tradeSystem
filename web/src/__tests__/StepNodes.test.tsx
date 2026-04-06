import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import StepNodes from '../components/review/StepNodes'
import type { ReviewStepValue } from '../lib/types'

function renderStep(data: ReviewStepValue = {}) {
  const onChange = vi.fn()
  render(<StepNodes data={data} onChange={onChange} />)
  return { onChange }
}

describe('StepNodes', () => {
  it('renders existing node values', () => {
    renderStep({
      market_node: '突破前高',
      sector_node: '主线启动日',
      style_node: '风格切换点',
      overall: '大盘和主线同步共振',
    })

    expect(screen.getByDisplayValue('突破前高')).toBeInTheDocument()
    expect(screen.getByDisplayValue('主线启动日')).toBeInTheDocument()
    expect(screen.getByDisplayValue('风格切换点')).toBeInTheDocument()
    expect(screen.getByDisplayValue('大盘和主线同步共振')).toBeInTheDocument()
  })

  it('emits nested payload when user edits overall assessment', () => {
    const { onChange } = renderStep({})

    fireEvent.change(screen.getByLabelText('综合节点评估'), { target: { value: '当前处于分歧后的观察点' } })

    expect(onChange).toHaveBeenCalledWith({
      overall: '当前处于分歧后的观察点',
    })
  })
})
