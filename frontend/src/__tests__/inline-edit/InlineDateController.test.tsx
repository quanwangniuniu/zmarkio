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

/** Let the auto-save timeout fire and the async save settle. */
async function flushAutoSave() {
  await act(async () => {
    jest.advanceTimersByTime(200)
  })
}

describe('InlineDateController', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('saves a date picked from the calendar', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    fireEvent.change(openEditor(container), { target: { value: '2026-10-31' } })
    await flushAutoSave()

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('2026-10-31')
  })

  it('does not auto-save while a typed year is still incomplete', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    // Typing a year digit by digit fires change with 0002, 0020, 0202 first.
    fireEvent.change(openEditor(container), { target: { value: '0202-10-31' } })
    await flushAutoSave()

    expect(onSave).not.toHaveBeenCalled()
  })

  it('does not save when the same date is picked again', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value="2026-10-31" onSave={onSave} />)

    fireEvent.change(openEditor(container), { target: { value: '2026-10-31' } })
    await flushAutoSave()

    expect(onSave).not.toHaveBeenCalled()
  })

  it('saves once when the input blurs before the auto-save fires', async () => {
    const onSave = jest.fn().mockResolvedValue(undefined)
    const { container } = render(<InlineDateController value={null} onSave={onSave} />)

    const input = openEditor(container)
    fireEvent.change(input, { target: { value: '2026-10-31' } })
    await act(async () => {
      fireEvent.blur(input)
    })
    await flushAutoSave()

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(onSave).toHaveBeenCalledWith('2026-10-31')
  })
})
