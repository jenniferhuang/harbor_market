'use strict'

const path = require('node:path')

function product(code = 'TEA') {
  return {
    product_code: code, name: '海港茶', stock_status: 'in_stock', base_price_cents: 300,
    status: 'published', category: { name: '茶饮', is_active: true },
    specifications: [{ code: 'addon', name: '加料', selection_mode: 'multiple', min_select: 0, max_select: 1,
      options: [{ code: 'pearl', name: '珍珠', price_delta_cents: 50 }] }],
    skus: [{ sku_code: code + '-ONE', name: '一杯', is_active: true, price_cents: 300, stock_quantity: 5, attributes: {} }],
  }
}

function coupon(id = 1, changes = {}) {
  return { coupon_id: id, title: '满减好券', is_active: true, min_spend_cents: 600, discount_cents: 200,
    starts_at: null, expires_at: null, claimed_at: '2026-01-01T00:00:00Z', ...changes }
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

async function flush() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve()
}

function setup({ loggedIn = false, coupons = [], products = [] } = {}) {
  const root = path.resolve(__dirname, '../src') + path.sep
  for (const key of Object.keys(require.cache)) if (key.startsWith(root)) delete require.cache[key]
  const storage = new Map([['harbor_market_api_base_url', 'https://shop.example.cn']])
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)), setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    showModal: vi.fn(), switchTab: vi.fn(), navigateTo: vi.fn(),
  }
  const client = require('../src/api/client')
  let session = loggedIn ? { apiOrigin: client.getApiBaseUrl(), customer: { id: 1 }, accessToken: 'initial-token' } : null
  const listeners = new Set()
  const auth = {
    getAuthState: vi.fn(() => ({ status: session ? 'authenticated' : 'guest', customer: session?.customer || null })),
    subscribe: vi.fn((listener) => { listeners.add(listener); listener(auth.getAuthState()); return () => listeners.delete(listener) }),
  }
  require('../src/state/auth-store')
  require.cache[require.resolve('../src/state/auth-store')].exports = auth
  require.cache[require.resolve('../src/state/customer-session')].exports = {
    readSession: () => session && session.apiOrigin === client.getApiBaseUrl() ? session : null,
  }
  const customerApi = require('../src/api/customer-shop')
  customerApi.fetchMyCoupons = vi.fn(async () => coupons)
  const catalogApi = require('../src/api/catalog')
  catalogApi.fetchProducts = vi.fn(async () => ({ items: products }))
  let definition
  global.Page = (value) => { definition = value }
  require('../src/pages/cart/cart')
  const page = { ...definition, data: structuredClone(definition.data), setData(changes) { Object.assign(this.data, changes) } }
  return { page, store: require('../src/state/cart-store'), api: global.wx, client, customerApi, catalogApi,
    setIdentity(identity, notify = true) {
      session = identity ? { apiOrigin: client.getApiBaseUrl(), customer: { id: identity.id || 1 }, accessToken: identity.token || 'new-token' } : null
      if (notify) for (const listener of listeners) listener(auth.getAuthState())
    } }
}

afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

describe('cart page selection and shared stock', () => {
  it('shows remaining SKU stock and preserves later additions during quantity changes', () => {
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

  it('removes current storage after confirmation and ignores callbacks after unload', () => {
    const { page, store, api } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] }, 1)
    page.onShow()
    page.removeLine({ currentTarget: { dataset: { key: page.data.items[0].key } } })
    store.addItem(store.loadCart(), product('CAKE'), { addon: [] }, 1)
    api.showModal.mock.calls[0][0].success({ confirm: true })
    expect(store.loadCart().items.map((item) => item.productCode)).toEqual(['CAKE'])
    page.clearAll()
    page.onUnload()
    api.showModal.mock.calls[1][0].success({ confirm: true })
    expect(store.loadCart().items.map((item) => item.productCode)).toEqual(['CAKE'])
  })

  it('supports all, individual and inverse selection without releasing unchecked SKU stock', () => {
    const { page, store, api } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    store.addItem(store.loadCart(), product(), { addon: ['pearl'] }, 2)
    page.onShow()
    page.changeSelection({ currentTarget: { dataset: { key: page.data.items[0].key } } })
    expect(page.data).toMatchObject({ allSelected: false, displaySubtotal: '¥7.00', displayTotal: '¥7.00' })
    expect(page.data.summary.totalQuantity).toBe(4)
    expect(page.data.selectedSummary.totalQuantity).toBe(2)
    page.changeQuantity({ currentTarget: { dataset: { key: page.data.items[1].key } }, detail: { value: 4 } })
    expect(api.showModal).toHaveBeenCalledWith(expect.objectContaining({ content: '购买数量超过当前库存' }))
    page.invertSelected()
    expect(page.data.items.map((item) => item.selected)).toEqual([true, false])
    expect(page.data.displayTotal).toBe('¥6.00')
    page.toggleAll()
    expect(page.data.allSelected).toBe(true)
    page.toggleAll()
    expect(page.data.selectedSummary.totalQuantity).toBe(0)
    expect(page.data.displayTotal).toBe('¥0.00')
    page.checkout()
    expect(api.navigateTo).not.toHaveBeenCalled()
  })

  it('keeps displayed selection intact when persistence fails', () => {
    const { page, store, api } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] })
    page.onShow()
    api.setStorageSync.mockImplementation(() => { throw new Error('quota') })
    page.toggleAll()
    expect(page.data.items[0].selected).toBe(true)
    expect(api.showModal).toHaveBeenCalledWith(expect.objectContaining({ content: '购物车保存失败，请检查设备存储空间。' }))
  })
})

