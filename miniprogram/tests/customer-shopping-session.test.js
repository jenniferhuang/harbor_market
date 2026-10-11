'use strict'

const path = require('node:path')
const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const PRODUCT = {
  product_code: 'FISH', name: '香酥带鱼', status: 'published', base_price_cents: 1200,
  category: { id: 1, code: 'SNACKS', name: '鱼干肉脯', is_active: true },
  unit: '包', stock_status: 'in_stock', inventory_count: 20, images: [], specifications: [],
  skus: [{ id: 1, sku_code: 'FISH-BAG', name: '袋装', attributes: {}, price_cents: 1200,
    stock_quantity: 20, is_active: true, is_default: true }],
}
const CUSTOMER = { id: 9, nickname: '验证后的顾客', avatar_url: '' }

function reply(options, data) { options.success({ statusCode: 200, data: { data } }) }
function clearSourceModules() {
  vi.resetModules()
  for (const key of Object.keys(require.cache)) {
    if (key.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[key]
  }
}

function runtime(storage, server, config = {}) {
  clearSourceModules()
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'isolated-unit-wx-code' })),
    request: vi.fn((options) => {
      if (config.intercept && config.intercept(options)) return
      const route = new URL(options.url).pathname
      if (route === '/api/v1/mini/auth/login') reply(options, {
        token_type: 'Bearer', access_token: 'verified-unit-customer-token-abcdefghijklmnop',
        expires_at: new Date(Date.now() + 300000).toISOString(), customer: CUSTOMER,
      })
      else if (route === '/api/v1/mini/auth/me') reply(options, CUSTOMER)
      else if (route === '/api/v1/mini/auth/logout') reply(options, null)
      else if (route === '/api/v1/catalog/products/FISH') reply(options, PRODUCT)
      else if (route === '/api/v1/mini/shop/favorites/FISH') {
        if (options.method === 'PUT') server.favorite = true
        if (options.method === 'DELETE') server.favorite = false
        reply(options, { is_favorite: server.favorite })
      }
      else if (route === '/api/v1/mini/shop/me') reply(options, { can_manage_store: false })
      else if (route === '/api/v1/mini/shop/cart/quote') {
        const items = options.data.items.map((item) => ({ ...item, name: PRODUCT.name, sku_name: '袋装',
          unit_price_cents: 1200, line_total_cents: 1200 * item.quantity }))
        const total = items.reduce((sum, item) => sum + item.line_total_cents, 0)
        reply(options, { items, subtotal_cents: total, discount_cents: 0, total_cents: total,
          coupon_id: null, coupon_evaluations: [] })
      }
      else throw new Error(`Unexpected isolated test route: ${route}`)
    }),
    showToast: vi.fn(), showModal: vi.fn(), navigateTo: vi.fn(), switchTab: vi.fn(),
    setNavigationBarTitle: vi.fn(),
  }
  global.wx = wxMock
  const auth = require('../src/state/auth-store')
  const cart = require('../src/state/cart-store')
  function mount(name, options = {}) {
    let definition
    global.Page = (value) => { definition = value }
    require(path.join(SOURCE_ROOT, `pages/${name}/${name}`))
    const page = { ...definition, data: structuredClone(definition.data),
      setData(changes) { Object.assign(this.data, changes) } }
    page.onLoad(options)
    page.onShow()
    return page
  }
  function launch() {
    let definition
    global.App = (value) => { definition = value }
    require('../src/app')
    definition.onLaunch()
  }
  return { auth, cart, mount, launch, wxMock }
}

afterEach(() => {
  delete global.wx
  delete global.Page
  delete global.App
  vi.restoreAllMocks()
})

