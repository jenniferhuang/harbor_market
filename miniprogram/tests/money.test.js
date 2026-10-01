'use strict'

const { formatCents } = require('../src/utils/money')

describe('formatCents', () => {
  it('formats integer fen without floating-point arithmetic', () => {
    expect(formatCents(0)).toBe('¥0.00')
    expect(formatCents(5)).toBe('¥0.05')
    expect(formatCents(12_345)).toBe('¥123.45')
    expect(formatCents(-101)).toBe('-¥1.01')
  })

  it('rejects fractional and unsafe money values', () => {
    expect(() => formatCents(1.5)).toThrow(TypeError)
    expect(() => formatCents(Number.MAX_SAFE_INTEGER + 1)).toThrow(TypeError)
  })
})
