import { readonly, reactive } from 'vue'
import { authApi, type AuthApi, type LoginCredentials, type RegistrationInput, type User } from '../api/auth'
import { ApiError } from '../api/client'

export type AuthStatus = 'unknown' | 'checking' | 'authenticated' | 'anonymous' | 'error'

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
  let authEpoch = 0

  function setAuthenticated(user: User) {
    state.user = user
    state.status = 'authenticated'
  }

  function clearSession() {
    authEpoch += 1
    state.user = null
    state.status = 'anonymous'
  }

  async function restore(force = false): Promise<void> {
    if (!force && (state.status === 'authenticated' || state.status === 'anonymous')) return
    if (restoration) return restoration

    const epoch = authEpoch
    state.status = 'checking'
    restoration = api
      .me()
      .then((user) => {
        if (epoch === authEpoch) setAuthenticated(user)
      })
      .catch((error: unknown) => {
        if (epoch !== authEpoch) return
        if (error instanceof ApiError && error.status === 401) {
          state.user = null
          state.status = 'anonymous'
          return
        }
        state.user = null
        state.status = 'error'
        throw error
      })
      .finally(() => {
        restoration = null
      })

    return restoration
  }

  async function login(credentials: LoginCredentials): Promise<User> {
    const epoch = ++authEpoch
    const user = await api.login(credentials)
    if (epoch === authEpoch) setAuthenticated(user)
    return user
  }

  async function register(input: RegistrationInput): Promise<void> {
    await api.register(input)
  }

  async function logout(): Promise<void> {
    const previousUser = state.user
    const previousStatus = state.status
    const epoch = ++authEpoch
    state.user = null
    state.status = 'anonymous'
    try {
      await api.logout()
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        return
      }
      if (epoch === authEpoch) {
        state.user = previousUser
        state.status = previousStatus
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
    get isAdmin() {
      return state.user?.isAdmin ?? state.user?.is_admin ?? false
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
