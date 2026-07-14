import { ApiError, apiClient } from './client'

export interface User {
  id?: string | number
  username: string
  isAdmin?: boolean
  is_admin?: boolean
  isActive?: boolean
  createdAt?: string
  lastLoginAt?: string | null
}

export interface LoginCredentials {
  username: string
  password: string
}

export type RegistrationInput = LoginCredentials

export interface AuthApi {
  register(input: RegistrationInput): Promise<void>
  login(input: LoginCredentials): Promise<User>
  me(): Promise<User>
  logout(): Promise<void>
}

const endpoints = {
  register: '/api/v1/auth/register',
  login: '/api/v1/auth/login',
  logout: '/api/v1/auth/logout',
  me: '/api/v1/auth/me',
} as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function unwrapUserPayload(payload: unknown): Record<string, unknown> | undefined {
  let candidate = payload

  for (let depth = 0; depth < 3; depth += 1) {
    if (!isRecord(candidate)) return undefined
    if (typeof candidate.username === 'string') return candidate
    candidate = candidate.user ?? candidate.data
  }

  return undefined
}

function parseUser(payload: unknown): User {
  const candidate = unwrapUserPayload(payload)
  if (!candidate || typeof candidate.username !== 'string') {
    throw new ApiError(502, 'The server returned an invalid user response.')
  }

  const user: User = { username: candidate.username }
  if (typeof candidate.id === 'string' || typeof candidate.id === 'number') user.id = candidate.id

  const isActive = candidate.is_active ?? candidate.isActive
  if (typeof isActive === 'boolean') user.isActive = isActive

  const isAdmin = candidate.is_admin ?? candidate.isAdmin
  if (typeof isAdmin === 'boolean') {
    user.isAdmin = isAdmin
    user.is_admin = isAdmin
  }

  const createdAt = candidate.created_at ?? candidate.createdAt
  if (typeof createdAt === 'string') user.createdAt = createdAt

  const lastLoginAt = candidate.last_login_at ?? candidate.lastLoginAt
  if (typeof lastLoginAt === 'string' || lastLoginAt === null) user.lastLoginAt = lastLoginAt

  return user
}

export const authApi: AuthApi = {
  async register(input) {
    await apiClient.post<unknown>(endpoints.register, input)
  },

  async login(input) {
    await apiClient.post<unknown>(endpoints.login, input)
    return this.me()
  },

  async me() {
    const payload = await apiClient.get<unknown>(endpoints.me)
    return parseUser(payload)
  },

  async logout() {
    await apiClient.post<unknown>(endpoints.logout)
  },
}
