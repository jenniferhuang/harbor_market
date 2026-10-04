'use strict'

const { ApiError, getApiBaseUrl } = require('../api/client')
const authApi = require('../api/auth')
const sessions = require('./customer-session')

const listeners = new Set()
let generation = 0
let restorePromise = null
let prompted = false
let state = {
  status: sessions.readSession() ? 'restoring' : 'guest',
  customer: null,
  avatarPath: '',
  busy: false,
  errorMessage: '',
}

function snapshot() {
  return { ...state, customer: state.customer ? { ...state.customer } : null }
}

function publish(changes) {
  state = { ...state, ...changes }
  for (const listener of listeners) {
    try { listener(snapshot()) } catch { /* One detached view must not interrupt authentication. */ }
  }
}

function guest(message = '') {
  publish({ status: 'guest', customer: null, avatarPath: '', busy: false, errorMessage: message })
}

function getAuthState() {
  if ((state.status === 'authenticated' || state.status === 'restoring') && !sessions.readSession()) {
    generation += 1
    sessions.clearSession()
    guest('登录状态已失效，请重新登录。')
  }
  return snapshot()
}

function subscribe(listener) {
  listeners.add(listener)
  listener(getAuthState())
  return () => listeners.delete(listener)
}

function safeMessage(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function current(operation, origin, session = null) {
  if (operation !== generation || origin !== getApiBaseUrl()) return false
  if (!session) return true
  const active = sessions.readSession()
  if (!active) {
    generation += 1
    sessions.clearSession()
    guest('登录状态已失效，请重新登录。')
    return false
  }
  return active.accessToken === session.accessToken
}

function assertCurrent(operation, origin, session = null) {
  if (!current(operation, origin, session)) {
    throw new ApiError(409, '登录状态或接口地址已改变，请重新操作。')
  }
}

async function refreshAvatar(session, operation, origin) {
  try {
    const avatarPath = await authApi.downloadAvatar(session)
    if (current(operation, origin, session)) publish({ avatarPath })
  } catch (error) {
    if (current(operation, origin, session) && error.status === 401) {
      sessions.clearSession()
      guest('登录状态已失效，请重新登录。')
    }
    // A missing or temporarily unreachable avatar does not invalidate identity.
  }
}

function restoreSession() {
  if (restorePromise) return restorePromise
  const session = sessions.readSession()
  if (!session) {
    guest()
    return Promise.resolve(null)
  }
  const operation = generation
  const origin = getApiBaseUrl()
  publish({ status: 'restoring', errorMessage: '' })
  restorePromise = (async () => {
    try {
      const customer = sessions.normalizeCustomer(await authApi.fetchCurrentCustomer(session))
      if (!current(operation, origin, session)) return null
      const verified = sessions.persistSession({ ...session, customer })
      publish({ status: 'authenticated', customer: verified.customer, errorMessage: '' })
      await refreshAvatar(verified, operation, origin)
      if (!current(operation, origin, verified)) return null
      return state.status === 'authenticated' ? { ...verified.customer } : null
    } catch (error) {
      if (current(operation, origin, session)) {
        if (error.status === 401 || error.status === 403) sessions.clearSession()
        guest(safeMessage(error, '登录状态暂时无法验证，请检查网络后重试。'))
      }
      return null
    } finally {
      restorePromise = null
    }
  })()
  return restorePromise
}

function weChatCode() {
  if (typeof wx === 'undefined' || typeof wx.login !== 'function') {
    return Promise.reject(new ApiError(0, '当前环境不支持微信登录，请在微信中打开。'))
  }
  return new Promise((resolve, reject) => {
    try {
      wx.login({
        timeout: 10_000,
        success(result) {
          if (typeof result.code === 'string' && result.code.length > 0 && result.code.length <= 512) {
            resolve(result.code)
          } else {
            reject(new ApiError(401, '未能获取微信登录凭证，请重试。'))
          }
        },
        fail() { reject(new ApiError(0, '微信登录未完成，请重试或稍后登录。')) },
      })
    } catch {
      reject(new ApiError(0, '微信登录未完成，请重试或稍后登录。'))
    }
  })
}

function normalizeProfile(input) {
  const nickname = typeof input.nickname === 'string' ? input.nickname.trim() : ''
  if ([...nickname].length > 64 || [...nickname].some((character) => {
    const code = character.codePointAt(0)
    return code < 32 || code === 127
  })) {
    throw new ApiError(422, '昵称不能超过六十四个字，也不能包含控制字符。')
  }
  const avatarPath = typeof input.avatarPath === 'string' ? input.avatarPath : ''
  if (avatarPath && !/^(?:wxfile:\/\/|https?:\/\/tmp\/|\/)/.test(avatarPath)) {
    throw new ApiError(422, '请通过微信头像按钮重新选择头像。')
  }
  return { nickname, avatarPath }
}

async function saveProfileChanges(session, profile, operation, origin) {
  let updated = session
  const warnings = []
  const changes = []
  if (profile.nickname && profile.nickname !== session.customer.nickname) {
    changes.push({ perform: () => authApi.saveNickname(updated, profile.nickname), label: '昵称' })
  }
  if (profile.avatarPath) {
    changes.push({ perform: () => authApi.uploadAvatar(updated, profile.avatarPath), label: '头像' })
  }
  for (const change of changes) {
    assertCurrent(operation, origin, updated)
    try {
      const customer = sessions.normalizeCustomer(await change.perform())
      assertCurrent(operation, origin, updated)
      updated = sessions.persistSession({ ...updated, customer })
      publish({ customer: updated.customer })
    } catch (error) {
      assertCurrent(operation, origin, updated)
      if (error.status === 401 || error.status === 403) throw error
      warnings.push(`${change.label}暂未保存，可在“我的”页面重试。`)
    }
  }
  assertCurrent(operation, origin, updated)
  publish({ errorMessage: warnings.join('') })
  await refreshAvatar(updated, operation, origin)
  assertCurrent(operation, origin, updated)
  if (state.status !== 'authenticated') throw new ApiError(401, '登录状态已失效，请重新登录。')
  return { ...updated.customer }
}

async function loginWithWeChat(input = {}) {
  if (state.busy) throw new ApiError(409, '正在处理登录，请稍候。')
  const profile = normalizeProfile(input)
  const operation = ++generation
  const origin = getApiBaseUrl()
  publish({ busy: true, errorMessage: '' })
  try {
    const code = await weChatCode()
    assertCurrent(operation, origin)
    const result = await authApi.exchangeLoginCode(code)
    assertCurrent(operation, origin)
    const session = sessions.createSession(result, origin)
    publish({ status: 'authenticated', customer: session.customer, avatarPath: '' })
    return await saveProfileChanges(session, profile, operation, origin)
  } catch (error) {
    if (current(operation, origin)) {
      if (error.status === 401 || error.status === 403) sessions.clearSession()
      if (!sessions.readSession()) guest(safeMessage(error, '微信登录失败，请稍后重试。'))
      else publish({ errorMessage: safeMessage(error, '微信登录失败，请稍后重试。') })
    }
    throw error
  } finally {
    if (current(operation, origin)) publish({ busy: false })
  }
}

async function updateProfile(input = {}) {
  getAuthState()
  if (state.busy) throw new ApiError(409, '资料正在保存，请稍候。')
  const session = sessions.readSession()
  if (!session || state.status !== 'authenticated') throw new ApiError(401, '请先登录后再修改资料。')
  const profile = normalizeProfile(input)
  if (!profile.nickname && !profile.avatarPath) throw new ApiError(422, '请输入昵称或选择头像。')
  const operation = ++generation
  const origin = getApiBaseUrl()
  publish({ busy: true, errorMessage: '' })
  try {
    return await saveProfileChanges(session, profile, operation, origin)
  } catch (error) {
    if (current(operation, origin)) {
      if (error.status === 401 || error.status === 403) {
        sessions.clearSession()
        guest('登录状态已失效，请重新登录。')
      } else publish({ errorMessage: safeMessage(error, '资料保存失败，请重试。') })
    }
    throw error
  } finally {
    if (current(operation, origin)) publish({ busy: false })
  }
}

async function logout() {
  const session = sessions.readSession()
  const operation = ++generation
  const origin = getApiBaseUrl()
  sessions.clearSession()
  guest()
  if (!session) return
  try {
    await authApi.revokeSession(session)
  } catch (error) {
    if (error.status !== 401 && error.status !== 403) {
      if (current(operation, origin) && state.status === 'guest') {
        publish({ errorMessage: '已退出当前设备，服务器会话撤销失败，请稍后重试。' })
      }
      throw error
    }
  }
}

function resetSessionForApiChange() {
  generation += 1
  sessions.clearSession()
  guest()
}

function shouldPromptForLogin() {
  if (prompted || getAuthState().status !== 'guest' || state.busy) return false
  prompted = true
  return true
}

module.exports = {
  getAuthState,
  loginWithWeChat,
  logout,
  resetSessionForApiChange,
  restoreSession,
  shouldPromptForLogin,
  subscribe,
  updateProfile,
}
