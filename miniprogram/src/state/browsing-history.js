'use strict'

const { getApiBaseUrl } = require('../api/client')

const HISTORY_STORAGE_KEY = 'harbor_market_browsing_history'
const MAX_HISTORY_ITEMS = 50

function validCode(value) {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= 64 &&
    !Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
}

function loadHistory() {
  if (typeof wx === 'undefined' || typeof wx.getStorageSync !== 'function') return []
  try {
    const stored = wx.getStorageSync(HISTORY_STORAGE_KEY)
    if (!stored || stored.version !== 1 || stored.apiOrigin !== getApiBaseUrl() || !Array.isArray(stored.items)) return []
    const seen = new Set()
    return stored.items.slice(0, MAX_HISTORY_ITEMS).filter((item) => {
      if (!item || !validCode(item.productCode) || !Number.isSafeInteger(item.viewedAt) || item.viewedAt <= 0 || item.viewedAt > Date.now()) return false
      const code = item.productCode.trim().toUpperCase()
      if (seen.has(code)) return false
      seen.add(code)
      return true
    }).map((item) => ({ productCode: item.productCode.trim().toUpperCase(), viewedAt: item.viewedAt }))
  } catch {
    return []
  }
}

function recordVisit(product) {
  if (!product || product.status !== 'published' || product.category?.is_active === false || !validCode(product.product_code)) return false
  if (typeof wx === 'undefined' || typeof wx.setStorageSync !== 'function') return false
  const productCode = product.product_code.trim().toUpperCase()
  const items = [{ productCode, viewedAt: Date.now() }, ...loadHistory().filter((item) => item.productCode !== productCode)]
    .slice(0, MAX_HISTORY_ITEMS)
  try {
    wx.setStorageSync(HISTORY_STORAGE_KEY, { version: 1, apiOrigin: getApiBaseUrl(), items })
    return true
  } catch {
    // An optional local visit record must never interrupt product browsing.
    return false
  }
}

function clearHistory() {
  try {
    if (typeof wx === 'undefined' || typeof wx.removeStorageSync !== 'function') throw new Error()
    wx.removeStorageSync(HISTORY_STORAGE_KEY)
  } catch {
    throw new Error('足迹清除失败，请稍后重试。')
  }
}

module.exports = { HISTORY_STORAGE_KEY, MAX_HISTORY_ITEMS, loadHistory, recordVisit, clearHistory }
