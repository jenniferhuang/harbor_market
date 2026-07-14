import { afterEach, describe, expect, it, vi } from 'vitest'
import { AUTH_REQUIRED_EVENT, ApiClient, ApiError } from '../api/client'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('API client', () => {
  it('uses a same-origin path and includes cookie credentials', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ data: { ok: true } })))
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

    await expect(client.post('/api/v1/auth/register', {})).rejects.toMatchObject<ApiError>({
      status: 422,
      fieldErrors: { confirmPassword: 'Passwords do not match' },
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

    await expect(client.post('/api/v1/auth/register', {})).rejects.toMatchObject<ApiError>({
      status: 409,
      code: 'USERNAME_TAKEN',
      message: 'Choose a different username.',
      fieldErrors: { username: 'This username is already registered.' },
    })
  })

  it('sends FormData without forcing a JSON content type', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true })))
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
    const fetchMock = vi.fn(async () =>
      new Response(new Uint8Array([80, 75, 3, 4]), {
        headers: { 'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
      }),
    )
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
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }))
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

    await expect(client.get('/api/v1/admin/products')).rejects.toMatchObject<ApiError>({
      status: 401,
    })

    expect(listener).toHaveBeenCalledOnce()
  })

  it('does not announce expected authentication-endpoint failures', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    const listener = vi.fn()
    window.addEventListener(AUTH_REQUIRED_EVENT, listener, { once: true })
    const client = new ApiClient()

    await expect(client.post('/api/v1/auth/login', {})).rejects.toMatchObject<ApiError>({
      status: 401,
    })

    expect(listener).not.toHaveBeenCalled()
    window.removeEventListener(AUTH_REQUIRED_EVENT, listener)
  })
})