describe('cart owned coupon previews', () => {
  it('updates shortfalls and discounts at the selected threshold, and sends only an eligible coupon to checkout', async () => {
    const { page, store, api } = setup({ loggedIn: true, coupons: [coupon()] })
    store.addItem(store.loadCart(), product(), { addon: [] }, 1)
    page.onShow()
    await flush()
    expect(page.data).toMatchObject({ couponHint: '差3.00元可减2.00元', discountCents: 0, displayTotal: '¥3.00' })
    page.changeQuantity({ currentTarget: { dataset: { key: page.data.items[0].key } }, detail: { value: 2 } })
    expect(page.data).toMatchObject({ chosenCouponId: 1, discountCents: 200, displaySubtotal: '¥6.00', displayTotal: '¥4.00' })
    page.checkout()
    expect(api.navigateTo).toHaveBeenLastCalledWith({ url: '/pages/checkout/checkout?coupon_id=1' })
    page.toggleAll()
    expect(page.data).toMatchObject({ chosenCouponId: null, discountCents: 0, displayTotal: '¥0.00' })
  })

  it('recommends the best eligible coupon but honors an explicit coupon and no-coupon choice', async () => {
    const { page, store } = setup({ loggedIn: true, coupons: [coupon(1, { discount_cents: 50 }), coupon(2),
      coupon(3, { discount_cents: 500, expires_at: '2020-01-01T00:00:00Z' })] })
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    page.onShow()
    await flush()
    expect(page.data.chosenCouponId).toBe(2)
    page.openCouponPicker()
    expect(page.data.couponPickerVisible).toBe(true)
    page.chooseCoupon({ currentTarget: { dataset: { id: 1 } } })
    expect(page.data).toMatchObject({ chosenCouponId: 1, discountCents: 50, couponPickerVisible: false })
    page.chooseCoupon({ currentTarget: { dataset: { id: 3 } } })
    expect(page.data.chosenCouponId).toBe(1)
    page.chooseNoCoupon()
    page.changeQuantity({ currentTarget: { dataset: { key: page.data.items[0].key } }, detail: { value: 3 } })
    expect(page.data).toMatchObject({ chosenCouponId: null, discountCents: 0, displayTotal: '¥9.00', noCouponSelected: true })
  })

  it('never fetches private coupons as a guest, but provides coupon-center and login entries', async () => {
    const { page, store, api, customerApi } = setup()
    store.addItem(store.loadCart(), product(), { addon: [] })
    page.onShow()
    await flush()
    expect(customerApi.fetchMyCoupons).not.toHaveBeenCalled()
    expect(page.data.discountCents).toBe(0)
    page.openCouponCenter()
    expect(api.navigateTo).toHaveBeenLastCalledWith({ url: '/pages/coupon-center/coupon-center?from=cart' })
    page.openCouponPicker()
    expect(api.switchTab).toHaveBeenLastCalledWith({ url: '/pages/account/account' })
    page.checkout()
    expect(api.navigateTo).toHaveBeenLastCalledWith({ url: '/pages/checkout/checkout' })
  })

  it('loads eligible coupons from later history pages and caps pagination at ten pages', async () => {
    const { page, store, customerApi } = setup({ loggedIn: true })
    customerApi.fetchMyCoupons.mockImplementation(async ({ page }) => page === 1
      ? Array.from({ length: 100 }, (_, index) => coupon(index + 1, { is_active: false })) : [coupon(101)])
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    page.onShow()
    await flush()
    expect(customerApi.fetchMyCoupons.mock.calls.map(([options]) => options)).toEqual([
      { page: 1, page_size: 100 }, { page: 2, page_size: 100 },
    ])
    expect(page.data).toMatchObject({ chosenCouponId: 101, discountCents: 200 })
    customerApi.fetchMyCoupons.mockClear()
    customerApi.fetchMyCoupons.mockImplementation(async ({ page }) => Array.from({ length: 100 }, (_, index) => coupon(page * 100 + index)))
    await page.loadCoupons()
    expect(customerApi.fetchMyCoupons).toHaveBeenCalledTimes(10)
    expect(page.data.couponRows).toHaveLength(1_000)
    expect(page.data.couponLimited).toBe(true)
  })

  it('clears prior discounts and partial data when reloading fails on a later page', async () => {
    const { page, store, customerApi } = setup({ loggedIn: true, coupons: [coupon()] })
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    page.onShow()
    await flush()
    expect(page.data.discountCents).toBe(200)
    customerApi.fetchMyCoupons.mockImplementation(async ({ page }) => {
      if (page === 1) return Array.from({ length: 100 }, (_, index) => coupon(index + 1))
      throw new Error('优惠券服务暂时不可用。')
    })
    await page.loadCoupons()
    expect(page.data).toMatchObject({ couponRows: [], discountCents: 0, displayTotal: '¥6.00', couponErrorMessage: '优惠券服务暂时不可用。' })
  })

  it('ignores late private results after logout, account replacement, and an API origin change', async () => {
    const { page, store, customerApi, setIdentity, client } = setup({ loggedIn: true })
    const old = deferred()
    customerApi.fetchMyCoupons.mockReturnValueOnce(old.promise)
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    page.onShow()
    setIdentity(null)
    old.resolve([coupon()])
    await flush()
    expect(page.data).toMatchObject({ loggedIn: false, couponRows: [], discountCents: 0 })
    const previous = deferred()
    customerApi.fetchMyCoupons.mockReturnValueOnce(previous.promise).mockResolvedValueOnce([coupon(5)])
    setIdentity({ id: 1, token: 'first' })
    setIdentity({ id: 2, token: 'second' })
    await flush()
    previous.resolve([coupon(4)])
    await flush()
    expect(page.data.chosenCouponId).toBe(5)
    const originPending = deferred()
    customerApi.fetchMyCoupons.mockReturnValueOnce(originPending.promise)
    const loading = page.loadCoupons()
    client.setApiBaseUrl('https://other.example.cn')
    originPending.resolve([coupon(9)])
    await loading
    expect(page.data).toMatchObject({ loggedIn: false, couponRows: [], chosenCouponId: null, discountCents: 0 })
  })

  it('rejects old-token results for the same customer and ignores results after the page hides', async () => {
    const { page, store, customerApi, setIdentity } = setup({ loggedIn: true })
    const first = deferred()
    customerApi.fetchMyCoupons.mockReturnValueOnce(first.promise).mockResolvedValueOnce([coupon(7)])
    store.addItem(store.loadCart(), product(), { addon: [] }, 2)
    page.onShow()
    setIdentity({ id: 1, token: 'replacement' })
    await flush()
    first.resolve([coupon(6)])
    await flush()
    expect(page.data.chosenCouponId).toBe(7)
    const pending = deferred()
    customerApi.fetchMyCoupons.mockReturnValueOnce(pending.promise)
    const loading = page.loadCoupons()
    page.onHide()
    pending.resolve([coupon(8)])
    await loading
    expect(page.data.couponRows).toEqual([])
    expect(page.data.discountCents).toBe(0)
  })
})

