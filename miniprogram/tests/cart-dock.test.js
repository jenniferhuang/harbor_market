const path = require('node:path')

function fixture(code = 'TEA') {
  return {
    product_code: code, name: '海港茶', base_price_cents: 350,
    stock_status: 'in_stock', specifications: [],
    images: [{ image_type: 'cover', url: '/api/v1/media/tea' }],
    skus: [{ sku_code: code + '-ONE', name: '一份', price_cents: 350,
      stock_quantity: 5, attributes: {}, is_default: true, is_active: true }],
  }
}

function setup() {
  vi.resetModules()
  const root = path.resolve(__dirname, '../src') + path.sep
  for (const key of Object.keys(require.cache)) if (key.startsWith(root)) delete require.cache[key]
  const storage = new Map([['harbor_market_api_base_url', 'https://shop.example.cn']])
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    showModal: vi.fn(),
    navigateTo: vi.fn(),
  }
  let definition
  global.Component = (value) => { definition = value }
  require('../src/components/cart-dock/cart-dock')
  const store = require('../src/state/cart-store')
  const component = {
    ...definition.methods,
    data: structuredClone(definition.data),
    properties: { insetBottom: 0 },
    setData(changes) { Object.assign(this.data, changes) },
    triggerEvent: vi.fn(),
  }
  definition.lifetimes.attached.call(component)
  return { component, store, definition, api: global.wx }
}

describe('catalog and product shared cart dock', () => {
  afterEach(() => { delete global.wx; delete global.Component; vi.restoreAllMocks() })

  it('refreshes device storage on show, formats integer totals and expands the selected items', () => {
    const { component, store, definition } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 2)
    definition.pageLifetimes.show.call(component)
    expect(component.data).toMatchObject({ totalQuantity: 2, displayTotal: '¥7.00' })
    expect(component.data.items[0]).toMatchObject({
      displayPrice: '¥3.50', coverUrl: 'https://shop.example.cn/api/v1/media/tea', maxQuantity: 5,
    })
    component.toggle()
    expect(component.data.expanded).toBe(true)
    component.close()
    expect(component.data.expanded).toBe(false)
  })

  it('changing a displayed line preserves products added since the last refresh', () => {
    const { component, store } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 2)
    component.refresh()
    const key = component.data.items[0].key
    store.addItem(store.loadCart(), fixture('CAKE'), {}, 1)
    component.changeQuantity({ currentTarget: { dataset: { key } }, detail: { value: 3 } })
    expect(store.loadCart().items.map((item) => item.quantity)).toEqual([3, 1])
    expect(component.data.displayTotal).toBe('¥14.00')
    component.changeQuantity({ currentTarget: { dataset: { key } }, detail: { value: 0 } })
    expect(store.loadCart().items.map((item) => item.productCode)).toEqual(['CAKE'])
  })

  it('does not change displayed quantities after a stock or persistence failure', () => {
    const { component, store, api } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 1)
    component.refresh()
    const key = component.data.items[0].key
    const event = (value) => ({ currentTarget: { dataset: { key } }, detail: { value } })
    component.changeQuantity(event(6))
    expect(api.showModal).toHaveBeenLastCalledWith(expect.objectContaining({ content: '购买数量超过当前库存' }))
    api.setStorageSync.mockImplementation(() => { throw new Error('quota') })
    component.changeQuantity(event(2))
    expect(api.showModal).toHaveBeenLastCalledWith(expect.objectContaining({ content: '购物车保存失败，请检查设备存储空间。' }))
    expect(component.data.items[0].quantity).toBe(1)
    expect(store.loadCart().items[0].quantity).toBe(1)
  })

  it('limits customization lines to their shared SKU stock remaining', () => {
    const { component, store, api } = setup()
    const product = fixture()
    product.specifications = [{ code: 'wrap', name: '包装', selection_mode: 'multiple',
      required: false, min_select: 0, max_select: 1,
      options: [{ code: 'gift', name: '礼品包装', price_delta_cents: 50 }] }]
    store.addItem(store.loadCart(), product, { wrap: [] }, 2)
    store.addItem(store.loadCart(), product, { wrap: ['gift'] }, 2)
    component.refresh()
    expect(component.data.items.map((item) => item.maxQuantity)).toEqual([3, 3])
    component.changeQuantity({ currentTarget: { dataset: { key: component.data.items[0].key } }, detail: { value: 4 } })
    expect(store.loadCart().items.map((item) => item.quantity)).toEqual([2, 2])
    expect(api.showModal).toHaveBeenLastCalledWith(expect.objectContaining({ content: '购买数量超过当前库存' }))
  })

  it('requires confirmation before clearing and ignores confirmation after detach', () => {
    const { component, store, api, definition } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 1)
    component.refresh()
    component.clearAll()
    component.clearAll()
    expect(api.showModal).toHaveBeenCalledTimes(1)
    let modal = api.showModal.mock.calls[0][0]
    modal.success({ confirm: false })
    modal.complete()
    expect(store.loadCart().items).toHaveLength(1)
    component.clearAll()
    modal = api.showModal.mock.calls[1][0]
    definition.lifetimes.detached.call(component)
    modal.success({ confirm: true })
    modal.complete()
    expect(store.loadCart().items).toHaveLength(1)
  })

  it('clears persisted items and updates the dock after confirmation', () => {
    const { component, store, api } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 1)
    component.refresh()
    component.clearAll()
    api.showModal.mock.calls[0][0].success({ confirm: true })
    expect(component.data).toMatchObject({ items: [], totalQuantity: 0, displayTotal: '¥0.00' })
    expect(store.loadCart().items).toEqual([])
  })

  it('navigates to checkout only when the current stored cart has selected items', () => {
    const { component, store, api } = setup()
    component.checkout()
    expect(api.navigateTo).not.toHaveBeenCalled()
    store.addItem(store.loadCart(), fixture(), {}, 1)
    component.checkout()
    expect(api.navigateTo).toHaveBeenCalledWith({ url: '/pages/checkout/checkout' })
    store.setAllSelected(store.loadCart(), false)
    api.navigateTo.mockClear()
    component.checkout()
    expect(component.data).toMatchObject({ totalQuantity: 1, selectedQuantity: 0, displayTotal: '¥0.00' })
    expect(api.navigateTo).not.toHaveBeenCalled()
    store.clearCart()
    component.checkout()
    expect(api.navigateTo).not.toHaveBeenCalled()
    expect(api.showModal).not.toHaveBeenCalled()
  })

  it('keeps the badge quantity for every line while selection changes only the displayed total', () => {
    const { component, store } = setup()
    store.addItem(store.loadCart(), fixture(), {}, 2)
    store.addItem(store.loadCart(), fixture('CAKE'), {}, 1)
    component.refresh()
    component.changeSelection({ currentTarget: { dataset: { key: component.data.items[0].key } } })
    expect(component.data).toMatchObject({ totalQuantity: 3, selectedQuantity: 1, displayTotal: '¥3.50' })
    expect(store.loadCart().items.map((item) => item.selected)).toEqual([false, true])
    expect(component.triggerEvent).toHaveBeenCalledWith('change', { totalQuantity: 3 })
  })
})
