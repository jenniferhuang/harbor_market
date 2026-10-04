'use strict'

const { ApiError, getApiBaseUrl, parseApiResponse, request } = require('./client')
const { normalizeProduct } = require('./catalog')
const { normalizeStore } = require('./shop')
const { readSession } = require('../state/customer-session')
const { expireSession, getAuthState } = require('../state/auth-store')

const PREFIX = '/api/v1/mini/shop'

function verifiedSession() {
  const session = readSession()
  if (!session || getAuthState().status !== 'authenticated') {
    throw new ApiError(401, '请先通过微信登录。')
  }
  return session
}

function stillCurrent(session) {
  const state = getAuthState()
  return session.apiOrigin === getApiBaseUrl() && readSession()?.accessToken === session.accessToken &&
    state.status === 'authenticated'
}

function listQuery(options = {}) {
  const page = options.page ?? 1
  const pageSize = options.page_size ?? 20
  if (!Number.isInteger(page) || page < 1 || page > 1_000_000 || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new ApiError(422, '列表页码无效。')
  }
  return `?page=${page}&page_size=${pageSize}`
}

function checkCurrent(session) {
  if (!stillCurrent(session)) throw new ApiError(409, '账户或接口地址已改变，请重新操作。')
}

async function customerRequest(path, options = {}) {
  const session = verifiedSession()
  try {
    const value = await request(`${PREFIX}${path}`, {
      ...options, headers: { Authorization: `Bearer ${session.accessToken}` },
    })
    checkCurrent(session)
    return value
  } catch (error) {
    if (error.status === 401) expireSession(session.accessToken)
    throw error
  }
}

function arrayPayload(value) {
  if (!Array.isArray(value)) throw new ApiError(502, '账户数据格式不正确。')
  return value
}

function validClaimTimestamp(value) {
  return typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
}

function couponRow(value) {
  if (!value || !Number.isSafeInteger(value.coupon_id) || value.coupon_id < 1 || !validClaimTimestamp(value.claimed_at)) {
    throw new ApiError(502, '优惠券数据格式不正确。')
  }
  return value
}

function productPath(code) {
  if (typeof code !== 'string' || !code.trim()) throw new ApiError(422, '请选择需要收藏的商品。')
  return `/favorites/${encodeURIComponent(code.trim().toUpperCase())}`
}

async function fetchCustomerShopAccess() {
  const value = await customerRequest('/me')
  if (!value || typeof value.can_manage_store !== 'boolean') throw new ApiError(502, '商家权限暂时无法确认。')
  return value
}

async function fetchMyCoupons(options = {}) {
  return arrayPayload(await customerRequest(`/coupons${listQuery(options)}`))
    .filter((value) => value && validClaimTimestamp(value.claimed_at))
    .map(couponRow)
}

async function claimCoupon(id) {
  if (!Number.isSafeInteger(id) || id < 1) throw new ApiError(422, '优惠券编号无效。')
  return couponRow(await customerRequest(`/coupons/${id}/claim`, { method: 'POST' }))
}

async function fetchFavorites(options = {}) {
  return arrayPayload(await customerRequest(`/favorites${listQuery(options)}`)).map(normalizeProduct)
}

async function getFavorite(code) {
  const value = await customerRequest(productPath(code))
  if (!value || typeof value.is_favorite !== 'boolean') throw new ApiError(502, '收藏状态暂时无法读取。')
  return value
}

async function setFavorite(code, enabled) {
  const value = await customerRequest(productPath(code), { method: enabled ? 'PUT' : 'DELETE' })
  if (!value || value.is_favorite !== Boolean(enabled)) throw new ApiError(502, '收藏状态暂时无法确认，请刷新后重试。')
  return value
}

async function fetchMyOrders(options = {}) {
  return arrayPayload(await customerRequest(`/orders${listQuery(options)}`))
}

async function fetchMerchantStore() {
  return normalizeStore(await customerRequest('/store'))
}

async function updateMerchantStore(values) {
  // The customer endpoint cannot change the shop-owner binding.
  const { name, phone, address, latitude, longitude } = values
  return normalizeStore(await customerRequest('/store', {
    method: 'PATCH', data: { name, phone, address, latitude, longitude },
  }))
}

async function uploadMerchantAnnouncement(filePath) {
  const session = verifiedSession()
  if (typeof filePath !== 'string' || !/^(?:wxfile:\/\/|https?:\/\/tmp\/|\/)/.test(filePath)) {
    throw new ApiError(422, '请重新选择公告图片。')
  }
  if (typeof wx === 'undefined' || typeof wx.uploadFile !== 'function') {
    throw new ApiError(0, '当前微信版本不支持上传图片。')
  }
  try {
    const value = await new Promise((resolve, reject) => {
      try {
        wx.uploadFile({
          url: `${session.apiOrigin}${PREFIX}/store/announcement`,
          filePath,
          name: 'file',
          header: { Accept: 'application/json', Authorization: `Bearer ${session.accessToken}` },
          timeout: 30_000,
          success(response) {
            try {
              const payload = typeof response.data === 'string' ? JSON.parse(response.data) : response.data
              resolve(parseApiResponse(Number(response.statusCode), payload))
            } catch (error) {
              reject(error instanceof ApiError ? error : new ApiError(502, '公告上传响应异常，请稍后重试。'))
            }
          },
          fail() { reject(new ApiError(0, '公告图片上传失败，请检查网络后重试。')) },
        })
      } catch { reject(new ApiError(0, '公告图片上传失败，请检查网络后重试。')) }
    })
    checkCurrent(session)
    return normalizeStore(value)
  } catch (error) {
    if (error.status === 401) expireSession(session.accessToken)
    throw error
  }
}

module.exports = {
  claimCoupon, fetchCustomerShopAccess, fetchFavorites, fetchMerchantStore, fetchMyCoupons,
  fetchMyOrders, getFavorite, setFavorite, updateMerchantStore, uploadMerchantAnnouncement,
}
