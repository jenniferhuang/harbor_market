'use strict'

const path = require('node:path')
const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const OFFER = { id: 3, title: '满减券', min_spend_cents: 1000, discount_cents: 200, starts_at: null, expires_at: null }
const INPUT = { items: [{ product_code: 'FISH', sku_code: 'FISH-BAG', selections: { PACKAGE: ['BAG'] }, quantity: 2 }], coupon_id: null }
const QUOTE = {
  items: [{ ...INPUT.items[0], name: '香酥带鱼', sku_name: '袋装', unit_price_cents: 1200, line_total_cents: 2400 }],
  subtotal_cents: 2400, discount_cents: 0, total_cents: 2400, coupon_id: null,
  coupon_evaluations: [{ coupon_id: 3, title: '满减券', min_spend_cents: 1000, discount_cents: 200, eligible: true, shortfall_cents: 0, reason: 'eligible' }],
}

function success(options, data) { options.success({ statusCode: 200, data: { data } }) }

function setup(intercept) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  let logins = 0
  global.wx = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'wx-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/auth/login')) {
        logins += 1
        success(options, {
          token_type: 'Bearer', access_token: `verified-customer-token-abcdefghijklmnop-${logins}`,
          expires_at: new Date(Date.now() + 300000).toISOString(),
          customer: { id: 17, nickname: '微信用户', avatar_url: '' },
        })
      } else if (options.url.endsWith('/auth/logout')) success(options, null)
      else if (intercept && intercept(options)) return
      else if (options.url.includes('/shop/coupons?')) success(options, [OFFER])
      else if (options.url.endsWith('/cart/quote')) success(options, QUOTE)
      else throw new Error('Unexpected test request')
    }),
  }
  return {
    api: require('../src/api/cart'), auth: require('../src/state/auth-store'),
    client: require('../src/api/client'), sessions: require('../src/state/customer-session'), wxMock: global.wx,
  }
}

