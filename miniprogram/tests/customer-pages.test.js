'use strict'

const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const PRODUCT = {
  product_code: 'APPLE', name: '苹果美式', base_price_cents: 1600, stock_status: 'in_stock', images: [],
  skus: [{ sku_code: 'APPLE-450', price_cents: 1600, stock_quantity: 10, is_default: true, is_active: true, attributes: {} }],
}
const COUPON = { coupon_id: 3, title: '满减券', discount_cents: 100, min_spend_cents: 1000, is_active: true, expires_at: null, claimed_at: '2026-10-04T08:00:00Z' }
const ORDER = { id: 8, status: 'completed', total_cents: 1600, items: [{ product_code: 'APPLE', quantity: 1, unit_price_cents: 1600 }] }
const STORE = { name: '港湾集市', phone: '123', address: '厦门', latitude: 24.5, longitude: 118.1, announcement_image_url: '' }
const ROWS = { favorites: [PRODUCT], coupons: [COUPON], orders: [ORDER] }

function success(options, data) { options.success({ statusCode: 200, data: { data } }) }

function setup(responder) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  let logins = 0
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'native-wx-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/mini/auth/login')) {
        logins += 1
        success(options, {
          token_type: 'Bearer', access_token: `customer-token-abcdefghijklmnop-${logins}`,
          expires_at: new Date(Date.now() + 300_000).toISOString(),
          customer: { id: 16 + logins, nickname: `账户${logins}`, avatar_url: '' },
        })
      } else if (options.url.endsWith('/mini/auth/logout')) success(options, null)
      else if (responder && responder(options) === true) return
      else if (options.url.includes('/catalog/products/')) success(options, PRODUCT)
      else if (options.url.includes('/favorites/')) success(options, { is_favorite: options.method === 'PUT' })
      else if (new URL(options.url).pathname.endsWith('/favorites')) success(options, ROWS.favorites)
      else if (new URL(options.url).pathname.endsWith('/coupons')) success(options, ROWS.coupons)
      else if (new URL(options.url).pathname.endsWith('/orders')) success(options, ROWS.orders)
      else if (options.url.endsWith('/store')) success(options, STORE)
      else success(options, { can_manage_store: false })
    }),
    uploadFile: vi.fn((options) => options.success({ statusCode: 200, data: JSON.stringify({ data: STORE }) })),
    chooseMedia: vi.fn(), showModal: vi.fn(), showToast: vi.fn(),
    navigateTo: vi.fn(), switchTab: vi.fn(), setNavigationBarTitle: vi.fn(),
  }
  global.wx = wxMock
  const auth = require('../src/state/auth-store')
  function mount(name) {
    let definition
    global.Page = vi.fn((value) => { definition = value })
    require(path.join(SOURCE_ROOT, `pages/${name}/${name}.js`))
    const page = {
      ...definition, data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
    }
    page.onLoad({ code: 'APPLE' })
    if (page.onShow) page.onShow()
    return page
  }
  return { wxMock, auth, mount }
}

