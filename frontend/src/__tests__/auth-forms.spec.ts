import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'
import type { AuthApi, User } from '../api/auth'
import { ApiError } from '../api/client'
import { createAuthStore } from '../auth/store'
import { authKey } from '../auth/useAuth'
import HomeView from '../views/HomeView.vue'
import LoginView from '../views/LoginView.vue'
import RegisterView from '../views/RegisterView.vue'

const user: User = { id: 12, username: 'marina' }

function createApi(overrides: Partial<AuthApi> = {}): AuthApi {
  return {
    register: vi.fn(async () => undefined),
    login: vi.fn(async () => user),
    me: vi.fn(async () => user),
    logout: vi.fn(async () => undefined),
    ...overrides,
  }
}

function createTestRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: HomeView },
      { path: '/login', name: 'login', component: LoginView },
      { path: '/register', name: 'register', component: RegisterView },
    ],
  })
}

describe('authentication forms', () => {
  it('prevents registration when passwords do not match', async () => {
    const register = vi.fn(async () => undefined)
    const store = createAuthStore(createApi({ register }))
    const router = createTestRouter()
    await router.push('/register')

    render(RegisterView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })
    const interaction = userEvent.setup()

    await interaction.type(screen.getByLabelText('Username'), 'marina')
    await interaction.type(screen.getByLabelText('Password'), 'first password')
    await interaction.type(screen.getByLabelText('Confirm password'), 'second password')
    await interaction.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Passwords do not match.')).toBeVisible()
    expect(register).not.toHaveBeenCalled()
  })

  it('shows duplicate usernames next to the registration field', async () => {
    const register = vi.fn(async () =>
      Promise.reject(new ApiError(409, 'That value is already in use.')),
    )
    const store = createAuthStore(createApi({ register }))
    const router = createTestRouter()
    await router.push('/register')

    render(RegisterView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })
    const interaction = userEvent.setup()

    await interaction.type(screen.getByLabelText('Username'), 'marina')
    await interaction.type(screen.getByLabelText('Password'), 'matching password')
    await interaction.type(screen.getByLabelText('Confirm password'), 'matching password')
    await interaction.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('That username is already taken.')).toBeVisible()
  })

  it('keeps the username and clears the password after failed login', async () => {
    const login = vi.fn(async () =>
      Promise.reject(new ApiError(401, 'Your session is not authenticated.')),
    )
    const store = createAuthStore(createApi({ login }))
    const router = createTestRouter()
    await router.push('/login')

    render(LoginView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })
    const interaction = userEvent.setup()
    const username = screen.getByLabelText('Username')
    const password = screen.getByLabelText('Password')

    await interaction.type(username, 'marina')
    await interaction.type(password, 'wrong password')
    await interaction.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Username or password is incorrect.')
    expect(username).toHaveValue('marina')
    expect(password).toHaveValue('')
  })

  it('logs out and returns to the login page', async () => {
    const logout = vi.fn(async () => undefined)
    const store = createAuthStore(createApi({ logout }))
    await store.login({ username: 'marina', password: 'valid password' })
    const router = createTestRouter()
    await router.push('/')

    render(HomeView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })
    await userEvent.setup().click(screen.getByRole('button', { name: 'Sign out' }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(logout).toHaveBeenCalledOnce()
    expect(store.isAuthenticated).toBe(false)
  })
})
