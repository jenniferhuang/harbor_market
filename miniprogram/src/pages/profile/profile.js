const { getAuthState, subscribe, logout } = require('../../state/auth-store')
const { readSession } = require('../../state/customer-session')
const { getApiBaseUrl } = require('../../api/client')

function identityFor(state) {
  const session = readSession()
  return state.status === 'authenticated' && session && state.customer?.id === session.customer.id
    ? `${session.apiOrigin}|${session.customer.id}|${session.accessToken}` : ''
}

function developmentVersion() {
  try { return wx.getAccountInfoSync?.().miniProgram?.envVersion === 'develop' } catch { return false }
}

function messageFor(error, fallback) {
  return typeof error?.message === 'string' && /[\u3400-\u9fff]/.test(error.message) ? error.message : fallback
}

Page({
  data: {
    authStatus: 'guest', authBusy: false, customerId: '', nickname: '', avatarPath: '',
    errorMessage: '', loginVisible: false, loginMode: 'login', showConnectionSettings: false,
  },

  onLoad() {
    if (this._alive) return
    this._alive = true
    this._visible = false
    this._authRevision = 0
    this._identity = ''
    this._unsubscribe = subscribe((state) => this.applyAuthState(state))
  },

  onShow() {
    if (!this._alive) this.onLoad()
    this._visible = true
    this.applyAuthState(getAuthState())
    this.setData({ showConnectionSettings: developmentVersion() })
  },

  onHide() {
    this._visible = false
    this._logoutPending = null
    this._authRevision += 1
  },

  onUnload() {
    this._alive = false
    this._visible = false
    this._logoutPending = null
    if (this._unsubscribe) this._unsubscribe()
  },

  applyAuthState(state) {
    if (!this._alive) return
    const identity = identityFor(state)
    const changed = identity !== this._identity
    if (changed) {
      this._identity = identity
      this._authRevision += 1
      this._logoutPending = null
    }
    this.setData({
      authStatus: identity ? 'authenticated' : state.status === 'restoring' ? 'restoring' : 'guest',
      authBusy: Boolean(state.busy || state.status === 'restoring' || this._logoutInFlight),
      customerId: identity ? String(state.customer.id) : '',
      nickname: identity ? state.customer.nickname || '微信用户' : '',
      avatarPath: identity ? state.avatarPath || '' : '',
      errorMessage: state.errorMessage || '',
      ...(changed && this.data.loginMode === 'profile' ? { loginVisible: false } : {}),
    })
  },

  openProfileDialog() {
    if (!this._alive || !this._visible) return
    this.applyAuthState(getAuthState())
    if (this.data.authBusy) return
    this._editingIdentity = this._identity
    this.setData({ loginVisible: true, loginMode: this._identity ? 'profile' : 'login' })
  },

  closeLogin() {
    if (this._alive) this.setData({ loginVisible: false })
  },

  onLoginSuccess(event) {
    if (!this._alive || !this._visible || !this.data.loginVisible) return
    const state = getAuthState()
    this.applyAuthState(state)
    const detail = event.detail || {}
    if (!this._identity || detail.customerId !== state.customer?.id || detail.mode !== this.data.loginMode ||
        (detail.mode === 'profile' && this._identity !== this._editingIdentity)) return
    this.setData({ loginVisible: false })
    if (detail.warningMessage) {
      wx.showModal({ title: '部分资料尚未保存', content: detail.warningMessage, showCancel: false, confirmText: '知道了' })
    } else {
      wx.showToast({ title: detail.mode === 'profile' ? '资料已保存' : '微信登录成功', icon: 'success' })
    }
  },

  confirmLogout() {
    if (!this._alive || !this._visible) return
    this.applyAuthState(getAuthState())
    if (!this._identity || this.data.authBusy || this._logoutPending) return
    const pending = { identity: this._identity, revision: this._authRevision }
    this._logoutPending = pending
    try {
      wx.showModal({
        title: '退出登录', content: '确定退出当前账户吗？购物车中的商品会继续保留在本机。',
        confirmText: '退出登录', cancelText: '取消', confirmColor: '#0f6b5f',
        success: async (result) => {
          const current = getAuthState()
          if (!this._alive || !this._visible || this._logoutPending !== pending ||
              pending.revision !== this._authRevision || identityFor(current) !== pending.identity || current.busy) return
          this._logoutPending = null
          if (!result.confirm) return
          this._logoutInFlight = true
          this.applyAuthState(current)
          const origin = getApiBaseUrl()
          // logout clears local identity synchronously, before revoking the server session.
          const request = logout()
          const revision = this._authRevision
          const canReport = () => this._alive && this._visible && revision === this._authRevision &&
            origin === getApiBaseUrl() && getAuthState().status === 'guest'
          try {
            await request
            if (canReport()) wx.showToast({ title: '已退出登录', icon: 'success' })
          } catch (error) {
            if (canReport()) this.setData({ errorMessage: messageFor(error, '已退出当前设备，服务器会话暂未撤销，请稍后重试。') })
          } finally {
            this._logoutInFlight = false
            this.applyAuthState(getAuthState())
          }
        },
        complete: () => { if (this._logoutPending === pending) this._logoutPending = null },
      })
    } catch {
      this._logoutPending = null
      this.setData({ errorMessage: '退出确认暂时无法打开，请重试。' })
    }
  },

  onAvatarError() {
    if (this._alive) this.setData({ avatarPath: '' })
  },

  openConnectionSettings() {
    if (this._alive && this._visible && developmentVersion()) wx.navigateTo({ url: '/pages/settings/settings' })
  },

  continueShopping() {
    if (this._alive && this._visible) wx.switchTab({ url: '/pages/catalog/catalog' })
  },
})
