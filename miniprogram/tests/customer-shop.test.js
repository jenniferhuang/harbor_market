'use strict'

const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const CUSTOMER = { id: 17, nickname: '小港', avatar_url: '' }
const PRODUCT = { product_code: 'APPLE', name: '苹果美式', images: [], skus: [] }
const STORE = { name: '港湾集市', phone: '123', address: '厦门', latitude: 24.5, longitude: 118.1, announcement_image_url: '/api/v1/media/store/announcement.webp' }
const CLAIMED_AT = '2026-10-04T08:00:00Z'

function success(options, data) {
  options.success({ statusCode: 200, data: { data } })
}

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
          customer: { ...CUSTOMER, id: 16 + logins },
        })
      } else if (options.url.endsWith('/mini/auth/logout')) success(options, null)
      else if (responder && responder(options) === true) return
      else if (options.url.endsWith('/mini/shop/me')) success(options, { can_manage_store: false })
      else if (options.url.includes('/favorites/')) success(options, { is_favorite: options.method === 'PUT' })
      else if (options.url.endsWith('/claim')) success(options, { coupon_id: 3, claimed_at: CLAIMED_AT })
      else if (options.url.endsWith('/store')) success(options, STORE)
      else success(options, [])
    }),
    uploadFile: vi.fn((options) => options.success({ statusCode: 200, data: JSON.stringify({ data: STORE }) })),
  }
  global.wx = wxMock
  return {
    wxMock,
    api: require('../src/api/customer-shop'),
    auth: require('../src/state/auth-store'),
    sessions: require('../src/state/customer-session'),
    client: require('../src/api/client'),
  }
}

