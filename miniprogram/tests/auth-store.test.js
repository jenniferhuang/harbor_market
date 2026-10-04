'use strict'

const path = require('node:path')

const ORIGIN = 'https://shop.example.cn'
const SESSION_KEY = 'harbor_market_customer_session'
const CART_KEY = 'harbor_market_cart'
const NOW = Date.parse('2026-10-04T08:00:00Z')

function customerFixture(overrides = {}) {
  return { id: 17, nickname: '小港', avatar_url: '', ...overrides }
}

function loginFixture(overrides = {}) {
  return {
    access_token: 'customer-token-abcdefghijklmnop',
    token_type: 'Bearer',
    expires_at: new Date(NOW + 60_000).toISOString(),
    customer: customerFixture(),
    ...overrides,
  }
}

function sessionFixture(overrides = {}) {
  const login = loginFixture()
  return {
    version: 1,
    apiOrigin: ORIGIN,
    accessToken: login.access_token,
    expiresAt: login.expires_at,
    customer: login.customer,
    ...overrides,
  }
}

function success(options, data) {
  options.success({ statusCode: 200, data: { data } })
}

function failure(options, status, message = 'Authentication failed') {
  options.success({ statusCode: status, data: { error: { message } } })
}

function setup({ storedSession, storedCart, request, login, uploadFile, downloadFile } = {}) {
  vi.resetModules()
  const sourceRoot = `${path.resolve(__dirname, '../src')}${path.sep}`
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(sourceRoot)) delete require.cache[modulePath]
  }
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  if (storedSession !== undefined) storage.set(SESSION_KEY, storedSession)
  if (storedCart !== undefined) storage.set(CART_KEY, storedCart)
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn(login || ((options) => options.success({ code: 'wx-one-use-code' }))),
    request: vi.fn(request || ((options) => {
      if (options.url.endsWith('/login')) success(options, loginFixture())
      else if (options.url.endsWith('/logout')) success(options, null)
      else success(options, customerFixture())
    })),
    uploadFile: vi.fn(uploadFile),
    downloadFile: vi.fn(downloadFile),
  }
  global.wx = wxMock
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  return {
    store: require('../src/state/auth-store'),
    sessions: require('../src/state/customer-session'),
    client: require('../src/api/client'),
    wxMock,
    storage,
  }
}

async function nextRequest(wxMock) {
  // Native login completes on a promise continuation before the HTTP exchange.
  await Promise.resolve()
  expect(wxMock.request).toHaveBeenCalled()
  return wxMock.request.mock.calls.at(-1)[0]
}

