import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import '@testing-library/jest-dom'
import InlineDateController from '@/inline-edit/InlineDateController'

/** Open the editor and return its date input. */
function openEditor(container: HTMLElement): HTMLInputElement {
  fireEvent.click(screen.getByTitle('Click to edit'))
  const input = container.querySelector('input[type="date"]')
  if (!input) throw new Error('date input did not render')
  return input as HTMLInputElement
}

describe('InlineDateController', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('does not save while a date is only being browsed', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value="2026-09-30" onSave={onSave} />)

    // Moving between months in the native picker carries the selected day
    // along and changes the value — that must not be saved.
    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-30' } })
    await act(async () => {
      jest.advanceTimersByTime(1000)
    })

    expect(onSave).not.toHaveBeenCalled()
    expect(input).toBeInTheDocument()
  })

  it('saves the picked date on Enter', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-31' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('2026-10-31')
  })

  it('saves the picked date when the input loses focus', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-31' } })
    await act(async () => {
      fireEvent.blur(input)
    })

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('2026-10-31')
  })

  it('saves once when clicking outside, which also blurs the input', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-31' } })
    await act(async () => {
      fireEvent.mouseDown(document.body)
      fireEvent.blur(input)
    })

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('2026-10-31')
  })

  it('does not save when the committed date is unchanged', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value="2026-10-31" onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-31' } })
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' })
    })

    expect(onSave).not.toHaveBeenCalled()
  })

  it('saves the latest valid date after a slow earlier save finishes', async () => {
    let resolveFirst!: () => void
    let resolveSecond!: () => void
    const first = new Promise<void>((resolve) => { resolveFirst = resolve })
    const second = new Promise<void>((resolve) => { resolveSecond = resolve })
    const onSave = jest.fn()
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-30' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    fireEvent.change(input, { target: { value: '2026-10-31' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSave).toHaveBeenCalledTimes(1)

    await act(async () => { resolveFirst() })
    expect(onSave).toHaveBeenCalledTimes(2)
    expect(onSave).toHaveBeenLastCalledWith('2026-10-31')

    await act(async () => { resolveSecond() })
  })
})