describe('private customer shop API boundaries', () => {
  afterEach(() => { delete global.wx; vi.restoreAllMocks() })

  it('does not send a private request or bearer while browsing as a guest', async () => {
    const { api, wxMock } = setup()
    for (const operation of [
      () => api.fetchFavorites(), () => api.fetchMyCoupons(), () => api.fetchMyOrders(),
      () => api.fetchCustomerShopAccess(), () => api.getFavorite('APPLE'),
      () => api.setFavorite('APPLE', true), () => api.claimCoupon(3),
      () => api.fetchMerchantStore(), () => api.uploadMerchantAnnouncement('/tmp/banner.webp'),
    ]) await expect(operation()).rejects.toMatchObject({ status: 401 })
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(wxMock.uploadFile).not.toHaveBeenCalled()
  })

  it('authenticates private requests only on the verified session origin', async () => {
    const { api, auth, sessions, client, wxMock } = setup()
    await auth.loginWithWeChat()
    const token = sessions.readSession().accessToken
    await api.fetchFavorites()
    expect(wxMock.request.mock.calls.at(-1)[0]).toMatchObject({
      url: `${ORIGIN}/api/v1/mini/shop/favorites?page=1&page_size=20`,
      header: expect.objectContaining({ Authorization: `Bearer ${token}` }),
    })
    wxMock.request.mockClear()
    client.setApiBaseUrl('https://other.example.cn')
    await expect(api.fetchFavorites()).rejects.toMatchObject({ status: 401 })
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it('rejects a late private payload after logout without exposing its customer records', async () => {
    let pending
    const { api, auth } = setup((options) => { pending = options; return true })
    await auth.loginWithWeChat()
    const favorites = api.fetchFavorites()
    await auth.logout()
    success(pending, [PRODUCT])
    await expect(favorites).rejects.toMatchObject({ status: 409 })
    expect(auth.getAuthState().status).toBe('guest')
  })

  it('does not revoke a newer login when an old private request returns 401', async () => {
    let pending
    const { api, auth, sessions } = setup((options) => { pending = options; return true })
    await auth.loginWithWeChat()
    const oldToken = sessions.readSession().accessToken
    const favorites = api.fetchFavorites()
    await auth.logout()
    await auth.loginWithWeChat()
    const newToken = sessions.readSession().accessToken
    expect(newToken).not.toBe(oldToken)
    pending.success({ statusCode: 401, data: { error: { message: 'old session expired' } } })
    await expect(favorites).rejects.toMatchObject({ status: 401 })
    expect(auth.getAuthState()).toMatchObject({ status: 'authenticated', customer: { id: 18 } })
    expect(sessions.readSession().accessToken).toBe(newToken)
  })

  it('uses PUT and DELETE for favorites and requires the server to confirm the requested state', async () => {
    const { api, auth, wxMock } = setup()
    await auth.loginWithWeChat()
    await expect(api.setFavorite(' apple ', true)).resolves.toEqual({ is_favorite: true })
    await expect(api.setFavorite('APPLE', false)).resolves.toEqual({ is_favorite: false })
    const requests = wxMock.request.mock.calls.filter(([options]) => options.url.includes('/favorites/'))
    expect(requests.map(([options]) => options.method)).toEqual(['PUT', 'DELETE'])
    expect(requests[0][0].url).toBe(`${ORIGIN}/api/v1/mini/shop/favorites/APPLE`)
    wxMock.request.mockImplementation((options) => success(options, { is_favorite: true }))
    await expect(api.setFavorite('APPLE', false)).rejects.toMatchObject({ status: 502 })
  })

  it('claims coupons without supplied customer identity or claim fields and validates coupon IDs', async () => {
    const { api, auth, wxMock } = setup()
    await auth.loginWithWeChat()
    await expect(api.claimCoupon(3)).resolves.toMatchObject({ coupon_id: 3 })
    const request = wxMock.request.mock.calls.at(-1)[0]
    expect(request).toMatchObject({ url: `${ORIGIN}/api/v1/mini/shop/coupons/3/claim`, method: 'POST' })
    expect(request).not.toHaveProperty('data')
    wxMock.request.mockClear()
    for (const id of [0, -1, 1.5, '3', NaN, Number.MAX_SAFE_INTEGER + 1]) {
      await expect(api.claimCoupon(id)).rejects.toMatchObject({ status: 422 })
    }
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it.each([null, undefined, '', '   ', 'not-a-date', '2026-10-04', '2026-99-04T08:00:00Z'])('rejects a successful claim envelope without a valid claim timestamp (%s)', async (claimedAt) => {
    const { api, auth } = setup((options) => {
      success(options, { coupon_id: 3, claimed_at: claimedAt })
      return true
    })
    await auth.loginWithWeChat()
    await expect(api.claimCoupon(3)).rejects.toMatchObject({ status: 502 })
  })

  it('rejects a null claim result instead of treating the requested coupon as claimed', async () => {
    const { api, auth } = setup((options) => { success(options, null); return true })
    await auth.loginWithWeChat()
    await expect(api.claimCoupon(3)).rejects.toMatchObject({ status: 502 })
  })

  it('preserves valid claimed history while filtering activity rows and invalid claim timestamps', async () => {
    const inactiveClaim = { coupon_id: 3, claimed_at: CLAIMED_AT, is_active: false }
    const expiredClaim = { coupon_id: 4, claimed_at: '2026-09-01T08:00:00+08:00', expires_at: '2026-09-30T08:00:00Z', is_active: true }
    const { api, auth } = setup((options) => {
      success(options, [
        inactiveClaim, { coupon_id: 5, claimed_at: null, is_active: true },
        expiredClaim, { coupon_id: 6, claimed_at: 'invalid' }, null,
      ])
      return true
    })
    await auth.loginWithWeChat()
    await expect(api.fetchMyCoupons()).resolves.toEqual([inactiveClaim, expiredClaim])
  })

  it('strips merchant owner binding and administrative fields from profile updates', async () => {
    const { api, auth, wxMock } = setup()
    await auth.loginWithWeChat()
    await api.updateMerchantStore({
      ...STORE, owner_customer_id: 999, can_manage_store: true, is_admin: true,
      announcement_image_url: 'https://tracking.example/image.webp',
    })
    expect(wxMock.request.mock.calls.at(-1)[0]).toMatchObject({
      method: 'PATCH', data: { name: STORE.name, phone: STORE.phone, address: STORE.address, latitude: STORE.latitude, longitude: STORE.longitude },
    })
    expect(wxMock.request.mock.calls.at(-1)[0].data).not.toHaveProperty('owner_customer_id')
  })

  it('uploads a native announcement file on the fixed authenticated merchant endpoint', async () => {
    const { api, auth, sessions, wxMock } = setup()
    await auth.loginWithWeChat()
    await expect(api.uploadMerchantAnnouncement('wxfile://tmp/notice.jpg')).resolves.toMatchObject({
      announcement_image_url: `${ORIGIN}/api/v1/media/store/announcement.webp`,
    })
    expect(wxMock.uploadFile).toHaveBeenCalledWith(expect.objectContaining({
      url: `${ORIGIN}/api/v1/mini/shop/store/announcement`, name: 'file',
      filePath: 'wxfile://tmp/notice.jpg',
      header: { Accept: 'application/json', Authorization: `Bearer ${sessions.readSession().accessToken}` },
    }))
    await expect(api.uploadMerchantAnnouncement('https://tracking.example/banner.jpg')).rejects.toMatchObject({ status: 422 })
    expect(wxMock.uploadFile).toHaveBeenCalledOnce()
  })

  it.each([1, 'true', null, undefined])('rejects a nonboolean merchant permission value (%s)', async (value) => {
    const { api, auth } = setup((options) => { success(options, { can_manage_store: value }); return true })
    await auth.loginWithWeChat()
    await expect(api.fetchCustomerShopAccess()).rejects.toMatchObject({ status: 502 })
  })

  it('accepts strict false merchant permission without granting access', async () => {
    const { api, auth } = setup()
    await auth.loginWithWeChat()
    await expect(api.fetchCustomerShopAccess()).resolves.toEqual({ can_manage_store: false })
  })
})
