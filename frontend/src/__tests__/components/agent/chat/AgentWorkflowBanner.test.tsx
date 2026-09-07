import React from 'react'
import { render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import { AgentWorkflowBanner } from '@/components/agent/chat/AgentWorkflowBanner'

describe('AgentWorkflowBanner', () => {
  it('names the workflow and the target spreadsheet + sheet', () => {
    render(
      <AgentWorkflowBanner
        workflow="spreadsheet_analysis"
        spreadsheetName="Q3 Campaigns"
        sheetName="Meta Ads"
      />
    )
    expect(screen.getByText('Analyzing')).toBeInTheDocument()
    expect(screen.getByText(/Q3 Campaigns · Meta Ads/)).toBeInTheDocument()
  })

  it('uses a distinct label per workflow', () => {
    const { rerender } = render(
      <AgentWorkflowBanner workflow="pattern_generation" sheetName="Sheet1" />
    )
    expect(screen.getByText('Pattern generation')).toBeInTheDocument()

    rerender(<AgentWorkflowBanner workflow="pivot_table_generation" sheetName="Sheet1" />)
    expect(screen.getByText('Pivot table')).toBeInTheDocument()
  })

  it('tolerates a missing spreadsheet or sheet name', () => {
    render(<AgentWorkflowBanner workflow="pattern_generation" spreadsheetName="Only Book" />)
    expect(screen.getByText(/Only Book/)).toBeInTheDocument()
  })

  it('shows a spinner while busy', () => {
    const { container } = render(
      <AgentWorkflowBanner workflow="spreadsheet_analysis" sheetName="S" busy />
    )
    expect(container.querySelector('.animate-spin')).toBeInTheDocument()
  })
})
