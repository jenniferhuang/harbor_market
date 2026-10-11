'use strict'

const path = require('node:path')
const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const NOW = Date.parse('2026-10-11T08:00:00Z')
const OFFER = {
  id: 3, title: '店内满减券', min_spend_cents: 10000, discount_cents: 2000,
  starts_at: '2026-10-01T00:00:00Z', expires_at: '2026-10-31T16:00:00Z',
}
const CLAIM = { coupon_id: 3, claimed_at: '2026-10-10T00:00:00Z' }
const ITEM = {
  key: 'FISH::FISH-BAG::', productCode: 'FISH', productName: '香酥带鱼', skuCode: 'FISH-BAG', skuName: '袋装',
  coverUrl: '', selectionSummary: '', selections: {}, unitPriceCents: 3000, quantity: 2, stockQuantity: 10, selected: true,
}

function success(options, data) { options.success({ statusCode: 200, data: { data } }) }
function settle() { return new Promise((resolve) => setImmediate(resolve)) }
function event(id = 3) { return { currentTarget: { dataset: { id } } } }

function setup(config = {}) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  if (config.cart) storage.set('harbor_market_cart', config.cart)
  let logins = 0
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'wx-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/auth/login')) {
        logins += 1
        success(options, {
          token_type: 'Bearer', access_token: `verified-customer-token-abcdefghijklmnop-${logins}`,
          expires_at: new Date(NOW + 300000).toISOString(),
          customer: { id: 16 + logins, nickname: '微信用户', avatar_url: '' },
        })
      } else if (options.url.endsWith('/auth/logout')) success(options, null)
      else if (config.intercept && config.intercept(options)) return
      else if (new URL(options.url).pathname === '/api/v1/shop/coupons') success(options, config.offers || [OFFER])
      else if (new URL(options.url).pathname === '/api/v1/mini/shop/coupons') success(options, config.owned || [])
      else if (options.url.endsWith('/claim')) success(options, CLAIM)
      else throw new Error('Unexpected test request')
    }),
    showToast: vi.fn(), navigateTo: vi.fn(),
  }
  global.wx = wxMock
  let definition
  global.Page = vi.fn((value) => { definition = value })
  require('../src/pages/coupon-center/coupon-center')
  function mount(options = {}) {
    const page = {
      ...definition, data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
    }
    page.onLoad(options)
    page.onShow()
    return page
  }
  return {
    mount, wxMock, storage, auth: require('../src/state/auth-store'),
    client: require('../src/api/client'), sessions: require('../src/state/customer-session'),
  }
}

async function ready(page) {
  await vi.waitFor(() => expect(page.data).toMatchObject({ loading: false, claimsLoading: false, hasLoaded: true }))
}

