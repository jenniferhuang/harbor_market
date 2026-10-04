'use strict'

const { ApiError, absoluteMediaUrl, request } = require('./client')
const { normalizeProduct } = require('./catalog')

function requireArray(value) {
  if (!Array.isArray(value)) throw new ApiError(502, '店铺数据格式不正确，请稍后重试。')
  return value
}

function normalizeStore(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ApiError(502, '商家信息格式不正确。')
  }
  return { ...value, announcement_image_url: absoluteMediaUrl(value.announcement_image_url || '') }
}

async function fetchHome() {
  const value = await request('/api/v1/shop/home')
  if (!value || typeof value !== 'object') throw new ApiError(502, '首页数据格式不正确。')
  return {
    store: normalizeStore(value.store),
    carousel: requireArray(value.carousel).map((item) => ({ ...item, url: absoluteMediaUrl(item.url || '') })),
    coupons: requireArray(value.coupons),
    categories: requireArray(value.categories).map((item) => ({ ...item, image_url: absoluteMediaUrl(item.image_url || '') })),
    hot_products: requireArray(value.hot_products).map((item) => ({ ...item, product: normalizeProduct(item.product) })),
    hot_products_source: ['sales', 'featured', 'newest'].includes(value.hot_products_source)
      ? value.hot_products_source : 'newest',
  }
}

async function fetchHotSearches() {
  return requireArray(await request('/api/v1/shop/hot-searches')).map((item) => ({
    ...item, product: normalizeProduct(item.product),
  }))
}

async function searchProducts(query, options = {}) {
  const q = typeof query === 'string' ? query.trim() : ''
  const pageSize = options.page_size ?? 10
  if (!q || [...q].length > 160) throw new ApiError(422, '请输入一百六十个字以内的搜索关键词。')
  if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
    throw new ApiError(422, '每页商品数量无效。')
  }
  const value = await request('/api/v1/shop/search', {
    method: 'POST', data: { q, page: 1, page_size: pageSize },
  })
  if (!value || typeof value !== 'object') throw new ApiError(502, '搜索结果格式不正确。')
  return { ...value, items: requireArray(value.items).map(normalizeProduct) }
}

module.exports = { fetchHome, fetchHotSearches, normalizeStore, searchProducts }
