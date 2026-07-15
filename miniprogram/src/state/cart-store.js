'use strict'

const {
  calculateUnitPrice,
  resolveSku,
  selectionSummary,
  validateSelections,
} = require('../domain/catalog')

const CART_STORAGE_KEY = 'harbor_market_cart'
const CART_VERSION = 1
const MAX_ITEM_QUANTITY = 99
const MAX_CART_LINES = 100

function emptyCart() {
  return { version: CART_VERSION, items: [] }
}

function miniProgramApi() {
  return typeof wx === 'undefined' ? null : wx
}

function safeInteger(value, fallback = null) {
  return Number.isSafeInteger(value) ? value : fallback
}

function boundedString(value, maximum, allowEmpty = false) {
  return typeof value === 'string' &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0)
}

function sanitizeSelections(selections) {
  if (!selections || typeof selections !== 'object' || Array.isArray(selections)) return null
  const prototype = Object.getPrototypeOf(selections)
  if (prototype !== Object.prototype && prototype !== null) return null
  const entries = Object.entries(selections)
  if (entries.length > 20) return null

  const sanitized = {}
  for (const [code, values] of entries) {
    if (
      ['constructor', 'prototype', '__proto__'].includes(code) ||
      !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(code) ||
      !Array.isArray(values)
    ) {
      return null
    }
    if (values.length > 50) return null
    const options = values.filter(
      (value) =>
        typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(value),
    )
    if (options.length !== values.length || new Set(options).size !== options.length) return null
    sanitized[code] = [...options]
  }
  return sanitized
}

function sanitizeStoredItem(item) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) return null
  const prototype = Object.getPrototypeOf(item)
  if (prototype !== Object.prototype && prototype !== null) return null
  const selections = sanitizeSelections(item.selections)
  if (
    selections === null ||
    !boundedString(item.key, 24_000) ||
    !boundedString(item.productCode, 64) ||
    !boundedString(item.productName, 160) ||
    !boundedString(item.skuCode, 80) ||
    !boundedString(item.skuName, 160) ||
    !boundedString(item.coverUrl, 2_048, true) ||
    !boundedString(item.selectionSummary, 20_000, true) ||
    !Number.isSafeInteger(item.unitPriceCents) ||
    item.unitPriceCents < 0 ||
    !Number.isInteger(item.quantity) ||
    item.quantity < 1 ||
    item.quantity > MAX_ITEM_QUANTITY ||
    !(
      item.stockQuantity === null ||
      (Number.isSafeInteger(item.stockQuantity) && item.stockQuantity >= 0)
    ) ||
    item.key !== itemKey(item.productCode, item.skuCode, selections)
  ) {
    return null
  }

  // Reconstruct the persisted line from an explicit allowlist so stale or
  // attacker-controlled storage fields never flow into page state.
  return {
    key: item.key,
    productCode: item.productCode,
    productName: item.productName,
    skuCode: item.skuCode,
    skuName: item.skuName,
    coverUrl: item.coverUrl,
    selections,
    selectionSummary: item.selectionSummary,
    unitPriceCents: item.unitPriceCents,
    quantity: item.quantity,
    stockQuantity: item.stockQuantity,
  }
}

function normalizeCart(cart) {
  if (!cart || cart.version !== CART_VERSION || !Array.isArray(cart.items)) return emptyCart()
  const seenKeys = new Set()
  const items = cart.items
    .slice(0, MAX_CART_LINES)
    .map(sanitizeStoredItem)
    .filter(Boolean)
    .filter((item) => {
      if (seenKeys.has(item.key)) return false
      seenKeys.add(item.key)
      return true
    })
  return {
    version: CART_VERSION,
    items,
  }
}

function loadCart() {
  const api = miniProgramApi()
  if (!api || typeof api.getStorageSync !== 'function') return emptyCart()

  try {
    const stored = api.getStorageSync(CART_STORAGE_KEY)
    const parsed = typeof stored === 'string' ? JSON.parse(stored) : stored
    return normalizeCart(parsed)
  } catch {
    return emptyCart()
  }
}

function persist(cart) {
  const normalized = normalizeCart(cart)
  const api = miniProgramApi()
  if (!api || typeof api.setStorageSync !== 'function') return normalized

  try {
    api.setStorageSync(CART_STORAGE_KEY, normalized)
  } catch (error) {
    const persistenceError = new Error('购物车保存失败，请检查设备存储空间。')
    persistenceError.cause = error
    throw persistenceError
  }
  return normalized
}

