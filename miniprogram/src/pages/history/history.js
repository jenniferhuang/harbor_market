const { loadHistory, clearHistory } = require('../../state/browsing-history')
const { fetchProduct } = require('../../api/catalog')
const { getApiBaseUrl, absoluteMediaUrl } = require('../../api/client')
const { formatCents } = require('../../utils/money')
const { formatChinaDate } = require('../../utils/date')

const PAGE_SIZE = 10

function row(product, visit) {
  const cover = (product.images || []).find((image) => image.image_type === 'cover') || (product.images || [])[0]
  return {
    productCode: product.product_code,
    name: product.name,
    coverUrl: cover ? absoluteMediaUrl(cover.url || '') : '',
    displayPrice: formatCents(product.base_price_cents),
    dateLabel: formatChinaDate(new Date(visit.viewedAt).toISOString()),
  }
}

Page({
  data: { items: [], total: 0, loading: false, hasMore: false, errorMessage: '', unavailableCount: 0 },

  onLoad() { this._alive = true; this._sequence = 0 },
  onShow() { this._visible = true; this.loadItems() },
  onHide() { this._visible = false; ++this._sequence; this.setData({ loading: false }) },
  onUnload() { this._alive = false; ++this._sequence },

  async loadItems(append = false) {
    append = append === true
    if (append && (this.data.loading || !this.data.hasMore)) return
    const visits = loadHistory()
    const origin = getApiBaseUrl()
    const sequence = ++this._sequence
    const offset = append ? this._offset || 0 : 0
    const batch = visits.slice(offset, offset + PAGE_SIZE)
    this.setData({ loading: Boolean(batch.length), total: visits.length, errorMessage: '',
      ...(append ? {} : { items: [], unavailableCount: 0 }), hasMore: false })
    if (!batch.length) { this._offset = 0; return }
    const results = await Promise.all(batch.map(async (visit) => {
      try {
        const product = await fetchProduct(visit.productCode)
        if (product.status !== 'published' || product.category?.is_active === false) return { unavailable: true }
        return { item: row(product, visit) }
      } catch (error) {
        return error.status === 404 ? { unavailable: true } : { failed: true }
      }
    }))
    if (!this._alive || !this._visible || sequence !== this._sequence || origin !== getApiBaseUrl()) return
    const failed = results.some((result) => result.failed)
    // Keep the batch position on network failure so retry never silently skips a visit.
    if (failed) {
      this.setData({ loading: false, errorMessage: '部分商品暂时无法加载，请重试。', hasMore: false })
      return
    }
    this._offset = offset + batch.length
    this.setData({ loading: false, items: [...(append ? this.data.items : []), ...results.flatMap((result) => result.item ? [result.item] : [])],
      unavailableCount: (append ? this.data.unavailableCount : 0) + results.filter((result) => result.unavailable).length,
      hasMore: this._offset < visits.length })
  },

  loadMore() { return this.loadItems(true) },
  openProduct(event) {
    const code = event.currentTarget.dataset.code
    if (this.data.items.some((item) => item.productCode === code)) wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },
  continueShopping() { wx.switchTab({ url: '/pages/catalog/catalog' }) },
  clearVisits() {
    if (!this.data.total || this._confirming) return
    const origin = getApiBaseUrl()
    this._confirming = true
    wx.showModal({ title: '清除浏览足迹', content: '清除这台设备上的浏览记录？收藏、购物车和登录状态都会保留。',
      confirmText: '清除', cancelText: '取消', success: (result) => {
        if (!result.confirm || !this._alive || !this._visible || origin !== getApiBaseUrl()) return
        try { clearHistory(); ++this._sequence; this._offset = 0; this.setData({ items: [], total: 0, hasMore: false, loading: false, errorMessage: '', unavailableCount: 0 }) }
        catch (error) { this.setData({ errorMessage: error.message }) }
      }, complete: () => { this._confirming = false } })
  },
})
