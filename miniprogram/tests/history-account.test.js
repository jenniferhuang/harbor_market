'use strict'

const path = require('node:path')
const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const HISTORY_KEY = 'harbor_market_browsing_history'
const CART_KEY = 'harbor_market_cart'
const product = (code = 'TEA') => ({ product_code: code, name: '海港茶', status: 'published',
  base_price_cents: 1200, category: { name: '茶', is_active: true }, stock_status: 'in_stock', images: [],
  specifications: [], skus: [{ sku_code: `${code}-1`, name: '一盒', price_cents: 1200, is_default: true,
    is_active: true, attributes: {}, stock_quantity: 20 }] })
const respond = (options, data) => options.success({ statusCode: 200, data: { data } })

function setup(responder) {
  vi.resetModules()
  for (const key of Object.keys(require.cache)) if (key.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[key]
  const storage = new Map([['harbor_market_api_base_url', ORIGIN], [CART_KEY, { version: 1, items: [{ productCode: 'GIFT', quantity: 2 }] }]])
  let loginCount = 0
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'wechat-login-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/mini/auth/login')) {
        ++loginCount
        respond(options, { token_type: 'Bearer', access_token: `session-token-account-${loginCount}`,
          expires_at: new Date(Date.now() + 3600000).toISOString(), customer: { id: loginCount, nickname: '小港', avatar_url: '' } })
      } else if (options.url.endsWith('/mini/auth/logout')) respond(options, null)
      else if (responder?.(options)) return
      else if (options.url.endsWith('/mini/shop/me')) respond(options, { can_manage_store: false, favorites_count: 125, available_coupon_count: 3, order_count: 25 })
      else if (options.url.includes('/catalog/products/')) respond(options, product(decodeURIComponent(options.url.split('/').pop())))
      else if (options.url.includes('/favorites/')) respond(options, { is_favorite: false })
      else throw new Error('Unexpected test request')
    }),
    showModal: vi.fn(), showToast: vi.fn(), navigateTo: vi.fn(), switchTab: vi.fn(),
    setNavigationBarTitle: vi.fn(), hideTabBar: vi.fn(), showTabBar: vi.fn(),
  }
  const auth = require('../src/state/auth-store')
  const history = require('../src/state/browsing-history')
  const client = require('../src/api/client')
  function mount(name) {
    let definition
    global.Page = vi.fn((value) => { definition = value })
    require(path.join(SOURCE_ROOT, `pages/${name}/${name}.js`))
    const page = { ...definition, data: structuredClone(definition.data), setData(changes) { Object.assign(this.data, changes) } }
    page.onLoad({ code: 'TEA' })
    page.onShow?.()
    return page
  }
  return { storage, auth, history, client, mount, wxMock: global.wx }
}

