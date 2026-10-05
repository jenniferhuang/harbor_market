'use strict'

const path = require('node:path')

function product(code = 'TEA') {
  return {
    product_code: code, name: '海港茶', stock_status: 'in_stock', base_price_cents: 300,
    specifications: [{ code: 'addon', name: '加料', selection_mode: 'multiple', min_select: 0, max_select: 1,
      options: [{ code: 'pearl', name: '珍珠', price_delta_cents: 50 }] }],
    skus: [{ sku_code: code + '-ONE', name: '一杯', is_active: true, price_cents: 300, stock_quantity: 5, attributes: {} }],
  }
}

function setup() {
  const root = path.resolve(__dirname, '../src') + path.sep
  for (const key of Object.keys(require.cache)) if (key.startsWith(root)) delete require.cache[key]
  const storage = new Map([['harbor_market_api_base_url', 'https://shop.example.cn']])
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)), setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    showModal: vi.fn(), switchTab: vi.fn(),
  }
  let definition
  global.Page = (value) => { definition = value }
  require('../src/pages/cart/cart')
  const page = { ...definition, data: structuredClone(definition.data), setData(changes) { Object.assign(this.data, changes) } }
  return { page, store: require('../src/state/cart-store'), api: global.wx }
}

afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

describe('cart page shared stock and fresh persistence', () => {
  it('shows remaining SKU stock per customization and preserves later additions during quantity changes', () => {
    const { page, store } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    store.addItem(store.loadCart(), product(), { addon: ['pearl'] }, 2)
    page.onShow()
    expect(page.data.items.map((item) => item.maxQuantity)).toEqual([3, 3])
    const key = page.data.items[0].key
    store.addItem(store.loadCart(), product('CAKE'), { addon: [] }, 1)
    page.changeQuantity({ currentTarget: { dataset: { key } }, detail: { value: 3 } })
    expect(store.loadCart().items.map((item) => item.quantity)).toEqual([3, 2, 1])
    expect(page.data.summary.totalQuantity).toBe(6)
    expect(page.data.items[1].maxQuantity).toBe(2)
  })

  it('removes from the current cart after modal confirmation and preserves intervening additions', () => {
    const { page, store, api } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] }, 1)
    page.onShow()
    page.removeLine({ currentTarget: { dataset: { key: page.data.items[0].key } } })
    store.addItem(store.loadCart(), product('CAKE'), { addon: [] }, 1)
    api.showModal.mock.calls[0][0].success({ confirm: true })
    expect(store.loadCart().items.map((item) => item.productCode)).toEqual(['CAKE'])
    expect(page.data.items.map((item) => item.productCode)).toEqual(['CAKE'])
  })
})