describe('coupon center', () => {
  afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

  it('allows guest browsing and opens the existing login dialog only when claiming', async () => {
    const { mount, wxMock } = setup()
    const page = mount()
    await ready(page)
    expect(page.data.coupons[0]).toMatchObject({ id: 3, claimed: false, discountLabel: '20.00', thresholdLabel: '满 ¥100.00 使用', expiryLabel: '有效期至 2026-11-01' })
    expect(page.data.loginVisible).toBe(false)
    await page.claim(event())
    expect(page.data.loginVisible).toBe(true)
    expect(wxMock.login).not.toHaveBeenCalled()
    expect(wxMock.request.mock.calls.some(([options]) => options.url.includes('/mini/shop/'))).toBe(false)
    page.closeLogin()
    expect(page.data.loginVisible).toBe(false)
  })

  it('uses selected cart lines to show exact shortfalls without applying unclaimed discounts', async () => {
    const other = { ...ITEM, key: 'TEA::TEA-BAG::', productCode: 'TEA', skuCode: 'TEA-BAG', unitPriceCents: 5000, selected: false }
    const { mount } = setup({ cart: { version: 1, items: [ITEM, other] } })
    const page = mount({ from: 'cart' })
    await ready(page)
    expect(page.data.subtotalLabel).toBe('¥60.00')
    expect(page.data.coupons[0].contextText).toBe('再选 ¥40.00 可满减')
  })

  it('keeps offers browsable when an unsafe saved cart amount cannot be calculated', async () => {
    const { mount } = setup({ cart: { version: 1, items: [{ ...ITEM, unitPriceCents: Number.MAX_SAFE_INTEGER }] } })
    const page = mount({ from: 'cart' })
    await ready(page)
    expect(page.data.subtotalLabel).toBe('金额暂时无法计算')
    expect(page.data.coupons[0].contextText).toBe('')
  })

  it('shows a reached threshold as claimable and estimates a discount only after the server confirms ownership', async () => {
    const { mount, auth, wxMock } = setup({ cart: { version: 1, items: [{ ...ITEM, quantity: 4 }] } })
    await auth.loginWithWeChat()
    const page = mount({ from: 'cart' })
    await ready(page)
    expect(page.data.coupons[0].contextText).toBe('已达到使用门槛，领取后可用')
    await page.claim(event())
    expect(page.data.coupons[0]).toMatchObject({ claimed: true, contextText: '本次可减 ¥20.00' })
    expect(wxMock.showToast).toHaveBeenCalledWith({ title: '领取成功', icon: 'success' })
  })

  it('paginates public offers and owned histories, including a claim on the second history page', async () => {
    const { mount, auth, wxMock } = setup({
      intercept(options) {
        const url = new URL(options.url)
        const page = Number(url.searchParams.get('page'))
        if (url.pathname === '/api/v1/shop/coupons') {
          success(options, page === 1 ? Array.from({ length: 20 }, (_, index) => ({ ...OFFER, id: index + 1 })) : [{ ...OFFER, id: 21 }])
          return true
        }
        if (url.pathname === '/api/v1/mini/shop/coupons') {
          success(options, page === 1 ? Array.from({ length: 100 }, (_, index) => ({ ...CLAIM, coupon_id: index + 1000 })) : [{ ...CLAIM, coupon_id: 21 }])
          return true
        }
      },
    })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    expect(page.data.coupons).toHaveLength(20)
    expect(page.data.hasMore).toBe(true)
    await page.loadMore()
    expect(page.data.coupons).toHaveLength(21)
    expect(page.data.coupons.at(-1)).toMatchObject({ id: 21, claimed: true })
    expect(page.data.hasMore).toBe(false)
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.includes('/mini/shop/coupons?'))).toHaveLength(2)
  })

  it('prevents repeated taps, does not mark a failed claim as received, and permits retry', async () => {
    let pending
    const { mount, auth, wxMock } = setup({ intercept(options) { if (options.url.endsWith('/claim')) { pending = options; return true } } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    const claiming = page.claim(event())
    await page.claim(event())
    expect(page.data.coupons[0]).toMatchObject({ claiming: true, claimed: false })
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/claim'))).toHaveLength(1)
    pending.success({ statusCode: 503, data: { error: { message: 'database unavailable' } } })
    await claiming
    expect(page.data.coupons[0]).toMatchObject({ claiming: false, claimed: false })
    expect(page.data.claimsErrorMessage).toMatch(/[\u3400-\u9fff]/)
    expect(wxMock.showToast).not.toHaveBeenCalled()
    const retry = page.claim(event())
    success(pending, CLAIM)
    await retry
    expect(page.data.coupons[0].claimed).toBe(true)
  })

  it('does not accept a successful claim for a different coupon', async () => {
    const { mount, auth } = setup({ intercept(options) { if (options.url.endsWith('/claim')) { success(options, { ...CLAIM, coupon_id: 9 }); return true } } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    await page.claim(event())
    expect(page.data.coupons[0].claimed).toBe(false)
    expect(page.data.claimsErrorMessage).toContain('暂时无法确认')
  })

  it('clears owned state and discards old claim results when accounts change', async () => {
    let pending
    const { mount, auth, wxMock } = setup({ intercept(options) { if (options.url.endsWith('/claim')) { pending = options; return true } } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    const claiming = page.claim(event())
    await auth.logout()
    expect(page.data.coupons[0]).toMatchObject({ claiming: false, claimed: false })
    await auth.loginWithWeChat()
    await ready(page)
    success(pending, CLAIM)
    await claiming
    expect(page.data.coupons[0].claimed).toBe(false)
    expect(page.data.claimsErrorMessage).toBe('')
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('discards a late owned history after account switch and displays only the new account claims', async () => {
    const pending = []
    const { mount, auth } = setup({ intercept(options) { if (options.url.includes('/mini/shop/coupons?')) { pending.push(options); return true } } })
    await auth.loginWithWeChat()
    const page = mount()
    expect(pending).toHaveLength(1)
    await auth.logout()
    await auth.loginWithWeChat()
    expect(pending).toHaveLength(2)
    success(pending[1], [])
    await ready(page)
    success(pending[0], [CLAIM])
    await settle()
    expect(page.data.coupons[0].claimed).toBe(false)
  })

  it.each(['hide', 'unload', 'origin'])('discards private claim state after %s', async (change) => {
    let pending
    const { mount, auth, client, wxMock } = setup({ intercept(options) { if (options.url.endsWith('/claim')) { pending = options; return true } } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    const claiming = page.claim(event())
    if (change === 'hide') page.onHide()
    else if (change === 'unload') page.onUnload()
    else { client.setApiBaseUrl('https://other.example.cn'); page.applyAuthState(auth.getAuthState()) }
    const updates = page.setData.mock.calls.length
    success(pending, CLAIM)
    await claiming
    expect(page.setData.mock.calls).toHaveLength(updates)
    expect(wxMock.showToast).not.toHaveBeenCalled()
    expect(page._owned.size).toBe(0)
  })

  it('reloads on show after hiding, instead of leaving a stale receiving spinner', async () => {
    const { mount, auth } = setup({ owned: [CLAIM] })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    expect(page.data.coupons[0].claimed).toBe(true)
    page.onHide()
    expect(page.data.coupons[0].claimed).toBe(false)
    page.onShow()
    await ready(page)
    expect(page.data.coupons[0].claimed).toBe(true)
  })

  it('stops a repeating full history page and explains that ownership is not fully confirmed', async () => {
    const owned = Array.from({ length: 100 }, (_, index) => ({ ...CLAIM, coupon_id: index + 1000 }))
    const { mount, auth, wxMock } = setup({ owned })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    expect(page.data.claimsErrorMessage).toContain('未完整确认')
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.includes('/mini/shop/coupons?'))).toHaveLength(2)
  })

  it('discards late public offers on unload and shows a real empty state without invented promotions', async () => {
    let pending
    const { mount } = setup({ intercept(options) { pending = options; return true } })
    const page = mount()
    page.onUnload()
    const updates = page.setData.mock.calls.length
    success(pending, [OFFER])
    await settle()
    expect(page.data.coupons).toEqual([])
    expect(page.setData.mock.calls).toHaveLength(updates)
    const another = setup({ offers: [] }).mount()
    await ready(another)
    expect(another.data).toMatchObject({ coupons: [], hasMore: false, hasLoaded: true })
  })

  it('opens the owned wallet only after login and does not claim on a login dialog success event', async () => {
    const { mount, auth, wxMock } = setup()
    const page = mount()
    await ready(page)
    page.openWallet()
    expect(page.data.loginVisible).toBe(true)
    expect(wxMock.navigateTo).not.toHaveBeenCalled()
    await auth.loginWithWeChat()
    page.onLoginSuccess()
    await ready(page)
    expect(page.data.loginVisible).toBe(false)
    expect(wxMock.request.mock.calls.some(([options]) => options.url.endsWith('/claim'))).toBe(false)
    page.openWallet()
    expect(wxMock.navigateTo).toHaveBeenCalledWith({ url: '/pages/coupons/coupons' })
  })
})
