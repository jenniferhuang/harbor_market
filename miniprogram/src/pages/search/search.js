const { fetchProducts } = require('../../api/catalog')
const { fetchHotSearches, searchProducts } = require('../../api/shop')
const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
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
    queryInput: '',
    appliedQuery: '',
    inputFocused: true,
    searchSubmitted: false,
    hotSearches: [],
    hotLoading: true,
    hotErrorMessage: '',
    products: [],
    page: 1,
    total: 0,
    totalPages: 1,
    loading: false,
    errorMessage: '',
  },

  onLoad() {
    this._alive = true
    this._requestSequence = 0
    this._hotRequestSequence = 0
    this._apiOrigin = getApiBaseUrl()
    return this.loadHotSearches()
  },

  onShow() {
    if (this._apiOrigin !== getApiBaseUrl()) {
      this._apiOrigin = getApiBaseUrl()
      this._hotRequestSequence += 1
      this.setData({ hotSearches: [], hotErrorMessage: '' })
      return this.clearSearch()
    }
    this.setData({ inputFocused: true })
  },

  onUnload() {
    this._alive = false
    this._requestSequence += 1
    this._hotRequestSequence += 1
  },

  onQueryInput(event) {
    this.setData({ queryInput: event.detail.value })
  },

  async loadHotSearches() {
    const sequence = ++this._hotRequestSequence
    const origin = getApiBaseUrl()
    this.setData({ hotLoading: true, hotErrorMessage: '' })
    try {
      const ranked = await fetchHotSearches()
      if (!this._alive || sequence !== this._hotRequestSequence || origin !== getApiBaseUrl()) return
      this.setData({
        hotSearches: ranked.map((item, index) => ({
          ...productView(item.product),
          rank: index + 1,
          searchHitCount: item.search_hit_count,
        })),
        hotLoading: false,
      })
    } catch (error) {
      if (!this._alive || sequence !== this._hotRequestSequence || origin !== getApiBaseUrl()) return
      this.setData({ hotLoading: false, hotErrorMessage: messageFor(error, '热搜榜暂时无法加载，请稍后重试。') })
    }
  },

  submitSearch() {
    const query = this.data.queryInput.trim()
    if (!query) return this.clearSearch()
    if (this.data.loading && query === this.data.appliedQuery) return
    this.setData({
      appliedQuery: query, searchSubmitted: true, products: [], total: 0,
      page: 1, totalPages: 1, inputFocused: false,
    })
    // Only an explicit submission records a search; pagination and retries use GET.
    return this.loadResults(1, true)
  },

  clearSearch() {
    this._requestSequence += 1
    this.setData({
      queryInput: '', appliedQuery: '', searchSubmitted: false,
      products: [], total: 0, page: 1, totalPages: 1,
      loading: false, errorMessage: '', inputFocused: true,
    })
    return this.loadHotSearches()
  },

  previousPage() {
    if (this.data.loading || this.data.page <= 1) return
    return this.loadResults(this.data.page - 1)
  },

  nextPage() {
    if (this.data.loading || this.data.page >= this.data.totalPages) return
    return this.loadResults(this.data.page + 1)
  },

  retrySearch() {
    return this.loadResults(this.data.page)
  },

  openProduct(event) {
    const code = event.detail?.productCode || event.currentTarget?.dataset?.code
    if (!code) return
    wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },

  async loadResults(page, recordSearch = false) {
    const query = this.data.appliedQuery
    if (!query) return
    const sequence = ++this._requestSequence
    const origin = getApiBaseUrl()
    this.setData({ loading: true, errorMessage: '' })
    try {
      const result = recordSearch
        ? await searchProducts(query, { page_size: PAGE_SIZE })
        : await fetchProducts({ q: query, page, page_size: PAGE_SIZE })
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl() || query !== this.data.appliedQuery) return
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
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl() || query !== this.data.appliedQuery) return
      this.setData({ loading: false, errorMessage: messageFor(error, '搜索暂时无法完成，请稍后重试。') })
    }
  },
})
