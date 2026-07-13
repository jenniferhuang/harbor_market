import { readonly, reactive } from 'vue'
import { authApi, type AuthApi, type LoginCredentials, type RegistrationInput, type User } from '../api/auth'
import { ApiError } from '../api/client'

export type AuthStatus = 'unknown' | 'checking' | 'authenticated' | 'anonymous'

interface AuthState {
  status: AuthStatus
  user: User | null
}

export function createAuthStore(api: AuthApi = authApi) {
  const state = reactive<AuthState>({
    status: 'unknown',
    user: null,
  })

  let restoration: Promise<void> | null = null

  function setAuthenticated(user: User) {
    state.user = user
    state.status = 'authenticated'
  }

  function clearSession() {
    state.user = null
    state.status = 'anonymous'
  }

  async function restore(force = false): Promise<void> {
    if (!force && state.status !== 'unknown' && state.status !== 'checking') return
    if (restoration) return restoration

    state.status = 'checking'
    restoration = api
      .me()
      .then(setAuthenticated)
      .catch(() => clearSession())
      .finally(() => {
        restoration = null
      })

    return restoration
  }

  async function login(credentials: LoginCredentials): Promise<User> {
    const user = await api.login(credentials)
    setAuthenticated(user)
    return user
  }

  async function register(input: RegistrationInput): Promise<void> {
    await api.register(input)
  }

  async function logout(): Promise<void> {
    try {
      await api.logout()
      clearSession()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        clearSession()
        return
      }
      throw error
    }
  }

  return {
    state: readonly(state),
    get user() {
      return state.user
    },
    get isAuthenticated() {
      return state.status === 'authenticated' && state.user !== null
    },
    restore,
    login,
    register,
    logout,
    clearSession,
  }
}

export type AuthStore = ReturnType<typeof createAuthStore>
export const authStore = createAuthStore()
