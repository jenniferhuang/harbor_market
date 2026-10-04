'use strict'

const { ApiError, getApiBaseUrl, parseApiResponse, request } = require('./client')

const AUTH_PATH = '/api/v1/mini/auth'

function sessionHeaders(session) {
  if (!session || session.apiOrigin !== getApiBaseUrl() ||
      typeof session.accessToken !== 'string' || !/^[A-Za-z0-9._~-]{16,512}$/.test(session.accessToken) ||
      !Number.isFinite(Date.parse(session.expiresAt)) || Date.parse(session.expiresAt) <= Date.now()) {
    throw new ApiError(401, '登录状态已失效，请重新登录。')
  }
  return { Authorization: `Bearer ${session.accessToken}` }
}

function exchangeLoginCode(code) {
  return request(`${AUTH_PATH}/login`, { method: 'POST', data: { code } })
}

function fetchCurrentCustomer(session) {
  return request(`${AUTH_PATH}/me`, { headers: sessionHeaders(session) })
}

function saveNickname(session, nickname) {
  return request(`${AUTH_PATH}/profile`, {
    method: 'PATCH',
    headers: sessionHeaders(session),
    data: { nickname },
  })
}

function revokeSession(session) {
  return request(`${AUTH_PATH}/logout`, { method: 'POST', headers: sessionHeaders(session) })
}

function uploadAvatar(session, filePath) {
  if (typeof wx === 'undefined' || typeof wx.uploadFile !== 'function') {
    return Promise.reject(new ApiError(0, '当前微信版本无法上传头像，请升级后重试。'))
  }
  const headers = sessionHeaders(session)
  return new Promise((resolve, reject) => {
    try {
      wx.uploadFile({
        url: `${session.apiOrigin}${AUTH_PATH}/avatar`,
        filePath,
        name: 'file',
        header: { Accept: 'application/json', ...headers },
        timeout: 20_000,
        success(response) {
          try {
            const payload = typeof response.data === 'string' ? JSON.parse(response.data) : response.data
            resolve(parseApiResponse(Number(response.statusCode), payload))
          } catch (error) {
            reject(error instanceof ApiError ? error : new ApiError(502, '头像上传响应异常，请稍后重试。'))
          }
        },
        fail() { reject(new ApiError(0, '头像上传失败，请检查网络后重试。')) },
      })
    } catch {
      reject(new ApiError(0, '头像上传失败，请检查网络后重试。'))
    }
  })
}

function downloadAvatar(session) {
  if (!session.customer.avatar_url || typeof wx === 'undefined' || typeof wx.downloadFile !== 'function') {
    return Promise.resolve('')
  }
  const headers = sessionHeaders(session)
  // Only our authenticated current-avatar endpoint receives the bearer token.
  return new Promise((resolve, reject) => {
    try {
      wx.downloadFile({
        url: `${session.apiOrigin}${AUTH_PATH}/avatar`,
        header: headers,
        timeout: 15_000,
        success(response) {
          if (response.statusCode === 200 && typeof response.tempFilePath === 'string') {
            resolve(response.tempFilePath)
          } else {
            reject(new ApiError(Number(response.statusCode) || 502, '头像暂时无法加载。'))
          }
        },
        fail() { reject(new ApiError(0, '头像暂时无法加载。')) },
      })
    } catch {
      reject(new ApiError(0, '头像暂时无法加载。'))
    }
  })
}

module.exports = {
  downloadAvatar,
  exchangeLoginCode,
  fetchCurrentCustomer,
  revokeSession,
  saveNickname,
  uploadAvatar,
}
