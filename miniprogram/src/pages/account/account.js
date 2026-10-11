const {
  getAuthState,
  subscribe,
  logout,
} = require('../../state/auth-store')
const { fetchCustomerShopAccess } = require('../../api/customer-shop')
const { getApiBaseUrl } = require('../../api/client')
const { readSession } = require('../../state/customer-session')
const { loadHistory } = require('../../state/browsing-history')

function countLabel(value) {
  return Number.isSafeInteger(value) && value >= 0 ? String(value) : '—'
}

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
    favoritesCount: '—',
    availableCouponCount: '—',
    orderCount: '—',
    historyCount: 0,
    summaryLoading: false,
    summaryError: '',
  },

  onLoad() {
    this._alive = true
    this._accessSequence = 0
    this._unsubscribe = subscribe((state) => this.applyAuthState(state))
    this.applyAuthState(getAuthState())
  },

  onShow() {
    this._visible = true
    this.setData({ historyCount: loadHistory().length })
    this.applyAuthState(getAuthState())
    this.loadShopAccess()
  },

  onHide() {
    this._visible = false
    this._logoutPending = null
    ++this._accessSequence
    this._accessLoading = false
    this.setData({ summaryLoading: false })
  },

  onUnload() {
    this._alive = false
    this._visible = false
    this._logoutPending = null
    this._accessSequence += 1
    if (this._unsubscribe) this._unsubscribe()
  },

  applyAuthState(state) {
    if (!this._alive) return
    const session = readSession()
    const token = state.status === 'authenticated' && session && session.customer.id === state.customer?.id ? session.accessToken : ''
    this.setData({
      authStatus: token ? 'authenticated' : state.status === 'restoring' ? 'restoring' : 'guest',
      authBusy: state.busy || state.status === 'restoring' || Boolean(this._logoutInFlight),
      nickname: token ? state.customer.nickname || '微信用户' : '',
      avatarPath: token ? state.avatarPath || '' : '',
      errorMessage: state.errorMessage || '',
    })
    if (token !== this._accessToken || getApiBaseUrl() !== this._accessOrigin) {
      this._logoutPending = null
      this._accessSequence += 1
      this._accessToken = token
      this._accessOrigin = getApiBaseUrl()
      this._accessLoading = false
      this.setData({ canManageStore: false, favoritesCount: '—', availableCouponCount: '—', orderCount: '—', summaryLoading: false, summaryError: '' })
      if (this._visible && token) this.loadShopAccess()
    }
  },

  async loadShopAccess() {
    const session = readSession()
    if (!this._alive || !this._visible || !session || getAuthState().status !== 'authenticated') return
    if (this._accessLoading && this._accessToken === session.accessToken) return
    const sequence = ++this._accessSequence
    const origin = getApiBaseUrl()
    this._accessToken = session.accessToken
    this._accessOrigin = origin
    this._accessLoading = true
    this.setData({ canManageStore: false, summaryLoading: true, summaryError: '' })
    const current = () => this._alive && this._visible && sequence === this._accessSequence &&
      origin === getApiBaseUrl() && readSession()?.accessToken === session.accessToken && getAuthState().status === 'authenticated'
    try {
      const access = await fetchCustomerShopAccess()
      if (current()) this.setData({ canManageStore: access.can_manage_store === true,
        favoritesCount: countLabel(access.favorites_count), availableCouponCount: countLabel(access.available_coupon_count), orderCount: countLabel(access.order_count) })
    } catch {
      if (current()) this.setData({ favoritesCount: '—', availableCouponCount: '—', orderCount: '—', summaryError: '账户数据暂时无法加载，点击重试。' })
    } finally {
      if (sequence === this._accessSequence) {
        this._accessLoading = false
        if (this._alive) this.setData({ summaryLoading: false })
      }
    }
  },

  openLogin() {
    this.openProfileDialog()
  },

  openProfileDialog() {
    const state = getAuthState()
    this.applyAuthState(state)
    if (this.data.authBusy) return
    this.setData({ loginVisible: true, loginMode: this.data.authStatus === 'authenticated' ? 'profile' : 'login' })
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
    if (!this._alive || !this._visible || this.data.authBusy || this._logoutPending) return
    const session = readSession()
    if (!session) return
    const pending = {}
    this._logoutPending = pending
    wx.showModal({
      title: '退出登录',
      content: '确定退出当前账户吗？购物车中的商品会继续保留在本机。',
      confirmText: '退出登录',
      cancelText: '取消',
      confirmColor: '#0f6b5f',
      success: async (result) => {
        if (!result.confirm || !this._alive || !this._visible || this._logoutPending !== pending || readSession()?.accessToken !== session.accessToken || session.apiOrigin !== getApiBaseUrl()) return
        this._logoutPending = null
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
        if (this._logoutPending === pending) this._logoutPending = null
      },
    })
  },

  onAvatarError() {
    this.setData({ avatarPath: '' })
  },

  openSettings() {
    wx.navigateTo({ url: '/pages/profile/profile' })
  },

  continueShopping() {
    wx.switchTab({ url: '/pages/catalog/catalog' })
  },

  openCoupons() { this.openCustomerPage('coupons') },
  openFavorites() { this.openCustomerPage('favorites') },
  openOrders() { this.openCustomerPage('orders') },
  openHistory() { wx.navigateTo({ url: '/pages/history/history' }) },
  openCouponCenter() { wx.navigateTo({ url: '/pages/coupon-center/coupon-center' }) },
  openCustomerPage(page) {
    this.applyAuthState(getAuthState())
    if (this.data.authBusy) return
    if (this.data.authStatus !== 'authenticated') { this.openLogin(); return }
    wx.navigateTo({ url: `/pages/${page}/${page}` })
  },
  openMerchant() {
    if (this.data.canManageStore) wx.navigateTo({ url: '/pages/merchant/merchant' })
  },
})
