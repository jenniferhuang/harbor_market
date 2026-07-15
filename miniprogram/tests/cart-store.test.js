'use strict'

function productFixture(stock = 5) {
  return {
    product_code: 'TEA',
    name: '海港茶',
    base_price_cents: 300,
    stock_status: 'in_stock',
    images: [{ image_type: 'cover', url: 'https://shop.example/tea.webp' }],
    specifications: [
      {
        code: 'size',
        name: '杯型',
        selection_mode: 'single',
        required: true,
        min_select: 1,
        max_select: 1,
        options: [
          { code: 'large', name: '大杯', price_delta_cents: 100, is_default: true },
        ],
      },
      {
        code: 'addon',
        name: '加料',
        selection_mode: 'multiple',
        required: false,
        min_select: 0,
        max_select: 2,
        options: [
          { code: 'pearl', name: '珍珠', price_delta_cents: 50, is_default: true },
        ],
      },
    ],
    skus: [
      {
        sku_code: 'TEA-L',
        name: '大杯',
        price_cents: 400,
        stock_quantity: stock,
        attributes: { size: 'large' },
        is_default: true,
        is_active: true,
      },
    ],
  }
}

function setup(stored) {
  vi.resetModules()
  const storage = new Map()
  if (stored !== undefined) storage.set('harbor_market_cart', stored)
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
  }
  return { store: require('../src/state/cart-store'), storage }
}

describe('device-local cart store', () => {
  afterEach(() => {
    delete global.wx
    vi.restoreAllMocks()
  })

  it('adds, merges, prices, and persists a stable cart line', () => {
    const { store, storage } = setup()
    const product = productFixture()
    const selections = { size: ['large'], addon: ['pearl'] }

    let cart = store.addItem(store.loadCart(), product, selections, 1)
    cart = store.addItem(cart, product, selections, 2)

    expect(cart.items).toHaveLength(1)
    expect(cart.items[0]).toMatchObject({
      productCode: 'TEA',
      skuCode: 'TEA-L',
      quantity: 3,
      unitPriceCents: 450,
      selectionSummary: '杯型：大杯 / 加料：珍珠',
      stockQuantity: 5,
    })
    expect(storage.get('harbor_market_cart')).toEqual(cart)
    expect(store.cartSummary(cart)).toEqual({
      lineCount: 1,
      totalQuantity: 3,
      totalCents: 1_350,
    })
  })

  it('updates, removes, and clears persisted lines without mutating prior state', () => {
    const { store } = setup()
    const selections = { size: ['large'], addon: [] }
    const original = store.addItem(store.loadCart(), productFixture(), selections)
    const key = original.items[0].key
    const updated = store.updateQuantity(original, key, 2)

    expect(original.items[0].quantity).toBe(1)
    expect(updated.items[0].quantity).toBe(2)
    expect(store.updateQuantity(updated, key, 0).items).toEqual([])
    expect(store.removeItem(updated, key).items).toEqual([])
    expect(store.clearCart()).toEqual({ version: 1, items: [] })
  })

  it('fails closed for malformed or unknown stored schema versions', () => {
    expect(setup('{bad json').store.loadCart()).toEqual({ version: 1, items: [] })
    expect(setup({ version: 2, items: [{ key: 'legacy' }] }).store.loadCart()).toEqual({
      version: 1,
      items: [],
    })
  })

  it('allowlists restored fields and discards prototype-like stored lines', () => {
    const { store, storage } = setup()
    store.addItem(
      store.loadCart(),
      productFixture(),
      { size: ['large'], addon: [] },
    )
    const stored = storage.get('harbor_market_cart')
    stored.items[0].serverCalculatedTotal = 1
    storage.set('harbor_market_cart', stored)

    const restored = store.loadCart()
    expect(restored.items).toHaveLength(1)
    expect(restored.items[0]).not.toHaveProperty('serverCalculatedTotal')

    storage.set('harbor_market_cart', {
      version: 1,
      items: [restored.items[0], { ...restored.items[0] }],
    })
    expect(store.loadCart().items).toHaveLength(1)

    const inherited = Object.assign({ __proto__: { injected: true } }, restored.items[0])
    Object.setPrototypeOf(inherited, { injected: true })
    storage.set('harbor_market_cart', { version: 1, items: [inherited] })
    expect(store.loadCart()).toEqual({ version: 1, items: [] })
    expect({}.injected).toBeUndefined()
  })

  it('rejects invalid selections and quantities beyond SKU stock', () => {
    const { store } = setup()
    const product = productFixture(2)
    expect(() => store.addItem(store.loadCart(), product, { size: [], addon: [] })).toThrow(
      '请选择杯型',
    )
    expect(() =>
      store.addItem(
        store.loadCart(),
        product,
        { size: ['large'], addon: [] },
        3,
      ),
    ).toThrow('购买数量超过当前库存')

    product.skus[0].stock_quantity = -1
    expect(() =>
      store.addItem(
        store.loadCart(),
        product,
        { size: ['large'], addon: [] },
      ),
    ).toThrow('商品库存数据无效')
  })

  it('surfaces persistence failures rather than pretending a cart was saved', () => {
    vi.resetModules()
    global.wx = {
      getStorageSync: vi.fn(),
      setStorageSync: vi.fn(() => {
        throw new Error('quota')
      }),
    }
    const store = require('../src/state/cart-store')
    expect(() => store.clearCart()).toThrow('购物车保存失败')
  })
})
