import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, ApiError } from '../api/client'

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
})
