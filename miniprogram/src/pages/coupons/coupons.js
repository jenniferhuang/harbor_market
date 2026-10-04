const { fetchMyCoupons } = require('../../api/customer-shop')
const { getApiBaseUrl } = require('../../api/client')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { formatCents } = require('../../utils/money')
const { formatChinaDate } = require('../../utils/date')

Page({
  data: { loading: false, loggedIn: false, items: [], errorMessage: '', page: 0, hasMore: false },
  onLoad() {
    this._alive = true
    this._sequence = 0
    this._unsubscribe = subscribe((state) => {
      const id = state.status === 'authenticated' ? state.customer.id : null
      if (id === this._customerId) return
      this._customerId = id
      this._sequence += 1
      if (this._alive) this.setData({ items: [], loggedIn: Boolean(id), loading: false, errorMessage: '', page: 0, hasMore: false })
      if (id && this._visible) this.loadItems()
    })
  },
  onShow() { this._visible = true; if (getAuthState().status === 'authenticated') this.loadItems() },
  onHide() { this._visible = false },
  onUnload() { this._alive = false; this._sequence += 1; if (this._unsubscribe) this._unsubscribe() },
  async loadItems(append = false) {
    append = append === true
    if (append && (this.data.loading || !this.data.hasMore)) return
    const page = append ? this.data.page + 1 : 1
    const origin = getApiBaseUrl()
    const sequence = ++this._sequence
    this.setData({ loading: true, errorMessage: '' })
    try {
      const coupons = await fetchMyCoupons({ page, page_size: 20 })
      const now = Date.now()
      const items = coupons.map((item) => ({
        ...item, discount: formatCents(item.discount_cents), threshold: formatCents(item.min_spend_cents),
        expiresLabel: item.expires_at ? formatChinaDate(item.expires_at) || '有效期暂未提供' : '长期有效',
        statusLabel: !item.is_active ? '已停用' : item.expires_at && Date.parse(item.expires_at) <= now ? '已过期' : item.starts_at && Date.parse(item.starts_at) > now ? '尚未开始' : '已领取',
      }))
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ items: append ? [...this.data.items, ...items] : items, loading: false, page, hasMore: coupons.length === 20 })
    } catch (error) {
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ loading: false, errorMessage: error.message || '优惠券暂时无法加载。' })
    }
  },
  loadMore() { this.loadItems(true) },
  login() { wx.switchTab({ url: '/pages/account/account' }) },
  home() { wx.switchTab({ url: '/pages/home/home' }) },
})
