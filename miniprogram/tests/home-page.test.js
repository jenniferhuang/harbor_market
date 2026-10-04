'use strict'

const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const NOW = Date.parse('2026-10-06T08:00:00Z')
const CUSTOMER = { id: 17, nickname: '小港', avatar_url: '' }
const PRODUCT = {
  product_code: 'APPLE',
  name: '苹果美式',
  base_price_cents: 1600,
  stock_status: 'in_stock',
  images: [{ image_type: 'cover', url: '/api/v1/media/apple' }],
  skus: [{ is_active: true, stock_quantity: 10 }],
}
const COUPON_EVENT = { currentTarget: { dataset: { id: 42 } } }

function homeFixture(overrides = {}) {
  return {
    store: {
      name: '港湾小店', phone: '05921234567', address: '厦门市示例路一号',
      announcement_image_url: '/api/v1/shop/media/announcement', latitude: null, longitude: null,
    },
    carousel: [
      { id: 1, title: '今日好物', media_type: 'image', url: '/api/v1/shop/media/photo' },
      { id: 2, title: '店内视频', media_type: 'video', url: '/api/v1/shop/media/video' },
    ],
    coupons: [{
      id: 42, title: '店内满减券', min_spend_cents: 10000, discount_cents: 500,
      starts_at: '2026-10-01T00:00:00Z', expires_at: '2026-10-31T16:00:00Z',
    }],
    categories: [{ id: 1, code: 'COFFEE', name: '咖啡', image_url: '/api/v1/shop/media/category' }],
    hot_products: [{ product: PRODUCT, sold_quantity: 12, order_count: 8, repeat_purchase_count: 3 }],
    hot_products_source: 'sales',
    ...overrides,
  }
}

function success(options, data) {
  options.success({ statusCode: 200, data: { data } })
}

function setup(config = {}) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(SOURCE_ROOT + path.sep)) delete require.cache[modulePath]
  }
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  const payload = config.home || homeFixture()
  const claims = []
  function respondDefault(options) {
    if (options.url.endsWith('/shop/home')) success(options, payload)
    else if (options.url.endsWith('/auth/login')) {
      success(options, {
        token_type: 'Bearer', access_token: 'customer-token-abcdefghijklmnop',
        expires_at: new Date(NOW + 60_000).toISOString(), customer: CUSTOMER,
      })
    } else if (options.url.includes('/mini/shop/coupons?')) success(options, claims)
    else if (options.url.endsWith('/claim')) {
      const row = { coupon_id: 42, claimed_at: new Date(NOW).toISOString() }
      claims.push(row)
      success(options, row)
    } else if (options.url.endsWith('/auth/logout')) success(options, null)
    else throw new Error('Unexpected test request: ' + options.url)
  }
  const pauseVideo = vi.fn()
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'real-native-code' })),
    request: vi.fn((options) => {
      if (config.intercept && config.intercept(options)) return
      respondDefault(options)
    }),
    navigateTo: vi.fn(),
    switchTab: vi.fn(),
    showToast: vi.fn(),
    showModal: vi.fn(),
    makePhoneCall: vi.fn(),
    openLocation: vi.fn(),
    setClipboardData: vi.fn((options) => options.success()),
    previewImage: vi.fn(),
    createVideoContext: vi.fn(() => ({ pause: pauseVideo })),
  }
  global.wx = wxMock
  const store = require('../src/state/auth-store')
  const client = require('../src/api/client')
  const navigation = require('../src/state/catalog-navigation')
  let definition
  global.Page = vi.fn((value) => { definition = value })
  require('../src/pages/home/home')
  function mount() {
    const page = {
      ...definition,
      data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
    }
    page.onLoad()
    page.onShow()
    return page
  }
  return { mount, store, client, navigation, wxMock, pauseVideo, respondDefault }
}

async function ready(page) {
  await vi.waitFor(() => expect(page.data.loading).toBe(false))
}

function settle() {
  return new Promise((resolve) => setImmediate(resolve))
}

