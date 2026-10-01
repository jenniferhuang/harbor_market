'use strict'

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8080'
const API_BASE_STORAGE_KEY = 'harbor_market_api_base_url'
const DEFAULT_TIMEOUT_MS = 10_000

let configuredApiBaseUrl = null

class ApiError extends Error {
  constructor(status, message, options = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = options.code || undefined
    this.fieldErrors = options.fieldErrors || {}
  }
}

function miniProgramApi() {
  return typeof wx === 'undefined' ? null : wx
}

function normalizeApiBaseUrl(value) {
  if (typeof value !== 'string') {
    throw new TypeError('API base URL must be a string')
  }

  const normalized = value.trim().replace(/\/+$/, '')
  const match = normalized.match(
    /^https?:\/\/(?:localhost|(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-f:]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::(\d{1,5}))?$/i,
  )
  if (!match) {
    throw new TypeError('API base URL must be an absolute HTTP(S) origin without a path')
  }
  if (match[1] && (Number(match[1]) < 1 || Number(match[1]) > 65_535)) {
    throw new TypeError('API base URL port is invalid')
  }
  return normalized
}

function storedApiBaseUrl() {
  const api = miniProgramApi()
  if (!api || typeof api.getStorageSync !== 'function') return null

  try {
    const stored = api.getStorageSync(API_BASE_STORAGE_KEY)
    return stored ? normalizeApiBaseUrl(stored) : null
  } catch {
    return null
  }
}

function getApiBaseUrl() {
  if (configuredApiBaseUrl) return configuredApiBaseUrl
  configuredApiBaseUrl = storedApiBaseUrl() || DEFAULT_API_BASE_URL
  return configuredApiBaseUrl
}

function setApiBaseUrl(value) {
  const normalized = normalizeApiBaseUrl(value)
  configuredApiBaseUrl = normalized

  const api = miniProgramApi()
  if (api && typeof api.setStorageSync === 'function') {
    try {
      api.setStorageSync(API_BASE_STORAGE_KEY, normalized)
    } catch {
      // The active runtime still uses the configured value. A storage quota or
      // privacy failure must not prevent the current session from networking.
    }
  }
  return normalized
}

function absoluteMediaUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return ''
  const candidate = value.trim()
  if (/^https?:\/\//i.test(candidate)) {
    const baseUrl = getApiBaseUrl()
    return candidate === baseUrl || candidate.startsWith(`${baseUrl}/`) ? candidate : ''
  }

  // Protocol-relative input is treated as a local path instead of permitting
  // an API response to select an arbitrary remote host.
  const relative = candidate.replace(/^\/+/, '')
  return `${getApiBaseUrl()}/${relative}`
}

function normalizeRequestArguments(pathOrOptions, maybeOptions) {
  if (typeof pathOrOptions === 'string') {
    return { ...maybeOptions, path: pathOrOptions }
  }
  if (pathOrOptions && typeof pathOrOptions === 'object') {
    return { ...pathOrOptions }
  }
  throw new TypeError('request requires an API path')
}

function responsePayload(payload) {
  if (
    payload &&
    typeof payload === 'object' &&
    !Array.isArray(payload) &&
    Object.prototype.hasOwnProperty.call(payload, 'data')
  ) {
    return payload.data
  }
  throw new ApiError(502, '服务器响应格式不正确。')
}

function fieldErrors(payload) {
  const fields = payload?.error?.fields
  if (!Array.isArray(fields)) return {}

  return fields.reduce((errors, item) => {
    if (item && typeof item.field === 'string' && typeof item.message === 'string') {
      errors[item.field] = item.message
    }
    return errors
  }, {})
}

function errorFromResponse(status, payload) {
  const backendError = payload && typeof payload === 'object' ? payload.error : null
  const backendMessage =
    status >= 400 &&
    status < 500 &&
    backendError &&
    typeof backendError.message === 'string' &&
    backendError.message.trim().length <= 240
      ? backendError.message.trim()
      : null

  let fallback = '请求未能完成，请稍后重试。'
  if (status === 401) fallback = '登录状态已失效，请重新登录。'
  if (status === 404) fallback = '请求的内容不存在。'
  if (status === 429) fallback = '操作过于频繁，请稍后重试。'
  if (status >= 500) fallback = '服务暂时不可用，请稍后重试。'

  return new ApiError(status, backendMessage || fallback, {
    code: backendError && typeof backendError.code === 'string' ? backendError.code : undefined,
    fieldErrors: fieldErrors(payload),
  })
}

function request(pathOrOptions, maybeOptions = {}) {
  const options = normalizeRequestArguments(pathOrOptions, maybeOptions)
  if (typeof options.path !== 'string' || !options.path.startsWith('/api/')) {
    return Promise.reject(new TypeError('request path must start with /api/'))
  }

  const api = miniProgramApi()
  if (!api || typeof api.request !== 'function') {
    return Promise.reject(new ApiError(0, '当前环境不支持微信网络请求。'))
  }

  const method = String(options.method || 'GET').toUpperCase()
  const headers = {
    Accept: 'application/json',
    ...(options.headers || options.header || {}),
  }
  if (options.data !== undefined && !headers['Content-Type'] && !headers['content-type']) {
    headers['Content-Type'] = 'application/json'
  }

  return new Promise((resolve, reject) => {
    const requestOptions = {
      url: `${getApiBaseUrl()}${options.path}`,
      method,
      header: headers,
      timeout: options.timeout || DEFAULT_TIMEOUT_MS,
      success(response) {
        const status = Number(response.statusCode || 0)
        if (status >= 200 && status < 300) {
          try {
            resolve(responsePayload(response.data))
          } catch (error) {
            reject(error)
          }
          return
        }
        reject(errorFromResponse(status, response.data))
      },
      fail() {
        reject(new ApiError(0, '无法连接服务器，请检查网络后重试。'))
      },
    }
    if (options.data !== undefined) requestOptions.data = options.data

    try {
      api.request(requestOptions)
    } catch {
      reject(new ApiError(0, '无法连接服务器，请检查网络后重试。'))
    }
  })
}

module.exports = {
  ApiError,
  absoluteMediaUrl,
  getApiBaseUrl,
  request,
  setApiBaseUrl,
}