describe('verified customer authentication state', () => {
  afterEach(() => {
    delete global.wx
    vi.restoreAllMocks()
  })

  it('uses wx.login and persists only the server-verified customer, without admin identity fields', async () => {
    const { store, storage, wxMock } = setup({
      request: (options) => success(options, loginFixture({
        customer: customerFixture({
          is_admin: true, roles: ['admin'], openid: 'secret-openid', session_key: 'private-key',
          avatar_url: 'https://tracking.example/avatar.jpg',
        }),
      })),
    })

    await expect(store.loginWithWeChat()).resolves.toEqual(customerFixture())
    expect(wxMock.login).toHaveBeenCalledOnce()
    expect(wxMock.request.mock.calls[0][0].data).toEqual({ code: 'wx-one-use-code' })
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', busy: false, customer: customerFixture() })
    expect(storage.get(SESSION_KEY)).toEqual(sessionFixture())
    expect(JSON.stringify(storage.get(SESSION_KEY))).not.toMatch(/openid|session_key|is_admin|roles/)
  })

  it('restores a saved customer only after server verification and refreshes its private avatar', async () => {
    let pending
    const verifiedCustomer = customerFixture({ nickname: '服务器昵称', avatar_url: '/api/v1/mini/auth/avatar?v=2' })
    const { store, sessions, wxMock } = setup({
      storedSession: JSON.stringify(sessionFixture()),
      request: (options) => { pending = options },
      downloadFile: (options) => options.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/avatar.jpg' }),
    })
    expect(store.getAuthState()).toMatchObject({ status: 'restoring', customer: null })
    const first = store.restoreSession()
    expect(store.restoreSession()).toBe(first)
    expect(wxMock.request).toHaveBeenCalledOnce()
    expect(pending.header.Authorization).toBe(`Bearer ${sessionFixture().accessToken}`)
    success(pending, verifiedCustomer)

    await expect(first).resolves.toEqual(verifiedCustomer)
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', customer: verifiedCustomer, avatarPath: 'wxfile://tmp/avatar.jpg' })
    expect(sessions.readSession().customer).toEqual(verifiedCustomer)
    expect(wxMock.login).not.toHaveBeenCalled()
  })

  it('removes revoked credentials on 401 instead of restoring an unverified stored customer', async () => {
    const { store, sessions, storage } = setup({
      storedSession: sessionFixture(),
      request: (options) => failure(options, 401),
    })
    await expect(store.restoreSession()).resolves.toBeNull()
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, errorMessage: '登录状态已失效，请重新登录。' })
    expect(sessions.readSession()).toBeNull()
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it.each([
    ['expired', sessionFixture({ expiresAt: new Date(NOW - 1).toISOString() })],
    ['another origin', sessionFixture({ apiOrigin: 'https://other.example.cn' })],
    ['malformed JSON', '{broken'],
    ['invalid token', sessionFixture({ accessToken: 'Bearer token\r\nInjected: true' })],
    ['invalid customer', sessionFixture({ customer: { id: -1 } })],
  ])('does not use %s persisted credentials', async (_label, storedSession) => {
    const { store, sessions, wxMock } = setup({ storedSession })
    await expect(store.restoreSession()).resolves.toBeNull()
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null })
    expect(sessions.readSession()).toBeNull()
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it('falls back to guest on a verification outage and allows a later verified retry', async () => {
    const { store, storage, wxMock } = setup({
      storedSession: sessionFixture(),
      request: (options) => options.fail({ errMsg: 'socket unreachable' }),
    })
    await store.restoreSession()
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null })
    expect(store.getAuthState().errorMessage).toMatch(/[\u3400-\u9fff]/)
    expect(storage.has(SESSION_KEY)).toBe(true)
    wxMock.request.mockImplementation((options) => success(options, customerFixture()))
    await store.restoreSession()
    expect(store.getAuthState().status).toBe('authenticated')
  })

  it('clears only authentication on logout, preserving guest cart and connection settings', async () => {
    const cart = { version: 1, items: [{ productCode: 'APPLE', quantity: 2 }] }
    const { store, sessions, storage, wxMock } = setup({ storedSession: sessionFixture(), storedCart: cart })
    await store.restoreSession()
    await store.logout()

    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, avatarPath: '' })
    expect(sessions.readSession()).toBeNull()
    expect(storage.has(SESSION_KEY)).toBe(false)
    expect(storage.get(CART_KEY)).toEqual(cart)
    expect(storage.get('harbor_market_api_base_url')).toBe(ORIGIN)
    expect(wxMock.removeStorageSync.mock.calls).toEqual([[SESSION_KEY]])
    expect(wxMock.request.mock.calls.at(-1)[0]).toMatchObject({
      url: `${ORIGIN}/api/v1/mini/auth/logout`, method: 'POST',
      header: expect.objectContaining({ Authorization: `Bearer ${sessionFixture().accessToken}` }),
    })
  })

  it('stays signed out locally when server revocation fails', async () => {
    const { store, storage, wxMock } = setup({ storedSession: sessionFixture() })
    await store.restoreSession()
    wxMock.request.mockImplementation((options) => options.fail({ errMsg: 'offline' }))
    await expect(store.logout()).rejects.toMatchObject({ status: 0 })
    expect(storage.has(SESSION_KEY)).toBe(false)
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null })
    expect(store.getAuthState().errorMessage).toContain('已退出当前设备')
  })

  it.each(['logout', 'origin change'])('ignores an old restore response after %s', async (action) => {
    let restoreRequest
    const { store, storage, client } = setup({
      storedSession: sessionFixture(),
      request: (options) => {
        if (options.url.endsWith('/me')) restoreRequest = options
        else success(options, null)
      },
    })
    const restoring = store.restoreSession()
    if (action === 'logout') await store.logout()
    else {
      client.setApiBaseUrl('https://new-shop.example.cn')
      store.resetSessionForApiChange()
    }
    success(restoreRequest, customerFixture())
    await expect(restoring).resolves.toBeNull()
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it.each(['logout', 'origin change'])('rejects an old login response after %s', async (action) => {
    const { store, storage, client, wxMock } = setup({ request: () => {} })
    const loggingIn = store.loginWithWeChat()
    const request = await nextRequest(wxMock)
    if (action === 'logout') await store.logout()
    else {
      client.setApiBaseUrl('https://new-shop.example.cn')
      store.resetSessionForApiChange()
    }
    success(request, loginFixture())
    await expect(loggingIn).rejects.toMatchObject({ status: 409 })
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it('prevents overlapping native login and code-exchange requests', async () => {
    let nativeLogin
    const { store, wxMock } = setup({ login: (options) => { nativeLogin = options } })
    const loggingIn = store.loginWithWeChat()
    await expect(store.loginWithWeChat()).rejects.toMatchObject({ status: 409, message: '正在处理登录，请稍候。' })
    expect(wxMock.login).toHaveBeenCalledOnce()
    expect(wxMock.request).not.toHaveBeenCalled()
    nativeLogin.success({ code: 'real-wx-code' })
    await loggingIn
    expect(wxMock.request).toHaveBeenCalledOnce()
  })

  it('does not exchange a delayed native code after the customer has already logged out', async () => {
    let nativeLogin
    const { store, wxMock } = setup({ login: (options) => { nativeLogin = options } })
    const loggingIn = store.loginWithWeChat()
    await store.logout()
    nativeLogin.success({ code: 'old-wx-code' })
    await expect(loggingIn).rejects.toMatchObject({ status: 409 })
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(store.getAuthState()).toMatchObject({ status: 'guest', busy: false, customer: null })
  })

  it('ignores a pending profile response after logout instead of repersisting the customer', async () => {
    let profileRequest
    const { store, storage } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture())
        else if (options.url.endsWith('/profile')) profileRequest = options
        else success(options, null)
      },
    })
    await store.loginWithWeChat()
    const saving = store.updateProfile({ nickname: '旧操作' })
    await store.logout()
    success(profileRequest, customerFixture({ nickname: '旧操作' }))
    await expect(saving).rejects.toMatchObject({ status: 409 })
    expect(storage.has(SESSION_KEY)).toBe(false)
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, busy: false })
  })

  it('ignores a private avatar download that completes after logout', async () => {
    let avatarDownload
    const customer = customerFixture({ avatar_url: '/api/v1/mini/auth/avatar?v=2' })
    const { store, storage } = setup({
      storedSession: sessionFixture({ customer }),
      request: (options) => success(options, customer),
      downloadFile: (options) => { avatarDownload = options },
    })
    const restoring = store.restoreSession()
    await Promise.resolve()
    expect(avatarDownload).toBeDefined()
    await store.logout()
    avatarDownload.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/stale-avatar.jpg' })
    await expect(restoring).resolves.toBeNull()
    expect(storage.has(SESSION_KEY)).toBe(false)
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, avatarPath: '' })
  })

  it('does not let an older restore overwrite a newer successful login', async () => {
    let restoreRequest
    const newerCustomer = customerFixture({ id: 18, nickname: '新账户' })
    const { store, storage } = setup({
      storedSession: sessionFixture(),
      request: (options) => {
        if (options.url.endsWith('/me')) restoreRequest = options
        else success(options, loginFixture({ access_token: 'new-token-abcdefghijklmnop', customer: newerCustomer }))
      },
    })
    const restoring = store.restoreSession()
    await store.loginWithWeChat()
    success(restoreRequest, customerFixture())
    await expect(restoring).resolves.toBeNull()
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', customer: newerCustomer })
    expect(storage.get(SESSION_KEY).customer).toEqual(newerCustomer)
  })

  it.each(['success', 'unauthorized'])('leaves restoring state if the token expires while /me is pending (%s)', async (response) => {
    let restoreRequest
    const { store, storage } = setup({
      storedSession: sessionFixture(),
      request: (options) => { restoreRequest = options },
    })
    const observed = []
    const unsubscribe = store.subscribe((state) => observed.push(state))
    const restoring = store.restoreSession()
    Date.now.mockReturnValue(NOW + 60_001)
    if (response === 'success') success(restoreRequest, customerFixture())
    else failure(restoreRequest, 401)

    await expect(restoring).resolves.toBeNull()
    // Observe the published state before a getter can perform lazy expiry cleanup.
    expect(observed.at(-1)).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
    unsubscribe()
  })

  it('signs out when the token expires during the restore avatar download', async () => {
    let avatarDownload
    const customer = customerFixture({ avatar_url: '/api/v1/mini/auth/avatar?v=3' })
    const { store, storage } = setup({
      storedSession: sessionFixture({ customer }),
      request: (options) => success(options, customer),
      downloadFile: (options) => { avatarDownload = options },
    })
    const observed = []
    const unsubscribe = store.subscribe((state) => observed.push(state))
    const restoring = store.restoreSession()
    await Promise.resolve()
    expect(avatarDownload).toBeDefined()
    Date.now.mockReturnValue(NOW + 60_001)
    avatarDownload.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/expired-avatar.jpg' })

    await expect(restoring).resolves.toBeNull()
    expect(observed.at(-1)).toMatchObject({ status: 'guest', customer: null, avatarPath: '', busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
    unsubscribe()
  })

  it('signs out when the token expires while a profile update is pending', async () => {
    let profileRequest
    const { store, storage } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture())
        else profileRequest = options
      },
    })
    await store.loginWithWeChat()
    const observed = []
    const unsubscribe = store.subscribe((state) => observed.push(state))
    const saving = store.updateProfile({ nickname: '新昵称' })
    Date.now.mockReturnValue(NOW + 60_001)
    success(profileRequest, customerFixture({ nickname: '新昵称' }))

    await expect(saving).rejects.toMatchObject({ status: 409 })
    expect(observed.at(-1)).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
    unsubscribe()
  })

  it('returns no restored customer when logout and a new login happen during an old avatar download', async () => {
    let avatarDownload
    const oldCustomer = customerFixture({ avatar_url: '/api/v1/mini/auth/avatar?v=1' })
    const newerCustomer = customerFixture({ id: 18, nickname: '新账户' })
    const { store, storage } = setup({
      storedSession: sessionFixture({ customer: oldCustomer }),
      request: (options) => {
        if (options.url.endsWith('/me')) success(options, oldCustomer)
        else if (options.url.endsWith('/login')) success(options, loginFixture({ access_token: 'new-token-abcdefghijklmnop', customer: newerCustomer }))
        else success(options, null)
      },
      downloadFile: (options) => { avatarDownload = options },
    })
    const restoring = store.restoreSession()
    await Promise.resolve()
    expect(avatarDownload).toBeDefined()
    await store.logout()
    await store.loginWithWeChat()
    avatarDownload.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/old-avatar.jpg' })

    await expect(restoring).resolves.toBeNull()
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', customer: newerCustomer, avatarPath: '' })
    expect(storage.get(SESSION_KEY).customer).toEqual(newerCustomer)
  })

  it('rejects old profile success when logout and a new login happen during its avatar download', async () => {
    let avatarDownload
    let loginCount = 0
    const newerCustomer = customerFixture({ id: 18, nickname: '新账户' })
    const { store, storage } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) {
          loginCount += 1
          success(options, loginCount === 1 ? loginFixture() : loginFixture({ access_token: 'new-token-abcdefghijklmnop', customer: newerCustomer }))
        } else if (options.url.endsWith('/profile')) {
          success(options, customerFixture({ nickname: '旧账户新昵称', avatar_url: '/api/v1/mini/auth/avatar?v=3' }))
        } else success(options, null)
      },
      downloadFile: (options) => { avatarDownload = options },
    })
    await store.loginWithWeChat()
    const saving = store.updateProfile({ nickname: '旧账户新昵称' })
    await Promise.resolve()
    expect(avatarDownload).toBeDefined()
    await store.logout()
    await store.loginWithWeChat()
    avatarDownload.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/old-avatar.jpg' })

    await expect(saving).rejects.toMatchObject({ status: 409 })
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', customer: newerCustomer, avatarPath: '' })
    expect(storage.get(SESSION_KEY).customer).toEqual(newerCustomer)
  })

  it('does not publish a late logout revocation error into a new API origin', async () => {
    let revokeRequest
    const { store, client } = setup({
      request: (options) => {
        if (options.url.endsWith('/logout')) revokeRequest = options
        else success(options, loginFixture())
      },
    })
    await store.loginWithWeChat()
    const loggingOut = store.logout()
    client.setApiBaseUrl('https://new-shop.example.cn')
    store.resetSessionForApiChange()
    revokeRequest.fail({ errMsg: 'old server unreachable' })
    await expect(loggingOut).rejects.toMatchObject({ status: 0 })
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, errorMessage: '' })
  })

  it.each([
    ['token scheme', { token_type: 'Admin' }],
    ['token', { access_token: 'bad\nheader' }],
    ['expired token', { expires_at: new Date(NOW).toISOString() }],
    ['customer ID', { customer: customerFixture({ id: '17' }) }],
  ])('rejects a login response with an invalid %s', async (_label, overrides) => {
    const { store, storage } = setup({ request: (options) => success(options, loginFixture(overrides)) })
    await expect(store.loginWithWeChat()).rejects.toMatchObject({ status: 502 })
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it('treats optional nickname and avatar save failures as warnings after verified login', async () => {
    const { store, storage, wxMock } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture())
        else failure(options, 503, 'upstream detail')
      },
      uploadFile: (options) => options.fail({ errMsg: 'uploadFile:fail network' }),
    })
    await expect(store.loginWithWeChat({ nickname: '新昵称', avatarPath: 'wxfile://tmp/avatar.jpg' })).resolves.toEqual(customerFixture())
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', customer: customerFixture(), busy: false })
    expect(store.getAuthState().errorMessage).toContain('昵称暂未保存')
    expect(store.getAuthState().errorMessage).toContain('头像暂未保存')
    expect(storage.get(SESSION_KEY).customer).toEqual(customerFixture())
    expect(wxMock.request.mock.calls[0][0].data).toEqual({ code: 'wx-one-use-code' })
    expect(wxMock.uploadFile).toHaveBeenCalledOnce()
  })

  it('does not retain identity when a profile save reports revoked credentials', async () => {
    const { store, storage } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture())
        else failure(options, 401)
      },
    })
    await expect(store.loginWithWeChat({ nickname: '新昵称' })).rejects.toMatchObject({ status: 401 })
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, busy: false })
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it('saves verified profile changes with authentication and loads the resulting private avatar', async () => {
    const avatarCustomer = customerFixture({ nickname: '新昵称', avatar_url: '/api/v1/mini/auth/avatar?v=4' })
    const { store, storage, wxMock } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture())
        else success(options, customerFixture({ nickname: options.data.nickname }))
      },
      uploadFile: (options) => options.success({ statusCode: 200, data: JSON.stringify({ data: avatarCustomer }) }),
      downloadFile: (options) => options.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/private-avatar.jpg' }),
    })
    await store.loginWithWeChat()
    await expect(store.updateProfile({ nickname: ' 新昵称 ', avatarPath: '/tmp/avatar.png' })).resolves.toEqual(avatarCustomer)
    expect(storage.get(SESSION_KEY).customer).toEqual(avatarCustomer)
    expect(store.getAuthState()).toMatchObject({ avatarPath: 'wxfile://tmp/private-avatar.jpg', errorMessage: '', busy: false })
    expect(wxMock.request.mock.calls.at(-1)[0]).toMatchObject({
      method: 'PATCH', data: { nickname: '新昵称' },
      header: expect.objectContaining({ Authorization: `Bearer ${loginFixture().access_token}` }),
    })
  })

  it('allows an avatar-only profile update without changing or requiring a nickname', async () => {
    const customer = customerFixture({ avatar_url: '/api/v1/mini/auth/avatar?v=5' })
    const { store, wxMock } = setup({
      uploadFile: (options) => options.success({ statusCode: 200, data: JSON.stringify({ data: customer }) }),
      downloadFile: (options) => options.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/new-avatar.jpg' }),
    })
    await store.loginWithWeChat()
    wxMock.request.mockClear()
    await expect(store.updateProfile({ nickname: '', avatarPath: 'wxfile://tmp/new-avatar.jpg' })).resolves.toEqual(customer)
    expect(wxMock.uploadFile).toHaveBeenCalledOnce()
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(store.getAuthState().customer.nickname).toBe('小港')
    await expect(store.updateProfile({ nickname: '', avatarPath: '' })).rejects.toMatchObject({ status: 422 })
  })

  it('handles nickname length as Unicode characters without truncating emoji into broken text', async () => {
    const nickname = '😀'.repeat(64)
    const { store, sessions, wxMock } = setup({
      request: (options) => {
        if (options.url.endsWith('/login')) success(options, loginFixture({ customer: customerFixture({ nickname }) }))
        else success(options, customerFixture({ nickname: options.data.nickname }))
      },
    })
    await store.loginWithWeChat({ nickname })
    expect(sessions.readSession().customer.nickname).toBe(nickname)
    wxMock.login.mockClear()
    await expect(store.loginWithWeChat({ nickname: '😀'.repeat(65) })).rejects.toMatchObject({ status: 422 })
    expect(wxMock.login).not.toHaveBeenCalled()
  })

  it('rejects remote avatar input and control characters before requesting a WeChat code', async () => {
    const { store, wxMock } = setup()
    await expect(store.loginWithWeChat({ avatarPath: 'https://tracking.example/avatar.jpg' })).rejects.toMatchObject({ status: 422 })
    await expect(store.loginWithWeChat({ nickname: '小港\u0000' })).rejects.toMatchObject({ status: 422 })
    expect(wxMock.login).not.toHaveBeenCalled()
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(wxMock.uploadFile).not.toHaveBeenCalled()
  })

  it('offers optional login once per launch and lets guests continue without a request', () => {
    const { store, wxMock } = setup()
    expect(store.shouldPromptForLogin()).toBe(true)
    expect(store.shouldPromptForLogin()).toBe(false)
    expect(store.getAuthState().status).toBe('guest')
    store.resetSessionForApiChange()
    expect(store.shouldPromptForLogin()).toBe(false)
    expect(wxMock.login).not.toHaveBeenCalled()
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(setup().store.shouldPromptForLogin()).toBe(true)
  })

  it('does not prompt an authenticated customer or one whose session is being restored', async () => {
    const { store } = setup({ storedSession: sessionFixture() })
    expect(store.shouldPromptForLogin()).toBe(false)
    await store.restoreSession()
    expect(store.shouldPromptForLogin()).toBe(false)
  })

  it('expires an active identity and stops exposing its customer data', async () => {
    const { store, storage } = setup()
    await store.loginWithWeChat()
    Date.now.mockReturnValue(NOW + 60_001)
    expect(store.getAuthState()).toMatchObject({ status: 'guest', customer: null, errorMessage: '登录状态已失效，请重新登录。' })
    expect(storage.has(SESSION_KEY)).toBe(false)
  })

  it('shows a Chinese fallback for backend login errors and never sends a missing native code', async () => {
    const { store, wxMock } = setup({ request: (options) => failure(options, 503, 'Provider secret detail') })
    await expect(store.loginWithWeChat()).rejects.toMatchObject({ status: 503 })
    expect(store.getAuthState().errorMessage).toBe('服务暂时不可用，请稍后重试。')
    wxMock.login.mockImplementation((options) => options.success({}))
    wxMock.request.mockClear()
    await expect(store.loginWithWeChat()).rejects.toMatchObject({ status: 401 })
    expect(store.getAuthState().errorMessage).toContain('未能获取微信登录凭证')
    expect(wxMock.request).not.toHaveBeenCalled()
  })
})
