const { fetchCategories, fetchProducts } = require('../../api/catalog')
const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
const { consumeCatalogCategory } = require('../../state/catalog-navigation')
const { formatCents } = require('../../utils/money')

const PAGE_SIZE = 10

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function productView(product) {
  const images = Array.isArray(product.images) ? product.images : []
  const cover = images.find((image) => image.image_type === 'cover') || images[0]
  const skus = (Array.isArray(product.skus) ? product.skus : []).filter((sku) => sku.is_active !== false)
  const available = product.stock_status !== 'out_of_stock' && skus.some((sku) => Number(sku.stock_quantity) > 0)
  return {
    productCode: product.product_code,
    name: product.name,
    subtitle: product.subtitle || '',
    categoryName: product.category?.name || '商品',
    featured: Boolean(product.featured),
    coverUrl: cover ? absoluteMediaUrl(cover.url || cover.media_url || '') : '',
    displayPrice: formatCents(Number(product.base_price_cents) || 0),
    available,
    stockLabel: !skus.length ? '暂不可售' : !available ? '暂时售罄' : product.stock_status === 'preorder' ? '可预订' : '可选购',
  }
}

Page({
  data: {
    categories: [],
    selectedCategory: '',
    products: [],
    page: 1,
    total: 0,
    totalPages: 1,
    loading: true,
    errorMessage: '',
    categoryErrorMessage: '',
  },

  onLoad() {
    this._alive = true
    this._requestSequence = 0
    this._categoryRequestSequence = 0
    this._initialLoadStarted = false
    this._apiOrigin = getApiBaseUrl()
  },

  onShow() {
    const originChanged = this._apiOrigin !== getApiBaseUrl()
    if (originChanged) {
      this._apiOrigin = getApiBaseUrl()
      this._requestSequence += 1
      this._categoryRequestSequence += 1
      this.setData({ categories: [], selectedCategory: '', products: [], page: 1, total: 0, totalPages: 1, errorMessage: '', categoryErrorMessage: '' })
    }
    const category = consumeCatalogCategory()
    const changed = category !== null && category !== this.data.selectedCategory
    if (changed) this.setData({ selectedCategory: category })
    if (!this._initialLoadStarted || originChanged) {
      this._initialLoadStarted = true
      return this.loadInitialData()
    }
    if (changed) return this.loadProducts(1)
  },

  onUnload() {
    this._alive = false
    this._requestSequence += 1
    this._categoryRequestSequence += 1
  },

  async onPullDownRefresh() {
    try { await this.loadInitialData() } finally { wx.stopPullDownRefresh() }
  },

  loadInitialData() {
    return Promise.all([this.loadCategories(), this.loadProducts(1)])
  },

  async loadCategories() {
    const sequence = ++this._categoryRequestSequence
    const origin = getApiBaseUrl()
    this.setData({ categoryErrorMessage: '' })
    try {
      const categories = await fetchCategories()
      if (this._alive && sequence === this._categoryRequestSequence && origin === getApiBaseUrl()) this.setData({ categories })
    } catch (error) {
      if (this._alive && sequence === this._categoryRequestSequence && origin === getApiBaseUrl()) {
        this.setData({ categoryErrorMessage: messageFor(error, '类目暂时无法加载，仍可浏览全部商品。') })
      }
    }
  },

  openSearch() {
    wx.navigateTo({ url: '/pages/search/search' })
  },

  selectCategory(event) {
    const category = event.currentTarget.dataset.code || ''
    if (category === this.data.selectedCategory) return
    this.setData({ selectedCategory: category, products: [], total: 0 })
    return this.loadProducts(1)
  },

  resetFilters() {
    this.setData({ selectedCategory: '', products: [], total: 0 })
    return this.loadProducts(1)
  },

  previousPage() {
    if (this.data.loading || this.data.page <= 1) return
    return this.loadProducts(this.data.page - 1)
  },

  nextPage() {
    if (this.data.loading || this.data.page >= this.data.totalPages) return
    return this.loadProducts(this.data.page + 1)
  },

  retry() {
    return this.loadInitialData()
  },

  openProduct(event) {
    const code = event.detail?.productCode || event.currentTarget?.dataset?.code
    if (!code) return
    wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },

  async loadProducts(page) {
    const sequence = ++this._requestSequence
    const origin = getApiBaseUrl()
    this.setData({ loading: true, errorMessage: '' })
    try {
      const result = await fetchProducts({
        category: this.data.selectedCategory || undefined,
        page,
        page_size: PAGE_SIZE,
      })
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl()) return
      const total = Number(result.total) || 0
      const pageSize = Number(result.page_size) || PAGE_SIZE
      this.setData({
        products: (result.items || []).map(productView),
        page: Number(result.page) || page,
        total,
        totalPages: Math.max(1, Math.ceil(total / pageSize)),
        loading: false,
      })
    } catch (error) {
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl()) return
      this.setData({ loading: false, errorMessage: messageFor(error, '商品目录暂时无法加载，请稍后重试。') })
    }
  },
})
