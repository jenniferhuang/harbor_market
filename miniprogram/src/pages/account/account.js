const {
  getAuthState,
  subscribe,
  logout,
} = require('../../state/auth-store')
const { fetchCustomerShopAccess } = require('../../api/customer-shop')
const { getApiBaseUrl } = require('../../api/client')
const { readSession } = require('../../state/customer-session')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

Page({
  data: {
    authStatus: 'guest',
    authBusy: false,
    nickname: '',
    avatarPath: '',
    errorMessage: '',
    loginVisible: false,
    loginMode: 'login',
    canManageStore: false,
  },

  onLoad() {
    this._alive = true
    this._accessSequence = 0
    this._unsubscribe = subscribe((state) => this.applyAuthState(state))
    this.applyAuthState(getAuthState())
  },

  onShow() {
    this._visible = true
    this.applyAuthState(getAuthState())
    this.loadShopAccess()
  },

  onHide() { this._visible = false },

  onUnload() {
    this._alive = false
    this._accessSequence += 1
    if (this._unsubscribe) this._unsubscribe()
  },

  applyAuthState(state) {
    if (!this._alive) return
    this.setData({
      authStatus: state.status,
      authBusy: state.busy || state.status === 'restoring' || Boolean(this._logoutInFlight),
      nickname: state.customer ? state.customer.nickname || '微信用户' : '',
      avatarPath: state.avatarPath || '',
      errorMessage: state.errorMessage || '',
    })
    if (state.status !== 'authenticated') {
      this._accessSequence += 1
      this._accessToken = ''
      this._accessLoading = false
      this.setData({ canManageStore: false })
    } else if (this._visible && readSession()?.accessToken !== this._accessToken) {
      this.loadShopAccess()
    }
  },

  async loadShopAccess() {
    const session = readSession()
    if (!this._alive || !session || getAuthState().status !== 'authenticated') return
    if (this._accessLoading && this._accessToken === session.accessToken) return
    const sequence = ++this._accessSequence
    const origin = getApiBaseUrl()
    this._accessToken = session.accessToken
    this._accessLoading = true
    this.setData({ canManageStore: false })
    try {
      const access = await fetchCustomerShopAccess()
      if (this._alive && sequence === this._accessSequence && origin === getApiBaseUrl()) this.setData({ canManageStore: access.can_manage_store === true })
    } catch {
      // Optional merchant access must not interrupt customer profile or guest browsing.
    } finally {
      if (sequence === this._accessSequence) this._accessLoading = false
    }
  },

  openLogin() {
    this.openProfileDialog()
  },

  openProfileDialog() {
    const state = getAuthState()
    this.applyAuthState(state)
    if (this.data.authBusy) return
    this.setData({ loginVisible: true, loginMode: state.status === 'authenticated' ? 'profile' : 'login' })
  },

  editProfile() {
    this.openProfileDialog()
  },

  closeLogin() {
    this.setData({ loginVisible: false })
  },

  onLoginSuccess(event) {
    this.setData({ loginVisible: false })
    this.applyAuthState(getAuthState())
    const { mode, warningMessage } = event.detail
    if (warningMessage) {
      wx.showModal({
        title: mode === 'profile' ? '部分资料尚未保存' : '已登录，资料尚未保存',
        content: warningMessage,
        showCancel: false,
        confirmText: '知道了',
      })
      return
    }
    wx.showToast({ title: mode === 'profile' ? '资料已保存' : '微信登录成功', icon: 'success' })
  },

  confirmLogout() {
    if (this.data.authBusy || this._logoutPending) return
    this._logoutPending = true
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账户吗？购物车中的商品会继续保留在本机。',
      confirmText: '退出登录',
      cancelText: '取消',
      confirmColor: '#0f6b5f',
      success: async (result) => {
        if (!result.confirm) return
        this._logoutInFlight = true
        if (this._alive) this.setData({ authBusy: true })
        try {
          await logout()
          if (this._alive && getAuthState().status === 'guest') {
            wx.showToast({ title: '已退出登录', icon: 'success' })
          }
        } catch (error) {
          if (this._alive && getAuthState().status === 'guest') {
            this.setData({ errorMessage: messageFor(error, '退出登录失败，请稍后重试。') })
          }
        } finally {
          this._logoutInFlight = false
          this.applyAuthState(getAuthState())
        }
      },
      complete: () => {
        this._logoutPending = false
      },
    })
  },

  onAvatarError() {
    this.setData({ avatarPath: '' })
  },

  openSettings() {
    wx.navigateTo({ url: '/pages/settings/settings' })
  },

  continueShopping() {
    wx.switchTab({ url: '/pages/catalog/catalog' })
  },

  openCoupons() { this.openCustomerPage('coupons') },
  openFavorites() { this.openCustomerPage('favorites') },
  openOrders() { this.openCustomerPage('orders') },
  openCustomerPage(page) {
    if (this.data.authStatus !== 'authenticated') { this.openLogin(); return }
    wx.navigateTo({ url: `/pages/${page}/${page}` })
  },
  openMerchant() {
    if (this.data.canManageStore) wx.navigateTo({ url: '/pages/merchant/merchant' })
  },
})
