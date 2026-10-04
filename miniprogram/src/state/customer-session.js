'use strict'

const { ApiError, getApiBaseUrl } = require('../api/client')

const SESSION_STORAGE_KEY = 'harbor_market_customer_session'
let memorySession

function normalizeCustomer(value) {
  if (!value || !Number.isSafeInteger(value.id) || value.id < 1) {
    throw new ApiError(502, '登录信息异常，请重新登录。')
  }
  const nickname = typeof value.nickname === 'string' ? [...value.nickname.trim()].slice(0, 64).join('') : ''
  const avatar = typeof value.avatar_url === 'string' ? value.avatar_url : ''
  return {
    id: value.id,
    nickname: nickname || '微信用户',
    avatar_url: /^\/api\/v1\/mini\/auth\/avatar(?:\?[a-z0-9_=&.-]+)?$/i.test(avatar) ? avatar : '',
  }
}

function normalizeSession(value) {
  if (!value || value.version !== 1 || typeof value.apiOrigin !== 'string' ||
      !/^https?:\/\//.test(value.apiOrigin) || typeof value.accessToken !== 'string' ||
      !/^[A-Za-z0-9._~-]{16,512}$/.test(value.accessToken) ||
      typeof value.expiresAt !== 'string' || !Number.isFinite(Date.parse(value.expiresAt)) ||
      Date.parse(value.expiresAt) <= Date.now()) return null
  try {
    return {
      version: 1,
      apiOrigin: value.apiOrigin,
      accessToken: value.accessToken,
      expiresAt: value.expiresAt,
      customer: normalizeCustomer(value.customer),
    }
  } catch {
    return null
  }
}

function storedSession() {
  if (memorySession !== undefined) return memorySession
  try {
    const stored = typeof wx !== 'undefined' && typeof wx.getStorageSync === 'function'
      ? wx.getStorageSync(SESSION_STORAGE_KEY) : null
    memorySession = normalizeSession(typeof stored === 'string' ? JSON.parse(stored) : stored)
  } catch {
    memorySession = null
  }
  return memorySession
}

function readSession() {
  const session = storedSession()
  if (!session || session.apiOrigin !== getApiBaseUrl() || Date.parse(session.expiresAt) <= Date.now()) return null
  return { ...session, customer: { ...session.customer } }
}

function persistSession(session) {
  memorySession = normalizeSession(session)
  if (!memorySession) throw new ApiError(502, '登录信息异常，请重新登录。')
  if (typeof wx !== 'undefined' && typeof wx.setStorageSync === 'function') {
    try { wx.setStorageSync(SESSION_STORAGE_KEY, memorySession) } catch {
      // A verified identity can still use the current runtime without storage.
    }
  }
  return readSession()
}

function createSession(result, apiOrigin) {
  if (!result || result.token_type !== 'Bearer' || apiOrigin !== getApiBaseUrl()) {
    throw new ApiError(502, '登录信息异常，请重新登录。')
  }
  return persistSession({
    version: 1,
    apiOrigin,
    accessToken: result.access_token,
    expiresAt: result.expires_at,
    customer: result.customer,
  })
}

function clearSession() {
  memorySession = null
  if (typeof wx !== 'undefined' && typeof wx.removeStorageSync === 'function') {
    try { wx.removeStorageSync(SESSION_STORAGE_KEY) } catch { /* Runtime remains signed out. */ }
  } else if (typeof wx !== 'undefined' && typeof wx.setStorageSync === 'function') {
    try { wx.setStorageSync(SESSION_STORAGE_KEY, null) } catch { /* Runtime remains signed out. */ }
  }
}

module.exports = { clearSession, createSession, normalizeCustomer, persistSession, readSession }