describe('storefront landing page behavior', () => {
  afterEach(() => {
    delete global.wx
    delete global.Page
    vi.restoreAllMocks()
  })

  it('loads real store, mixed promotions, coupons, representative categories and sales products', async () => {
    const { mount } = setup()
    const page = mount()
    await ready(page)
    expect(page.data.store).toMatchObject({
      name: '港湾小店', phone: '05921234567', address: '厦门市示例路一号',
      announcementImageUrl: ORIGIN + '/api/v1/shop/media/announcement', hasCoordinates: false,
    })
    expect(page.data.carousel.map((item) => item.media_type)).toEqual(['image', 'video'])
    expect(page.data.carousel[1].url).toBe(ORIGIN + '/api/v1/shop/media/video')
    expect(page.data.categories[0]).toMatchObject({ code: 'COFFEE', imageUrl: ORIGIN + '/api/v1/shop/media/category' })
    expect(page.data.coupons[0]).toMatchObject({
      displayDiscount: '5.00', thresholdText: '满 100.00 元可用',
      validityText: '有效期至 2026.11.01', claimed: false,
    })
    expect(page.data.hotProducts[0]).toMatchObject({
      name: '苹果美式', displayPrice: '¥16.00', soldQuantity: 12, repeatPurchaseCount: 3,
    })
    expect(page.data.hotProductsTitle).toBe('热销商品')
  })

  it.each([
    ['featured', '店主推荐'],
    ['newest', '新上架好物'],
  ])('describes %s fallback products truthfully instead of calling them sales rankings', async (source, title) => {
    const { mount } = setup({ home: homeFixture({ hot_products_source: source }) })
    const page = mount()
    await ready(page)
    expect(page.data.hotProductsSource).toBe(source)
    expect(page.data.hotProductsTitle).toBe(title)
    expect(page.data.hotProductsIntro).not.toContain('销量')
  })

  it('keeps search, category navigation and product details available after a guest dismisses login', async () => {
    const { mount, navigation, wxMock } = setup()
    const page = mount()
    await ready(page)
    expect(page.data.loginVisible).toBe(true)
    page.closeLogin()
    page.openSearch()
    page.selectCategory({ currentTarget: { dataset: { code: 'COFFEE' } } })
    expect(navigation.consumeCatalogCategory()).toBe('COFFEE')
    expect(navigation.consumeCatalogCategory()).toBeNull()
    page.openProduct({ detail: { productCode: 'APPLE' } })
    expect(wxMock.navigateTo.mock.calls.map(([options]) => options.url)).toEqual([
      '/pages/search/search', '/pages/product/product?code=APPLE',
    ])
    expect(wxMock.switchTab).toHaveBeenCalledWith({ url: '/pages/catalog/catalog' })
    page.onHide()
    page.onShow()
    expect(page.data.loginVisible).toBe(false)
  })

  it('opens login for a guest coupon claim without inventing a received coupon', async () => {
    const { mount, wxMock } = setup()
    const page = mount()
    await ready(page)
    page.closeLogin()
    await page.claimCoupon(COUPON_EVENT)
    expect(page.data.loginVisible).toBe(true)
    expect(page.data.coupons[0]).toMatchObject({ claimed: false, claiming: false })
    expect(wxMock.request.mock.calls.some(([options]) => options.url.endsWith('/claim'))).toBe(false)
  })

  it('waits for a verified coupon claim and suppresses duplicate taps while it is pending', async () => {
    let pendingClaim
    const { mount, store, wxMock } = setup({
      intercept(options) {
        if (!options.url.endsWith('/claim')) return false
        pendingClaim = options
        return true
      },
    })
    await store.loginWithWeChat()
    const page = mount()
    await ready(page)
    const claiming = page.claimCoupon(COUPON_EVENT)
    await page.claimCoupon(COUPON_EVENT)
    expect(page.data.coupons[0]).toMatchObject({ claimed: false, claiming: true })
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/claim'))).toHaveLength(1)
    success(pendingClaim, { coupon_id: 42, claimed_at: new Date(NOW).toISOString() })
    await claiming
    expect(page.data.coupons[0]).toMatchObject({ claimed: true, claiming: false })
    expect(wxMock.showToast).toHaveBeenCalledWith({ title: '领取成功', icon: 'success' })
  })

  it('leaves a failed coupon unclaimed and permits a later real retry', async () => {
    let shouldFail = true
    const { mount, store, wxMock } = setup({
      intercept(options) {
        if (!shouldFail || !options.url.endsWith('/claim')) return false
        options.fail({ errMsg: 'offline' })
        return true
      },
    })
    await store.loginWithWeChat()
    const page = mount()
    await ready(page)
    await page.claimCoupon(COUPON_EVENT)
    expect(page.data.coupons[0]).toMatchObject({ claimed: false, claiming: false })
    expect(page.data.couponErrorMessage).toContain('检查网络')
    expect(wxMock.showToast).not.toHaveBeenCalled()
    shouldFail = false
    await page.claimCoupon(COUPON_EVENT)
    expect(page.data.coupons[0].claimed).toBe(true)
    expect(page.data.couponErrorMessage).toBe('')
  })

  it('restores visible claimed coupons beyond the first history page', async () => {
    const { mount, store, wxMock } = setup({
      intercept(options) {
        if (!options.url.includes('/mini/shop/coupons?')) return false
        if (options.url.includes('?page=1&')) {
          success(options, Array.from({ length: 100 }, (_item, index) => ({ coupon_id: index + 100, claimed_at: new Date(NOW).toISOString() })))
        } else success(options, [{ coupon_id: 42, claimed_at: new Date(NOW).toISOString() }])
        return true
      },
    })
    await store.loginWithWeChat()
    const page = mount()
    await ready(page)
    await vi.waitFor(() => expect(page.data.coupons[0].claimed).toBe(true))
    const urls = wxMock.request.mock.calls.map(([options]) => options.url).filter((url) => url.includes('/mini/shop/coupons?'))
    expect(urls).toEqual([
      ORIGIN + '/api/v1/mini/shop/coupons?page=1&page_size=100',
      ORIGIN + '/api/v1/mini/shop/coupons?page=2&page_size=100',
    ])
  })

  it.each([null, 'invalid timestamp'])('restores actual claims while leaving an active coupon with claimed_at=%s unclaimed', async (claimedAt) => {
    const firstCoupon = homeFixture().coupons[0]
    const { mount, store } = setup({
      home: homeFixture({ coupons: [firstCoupon, { ...firstCoupon, id: 43, title: '尚未领取的活动券' }] }),
      intercept(options) {
        if (!options.url.includes('/mini/shop/coupons?')) return false
        success(options, [
          { coupon_id: 42, is_active: true, claimed_at: new Date(NOW).toISOString() },
          { coupon_id: 43, is_active: true, claimed_at: claimedAt },
        ])
        return true
      },
    })
    await store.loginWithWeChat()
    const page = mount()
    await ready(page)
    await vi.waitFor(() => expect(page.data.coupons[0].claimed).toBe(true))
    expect(page.data.coupons[1]).toMatchObject({ id: 43, claimed: false, claiming: false })
    expect(page.data.couponErrorMessage).toBe('')
  })

  it.each(['logout', 'origin change', 'unload'])('ignores an old coupon claim after %s', async (action) => {
    let pendingClaim
    const { mount, store, client, wxMock } = setup({
      intercept(options) {
        if (!options.url.endsWith('/claim')) return false
        pendingClaim = options
        return true
      },
    })
    await store.loginWithWeChat()
    const page = mount()
    await ready(page)
    const claiming = page.claimCoupon(COUPON_EVENT)
    if (action === 'logout') await store.logout()
    if (action === 'origin change') {
      client.setApiBaseUrl('https://another-shop.example.cn')
      store.resetSessionForApiChange()
    }
    if (action === 'unload') page.onUnload()
    page.setData.mockClear()
    success(pendingClaim, { coupon_id: 42, claimed_at: new Date(NOW).toISOString() })
    await claiming
    expect(page.data.coupons[0].claimed).toBe(false)
    expect(page.setData).not.toHaveBeenCalled()
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('does not let an older home response replace a newer refresh', async () => {
    const pending = []
    const { mount } = setup({
      intercept(options) {
        if (!options.url.endsWith('/shop/home')) return false
        pending.push(options)
        return true
      },
    })
    const page = mount()
    const refreshing = page.loadInitialData()
    success(pending[1], homeFixture({ store: { name: '新店信息' } }))
    await refreshing
    success(pending[0], homeFixture({ store: { name: '旧店信息' } }))
    await settle()
    expect(page.data.store.name).toBe('新店信息')
  })

  it.each(['origin change', 'unload'])('discards a pending home response after %s', async (action) => {
    let pending
    const { mount, client, store } = setup({
      intercept(options) {
        if (!options.url.endsWith('/shop/home')) return false
        pending = options
        return true
      },
    })
    const page = mount()
    if (action === 'unload') page.onUnload()
    else {
      client.setApiBaseUrl('https://another-shop.example.cn')
      store.resetSessionForApiChange()
    }
    page.setData.mockClear()
    success(pending, homeFixture())
    await settle()
    expect(page.setData).not.toHaveBeenCalled()
    expect(page.data.hasLoaded).toBe(false)
  })

  it('calls the configured phone and copies an address when coordinates are absent', async () => {
    const { mount, wxMock } = setup()
    const page = mount()
    await ready(page)
    page.callStore()
    page.navigateToStore()
    expect(wxMock.makePhoneCall).toHaveBeenCalledWith(expect.objectContaining({ phoneNumber: '05921234567' }))
    expect(wxMock.setClipboardData).toHaveBeenCalledWith(expect.objectContaining({ data: '厦门市示例路一号' }))
    expect(wxMock.openLocation).not.toHaveBeenCalled()
    page.previewAnnouncement()
    expect(wxMock.previewImage).toHaveBeenCalledWith({
      current: ORIGIN + '/api/v1/shop/media/announcement', urls: [ORIGIN + '/api/v1/shop/media/announcement'],
    })
  })

  it('uses explicitly configured coordinates, including zero, for native navigation', async () => {
    const { mount, wxMock } = setup({
      home: homeFixture({ store: { name: '坐标店', address: '坐标地址', latitude: 0, longitude: 0 } }),
    })
    const page = mount()
    await ready(page)
    page.navigateToStore()
    expect(wxMock.openLocation).toHaveBeenCalledWith(expect.objectContaining({
      latitude: 0, longitude: 0, name: '坐标店', address: '坐标地址',
    }))
    expect(wxMock.setClipboardData).not.toHaveBeenCalled()
  })

  it('pauses the active video when switching slides, opening login, or leaving the page', async () => {
    const { mount, wxMock, pauseVideo } = setup()
    const page = mount()
    await ready(page)
    page.closeLogin()
    page.onCarouselChange({ detail: { current: 1 } })
    page.onVideoPlay({ currentTarget: { dataset: { index: 1 } } })
    expect(page.data.videoPlaying).toBe(true)
    page.openAccountDialog()
    expect(page.data.videoPlaying).toBe(false)
    expect(wxMock.createVideoContext).toHaveBeenCalledWith('home-promo-2', page)
    page.closeLogin()
    page.onVideoPlay({ currentTarget: { dataset: { index: 1 } } })
    page.onCarouselChange({ detail: { current: 0 } })
    page.onVideoPlay({ currentTarget: { dataset: { index: 1 } } })
    expect(page.data.videoPlaying).toBe(false)
    page.onCarouselChange({ detail: { current: 1 } })
    page.onVideoPlay({ currentTarget: { dataset: { index: 1 } } })
    page.onHide()
    expect(page.data.videoPlaying).toBe(false)
    expect(pauseVideo).toHaveBeenCalledTimes(3)
  })
})
