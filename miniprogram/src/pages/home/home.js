const { fetchCategories, fetchProducts } = require('../../api/catalog')
const { absoluteMediaUrl } = require('../../api/client')
const { formatCents } = require('../../utils/money')

const PAGE_SIZE = 10

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

function coverUrlFor(product) {
  const images = Array.isArray(product.images) ? product.images : []
  const cover = images.find((image) => image.image_type === 'cover') || images[0]
  if (!cover) return ''
  return absoluteMediaUrl(cover.url || cover.media_url || '')
}

function productAvailability(product) {
  if (product.stock_status === 'out_of_stock') {
    return { available: false, stockLabel: '暂时售罄' }
  }

  const activeSkus = Array.isArray(product.skus)
    ? product.skus.filter((sku) => sku.is_active !== false)
    : []
  if (!activeSkus.length) {
    return { available: false, stockLabel: '暂不可售' }
  }
  if (!activeSkus.some((sku) => Number(sku.stock_quantity) > 0)) {
    return { available: false, stockLabel: '暂时售罄' }
  }
  if (product.stock_status === 'preorder') {
    return { available: true, stockLabel: '可预订' }
  }
  return { available: true, stockLabel: '可选购' }
}

function productView(product) {
  const availability = productAvailability(product)
  return {
    productCode: product.product_code,
    name: product.name,
    subtitle: product.subtitle || '',
    categoryName: product.category && product.category.name ? product.category.name : '商品',
    featured: Boolean(product.featured),
    coverUrl: coverUrlFor(product),
    displayPrice: formatCents(Number(product.base_price_cents) || 0),
    available: availability.available,
    stockLabel: availability.stockLabel,
  }
}

Page({
  data: {
    queryInput: '',
    appliedQuery: '',
    categories: [],
    selectedCategory: '',
    products: [],
    page: 1,
    pageSize: PAGE_SIZE,
    total: 0,
    totalPages: 1,
    loading: true,
    errorMessage: '',
  },

  onLoad() {
    this._requestSequence = 0
    this.loadInitialData()
  },

  async onPullDownRefresh() {
    await this.loadInitialData()
    wx.stopPullDownRefresh()
  },

  async loadInitialData() {
    this.setData({ loading: true, errorMessage: '' })
    try {
      const categories = await fetchCategories()
      this.setData({ categories })
      await this.loadProducts(1)
    } catch (error) {
      this.setData({
        loading: false,
        errorMessage: messageFor(error, '商品目录暂时无法加载，请稍后重试。'),
      })
    }
  },

  onQueryInput(event) {
    this.setData({ queryInput: event.detail.value })
  },

  applySearch() {
    this.setData({ appliedQuery: this.data.queryInput.trim() })
    this.loadProducts(1)
  },

  clearSearch() {
    if (!this.data.queryInput && !this.data.appliedQuery) return
    this.setData({ queryInput: '', appliedQuery: '' })
    this.loadProducts(1)
  },

  resetFilters() {
    this.setData({
      queryInput: '',
      appliedQuery: '',
      selectedCategory: '',
    })
    this.loadProducts(1)
  },

  selectCategory(event) {
    const category = event.currentTarget.dataset.code || ''
    if (category === this.data.selectedCategory) return
    this.setData({ selectedCategory: category })
    this.loadProducts(1)
  },

  previousPage() {
    if (this.data.loading || this.data.page <= 1) return
    this.loadProducts(this.data.page - 1)
  },

  nextPage() {
    if (this.data.loading || this.data.page >= this.data.totalPages) return
    this.loadProducts(this.data.page + 1)
  },

  retry() {
    this.loadInitialData()
  },

  openProduct(event) {
    const productCode = event.detail.productCode
    if (!productCode) return
    wx.navigateTo({
      url: `/pages/product/product?code=${encodeURIComponent(productCode)}`,
    })
  },

  async loadProducts(page) {
    const requestSequence = ++this._requestSequence
    this.setData({ loading: true, errorMessage: '' })
    try {
      const result = await fetchProducts({
        q: this.data.appliedQuery || undefined,
        category: this.data.selectedCategory || undefined,
        page,
        page_size: PAGE_SIZE,
      })
      if (requestSequence !== this._requestSequence) return
      const total = Number(result.total) || 0
      const pageSize = Number(result.page_size) || PAGE_SIZE
      const totalPages = Math.max(1, Math.ceil(total / pageSize))
      this.setData({
        products: (result.items || []).map(productView),
        page: Number(result.page) || page,
        pageSize,
        total,
        totalPages,
        loading: false,
      })
    } catch (error) {
      if (requestSequence !== this._requestSequence) return
      this.setData({
        loading: false,
        errorMessage: messageFor(error, '商品目录暂时无法加载，请稍后重试。'),
      })
    }
  },
})
