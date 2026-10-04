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
  it('shows Chinese login guidance and accessible password controls', async () => {
    const store = createAuthStore(createApi())
    const router = createTestRouter()
    await router.push('/login?registered=1&auth_error=session_check_failed')
    render(LoginView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })

    expect(screen.getByRole('heading', { name: '登录' })).toBeVisible()
    expect(screen.getByText('港湾集市')).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('账号已创建，现在可以登录。')
    expect(screen.getByRole('alert')).toHaveTextContent('无法验证当前登录状态，请检查服务连接后重新登录。')
    await userEvent.setup().click(screen.getByRole('button', { name: '显示密码' }))
    expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'text')
    expect(screen.getByRole('button', { name: '隐藏密码' })).toBeVisible()
  })

  it('prevents registration when passwords do not match', async () => {
    const register = vi.fn(async () => undefined)
    const store = createAuthStore(createApi({ register }))
    const router = createTestRouter()
    await router.push('/register')

    render(RegisterView, {
      global: { plugins: [router], provide: { [authKey as symbol]: store } },
    })
    const interaction = userEvent.setup()

    await interaction.type(screen.getByLabelText('用户名'), 'marina')
    await interaction.type(screen.getByLabelText('密码'), 'first password')
    await interaction.type(screen.getByLabelText('确认密码'), 'second password')
    await interaction.click(screen.getByRole('button', { name: '创建账号' }))

    expect(await screen.findByText('两次输入的密码不一致。')).toBeVisible()
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

    await interaction.type(screen.getByLabelText('用户名'), 'marina')
    await interaction.type(screen.getByLabelText('密码'), 'matching password')
    await interaction.type(screen.getByLabelText('确认密码'), 'matching password')
    await interaction.click(screen.getByRole('button', { name: '创建账号' }))

    expect(await screen.findByText('该用户名已被注册。')).toBeVisible()
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
    const username = screen.getByLabelText('用户名')
    const password = screen.getByLabelText('密码')

    await interaction.type(username, 'marina')
    await interaction.type(password, 'wrong password')
    await interaction.click(screen.getByRole('button', { name: '登录' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码不正确。')
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
    await userEvent.setup().click(screen.getByRole('button', { name: '退出登录' }))

    await waitFor(() => expect(router.currentRoute.value.name).toBe('login'))
    expect(logout).toHaveBeenCalledOnce()
    expect(store.isAuthenticated).toBe(false)
  })
})
