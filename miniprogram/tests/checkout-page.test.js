'use strict'

const path = require('node:path')
const ROOT = path.resolve(__dirname, '../src') + path.sep
const ORIGIN = 'https://shop.example.cn'
const COUPON = { coupon_id: 7, title: '满6元减1元', min_spend_cents: 600, discount_cents: 100,
  eligible: true, shortfall_cents: 0, reason: 'eligible' }

function product(code = 'FISH') {
  return { product_code: code, name: '鱼干', stock_status: 'in_stock', base_price_cents: 300,
    specifications: [], skus: [{ sku_code: code + '-ONE', name: '单包', price_cents: 300,
      stock_quantity: 9, attributes: {}, is_default: true, is_active: true }] }
}
function reply(options, data) { options.success({ statusCode: 200, data: { data } }) }
function quote(input, unitPrice = 300) {
  const items = input.items.map((item) => ({ ...item, name: '鱼干', sku_name: '单包', unit_price_cents: unitPrice, line_total_cents: unitPrice * item.quantity }))
  const subtotal = items.reduce((sum, item) => sum + item.line_total_cents, 0)
  const discount = input.coupon_id ? 100 : 0
  return { items, subtotal_cents: subtotal, discount_cents: discount, total_cents: subtotal - discount,
    coupon_id: input.coupon_id, coupon_evaluations: [{ ...COUPON, shortfall_cents: Math.max(0, 600 - subtotal), eligible: subtotal >= 600, reason: subtotal >= 600 ? 'eligible' : 'below_minimum' }] }
}
function setup(config = {}) {
  vi.resetModules()
  for (const key of Object.keys(require.cache)) if (key.startsWith(ROOT)) delete require.cache[key]
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  let logins = 0
  global.wx = { getStorageSync: vi.fn((key) => storage.get(key)), setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)), navigateTo: vi.fn(), switchTab: vi.fn(),
    login: vi.fn((options) => options.success({ code: 'wx-valid-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/auth/login')) {
        logins += 1
        reply(options, { token_type: 'Bearer', access_token: `valid-customer-token-abcdefghijklmnop-${logins}`,
          expires_at: new Date(Date.now() + 300_000).toISOString(), customer: { id: 9, nickname: '海港', avatar_url: '' } })
      }
      else if (options.url.endsWith('/auth/logout')) reply(options, null)
      else if (options.url.endsWith('/cart/quote')) {
        if (config.intercept && config.intercept(options)) return
        reply(options, quote(options.data, config.unitPrice || 300))
      } else throw new Error('Unexpected request')
    }) }
  const auth = require('../src/state/auth-store')
  const cart = require('../src/state/cart-store')
  let definition
  global.Page = (value) => { definition = value }
  require('../src/pages/checkout/checkout')
  const page = { ...definition, data: structuredClone(definition.data), setData(changes) { Object.assign(this.data, changes) } }
  return { page, auth, cart, api: global.wx, storage,
    mount(options = {}) { page.onLoad(options); page.onShow() },
    requests() { return global.wx.request.mock.calls.map(([options]) => options).filter((options) => options.url.endsWith('/cart/quote')) } }
}
afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

