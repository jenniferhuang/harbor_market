'use strict'

function formatCents(cents) {
  if (!Number.isSafeInteger(cents)) {
    throw new TypeError('cents must be a safe integer')
  }
  const sign = cents < 0 ? '-' : ''
  const absolute = Math.abs(cents)
  const yuan = Math.floor(absolute / 100)
  const fraction = String(absolute % 100).padStart(2, '0')
  return `${sign}¥${yuan}.${fraction}`
}

module.exports = { formatCents }
