const { fetchFavorites, setFavorite } = require('../../api/customer-shop')
const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { formatCents } = require('../../utils/money')

function card(product) {
  const images = product.images || []
  const cover = images.find((item) => item.image_type === 'cover') || images[0]
  const available = product.stock_status !== 'out_of_stock' && (product.skus || []).some((sku) => sku.is_active !== false && sku.stock_quantity > 0)
  return {
    productCode: product.product_code, name: product.name, subtitle: product.subtitle || '',
    categoryName: product.category?.name || '商品', coverUrl: absoluteMediaUrl(cover?.url || ''),
    displayPrice: formatCents(product.base_price_cents), available,
    stockLabel: available ? '可选购' : '暂时售罄', featured: Boolean(product.featured),
  }
}

Page({
  data: { loading: false, loggedIn: false, items: [], errorMessage: '', removing: '', page: 0, hasMore: false },
  onLoad() {
    this._alive = true
    this._sequence = 0
    this._unsubscribe = subscribe((state) => {
      const id = state.status === 'authenticated' ? state.customer.id : null
      if (id === this._customerId) return
      this._customerId = id
      this._sequence += 1
      if (this._alive) this.setData({ items: [], loggedIn: Boolean(id), errorMessage: '', loading: false, removing: '', page: 0, hasMore: false })
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
      const items = await fetchFavorites({ page, page_size: 20 })
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ items: append ? [...this.data.items, ...items.map(card)] : items.map(card), loading: false, page, hasMore: items.length === 20 })
    } catch (error) {
      if (this._alive && sequence === this._sequence && origin === getApiBaseUrl()) this.setData({ loading: false, errorMessage: error.message || '收藏暂时无法加载。' })
    }
  },
  openProduct(event) {
    const code = event.detail.productCode
    if (code) wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },
  async removeFavorite(event) {
    if (this.data.removing) return
    const code = event.currentTarget.dataset.code
    const customerId = this._customerId
    const origin = getApiBaseUrl()
    this.setData({ removing: code, errorMessage: '' })
    try {
      await setFavorite(code, false)
      if (this._alive && customerId === this._customerId && origin === getApiBaseUrl()) { this.setData({ items: this.data.items.filter((item) => item.productCode !== code) }); await this.loadItems() }
    } catch (error) {
      if (this._alive && customerId === this._customerId && origin === getApiBaseUrl()) this.setData({ errorMessage: error.message || '取消收藏失败。' })
    } finally {
      if (this._alive && customerId === this._customerId && origin === getApiBaseUrl()) this.setData({ removing: '' })
    }
  },
  loadMore() { this.loadItems(true) },
  login() { wx.switchTab({ url: '/pages/account/account' }) },
  continueShopping() { wx.switchTab({ url: '/pages/catalog/catalog' }) },
})