function cloneSelections(selections) {
  return sanitizeSelections(selections) || {}
}

function itemKey(productCode, skuCode, selections) {
  const signature = Object.keys(selections)
    .sort()
    .map((code) => `${code}=${[...selections[code]].sort().join(',')}`)
    .join('&')
  return `${productCode}::${skuCode}::${signature}`
}

function coverUrl(product) {
  const images = Array.isArray(product?.images) ? product.images : []
  return images.find((image) => image.image_type === 'cover')?.url || images[0]?.url || ''
}

function positiveQuantity(quantity) {
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > MAX_ITEM_QUANTITY) {
    throw new RangeError(`quantity must be an integer between 1 and ${MAX_ITEM_QUANTITY}`)
  }
  return quantity
}

function addItem(cart, product, selections, quantity = 1) {
  positiveQuantity(quantity)
  const validation = validateSelections(product, selections)
  if (!validation.valid) throw new Error(validation.message)
  if (product?.stock_status === 'out_of_stock') throw new Error('商品暂时缺货')

  const sku = resolveSku(product, selections)
  if (!sku) throw new Error('所选规格暂不可售')
  const stockQuantity = safeInteger(sku.stock_quantity)
  if (stockQuantity === null || stockQuantity < 0) throw new Error('商品库存数据无效')
  if (stockQuantity === 0) throw new Error('所选规格暂时缺货')

  const normalized = normalizeCart(cart)
  const copiedSelections = cloneSelections(selections)
  const key = itemKey(product.product_code, sku.sku_code, copiedSelections)
  const existingIndex = normalized.items.findIndex((item) => item.key === key)
  const existingQuantity = existingIndex >= 0 ? normalized.items[existingIndex].quantity : 0
  const nextQuantity = existingQuantity + quantity
  if (nextQuantity > MAX_ITEM_QUANTITY) {
    throw new RangeError(`单件商品最多购买${MAX_ITEM_QUANTITY}件`)
  }
  if (stockQuantity !== null && nextQuantity > stockQuantity) {
    throw new RangeError('购买数量超过当前库存')
  }

  const item = {
    key,
    productCode: product.product_code,
    productName: product.name,
    skuCode: sku.sku_code,
    skuName: sku.name,
    coverUrl: coverUrl(product),
    selections: copiedSelections,
    selectionSummary: selectionSummary(product, copiedSelections),
    unitPriceCents: calculateUnitPrice(product, copiedSelections, sku),
    quantity: nextQuantity,
    stockQuantity,
  }
  const safeItem = sanitizeStoredItem(item)
  if (!safeItem) throw new Error('商品数据格式不正确，无法加入购物车')
  const items = normalized.items.slice()
  if (existingIndex >= 0) items[existingIndex] = safeItem
  else {
    if (items.length >= MAX_CART_LINES) throw new RangeError('购物车商品种类已达到上限')
    items.push(safeItem)
  }
  return persist({ version: CART_VERSION, items })
}

function updateQuantity(cart, key, quantity) {
  if (!Number.isInteger(quantity)) throw new TypeError('quantity must be an integer')
  if (quantity <= 0) return removeItem(cart, key)
  positiveQuantity(quantity)

  const normalized = normalizeCart(cart)
  const index = normalized.items.findIndex((item) => item.key === key)
  if (index < 0) return normalized
  const item = normalized.items[index]
  if (item.stockQuantity !== null && quantity > item.stockQuantity) {
    throw new RangeError('购买数量超过当前库存')
  }
  const items = normalized.items.slice()
  items[index] = { ...item, quantity }
  return persist({ version: CART_VERSION, items })
}

function removeItem(cart, key) {
  const normalized = normalizeCart(cart)
  return persist({
    version: CART_VERSION,
    items: normalized.items.filter((item) => item.key !== key),
  })
}

function clearCart() {
  return persist(emptyCart())
}

function cartSummary(cart) {
  const normalized = normalizeCart(cart)
  let totalQuantity = 0
  let totalCents = 0
  for (const item of normalized.items) {
    totalQuantity += item.quantity
    totalCents += item.unitPriceCents * item.quantity
    if (!Number.isSafeInteger(totalQuantity) || !Number.isSafeInteger(totalCents)) {
      throw new RangeError('Cart total exceeds the safe integer range')
    }
  }
  return {
    lineCount: normalized.items.length,
    totalQuantity,
    totalCents,
  }
}

module.exports = {
  addItem,
  cartSummary,
  clearCart,
  loadCart,
  removeItem,
  updateQuantity,
}