describe('customer product and account pages', () => {
  afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

  it('does not optimistically mark favorites and guards duplicate clicks and onShow during a save', async () => {
    let savingRequest
    const { auth, mount, wxMock } = setup((options) => {
      if (options.method !== 'PUT' || !options.url.includes('/favorites/')) return
      savingRequest = options
      return true
    })
    await auth.loginWithWeChat()
    const page = mount('product')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    const saving = page.toggleFavorite()
    expect(page.data).toMatchObject({ isFavorite: false, favoriteBusy: true })
    await page.toggleFavorite()
    page.onShow()
    expect(page.data.favoriteBusy).toBe(true)
    savingRequest.success({ statusCode: 503, data: { error: { message: 'upstream socket' } } })
    await saving
    expect(page.data).toMatchObject({ isFavorite: false, favoriteBusy: false })
    expect(page.data.favoriteError).toMatch(/[\u3400-\u9fff]/)
    expect(wxMock.request.mock.calls.filter(([options]) => options.method === 'PUT')).toHaveLength(1)
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('offers optional login for a guest favorite without interrupting public product browsing', async () => {
    const { mount, wxMock } = setup()
    const page = mount('product')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    await page.toggleFavorite()
    expect(page.data.product.product_code).toBe('APPLE')
    expect(wxMock.showModal).toHaveBeenCalledWith(expect.objectContaining({ title: '登录后收藏商品' }))
    expect(wxMock.request.mock.calls.some(([options]) => options.url.includes('/mini/shop/favorites'))).toBe(false)
    expect(wxMock.login).not.toHaveBeenCalled()
  })

  it.each(['favorites', 'coupons', 'orders'])('clears private %s records immediately on logout', async (name) => {
    const { auth, mount } = setup()
    await auth.loginWithWeChat()
    const page = mount(name)
    await vi.waitFor(() => expect(page.data.items).toHaveLength(1))
    await auth.logout()
    expect(page.data).toMatchObject({ loggedIn: false, items: [], loading: false })
  })

  it.each(['favorites', 'coupons', 'orders'])('discards late old %s records after switching customer accounts', async (name) => {
    const pending = []
    const { auth, mount } = setup((options) => {
      if (!new URL(options.url).pathname.endsWith(`/mini/shop/${name}`)) return
      pending.push(options)
      return true
    })
    await auth.loginWithWeChat()
    const page = mount(name)
    // Native onLoad followed by onShow must issue one initial fetch.
    expect(pending).toHaveLength(1)
    await auth.logout()
    expect(page.data.items).toEqual([])
    await auth.loginWithWeChat()
    expect(pending).toHaveLength(2)
    const newerRows = name === 'favorites'
      ? [{ ...PRODUCT, product_code: 'TEA', name: '海港茶' }]
      : name === 'coupons' ? [{ ...COUPON, coupon_id: 4 }] : [{ ...ORDER, id: 9 }]
    success(pending[1], newerRows)
    await vi.waitFor(() => expect(page.data.items).toHaveLength(1))
    const currentItems = structuredClone(page.data.items)
    success(pending[0], ROWS[name])
    for (let index = 0; index < 6; index += 1) await Promise.resolve()
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    expect(page.data.items).toEqual(currentItems)
    expect(page.data.loggedIn).toBe(true)
    expect(page.data.errorMessage).toBe('')
  })

  it('prevents merchant actions after a server permission denial', async () => {
    const { auth, mount, wxMock } = setup((options) => {
      if (!options.url.endsWith('/store')) return
      options.success({ statusCode: 403, data: { error: { message: 'Forbidden' } } })
      return true
    })
    await auth.loginWithWeChat()
    const page = mount('merchant')
    await vi.waitFor(() => expect(page.data.loading).toBe(false))
    expect(page.data.allowed).toBe(false)
    expect(page.data.errorMessage).toContain('没有商家编辑权限')
    await page.save()
    page.chooseAnnouncement()
    await page.uploadAnnouncement('wxfile://tmp/banner.jpg')
    expect(wxMock.uploadFile).not.toHaveBeenCalled()
    expect(wxMock.chooseMedia).not.toHaveBeenCalled()
    expect(wxMock.request.mock.calls.some(([options]) => options.method === 'PATCH')).toBe(false)
  })

  it.each([
    ['91', '118.1'], ['24.5', '181'], ['24.5', ''], ['NaN', '118.1'], ['24.5e2', '118.1'],
  ])('validates paired merchant coordinates before saving (%s,%s)', async (latitude, longitude) => {
    const { auth, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount('merchant')
    await vi.waitFor(() => expect(page.data.allowed).toBe(true))
    page.setData({ form: { ...page.data.form, latitude, longitude } })
    await page.save()
    expect(page.data.errorMessage).toMatch(/[\u3400-\u9fff]/)
    expect(wxMock.request.mock.calls.some(([options]) => options.method === 'PATCH')).toBe(false)
  })

  it('allows blank paired coordinates and prevents upload while a save is pending', async () => {
    let savingRequest
    const { auth, mount, wxMock } = setup((options) => {
      if (options.method !== 'PATCH') return
      savingRequest = options
      return true
    })
    await auth.loginWithWeChat()
    const page = mount('merchant')
    await vi.waitFor(() => expect(page.data.allowed).toBe(true))
    page.setData({ form: { ...page.data.form, latitude: '', longitude: '' } })
    const saving = page.save()
    expect(savingRequest.data).toMatchObject({ latitude: null, longitude: null })
    page.chooseAnnouncement()
    await page.uploadAnnouncement('wxfile://tmp/banner.jpg')
    await page.save()
    expect(wxMock.chooseMedia).not.toHaveBeenCalled()
    expect(wxMock.uploadFile).not.toHaveBeenCalled()
    expect(wxMock.request.mock.calls.filter(([options]) => options.method === 'PATCH')).toHaveLength(1)
    success(savingRequest, { ...STORE, latitude: null, longitude: null })
    await saving
    expect(page.data).toMatchObject({ saving: false, successMessage: '商家信息已保存。' })
  })

  it('prevents save and duplicate uploads while a native announcement upload is pending', async () => {
    const { auth, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount('merchant')
    await vi.waitFor(() => expect(page.data.allowed).toBe(true))
    let uploadingRequest
    wxMock.uploadFile.mockImplementation((options) => { uploadingRequest = options })
    const uploading = page.uploadAnnouncement('wxfile://tmp/banner.jpg')
    await page.save()
    await page.uploadAnnouncement('wxfile://tmp/duplicate.jpg')
    page.chooseAnnouncement()
    expect(wxMock.uploadFile).toHaveBeenCalledOnce()
    expect(wxMock.chooseMedia).not.toHaveBeenCalled()
    expect(wxMock.request.mock.calls.some(([options]) => options.method === 'PATCH')).toBe(false)
    uploadingRequest.success({ statusCode: 200, data: JSON.stringify({ data: { ...STORE, announcement_image_url: '/api/v1/media/store/new.webp' } }) })
    await uploading
    expect(page.data).toMatchObject({ uploading: false, announcementUrl: `${ORIGIN}/api/v1/media/store/new.webp` })
  })

  it('keeps optional merchant controls hidden for a guest account page', () => {
    const { mount, wxMock } = setup()
    const page = mount('account')
    expect(page.data.canManageStore).toBe(false)
    page.openMerchant()
    expect(wxMock.navigateTo).not.toHaveBeenCalled()
    expect(wxMock.request).not.toHaveBeenCalled()
    page.openFavorites()
    expect(page.data.loginVisible).toBe(true)
  })

  it.each(['coupons', 'orders'])('displays %s dates in China time at day and year boundaries', async (name) => {
    const timestamps = [
      '2026-10-31T15:59:59Z',
      '2026-10-31T16:00:00Z',
      '2026-12-31T16:00:00Z',
      '2026-11-01T00:00:00+08:00',
    ]
    const { auth, mount } = setup((options) => {
      if (!new URL(options.url).pathname.endsWith(`/mini/shop/${name}`)) return
      const rows = timestamps.map((timestamp, index) => name === 'coupons'
        ? { ...COUPON, coupon_id: index + 1, expires_at: timestamp }
        : { ...ORDER, id: index + 1, completed_at: timestamp })
      success(options, rows)
      return true
    })
    await auth.loginWithWeChat()
    const page = mount(name)
    await vi.waitFor(() => expect(page.data.items).toHaveLength(4))
    const field = name === 'coupons' ? 'expiresLabel' : 'completedLabel'
    expect(page.data.items.map((item) => item[field])).toEqual([
      '2026-10-31', '2026-11-01', '2027-01-01', '2026-11-01',
    ])
  })

  it.each(['openProfileDialog', 'editProfile', 'openLogin'])('opens login through %s when the visible account session expires', async (entry) => {
    const { auth, mount } = setup()
    await auth.loginWithWeChat()
    const page = mount('account')
    expect(page.data.authStatus).toBe('authenticated')
    const expiredTime = Date.now() + 300_001
    vi.spyOn(Date, 'now').mockReturnValue(expiredTime)

    page[entry]()

    expect(page.data).toMatchObject({ authStatus: 'guest', loginVisible: true, loginMode: 'login', authBusy: false })
    expect(auth.getAuthState().status).toBe('guest')
  })

  it.each(['openProfileDialog', 'editProfile'])('keeps %s in profile mode while the session remains valid', async (entry) => {
    const { auth, mount } = setup()
    await auth.loginWithWeChat()
    const page = mount('account')
    page[entry]()
    expect(page.data).toMatchObject({ authStatus: 'authenticated', loginVisible: true, loginMode: 'profile' })
  })
})