describe('cart and available coupon API', () => {
  afterEach(() => { delete global.wx; vi.restoreAllMocks() })

  it('fetches paginated public offers without a bearer and treats the active-only response as active', async () => {
    const { api, wxMock } = setup()
    await expect(api.fetchAvailableCoupons({ page: 2, page_size: 10 })).resolves.toEqual([{ ...OFFER, is_active: true }])
    expect(wxMock.request).toHaveBeenCalledWith(expect.objectContaining({
      url: `${ORIGIN}/api/v1/shop/coupons?page=2&page_size=10`, method: 'GET',
      header: { Accept: 'application/json' },
    }))
  })

  it.each([{ page: 0 }, { page: 1.5 }, { page_size: 101 }, { page_size: '20' }])('rejects invalid pagination (%s)', async (options) => {
    const { api, wxMock } = setup()
    await expect(api.fetchAvailableCoupons(options)).rejects.toMatchObject({ status: 422 })
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it.each([{ discount_cents: '200' }, { min_spend_cents: -1 }, { id: 0 }, { expires_at: 'invalid' }])('rejects malformed offer money and identifiers (%s)', async (changes) => {
    const { api } = setup((options) => { success(options, [{ ...OFFER, ...changes }]); return true })
    await expect(api.fetchAvailableCoupons()).rejects.toMatchObject({ status: 502 })
  })

  it('requires a verified customer before requesting a quote', async () => {
    const { api, wxMock } = setup()
    await expect(api.quoteCart(INPUT)).rejects.toMatchObject({ status: 401 })
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it('posts only product choices and quantities, using server prices in the result', async () => {
    const { api, auth, sessions, wxMock } = setup()
    await auth.loginWithWeChat()
    await expect(api.quoteCart({ ...INPUT, total_cents: 1, customer_id: 999, items: [{ ...INPUT.items[0], unit_price_cents: 1 }] })).resolves.toEqual(QUOTE)
    expect(wxMock.request.mock.calls.at(-1)[0]).toMatchObject({
      url: `${ORIGIN}/api/v1/mini/shop/cart/quote`, method: 'POST', data: INPUT,
      header: expect.objectContaining({ Authorization: `Bearer ${sessions.readSession().accessToken}` }),
    })
  })

  it('accepts an empty quote and an explicitly selected eligible coupon, capped at the subtotal', async () => {
    const { api, auth } = setup((options) => {
      success(options, options.data.items.length ? { ...QUOTE, coupon_id: 3, discount_cents: 200, total_cents: 2200 }
        : { items: [], subtotal_cents: 0, discount_cents: 0, total_cents: 0, coupon_id: null, coupon_evaluations: [] })
      return true
    })
    await auth.loginWithWeChat()
    await expect(api.quoteCart({ items: [], coupon_id: null })).resolves.toMatchObject({ total_cents: 0 })
    await expect(api.quoteCart({ ...INPUT, coupon_id: 3 })).resolves.toMatchObject({ discount_cents: 200, total_cents: 2200 })
  })

  it.each([
    { subtotal_cents: 2401 }, { total_cents: -1 }, { discount_cents: 100, total_cents: 2300 },
    { total_cents: '2400' }, { coupon_id: 3, discount_cents: 200, total_cents: 2200 },
    { items: [{ ...QUOTE.items[0], quantity: 3 }] },
    { items: [{ ...QUOTE.items[0], selections: { PACKAGE: ['OTHER'] } }] },
    { items: [{ ...QUOTE.items[0], unit_price_cents: 1201 }] },
    { coupon_evaluations: [{ ...QUOTE.coupon_evaluations[0], shortfall_cents: 1 }] },
  ])('rejects unsafe or inconsistent quote responses (%s)', async (changes) => {
    const { api, auth } = setup((options) => { success(options, { ...QUOTE, ...changes }); return true })
    await auth.loginWithWeChat()
    await expect(api.quoteCart(INPUT)).rejects.toMatchObject({ status: 502 })
  })

  it('discards a quote after the same customer logs in with a new token', async () => {
    let pending
    const { api, auth } = setup((options) => { pending = options; return true })
    await auth.loginWithWeChat()
    const quoting = api.quoteCart(INPUT)
    await auth.logout()
    await auth.loginWithWeChat()
    success(pending, QUOTE)
    await expect(quoting).rejects.toMatchObject({ status: 409 })
  })

  it('expires only the token that received 401, preserving a newer session', async () => {
    let pending
    const { api, auth, sessions } = setup((options) => { pending = options; return true })
    await auth.loginWithWeChat()
    const quoting = api.quoteCart(INPUT)
    await auth.logout()
    await auth.loginWithWeChat()
    const currentToken = sessions.readSession().accessToken
    pending.success({ statusCode: 401, data: { error: { message: '登录已过期' } } })
    await expect(quoting).rejects.toMatchObject({ status: 401 })
    expect(sessions.readSession().accessToken).toBe(currentToken)
    expect(auth.getAuthState().status).toBe('authenticated')
  })

  it('expires the active session on a quote 401', async () => {
    const { api, auth } = setup((options) => {
      options.success({ statusCode: 401, data: { error: { message: '登录已过期' } } }); return true
    })
    await auth.loginWithWeChat()
    await expect(api.quoteCart(INPUT)).rejects.toMatchObject({ status: 401 })
    expect(auth.getAuthState().status).toBe('guest')
  })

  it('does not send credentials to a changed origin and discards late public responses', async () => {
    let pending
    const { api, auth, client, wxMock } = setup((options) => { pending = options; return true })
    await auth.loginWithWeChat()
    const offers = api.fetchAvailableCoupons()
    client.setApiBaseUrl('https://other.example.cn')
    success(pending, [OFFER])
    await expect(offers).rejects.toMatchObject({ status: 409 })
    wxMock.request.mockClear()
    await expect(api.quoteCart(INPUT)).rejects.toMatchObject({ status: 401 })
    expect(wxMock.request).not.toHaveBeenCalled()
  })
})