describe('selected cart order confirmation', () => {
  it('keeps guests shopping without making a private quote request', () => {
    const test = setup()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    expect(test.page.data.loggedIn).toBe(false)
    expect(test.requests()).toHaveLength(0)
    test.page.login()
    expect(test.page.data.loginVisible).toBe(true)
    test.page.backToCart()
    expect(test.api.switchTab).toHaveBeenCalledWith({ url: '/pages/cart/cart' })
    expect(test.cart.loadCart().items).toHaveLength(1)
  })
  it('requests one quote when a guest login finishes and emits the dialog success event', async () => {
    const test = setup()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    test.page.login()
    await test.auth.loginWithWeChat()
    test.page.loginSucceeded()
    await vi.waitFor(() => expect(test.page.data.quote).not.toBeNull())
    expect(test.requests()).toHaveLength(1)
    expect(test.page.data).toMatchObject({ loggedIn: true, loginVisible: false })
    expect(test.cart.loadCart().items).toHaveLength(1)
  })
  it('clears and refreshes a completed quote when the same customer receives a new session token', async () => {
    let pending
    let pause = false
    const test = setup({ intercept(options) { if (pause) { pending = options; return true } } })
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    await vi.waitFor(() => expect(test.page.data.quote).not.toBeNull())
    pause = true
    await test.auth.loginWithWeChat()
    expect(test.page.data).toMatchObject({ quote: null, coupons: [], items: [], loading: true })
    expect(test.requests()).toHaveLength(2)
    reply(pending, quote(pending.data, 400))
    await vi.waitFor(() => expect(test.page.data.quote?.total_cents).toBe(800))
  })
  it('quotes only checked lines using current server prices and no submitted amount', async () => {
    const test = setup({ unitPrice: 400 })
    await test.auth.loginWithWeChat()
    let cart = test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    cart = test.cart.addItem(cart, product('CAKE'), {}, 1)
    test.cart.setItemSelected(cart, cart.items[1].key, false)
    test.mount({ coupon_id: '7' })
    await vi.waitFor(() => expect(test.page.data.quote).not.toBeNull())
    expect(test.requests()[0].data).toEqual({ items: [{ product_code: 'FISH', sku_code: 'FISH-ONE', selections: {}, quantity: 2 }], coupon_id: 7 })
    expect(test.page.data.quote.total_cents).toBe(700)
    expect(test.page.data.priceChanged).toBe(true)
    expect(test.cart.loadCart().items).toHaveLength(2)
  })
  it('rechecks coupon changes on the server and allows no coupon', async () => {
    const test = setup()
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    await vi.waitFor(() => expect(test.page.data.quote).not.toBeNull())
    test.page.chooseCoupon({ currentTarget: { dataset: { id: 7 } } })
    await vi.waitFor(() => expect(test.page.data.quote?.discount_cents).toBe(100))
    test.page.chooseCoupon({ currentTarget: { dataset: { id: 0 } } })
    await vi.waitFor(() => expect(test.page.data.quote?.discount_cents).toBe(0))
    expect(test.requests().map((request) => request.data.coupon_id)).toEqual([null, 7, null])
  })
  it('ignores late confirmation after the user changes the cart', async () => {
    let pending
    const test = setup({ intercept(options) { pending = options; return true } })
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    test.cart.updateQuantity(test.cart.loadCart(), test.cart.loadCart().items[0].key, 3)
    reply(pending, quote(pending.data))
    await vi.waitFor(() => expect(test.page.data.loading).toBe(false))
    expect(test.page.data.quote).toBeNull()
    expect(test.page.data.errorMessage).toContain('购物车已改变')
  })
  it('discards private quote data immediately on logout and ignores late responses', async () => {
    let pending
    const test = setup({ intercept(options) { pending = options; return true } })
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount({ coupon_id: 7 })
    await test.auth.logout()
    reply(pending, quote(pending.data))
    await Promise.resolve(); await Promise.resolve()
    expect(test.page.data).toMatchObject({ loggedIn: false, quote: null, coupons: [], items: [], displayTotal: '¥0.00' })
    expect(test.cart.loadCart().items).toHaveLength(1)
  })
  it('can remove an expired selected coupon after the server rejects it', async () => {
    let rejected = false
    const test = setup({ intercept(options) {
      if (options.data.coupon_id && !rejected) {
        rejected = true
        options.success({ statusCode: 422, data: { error: { code: 'coupon_unavailable', message: '优惠券已过期，请重新选择。' } } })
        return true
      }
      return false
    } })
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount({ coupon_id: 7 })
    await vi.waitFor(() => expect(test.page.data.errorMessage).toContain('优惠券'))
    expect(test.page.data.quote).toBeNull()
    test.page.withoutCoupon()
    await vi.waitFor(() => expect(test.page.data.quote?.total_cents).toBe(600))
  })
  it('does not quote when no lines are selected', async () => {
    const test = setup()
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 1)
    test.cart.setAllSelected(test.cart.loadCart(), false)
    test.mount()
    expect(test.page.data.empty).toBe(true)
    expect(test.requests()).toHaveLength(0)
  })
  it('discards a hidden-page response and requests fresh prices when reopened', async () => {
    let pending
    const test = setup({ intercept(options) { pending = options; return true } })
    await test.auth.loginWithWeChat()
    test.cart.addItem(test.cart.loadCart(), product(), {}, 2)
    test.mount()
    test.page.onHide()
    reply(pending, quote(pending.data))
    await Promise.resolve(); await Promise.resolve()
    expect(test.page.data.quote).toBeNull()
    test.page.onShow()
    reply(pending, quote(pending.data))
    await vi.waitFor(() => expect(test.page.data.quote).not.toBeNull())
    test.page.onHide()
    test.cart.setAllSelected(test.cart.loadCart(), false)
    test.page.onShow()
    expect(test.page.data).toMatchObject({ empty: true, quote: null, coupons: [], items: [], displayTotal: '¥0.00' })
    expect(test.requests()).toHaveLength(2)
  })
})
