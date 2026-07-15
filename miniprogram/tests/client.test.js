'use strict'

function storageWx(overrides = {}) {
  const storage = new Map()
  return {
    storage,
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    request: vi.fn(),
    ...overrides,
  }
}

function freshClient(wxMock) {
  vi.resetModules()
  global.wx = wxMock
  return require('../src/api/client')
}

describe('Mini Program API client', () => {
  afterEach(() => {
    delete global.wx
    vi.restoreAllMocks()
  })

  it('validates, normalizes, and persists an origin-only API base URL', () => {
    const wxMock = storageWx()
    const client = freshClient(wxMock)

    expect(client.getApiBaseUrl()).toBe('http://127.0.0.1:8080')
    expect(client.setApiBaseUrl(' https://shop.example.cn/ ')).toBe(
      'https://shop.example.cn',
    )
    expect(client.getApiBaseUrl()).toBe('https://shop.example.cn')
    expect(wxMock.setStorageSync).toHaveBeenCalledWith(
      'harbor_market_api_base_url',
      'https://shop.example.cn',
    )

    for (const invalid of [
      '/api/v1',
      'ftp://example.cn',
      'https://user@example.cn',
      'https://example.cn/api',
      'https://example.cn:70000',
    ]) {
      expect(() => client.setApiBaseUrl(invalid)).toThrow(TypeError)
    }
  })

  it('resolves only local relative or same-origin media URLs', () => {
    const client = freshClient(storageWx())
    client.setApiBaseUrl('https://shop.example.cn')

    expect(client.absoluteMediaUrl('/api/v1/media/products/a.webp')).toBe(
      'https://shop.example.cn/api/v1/media/products/a.webp',
    )
    expect(client.absoluteMediaUrl('https://shop.example.cn/api/v1/media/a.webp')).toBe(
      'https://shop.example.cn/api/v1/media/a.webp',
    )
    expect(client.absoluteMediaUrl('https://tracker.example/a.webp')).toBe('')
    expect(client.absoluteMediaUrl('//tracker.example/a.webp')).toBe(
      'https://shop.example.cn/tracker.example/a.webp',
    )
  })

  it('unwraps the standard data envelope and sends bounded request defaults', async () => {
    const wxMock = storageWx({
      request: vi.fn((options) => {
        options.success({ statusCode: 200, data: { data: { status: 'ok' } } })
      }),
    })
    const client = freshClient(wxMock)
    client.setApiBaseUrl('http://127.0.0.1:8080')

    await expect(client.request('/api/v1/health')).resolves.toEqual({ status: 'ok' })
    expect(wxMock.request).toHaveBeenCalledWith(
      expect.objectContaining({
        url: 'http://127.0.0.1:8080/api/v1/health',
        method: 'GET',
        timeout: 10_000,
        header: { Accept: 'application/json' },
      }),
    )
  })

  it('rejects malformed successful envelopes instead of trusting arbitrary payloads', async () => {
    const client = freshClient(
      storageWx({
        request: vi.fn((options) => {
          options.success({ statusCode: 200, data: { items: [] } })
        }),
      }),
    )

    await expect(client.request('/api/v1/catalog/products')).rejects.toMatchObject({
      name: 'ApiError',
      status: 502,
    })
  })

  it('retains safe backend 4xx details but hides server error messages', async () => {
    const responses = [
      {
        statusCode: 422,
        data: {
          error: {
            code: 'invalid_filter',
            message: '筛选条件无效',
            fields: [{ field: 'page', message: '页码无效' }],
          },
        },
      },
      {
        statusCode: 500,
        data: { error: { code: 'sql_error', message: 'secret database detail' } },
      },
    ]
    const client = freshClient(
      storageWx({
        request: vi.fn((options) => options.success(responses.shift())),
      }),
    )

    await expect(client.request('/api/v1/catalog/products')).rejects.toMatchObject({
      status: 422,
      code: 'invalid_filter',
      message: '筛选条件无效',
      fieldErrors: { page: '页码无效' },
    })
    await expect(client.request('/api/v1/catalog/products')).rejects.toMatchObject({
      status: 500,
      message: '服务暂时不可用，请稍后重试。',
    })
  })

  it('normalizes transport failures and rejects paths outside the API namespace', async () => {
    const client = freshClient(
      storageWx({ request: vi.fn((options) => options.fail({ errMsg: 'offline' })) }),
    )

    await expect(client.request('/api/v1/health')).rejects.toMatchObject({ status: 0 })
    await expect(client.request('https://evil.example')).rejects.toBeInstanceOf(TypeError)
  })
})
