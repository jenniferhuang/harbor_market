'use strict'

const { ApiError, getApiBaseUrl, request } = require('./client')
const { expireSession, getAuthState } = require('../state/auth-store')
const { readSession } = require('../state/customer-session')

function money(value) { return Number.isSafeInteger(value) && value >= 0 }
function identifier(value) { return Number.isSafeInteger(value) && value > 0 }
function record(value) { return value && typeof value === 'object' && !Array.isArray(value) }
function timestamp(value) {
  return value === null || (typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value)))
}
function malformed() { return new ApiError(502, '购物车或优惠券数据格式不正确，请刷新后重试。') }

function normalizeCoupon(value) {
  if (!record(value) || !identifier(value.id) || typeof value.title !== 'string' || !value.title.trim() ||
      value.title.length > 160 || !money(value.min_spend_cents) || !money(value.discount_cents) ||
      value.discount_cents === 0 || (value.is_active !== undefined && typeof value.is_active !== 'boolean') ||
      !timestamp(value.starts_at) || !timestamp(value.expires_at)) throw malformed()
  return {
    id: value.id, title: value.title, min_spend_cents: value.min_spend_cents,
    discount_cents: value.discount_cents, starts_at: value.starts_at,
    expires_at: value.expires_at, is_active: value.is_active !== false,
  }
}

async function fetchAvailableCoupons(options = {}) {
  const page = options.page ?? 1
  const pageSize = options.page_size ?? 20
  if (!Number.isInteger(page) || page < 1 || page > 1_000_000 ||
      !Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new ApiError(422, '优惠券列表页码无效。')
  }
  const origin = getApiBaseUrl()
  const values = await request(`/api/v1/shop/coupons?page=${page}&page_size=${pageSize}`)
  if (origin !== getApiBaseUrl()) throw new ApiError(409, '接口地址已改变，请刷新后重试。')
  if (!Array.isArray(values) || values.length > pageSize) throw malformed()
  return values.map(normalizeCoupon)
}

function selections(value, status = 422) {
  if (!record(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value)) ||
      Object.keys(value).length > 20) throw new ApiError(status, '商品规格数据无效。')
  const result = {}
  for (const [key, options] of Object.entries(value)) {
    if (['constructor', 'prototype', '__proto__'].includes(key) ||
        !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(key) || !Array.isArray(options) ||
        options.length > 50 || options.some((option) => typeof option !== 'string' ||
          !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(option)) ||
        new Set(options).size !== options.length) throw new ApiError(status, '商品规格数据无效。')
    result[key] = [...options]
  }
  return result
}

function code(value, maximum) {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum &&
    /^[A-Za-z0-9][A-Za-z0-9_.-]*$/.test(value)
}

function normalizeInput(input) {
  if (!record(input) || !Array.isArray(input.items) || input.items.length > 100 ||
      !(input.coupon_id === null || identifier(input.coupon_id))) throw new ApiError(422, '请选择需要结算的商品。')
  return {
    items: input.items.map((item) => {
      if (!record(item) || !code(item.product_code, 64) || !code(item.sku_code, 80) ||
          !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 99) {
        throw new ApiError(422, '商品或购买数量无效。')
      }
      return {
        product_code: item.product_code, sku_code: item.sku_code,
        selections: selections(item.selections), quantity: item.quantity,
      }
    }),
    coupon_id: input.coupon_id,
  }
}

function selectionSignature(value) {
  return JSON.stringify(Object.keys(value).sort().map((key) => [key, [...value[key]].sort()]))
}

function normalizeQuote(value, input) {
  if (!record(value) || !Array.isArray(value.items) || value.items.length !== input.items.length ||
      !money(value.subtotal_cents) || !money(value.discount_cents) || !money(value.total_cents) ||
      value.discount_cents > value.subtotal_cents || value.total_cents !== value.subtotal_cents - value.discount_cents ||
      !(value.coupon_id === null || identifier(value.coupon_id)) || value.coupon_id !== input.coupon_id ||
      (value.coupon_id === null && value.discount_cents !== 0) || !Array.isArray(value.coupon_evaluations)) throw malformed()
  let subtotal = 0
  const items = value.items.map((item, index) => {
    const submitted = input.items[index]
    if (!record(item) || item.product_code !== submitted.product_code || item.sku_code !== submitted.sku_code ||
        item.quantity !== submitted.quantity || typeof item.name !== 'string' || !item.name.trim() || item.name.length > 160 ||
        typeof item.sku_name !== 'string' || !item.sku_name.trim() || item.sku_name.length > 160 ||
        !money(item.unit_price_cents) || !money(item.line_total_cents) ||
        item.line_total_cents !== item.unit_price_cents * item.quantity) throw malformed()
    const normalizedSelections = selections(item.selections, 502)
    if (selectionSignature(normalizedSelections) !== selectionSignature(submitted.selections)) throw malformed()
    subtotal += item.line_total_cents
    if (!Number.isSafeInteger(subtotal)) throw malformed()
    return {
      product_code: item.product_code, sku_code: item.sku_code, name: item.name, sku_name: item.sku_name,
      selections: normalizedSelections, quantity: item.quantity,
      unit_price_cents: item.unit_price_cents, line_total_cents: item.line_total_cents,
    }
  })
  if (subtotal !== value.subtotal_cents) throw malformed()
  const seen = new Set()
  const couponEvaluations = value.coupon_evaluations.map((item) => {
    if (!record(item) || !identifier(item.coupon_id) || seen.has(item.coupon_id) ||
        typeof item.title !== 'string' || !item.title.trim() || !money(item.min_spend_cents) ||
        !money(item.discount_cents) || item.discount_cents === 0 || typeof item.eligible !== 'boolean' ||
        !money(item.shortfall_cents) || typeof item.reason !== 'string' || !item.reason || item.reason.length > 80 ||
        item.shortfall_cents !== Math.max(0, item.min_spend_cents - subtotal) ||
        (item.eligible && item.shortfall_cents > 0)) throw malformed()
    seen.add(item.coupon_id)
    return {
      coupon_id: item.coupon_id, title: item.title, min_spend_cents: item.min_spend_cents,
      discount_cents: item.discount_cents, eligible: item.eligible,
      shortfall_cents: item.shortfall_cents, reason: item.reason,
    }
  })
  if (value.coupon_id !== null) {
    const chosen = couponEvaluations.find((item) => item.coupon_id === value.coupon_id)
    if (!chosen || !chosen.eligible || value.discount_cents !== Math.min(chosen.discount_cents, subtotal)) throw malformed()
  }
  return {
    items, subtotal_cents: subtotal, discount_cents: value.discount_cents,
    total_cents: value.total_cents, coupon_id: value.coupon_id, coupon_evaluations: couponEvaluations,
  }
}

async function quoteCart(input) {
  const session = readSession()
  if (!session || getAuthState().status !== 'authenticated') throw new ApiError(401, '请先通过微信登录。')
  const submitted = normalizeInput(input)
  try {
    const value = await request('/api/v1/mini/shop/cart/quote', {
      method: 'POST', headers: { Authorization: `Bearer ${session.accessToken}` }, data: submitted,
    })
    if (session.apiOrigin !== getApiBaseUrl() || readSession()?.accessToken !== session.accessToken ||
        getAuthState().status !== 'authenticated') throw new ApiError(409, '账户或接口地址已改变，请重新操作。')
    return normalizeQuote(value, submitted)
  } catch (error) {
    if (error.status === 401) expireSession(session.accessToken)
    throw error
  }
}

module.exports = { fetchAvailableCoupons, quoteCart }
