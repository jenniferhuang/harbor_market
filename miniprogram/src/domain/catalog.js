'use strict'

function specificationsFor(product) {
  return Array.isArray(product?.specifications) ? product.specifications : []
}

function activeSkus(product) {
  return (Array.isArray(product?.skus) ? product.skus : [])
    .filter((sku) => sku && sku.is_active !== false)
    .slice()
    .sort((left, right) => {
      if (Boolean(left.is_default) !== Boolean(right.is_default)) {
        return left.is_default ? -1 : 1
      }
      const order = numberOr(left.sort_order, 0) - numberOr(right.sort_order, 0)
      return order || numberOr(left.id, 0) - numberOr(right.id, 0)
    })
}

function numberOr(value, fallback) {
  return Number.isFinite(value) ? value : fallback
}

function selectionValues(selections, specificationCode) {
  const values = selections && Array.isArray(selections[specificationCode])
    ? selections[specificationCode]
    : []
  return [...new Set(values.filter((value) => typeof value === 'string'))]
}

function initialSelections(product) {
  return specificationsFor(product).reduce((selections, specification) => {
    const options = Array.isArray(specification.options) ? specification.options : []
    const defaults = options
      .filter((option) => option && option.is_default)
      .sort((left, right) => numberOr(left.sort, 0) - numberOr(right.sort, 0))
      .map((option) => option.code)

    selections[specification.code] = specification.selection_mode === 'single'
      ? defaults.slice(0, 1)
      : defaults.slice(0, numberOr(specification.max_select, defaults.length))
    return selections
  }, {})
}

function toggleSelection(product, selections, specificationCode, optionCode) {
  const specifications = specificationsFor(product)
  const specification = specifications.find(
    (item) => item.code === specificationCode,
  )
  const next = Object.fromEntries(
    specifications.map((item) => [item.code, selectionValues(selections, item.code)]),
  )
  if (!specification || !Array.isArray(specification.options)) return next
  if (!specification.options.some((option) => option.code === optionCode)) return next

  const current = selectionValues(selections, specificationCode)
  const selected = current.includes(optionCode)
  if (specification.selection_mode === 'single') {
    if (selected && !specification.required && numberOr(specification.min_select, 0) === 0) {
      next[specificationCode] = []
    } else {
      next[specificationCode] = [optionCode]
    }
    return next
  }

  if (selected) {
    next[specificationCode] = current.filter((value) => value !== optionCode)
    return next
  }
  const max = numberOr(specification.max_select, specification.options.length)
  next[specificationCode] = current.length < max ? [...current, optionCode] : current
  return next
}

function validateSelections(product, selections) {
  for (const specification of specificationsFor(product)) {
    const selected = selectionValues(selections, specification.code)
    const known = new Set(
      (Array.isArray(specification.options) ? specification.options : []).map(
        (option) => option.code,
      ),
    )
    if (selected.some((optionCode) => !known.has(optionCode))) {
      return { valid: false, message: `${specification.name}包含无效选项` }
    }

    const minimum = numberOr(
      specification.min_select,
      specification.required ? 1 : 0,
    )
    const maximum = specification.selection_mode === 'single'
      ? 1
      : numberOr(specification.max_select, known.size)
    if (selected.length < minimum) {
      return {
        valid: false,
        message: minimum === 1
          ? `请选择${specification.name}`
          : `${specification.name}至少选择${minimum}项`,
      }
    }
    if (selected.length > maximum) {
      return { valid: false, message: `${specification.name}最多选择${maximum}项` }
    }
  }

  const skus = activeSkus(product)
  if (skus.length > 0 && resolveSku(product, selections) === null) {
    return { valid: false, message: '所选规格暂不可售' }
  }
  return { valid: true, message: '' }
}

function resolveSku(product, selections) {
  const skus = activeSkus(product)
  if (skus.length === 0) return null

  const variantCodes = new Set(
    skus.flatMap((sku) => Object.keys(sku.attributes || {})),
  )
  if (variantCodes.size === 0) return skus[0]

  const matching = skus.filter((sku) => {
    const attributes = sku.attributes || {}
    return [...variantCodes].every((code) => {
      const selected = selectionValues(selections, code)
      if (!Object.prototype.hasOwnProperty.call(attributes, code)) {
        return selected.length === 0
      }
      return selected.length === 1 && selected[0] === attributes[code]
    })
  })
  return matching[0] || null
}

function calculateUnitPrice(product, selections, sku) {
  const selectedSku = sku === undefined ? resolveSku(product, selections) : sku
  const basePrice = selectedSku ? selectedSku.price_cents : product?.base_price_cents
  if (!Number.isSafeInteger(basePrice)) {
    throw new TypeError('Product or SKU price must be a safe integer')
  }

  // SKU prices already represent the variant attributes that select that SKU.
  // Only pure customization/add-on options contribute their price deltas here.
  const skuAttributeCodes = new Set(Object.keys(selectedSku?.attributes || {}))
  let total = basePrice
  for (const specification of specificationsFor(product)) {
    if (skuAttributeCodes.has(specification.code)) continue
    const selected = new Set(selectionValues(selections, specification.code))
    for (const option of Array.isArray(specification.options) ? specification.options : []) {
      if (!selected.has(option.code)) continue
      if (!Number.isSafeInteger(option.price_delta_cents)) {
        throw new TypeError('Specification price delta must be a safe integer')
      }
      total += option.price_delta_cents
      if (!Number.isSafeInteger(total)) {
        throw new RangeError('Calculated product price exceeds the safe integer range')
      }
    }
  }
  return Math.max(0, total)
}

function selectionSummary(product, selections) {
  return specificationsFor(product)
    .map((specification) => {
      const selected = new Set(selectionValues(selections, specification.code))
      const names = (Array.isArray(specification.options) ? specification.options : [])
        .filter((option) => selected.has(option.code))
        .sort((left, right) => numberOr(left.sort, 0) - numberOr(right.sort, 0))
        .map((option) => option.name)
      return names.length > 0 ? `${specification.name}：${names.join('、')}` : ''
    })
    .filter(Boolean)
    .join(' / ')
}

module.exports = {
  calculateUnitPrice,
  initialSelections,
  resolveSku,
  selectionSummary,
  toggleSelection,
  validateSelections,
}