describe('real catalog recommendations', () => {
  it('shows only published products in active categories, at most four, and opens a real code', async () => {
    const products = [product('TEA'), { ...product('DRAFT'), status: 'draft' },
      { ...product('HIDDEN'), category: { is_active: false } }, product('CAKE'), product('FRUIT'), product('SNACK'), product('EXTRA')]
    const { page, api, catalogApi } = setup({ products })
    page.onShow()
    await flush()
    expect(catalogApi.fetchProducts).toHaveBeenCalledWith({ page: 1, page_size: 4 })
    expect(page.data.recommendations.map((item) => item.productCode)).toEqual(['TEA', 'CAKE', 'FRUIT', 'SNACK'])
    expect(page.data.recommendations[0]).toMatchObject({ displayPrice: '¥3.00', available: true, stockLabel: '可选购' })
    page.openProduct({ detail: { productCode: 'CAKE' } })
    expect(api.navigateTo).toHaveBeenCalledWith({ url: '/pages/product/product?code=CAKE' })
    page.openProduct({ detail: { productCode: 'DRAFT' } })
    expect(api.navigateTo).toHaveBeenCalledTimes(1)
  })

  it('shows failures without invented products and ignores results from an older API origin', async () => {
    const { page, catalogApi, client } = setup()
    catalogApi.fetchProducts.mockRejectedValueOnce(new Error('推荐商品暂时无法加载。'))
    page.onShow()
    await flush()
    expect(page.data).toMatchObject({ recommendations: [], recommendationsErrorMessage: '推荐商品暂时无法加载。' })
    const old = deferred()
    catalogApi.fetchProducts.mockReturnValueOnce(old.promise)
    const loading = page.loadRecommendations()
    client.setApiBaseUrl('https://new.example.cn')
    old.resolve({ items: [product()] })
    await loading
    expect(page.data.recommendations).toEqual([])
  })
})
