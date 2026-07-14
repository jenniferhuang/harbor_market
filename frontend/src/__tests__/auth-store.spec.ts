import { describe, expect, it, vi } from 'vitest'
import type { AuthApi, User } from '../api/auth'
import { ApiError } from '../api/client'
import { createAuthStore } from '../auth/store'

const user: User = { id: 7, username: 'marina' }

function createApi(overrides: Partial<AuthApi> = {}): AuthApi {
  return {
    register: vi.fn(async () => undefined),
    login: vi.fn(async () => user),
    me: vi.fn(async () => user),
    logout: vi.fn(async () => undefined),
    ...overrides,
  }
}

describe('auth store', () => {
  it('restores the current user once for concurrent route checks', async () => {
    let resolveUser: ((value: User) => void) | undefined
    const me = vi.fn(
      () =>
        new Promise<User>((resolve) => {
          resolveUser = resolve
        }),
    )
    const store = createAuthStore(createApi({ me }))

    const first = store.restore()
    const second = store.restore()
    resolveUser?.(user)
    await Promise.all([first, second])

    expect(me).toHaveBeenCalledOnce()
    expect(store.isAuthenticated).toBe(true)
    expect(store.user).toEqual(user)
  })

  it('becomes anonymous when restoration is unauthorized', async () => {
    const store = createAuthStore(
      createApi({ me: vi.fn(async () => Promise.reject(new ApiError(401, 'Unauthorized'))) }),
    )

    await store.restore()

    expect(store.state.status).toBe('anonymous')
    expect(store.user).toBeNull()
  })

  it('sets the authenticated user on login and clears it after logout', async () => {
    const logout = vi.fn(async () => undefined)
    const store = createAuthStore(createApi({ logout }))

    await store.login({ username: 'marina', password: 'correct horse' })
    expect(store.user?.username).toBe('marina')

    await store.logout()
    expect(logout).toHaveBeenCalledOnce()
    expect(store.isAuthenticated).toBe(false)
    expect(store.user).toBeNull()
  })

  it('exposes administrator status from the current user', async () => {
    const administrator: User = { id: 8, username: 'catalog-admin', is_admin: true }
    const store = createAuthStore(createApi({ me: vi.fn(async () => administrator) }))

    await store.restore()

    expect(store.isAdmin).toBe(true)
  })
})
