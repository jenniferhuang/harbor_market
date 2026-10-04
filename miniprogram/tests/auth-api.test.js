'use strict'

const path = require('node:path')

const ORIGIN = 'https://shop.example.cn'
const AUTH_PATH = `${ORIGIN}/api/v1/mini/auth`
const NOW = Date.parse('2026-10-04T08:00:00Z')

function sessionFixture(overrides = {}) {
  return {
    version: 1,
    apiOrigin: ORIGIN,
    accessToken: 'customer-token-abcdefghijklmnop',
    expiresAt: new Date(NOW + 60_000).toISOString(),
    customer: { id: 17, nickname: '小港', avatar_url: '/api/v1/mini/auth/avatar?v=1' },
    ...overrides,
  }
}

function setup(overrides = {}) {
  vi.resetModules()
  const sourceRoot = `${path.resolve(__dirname, '../src')}${path.sep}`
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(sourceRoot)) delete require.cache[modulePath]
  }
  const wxMock = {
    getStorageSync: vi.fn((key) => key === 'harbor_market_api_base_url' ? ORIGIN : undefined),
    request: vi.fn(),
    uploadFile: vi.fn(),
    downloadFile: vi.fn(),
    ...overrides,
  }
  global.wx = wxMock
  vi.spyOn(Date, 'now').mockReturnValue(NOW)
  return { api: require('../src/api/auth'), wxMock }
}

