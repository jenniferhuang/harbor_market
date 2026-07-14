import { createMemoryHistory } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import { createAppRouter, type AuthGate } from '../router'

function createGate(authenticated: boolean, admin = false): AuthGate {
  return {
    get isAuthenticated() {
      return authenticated
    },
    get isAdmin() {
      return admin
    },
    restore: vi.fn(async () => undefined),
  }
}

describe('router authentication guards', () => {
  it('redirects anonymous users to login and preserves the destination', async () => {
    const gate = createGate(false)
    const router = createAppRouter(createMemoryHistory(), gate)

    await router.push('/?section=account')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/?section=account')
    expect(gate.restore).toHaveBeenCalled()
  })

  it('redirects authenticated users away from guest-only pages', async () => {
    const router = createAppRouter(createMemoryHistory(), createGate(true))

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('home')
  })

  it('allows anonymous users to open registration', async () => {
    const router = createAppRouter(createMemoryHistory(), createGate(false))

    await router.push('/register')

    expect(router.currentRoute.value.name).toBe('register')
  })

  it('redirects signed-in non-admin users away from the admin catalog', async () => {
    const gate = createGate(true, false)
    const router = createAppRouter(createMemoryHistory(), gate)

    await router.push('/admin/products')

    expect(router.currentRoute.value.name).toBe('home')
    expect(gate.restore).toHaveBeenCalled()
  })

  it('allows administrators to open the admin catalog', async () => {
    const router = createAppRouter(createMemoryHistory(), createGate(true, true))

    await router.push('/admin/products')

    expect(router.currentRoute.value.name).toBe('admin-products')
  })
})
