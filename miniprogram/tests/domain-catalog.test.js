'use strict'

const {
  calculateUnitPrice,
  initialSelections,
  resolveSku,
  selectionSummary,
  toggleSelection,
  validateSelections,
} = require('../src/domain/catalog')

function productFixture() {
  return {
    product_code: 'LATTE',
    name: '海港拿铁',
    base_price_cents: 500,
    stock_status: 'in_stock',
    specifications: [
      {
        code: 'temperature',
        name: '温度',
        selection_mode: 'single',
        required: true,
        min_select: 1,
        max_select: 1,
        options: [
          { code: 'iced', name: '冰', price_delta_cents: 200, sort: 0, is_default: true },
          { code: 'hot', name: '热', price_delta_cents: 300, sort: 1, is_default: false },
        ],
      },
      {
        code: 'addons',
        name: '加料',
        selection_mode: 'multiple',
        required: false,
        min_select: 0,
        max_select: 2,
        options: [
          { code: 'pearl', name: '珍珠', price_delta_cents: 50, sort: 0, is_default: true },
          { code: 'cream', name: '奶盖', price_delta_cents: 100, sort: 1, is_default: false },
          { code: 'jelly', name: '椰果', price_delta_cents: 80, sort: 2, is_default: false },
        ],
      },
    ],
    skus: [
      {
        id: 1,
        sku_code: 'LATTE-ICED',
        name: '冰拿铁',
        price_cents: 500,
        stock_quantity: 8,
        attributes: { temperature: 'iced' },
        is_default: true,
        is_active: true,
        sort_order: 0,
      },
      {
        id: 2,
        sku_code: 'LATTE-HOT',
        name: '热拿铁',
        price_cents: 550,
        stock_quantity: 4,
        attributes: { temperature: 'hot' },
        is_default: false,
        is_active: true,
        sort_order: 1,
      },
    ],
  }
}

describe('catalog selection domain', () => {
  it('builds defaults and changes single/multiple selections immutably', () => {
    const product = productFixture()
    const defaults = initialSelections(product)
    expect(defaults).toEqual({ temperature: ['iced'], addons: ['pearl'] })

    const hot = toggleSelection(product, defaults, 'temperature', 'hot')
    expect(hot).toEqual({ temperature: ['hot'], addons: ['pearl'] })
    expect(defaults.temperature).toEqual(['iced'])

    const twoAddons = toggleSelection(product, hot, 'addons', 'cream')
    const atMaximum = toggleSelection(product, twoAddons, 'addons', 'jelly')
    expect(twoAddons.addons).toEqual(['pearl', 'cream'])
    expect(atMaximum.addons).toEqual(['pearl', 'cream'])
    expect(toggleSelection(product, twoAddons, 'addons', 'pearl').addons).toEqual(['cream'])
  })

  it('validates required options and resolves only an exact active SKU', () => {
    const product = productFixture()
    expect(validateSelections(product, { temperature: [], addons: [] })).toEqual({
      valid: false,
      message: '请选择温度',
    })
    expect(resolveSku(product, { temperature: ['hot'], addons: [] })).toMatchObject({
      sku_code: 'LATTE-HOT',
    })
    expect(resolveSku(product, { temperature: ['warm'], addons: [] })).toBeNull()
    expect(validateSelections(product, { temperature: ['warm'], addons: [] }).valid).toBe(false)
  })

  it('uses SKU price for variant attributes and adds only customization deltas', () => {
    const product = productFixture()
    const selections = { temperature: ['hot'], addons: ['pearl', 'cream'] }
    const sku = resolveSku(product, selections)

    // Temperature selects a SKU whose 550-fen price already includes that
    // variant. Only the 50 + 100 fen add-ons are added.
    expect(calculateUnitPrice(product, selections, sku)).toBe(700)
    expect(selectionSummary(product, selections)).toBe('温度：热 / 加料：珍珠、奶盖')
  })

  it('floors negative customization totals at zero and rejects unsafe arithmetic', () => {
    const product = productFixture()
    product.skus[0].price_cents = 50
    product.specifications[1].options[0].price_delta_cents = -100
    expect(calculateUnitPrice(product, initialSelections(product), product.skus[0])).toBe(0)

    product.specifications[1].options[0].price_delta_cents = Number.MAX_SAFE_INTEGER
    product.skus[0].price_cents = 1
    expect(() => calculateUnitPrice(product, initialSelections(product), product.skus[0])).toThrow(
      RangeError,
    )
  })
})
