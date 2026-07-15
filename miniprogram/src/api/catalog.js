'use strict'

const { ApiError, absoluteMediaUrl, request } = require('./client')

function normalizeImage(image) {
  if (!image || typeof image !== 'object') return image
  return {
    ...image,
    url: absoluteMediaUrl(image.url || ''),
  }
}

function normalizeProduct(product) {
  if (!product || typeof product !== 'object' || Array.isArray(product)) {
    throw new ApiError(502, '商品数据格式不正确。')
  }
  return {
    ...product,
    images: Array.isArray(product.images) ? product.images.map(normalizeImage) : [],
    skus: Array.isArray(product.skus) ? product.skus : [],
    specifications: Array.isArray(product.specifications) ? product.specifications : [],
  }
}

function productQuery(filters) {
  const values = {
    page: filters.page ?? 1,
    page_size: filters.page_size ?? 10,
  }
  if (!Number.isInteger(values.page) || values.page < 1) {
    throw new TypeError('page must be a positive integer')
  }
  if (!Number.isInteger(values.page_size) || values.page_size < 1 || values.page_size > 100) {
    throw new TypeError('page_size must be an integer between 1 and 100')
  }

  const pairs = []
  if (typeof filters.q === 'string' && filters.q.trim()) {
    pairs.push(['q', filters.q.trim()])
  }
  if (typeof filters.category === 'string' && filters.category.trim()) {
    pairs.push(['category', filters.category.trim()])
  }
  pairs.push(['page', String(values.page)], ['page_size', String(values.page_size)])
  return pairs.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join('&')
}

async function fetchCategories() {
  const categories = await request('/api/v1/catalog/categories')
  if (!Array.isArray(categories)) throw new ApiError(502, '类目数据格式不正确。')
  return categories
}

async function fetchProducts(filters = {}) {
  const payload = await request(`/api/v1/catalog/products?${productQuery(filters)}`)
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.items)) {
    throw new ApiError(502, '商品列表数据格式不正确。')
  }
  return {
    items: payload.items.map(normalizeProduct),
    total: Number.isInteger(payload.total) ? payload.total : payload.items.length,
    page: Number.isInteger(payload.page) ? payload.page : filters.page ?? 1,
    page_size: Number.isInteger(payload.page_size) ? payload.page_size : filters.page_size ?? 10,
  }
}

async function fetchProduct(productCode) {
  if (typeof productCode !== 'string' || !productCode.trim()) {
    throw new TypeError('productCode is required')
  }
  const product = await request(
    `/api/v1/catalog/products/${encodeURIComponent(productCode.trim().toUpperCase())}`,
  )
  return normalizeProduct(product)
}

module.exports = {
  fetchCategories,
  fetchProduct,
  fetchProducts,
}
