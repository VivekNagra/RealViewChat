import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import ClassificationBadge, { CLASSIFICATION_META } from './ClassificationBadge'

describe('ClassificationBadge', () => {
  it('renders the human label for a known classification', () => {
    render(<ClassificationBadge classification="correct" />)
    expect(screen.getByText('Correct')).toBeInTheDocument()
  })

  it('renders the false-positive label', () => {
    render(<ClassificationBadge classification="fp" />)
    expect(screen.getByText('False Positive')).toBeInTheDocument()
  })

  it('renders nothing for an unknown classification', () => {
    const { container } = render(<ClassificationBadge classification="bogus" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('defines metadata for exactly the three classifications', () => {
    expect(Object.keys(CLASSIFICATION_META)).toEqual(['correct', 'fp', 'fn'])
  })
})
