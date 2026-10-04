const { fetchMyOrders } = require('../../api/customer-shop')
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
      const orders = await fetchMyOrders({ page, page_size: 20 })
      const items = orders.map((order) => ({
        ...order, displayTotal: formatCents(order.total_cents),
        statusLabel: order.status === 'completed' ? '已成交' : '已撤销',
        completedLabel: formatChinaDate(order.completed_at),
        items: (order.items || []).map((item) => ({ ...item, displayPrice: formatCents(item.unit_price_cents) })),
      }))
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ items: append ? [...this.data.items, ...items] : items, loading: false, page, hasMore: orders.length === 20 })
    } catch (error) {
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ loading: false, errorMessage: error.message || '购买记录暂时无法加载。' })
    }
  },
  loadMore() { this.loadItems(true) },
  login() { wx.switchTab({ url: '/pages/account/account' }) },
  openProduct(event) {
    const code = event.currentTarget.dataset.code
    if (code) wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },
})
