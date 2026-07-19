export type ApiFieldErrors = Record<string, string>
export const AUTH_REQUIRED_EVENT = 'harbor-market:auth-required'
export const ADMIN_PERMISSION_CHANGED_EVENT = 'harbor-market:admin-permission-changed'

interface ValidationIssue {
  loc?: Array<string | number>
  msg?: string
  message?: string
  field?: string
}

interface BackendError {
  code?: string
  message?: string
  fields?: ValidationIssue[]
}

interface ErrorPayload {
  detail?: string | ValidationIssue[]
  errors?: Record<string, string | string[]>
  error?: BackendError
}

const fieldAliases: Record<string, string> = {
  confirm_password: 'confirmPassword',
}

export class ApiError extends Error {
  readonly status: number
  readonly fieldErrors: ApiFieldErrors
  readonly code?: string

  constructor(status: number, message: string, fieldErrors: ApiFieldErrors = {}, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fieldErrors = fieldErrors
    this.code = code
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function normalizeFieldName(field: string): string {
  return fieldAliases[field] ?? field
}

function extractFieldErrors(payload: unknown): ApiFieldErrors {
  if (!isRecord(payload)) return {}

  const errors: ApiFieldErrors = {}
  const typedPayload = payload as ErrorPayload

  if (isRecord(typedPayload.errors)) {
    Object.entries(typedPayload.errors).forEach(([field, value]) => {
      const message = Array.isArray(value) ? value[0] : value
      if (typeof message === 'string') errors[normalizeFieldName(field)] = message
    })
  }

  const validationIssues = [
    ...(Array.isArray(typedPayload.detail) ? typedPayload.detail : []),
    ...(Array.isArray(typedPayload.error?.fields) ? typedPayload.error.fields : []),
  ]

  validationIssues.forEach((issue) => {
      if (!isRecord(issue)) return
      const location = Array.isArray(issue.loc) ? issue.loc : []
      const rawField = issue.field ?? location.at(-1)
      const message = issue.msg ?? issue.message
      if (typeof rawField === 'string' && typeof message === 'string') {
        errors[normalizeFieldName(rawField)] = message
      }
  })

  return errors
}

function extractErrorCode(payload: unknown): string | undefined {
  if (!isRecord(payload) || !isRecord(payload.error)) return undefined
  return typeof payload.error.code === 'string' ? payload.error.code : undefined
}

function extractSafeMessage(payload: unknown, status: number): string | undefined {
  if (status < 400 || status >= 500 || !isRecord(payload) || !isRecord(payload.error)) {
    return undefined
  }

  const message = payload.error.message
  if (typeof message !== 'string') return undefined

  const normalized = message.replaceAll(/\s+/g, ' ').trim()
  return normalized && normalized.length <= 240 ? normalized : undefined
}

function safeErrorMessage(status: number): string {
  if (status === 0) return 'Unable to reach the server. Check your connection and try again.'
  if (status === 401) return 'Your session is not authenticated.'
  if (status === 409) return 'That value is already in use.'
  if (status === 422) return 'Please review the highlighted fields.'
  if (status === 429) return 'Too many attempts. Please wait a moment and try again.'
  if (status >= 500) return 'The service is temporarily unavailable. Please try again.'
  return 'The request could not be completed. Please try again.'
}

async function readJson(response: Response): Promise<unknown> {
  if (response.status === 204) return undefined
  const text = await response.text()
  if (!text) return undefined

  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

function notifyAccessFailure(
  path: `/api/${string}`,
  status: number,
  code: string | undefined,
): void {
  if (typeof window === 'undefined') return
  if (status === 401 && !path.startsWith('/api/v1/auth/')) {
    window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
  }
  if (status === 403 && code === 'admin_required' && path.startsWith('/api/v1/admin/')) {
    window.dispatchEvent(new Event(ADMIN_PERMISSION_CHANGED_EVENT))
  }
}

function requestHeaders(init: RequestInit): Headers {
  const headers = new Headers(init.headers)
  if (!headers.has('Accept')) headers.set('Accept', 'application/json')

  const isFormData = typeof FormData !== 'undefined' && init.body instanceof FormData
  if (init.body && !isFormData && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  return headers
}

export class ApiClient {
  private async fetch(path: `/api/${string}`, init: RequestInit): Promise<Response> {
    let response: Response

    try {
      response = await fetch(path, {
        ...init,
        credentials: 'include',
        headers: requestHeaders(init),
      })
    } catch {
      throw new ApiError(0, safeErrorMessage(0))
    }

    return response
  }

  private async throwResponseError(
    response: Response,
    path: `/api/${string}`,
  ): Promise<never> {
    const payload = await readJson(response)
    const code = extractErrorCode(payload)
    notifyAccessFailure(path, response.status, code)
    throw new ApiError(
      response.status,
      extractSafeMessage(payload, response.status) ?? safeErrorMessage(response.status),
      extractFieldErrors(payload),
      code,
    )
  }

  async request<T>(path: `/api/${string}`, init: RequestInit = {}): Promise<T> {
    const response = await this.fetch(path, init)

    const payload = await readJson(response)
    if (!response.ok) {
      const code = extractErrorCode(payload)
      notifyAccessFailure(path, response.status, code)
      throw new ApiError(
        response.status,
        extractSafeMessage(payload, response.status) ?? safeErrorMessage(response.status),
        extractFieldErrors(payload),
        code,
      )
    }

    return payload as T
  }

  get<T>(path: `/api/${string}`): Promise<T> {
    return this.request<T>(path, { method: 'GET' })
  }

  post<T>(path: `/api/${string}`, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'POST',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  patch<T>(path: `/api/${string}`, body?: unknown): Promise<T> {
    return this.request<T>(path, {
      method: 'PATCH',
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  delete<T>(path: `/api/${string}`): Promise<T> {
    return this.request<T>(path, { method: 'DELETE' })
  }

  postForm<T>(path: `/api/${string}`, body: FormData, headers?: HeadersInit): Promise<T> {
    return this.request<T>(path, { method: 'POST', body, headers })
  }

  async getBlob(path: `/api/${string}`): Promise<Blob> {
    const response = await this.fetch(path, { method: 'GET' })
    if (!response.ok) {
      return this.throwResponseError(response, path)
    }
    return response.blob()
  }
}

export const apiClient = new ApiClient()