describe('personal center and local browsing history', () => {
  afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

  it('deduplicates recent published goods, bounds storage, and stores only code and date', () => {
    const { history, storage } = setup()
    for (let i = 0; i < 55; i++) history.recordVisit(product(`P${i}`))
    history.recordVisit({ ...product('P10'), app_secret: 'not-to-be-stored' })
    expect(history.loadHistory()).toHaveLength(50)
    expect(history.loadHistory()[0].productCode).toBe('P10')
    expect(history.loadHistory().filter((row) => row.productCode === 'P10')).toHaveLength(1)
    expect(Object.keys(storage.get(HISTORY_KEY).items[0]).sort()).toEqual(['productCode', 'viewedAt'])
    expect(history.recordVisit({ ...product(), status: 'draft' })).toBe(false)
  })

  it('separates API origins and tolerates malformed or inaccessible storage', () => {
    const { history, storage, client, wxMock } = setup()
    history.recordVisit(product())
    client.setApiBaseUrl('https://another-shop.example.cn')
    expect(history.loadHistory()).toEqual([])
    client.setApiBaseUrl(ORIGIN)
    storage.set(HISTORY_KEY, { version: 1, apiOrigin: ORIGIN, items: [null, { productCode: 'TEA', viewedAt: 1 }, { productCode: 'TEA', viewedAt: 2 }, { productCode: 'bad\ncode', viewedAt: 1 }] })
    expect(history.loadHistory()).toEqual([{ productCode: 'TEA', viewedAt: 1 }])
    wxMock.setStorageSync.mockImplementation(() => { throw new Error('quota') })
    expect(history.recordVisit(product())).toBe(false)
    wxMock.getStorageSync.mockImplementation(() => { throw new Error('storage unavailable') })
    expect(history.loadHistory()).toEqual([])
  })

  it('records a successful public product visit without interrupting product selection', async () => {
    const { history, mount } = setup()
    const page = mount('product')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    expect(history.loadHistory()).toEqual([{ productCode: 'TEA', viewedAt: expect.any(Number) }])
    expect(page.data.product.product_code).toBe('TEA')
  })

  it('uses exact server counters, refreshes on return, and clears private counts on logout', async () => {
    const { auth, mount, history, wxMock } = setup()
    history.recordVisit(product())
    await auth.loginWithWeChat()
    const page = mount('account')
    await vi.waitFor(() => expect(page.data.summaryLoading).toBe(false))
    expect(page.data).toMatchObject({ favoritesCount: '125', availableCouponCount: '3', orderCount: '25', historyCount: 1 })
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/mini/shop/me'))).toHaveLength(1)
    page.onHide(); page.onShow()
    await vi.waitFor(() => expect(page.data.summaryLoading).toBe(false))
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/mini/shop/me'))).toHaveLength(2)
    await auth.logout()
    expect(page.data).toMatchObject({ favoritesCount: '—', availableCouponCount: '—', orderCount: '—', historyCount: 1, canManageStore: false })
  })

  it('rejects late account counters from a previous session', async () => {
    const pending = []
    const { auth, mount } = setup((options) => {
      if (!options.url.endsWith('/mini/shop/me')) return false
      pending.push(options); return true
    })
    await auth.loginWithWeChat()
    const page = mount('account')
    await auth.logout(); await auth.loginWithWeChat()
    respond(pending[1], { can_manage_store: false, favorites_count: 2, available_coupon_count: 1, order_count: 0 })
    await vi.waitFor(() => expect(page.data.favoritesCount).toBe('2'))
    respond(pending[0], { can_manage_store: true, favorites_count: 99, available_coupon_count: 50, order_count: 99 })
    await Promise.resolve(); await Promise.resolve()
    expect(page.data).toMatchObject({ favoritesCount: '2', availableCouponCount: '1', orderCount: '0', canManageStore: false })
  })

  it('shows unavailable counters on failure instead of inventing zeros, then retries', async () => {
    let fail = true
    const { auth, mount } = setup((options) => {
      if (!options.url.endsWith('/mini/shop/me') || !fail) return false
      options.success({ statusCode: 503, data: { error: { message: 'Unavailable' } } }); return true
    })
    await auth.loginWithWeChat()
    const page = mount('account')
    await vi.waitFor(() => expect(page.data.summaryLoading).toBe(false))
    expect(page.data.favoritesCount).toBe('—')
    expect(page.data.summaryError).toContain('重试')
    fail = false
    await page.loadShopAccess()
    expect(page.data.favoritesCount).toBe('125')
    expect(page.data.summaryError).toBe('')
  })

  it('keeps guest history and coupon center open while private destinations request login', () => {
    const { mount, wxMock } = setup()
    const page = mount('account')
    page.openFavorites()
    expect(page.data.loginVisible).toBe(true)
    page.closeLogin(); page.openHistory(); page.openCouponCenter(); page.openSettings()
    expect(wxMock.navigateTo.mock.calls.map(([arg]) => arg.url)).toEqual(['/pages/history/history', '/pages/coupon-center/coupon-center', '/pages/profile/profile'])
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it('does not sign out a replacement account when an old confirmation is accepted', async () => {
    const { auth, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount('account')
    page.confirmLogout()
    const confirmation = wxMock.showModal.mock.calls[0][0]
    await auth.logout(); await auth.loginWithWeChat()
    await confirmation.success({ confirm: true })
    expect(auth.getAuthState().status).toBe('authenticated')
    expect(auth.getAuthState().customer.id).toBe(2)
  })

  it('invalidates a logout confirmation when the account page is hidden', async () => {
    const { auth, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount('account')
    page.confirmLogout()
    const confirmation = wxMock.showModal.mock.calls[0][0]
    page.onHide()
    await confirmation.success({ confirm: true })
    expect(auth.getAuthState().status).toBe('authenticated')
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/mini/auth/logout'))).toHaveLength(0)
  })

  it('keeps a new logout confirmation independent of callbacks from a prior visit', async () => {
    const { auth, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount('account')
    page.confirmLogout()
    const stale = wxMock.showModal.mock.calls[0][0]
    page.onHide(); page.onShow(); page.confirmLogout()
    const current = wxMock.showModal.mock.calls[1][0]
    await stale.success({ confirm: true }); stale.complete()
    expect(auth.getAuthState().status).toBe('authenticated')
    page.confirmLogout()
    expect(wxMock.showModal).toHaveBeenCalledTimes(2)
    await current.success({ confirm: true })
    expect(auth.getAuthState().status).toBe('guest')
  })

  it('hydrates history from current public goods, skips missing goods, and paginates', async () => {
    const { history, mount } = setup((options) => {
      if (!options.url.endsWith('/catalog/products/P10')) return false
      options.success({ statusCode: 404, data: { error: { message: 'Not found' } } }); return true
    })
    for (let i = 0; i < 12; i++) history.recordVisit(product(`P${i}`))
    const page = mount('history')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    expect(page.data).toMatchObject({ total: 12, unavailableCount: 1, hasMore: true })
    expect(page.data.items).toHaveLength(9)
    expect(page.data.items[0]).toMatchObject({ productCode: 'P11', displayPrice: '¥12.00' })
    await page.loadMore()
    expect(page.data.items).toHaveLength(11)
    expect(page.data.hasMore).toBe(false)
  })

  it('clears pending visits without touching cart, credentials, or a replacement API origin', async () => {
    let pending
    const { history, mount, auth, storage, wxMock, client } = setup((options) => {
      if (!options.url.includes('/catalog/products/')) return false
      pending = options; return true
    })
    await auth.loginWithWeChat()
    history.recordVisit(product())
    const cart = structuredClone(storage.get(CART_KEY))
    const page = mount('history')
    page.clearVisits()
    wxMock.showModal.mock.calls[0][0].success({ confirm: true })
    respond(pending, product())
    await Promise.resolve(); await Promise.resolve()
    expect(page.data.items).toEqual([])
    expect(history.loadHistory()).toEqual([])
    expect(storage.get(CART_KEY)).toEqual(cart)
    expect(auth.getAuthState().status).toBe('authenticated')
    page._confirming = false
    history.recordVisit(product()); page.setData({ total: 1 }); page.clearVisits()
    const staleConfirmation = wxMock.showModal.mock.calls[1][0]
    client.setApiBaseUrl('https://another-shop.example.cn')
    history.recordVisit(product('OTHER'))
    staleConfirmation.success({ confirm: true })
    expect(history.loadHistory()[0].productCode).toBe('OTHER')
  })

  it('lets a failed history batch retry without skipping goods or displaying an old origin', async () => {
    let fail = true
    const { history, mount, client } = setup((options) => {
      if (!options.url.includes('/catalog/products/') || !fail) return false
      options.fail({ errMsg: 'offline' }); return true
    })
    history.recordVisit(product())
    const page = mount('history')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    expect(page.data.errorMessage).toContain('重试')
    expect(page.data.items).toEqual([])
    fail = false
    await page.loadItems()
    expect(page.data.items).toHaveLength(1)
    client.setApiBaseUrl('https://another-shop.example.cn')
    page.onShow()
    expect(page.data.items).toEqual([])
    expect(page.data.total).toBe(0)
  })
})
