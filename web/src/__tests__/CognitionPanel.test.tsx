import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import CognitionPanel from '../components/review/CognitionPanel'
import type { CognitionSummary } from '../lib/types'

function renderPanel(props: Parameters<typeof CognitionPanel>[0]) {
  return render(
    <MemoryRouter>
      <CognitionPanel {...props} />
    </MemoryRouter>
  )
}

function makeCognition(overrides: Partial<CognitionSummary> = {}): CognitionSummary {
  return {
    cognition_id: 'cog-001',
    title: '连板高度决定板块生命周期',
    category: 'structure',
    sub_category: '节奏',
    evidence_level: 'supported',
    confidence: 0.82,
    instance_count: 12,
    validated_count: 9,
    invalidated_count: 1,
    pattern: '当首板溢价连续两日萎缩 → 进入分歧',
    conflict_group: null,
    tags: ['rhythm', 'sector'],
    ...overrides,
  }
}

describe('CognitionPanel', () => {
  it('当 cognitions 为空时不渲染', () => {
    const { container } = renderPanel({ cognitions: [] })
    expect(container.firstChild).toBeNull()
  })

  it('当 cognitions 为 undefined 时不渲染', () => {
    const { container } = renderPanel({})
    expect(container.firstChild).toBeNull()
  })

  it('默认折叠，展开按钮文案为「展开」', () => {
    renderPanel({ cognitions: [makeCognition()] })
    const panel = screen.getByTestId('cognition-panel')
    expect(within(panel).getByText('相关底层认知 · 1 条')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: '展开' })).toBeInTheDocument()
    expect(
      within(panel).queryByText('连板高度决定板块生命周期')
    ).not.toBeInTheDocument()
  })

  it('defaultExpanded=true 直接展开，显示标题 / category / 置信度 / 实例统计', () => {
    renderPanel({
      cognitions: [makeCognition()],
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    expect(within(panel).getByText('连板高度决定板块生命周期')).toBeInTheDocument()
    expect(within(panel).getByText('结构')).toBeInTheDocument()
    expect(within(panel).getByText('节奏')).toBeInTheDocument()
    expect(within(panel).getByText('supported')).toBeInTheDocument()
    expect(within(panel).getByText('82%')).toBeInTheDocument()
    expect(
      within(panel).getByText('实例 12（验证 9/推翻 1）')
    ).toBeInTheDocument()
    expect(
      within(panel).getByText('当首板溢价连续两日萎缩 → 进入分歧')
    ).toBeInTheDocument()
  })

  it('点击展开按钮切换内容可见性', () => {
    renderPanel({ cognitions: [makeCognition()] })
    const panel = screen.getByTestId('cognition-panel')
    fireEvent.click(within(panel).getByRole('button', { name: '展开' }))
    expect(within(panel).getByText('连板高度决定板块生命周期')).toBeInTheDocument()
    expect(within(panel).getByRole('button', { name: '收起' })).toBeInTheDocument()
  })

  it('Link 包含 from=stepKey 查询字符串', () => {
    renderPanel({
      cognitions: [makeCognition({ cognition_id: 'cog-xyz' })],
      stepKey: 'step3_emotion',
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    const link = within(panel).getByRole('link', {
      name: '连板高度决定板块生命周期',
    })
    expect(link.getAttribute('href')).toBe('/cognition?from=step3_emotion')
  })

  it('未指定 stepKey 时 Link 无 query', () => {
    renderPanel({
      cognitions: [makeCognition()],
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    const link = within(panel).getByRole('link', {
      name: '连板高度决定板块生命周期',
    })
    expect(link.getAttribute('href')).toBe('/cognition')
  })

  it('stepKey 含特殊字符时用 encodeURIComponent 编码', () => {
    renderPanel({
      cognitions: [makeCognition()],
      stepKey: 'step one & "two"',
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    const link = within(panel).getByRole('link', {
      name: '连板高度决定板块生命周期',
    })
    expect(link.getAttribute('href')).toBe(
      '/cognition?from=step%20one%20%26%20%22two%22'
    )
  })

  it('conflict_group 为 null 时不渲染冲突组标签', () => {
    renderPanel({
      cognitions: [makeCognition({ conflict_group: null })],
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    expect(within(panel).queryByText(/^冲突组/)).not.toBeInTheDocument()
  })

  it('conflict_group 有值时渲染冲突组标签', () => {
    renderPanel({
      cognitions: [makeCognition({ conflict_group: 'trend-vs-reversal' })],
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    expect(
      within(panel).getByText('冲突组 trend-vs-reversal')
    ).toBeInTheDocument()
  })

  it('pattern 为 null 时不渲染 pattern 文本', () => {
    renderPanel({
      cognitions: [
        makeCognition({ title: 'no-pattern-cog', pattern: null }),
      ],
      defaultExpanded: true,
    })
    const panel = screen.getByTestId('cognition-panel')
    expect(within(panel).getByText('no-pattern-cog')).toBeInTheDocument()
    expect(
      within(panel).queryByText('当首板溢价连续两日萎缩 → 进入分歧')
    ).not.toBeInTheDocument()
  })

  it('渲染多条认知并保留传入顺序', () => {
    const items: CognitionSummary[] = [
      makeCognition({ cognition_id: 'a', title: 'A-cog', confidence: 0.9 }),
      makeCognition({ cognition_id: 'b', title: 'B-cog', confidence: 0.6 }),
      makeCognition({ cognition_id: 'c', title: 'C-cog', confidence: 0.3 }),
    ]
    renderPanel({ cognitions: items, defaultExpanded: true })
    const panel = screen.getByTestId('cognition-panel')
    expect(within(panel).getByText('相关底层认知 · 3 条')).toBeInTheDocument()
    const titles = within(panel).getAllByRole('link').map(l => l.textContent)
    expect(titles).toEqual(['A-cog', 'B-cog', 'C-cog'])
    expect(within(panel).getByText('90%')).toBeInTheDocument()
    expect(within(panel).getByText('60%')).toBeInTheDocument()
    expect(within(panel).getByText('30%')).toBeInTheDocument()
  })
})