describe('customer authentication API boundary', () => {
  afterEach(() => {
    delete global.wx
    vi.restoreAllMocks()
  })

  it('exchanges only the native WeChat code without a bearer token or supplied identity', async () => {
    const loginResult = { access_token: 'opaque-server-token', token_type: 'Bearer' }
    const { api, wxMock } = setup({
      request: vi.fn((options) => options.success({ statusCode: 200, data: { data: loginResult } })),
    })

    await expect(api.exchangeLoginCode('wx-one-use-code')).resolves.toEqual(loginResult)
    expect(wxMock.request).toHaveBeenCalledWith(expect.objectContaining({
      url: `${AUTH_PATH}/login`,
      method: 'POST',
      data: { code: 'wx-one-use-code' },
      header: { Accept: 'application/json', 'Content-Type': 'application/json' },
    }))
  })

  it('authenticates customer reads, nickname updates, and revocation on their own origin', async () => {
    const customer = sessionFixture().customer
    const { api, wxMock } = setup({
      request: vi.fn((options) => options.success({ statusCode: 200, data: { data: customer } })),
    })
    const session = sessionFixture()
    await api.fetchCurrentCustomer(session)
    await api.saveNickname(session, '新昵称')
    await api.revokeSession(session)

    const requests = wxMock.request.mock.calls.map(([options]) => options)
    expect(requests.map(({ url, method }) => ({ url, method }))).toEqual([
      { url: `${AUTH_PATH}/me`, method: 'GET' },
      { url: `${AUTH_PATH}/profile`, method: 'PATCH' },
      { url: `${AUTH_PATH}/logout`, method: 'POST' },
    ])
    for (const options of requests) {
      expect(options.header.Authorization).toBe(`Bearer ${session.accessToken}`)
    }
    expect(requests[1].data).toEqual({ nickname: '新昵称' })
  })

  it('never sends a customer token after its expiry or to another configured server', () => {
    const { api, wxMock } = setup()
    const expired = sessionFixture({ expiresAt: new Date(NOW).toISOString() })
    const otherOrigin = sessionFixture({ apiOrigin: 'https://other.example.cn' })

    for (const session of [expired, otherOrigin]) {
      expect(() => api.fetchCurrentCustomer(session)).toThrow('登录状态已失效')
      expect(() => api.saveNickname(session, '小港')).toThrow('登录状态已失效')
      expect(() => api.revokeSession(session)).toThrow('登录状态已失效')
      expect(() => api.uploadAvatar(session, 'wxfile://tmp/avatar.jpg')).toThrow('登录状态已失效')
      expect(() => api.downloadAvatar(session)).toThrow('登录状态已失效')
    }
    expect(wxMock.request).not.toHaveBeenCalled()
    expect(wxMock.uploadFile).not.toHaveBeenCalled()
    expect(wxMock.downloadFile).not.toHaveBeenCalled()
  })

  it('uploads a chosen native file with authentication and parses the server envelope', async () => {
    const customer = sessionFixture().customer
    const { api, wxMock } = setup({
      uploadFile: vi.fn((options) => options.success({
        statusCode: 200,
        data: JSON.stringify({ data: customer }),
      })),
    })
    const session = sessionFixture()

    await expect(api.uploadAvatar(session, 'wxfile://tmp/avatar.jpg')).resolves.toEqual(customer)
    expect(wxMock.uploadFile).toHaveBeenCalledWith(expect.objectContaining({
      url: `${AUTH_PATH}/avatar`,
      filePath: 'wxfile://tmp/avatar.jpg',
      name: 'file',
      header: { Accept: 'application/json', Authorization: `Bearer ${session.accessToken}` },
    }))
    expect(wxMock.request).not.toHaveBeenCalled()
  })

  it('downloads private avatars from the authenticated endpoint rather than a supplied URL', async () => {
    const { api, wxMock } = setup({
      downloadFile: vi.fn((options) => options.success({ statusCode: 200, tempFilePath: 'wxfile://tmp/private.jpg' })),
    })
    const session = sessionFixture({
      customer: { id: 17, nickname: '小港', avatar_url: 'https://tracking.example/avatar.jpg' },
    })

    await expect(api.downloadAvatar(session)).resolves.toBe('wxfile://tmp/private.jpg')
    expect(wxMock.downloadFile).toHaveBeenCalledWith(expect.objectContaining({
      url: `${AUTH_PATH}/avatar`,
      header: { Authorization: `Bearer ${session.accessToken}` },
    }))
  })

  it('skips avatar downloads for customers without an avatar', async () => {
    const { api, wxMock } = setup()
    await expect(api.downloadAvatar(sessionFixture({
      customer: { id: 17, nickname: '小港', avatar_url: '' },
    }))).resolves.toBe('')
    expect(wxMock.downloadFile).not.toHaveBeenCalled()
  })

  it('keeps public catalog requests anonymous even after an authenticated request', async () => {
    const { api, wxMock } = setup({
      request: vi.fn((options) => options.success({ statusCode: 200, data: { data: [] } })),
    })
    await api.fetchCurrentCustomer(sessionFixture())
    await require('../src/api/client').request('/api/v1/catalog/products')
    expect(wxMock.request.mock.calls[0][0].header).toHaveProperty('Authorization')
    expect(wxMock.request.mock.calls[1][0]).toMatchObject({
      url: `${ORIGIN}/api/v1/catalog/products`,
      header: { Accept: 'application/json' },
    })
    expect(wxMock.request.mock.calls[1][0].header).not.toHaveProperty('Authorization')
  })

  it('rejects malformed avatar responses and localizes transport failures', async () => {
    const { api, wxMock } = setup({
      uploadFile: vi.fn((options) => options.success({ statusCode: 200, data: '<html>upstream error</html>' })),
      downloadFile: vi.fn((options) => options.fail({ errMsg: 'request:fail socket timeout' })),
    })
    await expect(api.uploadAvatar(sessionFixture(), '/tmp/avatar.png')).rejects.toMatchObject({
      status: 502,
      message: '头像上传响应异常，请稍后重试。',
    })
    await expect(api.downloadAvatar(sessionFixture())).rejects.toMatchObject({
      status: 0,
      message: '头像暂时无法加载。',
    })
    wxMock.uploadFile.mockImplementation((options) => options.success({
      statusCode: 401,
      data: JSON.stringify({ error: { message: 'Invalid credentials' } }),
    }))
    await expect(api.uploadAvatar(sessionFixture(), '/tmp/avatar.png')).rejects.toMatchObject({
      status: 401,
      message: '登录状态已失效，请重新登录。',
    })
  })
})