describe('verified customer shopping across app restarts', () => {
  it('retains the cart, re-verifies the saved customer, restores favorites and quotes without another wx.login', async () => {
    const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
    const server = { favorite: false }
    const first = runtime(storage, server)
    await first.auth.loginWithWeChat()
    const product = first.mount('product', { code: 'FISH' })
    await vi.waitFor(() => expect(product.data.loading).toBe(false))
    await product.toggleFavorite()
    product.changeQuantity({ detail: { value: 2 } })
    product.addToCart()
    expect(product.data.isFavorite).toBe(true)
    expect(first.cart.loadCart().items[0]).toMatchObject({ productCode: 'FISH', quantity: 2, selected: true })
    product.onUnload()

    let verification
    const second = runtime(storage, server, { intercept(options) {
      if (options.url.endsWith('/mini/auth/me')) { verification = options; return true }
    } })
    expect(second.auth.getAuthState()).toMatchObject({ status: 'restoring', customer: null })
    second.launch()
    const reopened = second.mount('product', { code: 'FISH' })
    await vi.waitFor(() => expect(reopened.data.loading).toBe(false))
    expect(reopened.data.isFavorite).toBe(false)
    expect(second.wxMock.request.mock.calls.some(([options]) => options.url.includes('/mini/shop/'))).toBe(false)
    expect(second.cart.loadCart().items[0].quantity).toBe(2)

    reply(verification, { ...CUSTOMER, nickname: '服务器已核验的昵称' })
    await vi.waitFor(() => expect(reopened.data.isFavorite).toBe(true))
    expect(second.auth.getAuthState()).toMatchObject({ status: 'authenticated', customer: { id: 9, nickname: '服务器已核验的昵称' } })
    expect(second.wxMock.login).not.toHaveBeenCalled()
    const checkout = second.mount('checkout')
    await vi.waitFor(() => expect(checkout.data.quote?.total_cents).toBe(2400))
    expect(checkout.data.items[0]).toMatchObject({ product_code: 'FISH', quantity: 2 })
    expect(second.cart.loadCart().items[0].quantity).toBe(2)
    const account = second.mount('account')
    expect(account.data).toMatchObject({ authStatus: 'authenticated', nickname: '服务器已核验的昵称' })
  })

  it('treats a revoked saved session as guest while preserving the cart and blocking private favorites/quotes', async () => {
    const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
    const server = { favorite: true }
    const first = runtime(storage, server)
    await first.auth.loginWithWeChat()
    first.cart.addItem(first.cart.loadCart(), PRODUCT, {}, 2)
    const second = runtime(storage, server, { intercept(options) {
      if (options.url.endsWith('/mini/auth/me')) {
        options.success({ statusCode: 401, data: { error: { message: '登录已失效' } } })
        return true
      }
    } })
    second.launch()
    await vi.waitFor(() => expect(second.auth.getAuthState().status).toBe('guest'))
    const product = second.mount('product', { code: 'FISH' })
    await vi.waitFor(() => expect(product.data.loading).toBe(false))
    await product.toggleFavorite()
    const checkout = second.mount('checkout')
    expect(checkout.data.loggedIn).toBe(false)
    expect(second.wxMock.request.mock.calls.some(([options]) => options.url.includes('/mini/shop/'))).toBe(false)
    expect(second.cart.loadCart().items[0].quantity).toBe(2)
    expect(storage.has('harbor_market_customer_session')).toBe(false)
  })

  it('expires the current customer on a favorite 401 without removing cart contents or claiming a saved favorite', async () => {
    const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
    const server = { favorite: false }
    const current = runtime(storage, server, { intercept(options) {
      if (options.method === 'PUT' && options.url.includes('/favorites/')) {
        options.success({ statusCode: 401, data: { error: { message: '登录已失效' } } })
        return true
      }
    } })
    await current.auth.loginWithWeChat()
    current.cart.addItem(current.cart.loadCart(), PRODUCT, {}, 2)
    const product = current.mount('product', { code: 'FISH' })
    await vi.waitFor(() => expect(product.data.loading).toBe(false))
    await product.toggleFavorite()
    expect(current.auth.getAuthState().status).toBe('guest')
    expect(product.data).toMatchObject({ isFavorite: false, favoriteBusy: false })
    expect(server.favorite).toBe(false)
    expect(current.cart.loadCart().items[0].quantity).toBe(2)
  })
})
