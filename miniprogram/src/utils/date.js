'use strict'

const CHINA_OFFSET_MS = 8 * 60 * 60 * 1000

function formatChinaDate(value) {
  if (typeof value !== 'string' || !value) return ''
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return ''
  const date = new Date(timestamp + CHINA_OFFSET_MS)
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}-${String(date.getUTCDate()).padStart(2, '0')}`
}

module.exports = { formatChinaDate }
