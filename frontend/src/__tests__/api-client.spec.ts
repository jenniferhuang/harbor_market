import { afterEach, describe, expect, it, vi } from 'vitest'
import { ADMIN_PERMISSION_CHANGED_EVENT, AUTH_REQUIRED_EVENT, ApiClient } from '../api/client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API client', () => {
  it('uses a same-origin path and includes cookie credentials', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(JSON.stringify({ data: { ok: true } }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient()

    await client.post('/api/v1/example', { name: 'test' })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/example',
      expect.objectContaining({ method: 'POST', credentials: 'include' }),
    )
  })

  it('normalizes backend validation issues into field errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            detail: [{ loc: ['body', 'confirm_password'], msg: 'Passwords do not match' }],
          }),
          { status: 422 },
        ),
      ),
    )
    const client = new ApiClient()

    await expect(client.post('/api/v1/auth/register', {})).rejects.toMatchObject({
      status: 422,
      fieldErrors: { confirmPassword: '两次输入的密码不一致。' },
    })
  })

  it('parses the FastAPI error envelope message, code, and field array', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: {
              code: 'USERNAME_TAKEN',
              message: 'Choose a different username.',
              fields: [{ field: 'username', message: 'This username is already registered.' }],
            },
          }),
          { status: 409 },
        ),
      ),
    )
    const client = new ApiClient()

    await expect(client.post('/api/v1/auth/register', {})).rejects.toMatchObject({
      status: 409,
      code: 'USERNAME_TAKEN',
      message: '该用户名已被注册，请更换用户名。',
      fieldErrors: { username: '该用户名已被注册。' },
    })
  })

  it('translates actual Pydantic auth validation without exposing English messages', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      error: {
        code: 'validation_error',
        message: 'Request validation failed',
        fields: [
          { field: 'username', message: "String should match pattern '^[a-z0-9][a-z0-9._-]*$'" },
          { field: 'password', message: 'String should have at least 12 characters' },
        ],
      },
    }), { status: 422 })))

    await expect(new ApiClient().post('/api/v1/auth/register', {})).rejects.toMatchObject({
      message: '请检查并修改标出的字段。',
      fieldErrors: {
        username: '用户名须以小写字母或数字开头，仅可包含小写字母、数字、点、下划线和连字符。',
        password: '密码至少需要 12 个字符。',
      },
    })
  })

  it('uses Chinese fallbacks for unknown errors and network failures', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: {
          code: 'unrecognized_error', message: 'Unexpected backend wording',
          fields: [{ field: 'username', message: 'Unrecognized validation failure' }],
        },
      }), { status: 422 }))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient()

    await expect(client.post('/api/v1/auth/register', {})).rejects.toMatchObject({
      message: '请检查并修改标出的字段。',
      fieldErrors: { username: '用户名格式不正确，请检查后重试。' },
    })
    await expect(client.get('/api/v1/admin/products')).rejects.toMatchObject({
      message: '无法连接服务器，请检查网络连接后重试。',
    })
  })

  it('preserves actionable cleanup-pending semantics in Chinese even on a 503', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      error: { code: 'cleanup_pending', message: 'Database change completed; cleanup remains retryable' },
    }), { status: 503 })))

    await expect(new ApiClient().delete('/api/v1/admin/products/4')).rejects.toMatchObject({
      code: 'cleanup_pending',
      message: '数据已保存，图片清理仍待重试，请查看下方清理任务。',
    })
  })

  it('sends FormData without forcing a JSON content type', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(JSON.stringify({ ok: true }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient()
    const form = new FormData()
    form.append('file', new File(['workbook'], 'products.xlsx'))

    await client.postForm('/api/v1/admin/products/import?dry_run=true', form)

    const init = fetchMock.mock.calls[0]?.[1]
    const headers = new Headers(init?.headers)
    expect(init?.body).toBe(form)
    expect(headers.get('Content-Type')).toBeNull()
    expect(headers.get('Accept')).toBe('application/json')
  })

  it('downloads binary responses with cookie credentials', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(new Uint8Array([80, 75, 3, 4]), {
        headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient()

    const blob = await client.getBlob('/api/v1/admin/products/template.xlsx')

    expect(blob.size).toBe(4)
    expect(blob.type).toContain('spreadsheetml')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/admin/products/template.xlsx',
      expect.objectContaining({ method: 'GET', credentials: 'include' }),
    )
  })

  it('supports PATCH and DELETE methods', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(null, { status: 204 })
    })
    vi.stubGlobal('fetch', fetchMock)
    const client = new ApiClient()

    await client.patch('/api/v1/admin/products/4', { name: 'New name' })
    await client.delete('/api/v1/admin/products/4')

    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('PATCH')
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe('DELETE')
  })

  it('announces an expired session for protected API requests', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    const listener = vi.fn()
    window.addEventListener(AUTH_REQUIRED_EVENT, listener, { once: true })
    const client = new ApiClient()

    await expect(client.get('/api/v1/admin/products')).rejects.toMatchObject({
      status: 401,
    })

    expect(listener).toHaveBeenCalledOnce()
  })

  it('does not announce expected authentication-endpoint failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    const listener = vi.fn()
    window.addEventListener(AUTH_REQUIRED_EVENT, listener, { once: true })
    const client = new ApiClient()

    await expect(client.post('/api/v1/auth/login', {})).rejects.toMatchObject({
      status: 401,
    })

    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener)
  })

  it('announces when the backend reports that administrator permission was revoked', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(
          JSON.stringify({
            error: { code: 'admin_required', message: 'Administrator permission is required' },
          }),
          { status: 403 },
        ),
      ),
    )
    const listener = vi.fn()
    window.addEventListener(ADMIN_PERMISSION_CHANGED_EVENT, listener, { once: true })
    const client = new ApiClient()

    await expect(client.get('/api/v1/admin/products')).rejects.toMatchObject({
      status: 403,
      code: 'admin_required',
    })

    expect(listener).toHaveBeenCalledOnce()
  })
})
