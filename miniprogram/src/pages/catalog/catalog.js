'use strict'

const { fetchCategories, fetchProducts } = require('../../api/catalog')
const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
const { consumeCatalogCategory } = require('../../state/catalog-navigation')
const { formatCents } = require('../../utils/money')

const PAGE_SIZE = 20

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function productView(product) {
  const images = Array.isArray(product.images) ? product.images : []
  const cover = images.find((image) => image.image_type === 'cover') || images[0]
  const skus = (Array.isArray(product.skus) ? product.skus : []).filter((sku) => sku.is_active !== false)
  const available = product.stock_status !== 'out_of_stock' && skus.some((sku) => Number(sku.stock_quantity) > 0)
  const marketPrice = Number(product.market_price_cents)
  return {
    productCode: product.product_code,
    name: product.name,
    subtitle: product.subtitle || '',
    coverUrl: cover ? absoluteMediaUrl(cover.url || cover.media_url || '') : '',
    displayPrice: formatCents(Number(product.base_price_cents) || 0),
    marketPrice: Number.isSafeInteger(marketPrice) && marketPrice > Number(product.base_price_cents) ? formatCents(marketPrice) : '',
    unit: product.unit || '',
    available,
    stockLabel: !skus.length ? '暂不可售' : !available ? '暂时售罄' : product.stock_status === 'preorder' ? '可预订' : '可选购',
  }
}

function sectionView(category, index) {
  return {
    code: category.code, name: category.name, index, anchor: `catalog-section-${index}`,
    products: [], total: 0, nextPage: 1, complete: false, errorMessage: '',
  }
}

Page({
  data: {
    categories: [], selectedCategory: '', sections: [], railCollapsed: false,
    railIntoView: '', productIntoView: '', loading: true,
    errorMessage: '', categoryErrorMessage: '', nextCategoryName: '', endReached: false,
  },

  onLoad() {
    this._alive = true
    this._generation = 0
    this._scrollSequence = 0
    this._apiOrigin = getApiBaseUrl()
    this._initialLoadStarted = false
    this._requestedCategory = ''
  },

  onShow() {
    const dock = typeof this.selectComponent === 'function' ? this.selectComponent('#cart-dock') : null
    if (dock && typeof dock.refresh === 'function') dock.refresh()
    const originChanged = this._apiOrigin !== getApiBaseUrl()
    if (originChanged) this._apiOrigin = getApiBaseUrl()
    const category = consumeCatalogCategory()
    if (!this._initialLoadStarted || originChanged) {
      this._initialLoadStarted = true
      return this.loadInitialData(category === null ? (originChanged ? '' : this._requestedCategory) : category)
    }
    if (category !== null) {
      this._requestedCategory = category
      if (this._loadingCategories) return this._reloadWorker
      return this.jumpCategory(category)
    }
  },

  onUnload() {
    this._alive = false
    this._generation += 1
    this._scrollSequence += 1
    this._queuedProduct = null
    this._queuedReload = null
  },

  async onPullDownRefresh() {
    try { await this.loadInitialData(this.data.selectedCategory) } finally { wx.stopPullDownRefresh() }
  },

  _current(task) {
    return this._alive && task.generation === this._generation && task.origin === getApiBaseUrl()
  },

  loadInitialData(requestedCode = this.data.selectedCategory) {
    this._requestedCategory = requestedCode
    const generation = ++this._generation
    this._scrollSequence += 1
    this._queuedProduct = null
    this.setData({ categories: [], sections: [], selectedCategory: '', railIntoView: '', productIntoView: '', loading: true, errorMessage: '', categoryErrorMessage: '', nextCategoryName: '', endReached: false })
    this._queuedReload = { generation, origin: getApiBaseUrl() }
    if (!this._reloadWorker) {
      // Coalesce repeated refreshes while keeping category requests sequential.
      this._reloadWorker = Promise.resolve().then(() => this._drainReloads()).finally(() => { this._reloadWorker = null })
    }
    return this._reloadWorker
  },

  async _drainReloads() {
    while (this._queuedReload) {
      const task = this._queuedReload
      this._queuedReload = null
      let categories = []
      let categoryErrorMessage = ''
      this._loadingCategories = true
      try {
        const result = await fetchCategories()
        categories = result.filter((category) => category && typeof category.code === 'string' && category.code && typeof category.name === 'string')
      } catch (error) {
        categoryErrorMessage = messageFor(error, '类目暂时无法加载，可先浏览全部商品。')
      } finally {
        this._loadingCategories = false
      }
      if (!this._current(task)) continue
      this.setData({ categories, categoryErrorMessage })
      const index = Math.max(0, categories.findIndex((category) => category.code === this._requestedCategory))
      const category = categories[index] || { code: '', name: '全部商品' }
      this.setData({ sections: [sectionView(category, index)], selectedCategory: category.code, railIntoView: `catalog-category-${index}`, productIntoView: `catalog-section-${index}` })
      await this._queuePage(0, 1)
    }
  },

  _queuePage(sectionIndex, page) {
    const section = this.data.sections[sectionIndex]
    if (!section || !this._alive) return
    const task = { generation: this._generation, origin: getApiBaseUrl(), sectionIndex, code: section.code, page }
    const pending = this._activeProduct || this._queuedProduct
    if (pending && pending.generation === task.generation && pending.sectionIndex === sectionIndex && pending.page === page) return this._productWorker
    this._queuedProduct = task
    this.setData({ loading: true, errorMessage: '' })
    if (!this._productWorker) {
      // One request at a time; rapid category clicks replace the queued destination.
      this._productWorker = Promise.resolve().then(() => this._drainProducts()).finally(() => { this._productWorker = null })
    }
    return this._productWorker
  },

  async _drainProducts() {
    while (this._queuedProduct) {
      const task = this._queuedProduct
      this._queuedProduct = null
      if (!this._current(task)) continue
      this._activeProduct = task
      try {
        const result = await fetchProducts({ category: task.code || undefined, page: task.page, page_size: PAGE_SIZE })
        if (!this._current(task)) continue
        const section = this.data.sections[task.sectionIndex]
        if (!section || section.code !== task.code) continue
        const pageSize = Number(result.page_size)
        const total = Number(result.total)
        if (result.page !== task.page || !Number.isInteger(pageSize) || pageSize < 1 || pageSize > PAGE_SIZE || !Number.isSafeInteger(total) || total < 0 || result.items.length > pageSize) throw new Error('商品分页数据异常，请重新加载。')
        const products = task.page === 1 ? [] : section.products
        const seen = new Set(products.map((item) => item.productCode))
        const items = result.items.map(productView)
        for (const item of items) {
          if (typeof item.productCode !== 'string' || !item.productCode || seen.has(item.productCode)) throw new Error('商品分页数据重复，请重新加载。')
          seen.add(item.productCode)
        }
        const nextProducts = [...products, ...items]
        // An empty page terminates traversal even when a changing catalog has stale totals.
        const complete = items.length === 0 || task.page * pageSize >= total
        const sections = this.data.sections.map((row, index) => index === task.sectionIndex
          ? { ...row, products: nextProducts, total, nextPage: task.page + 1, complete, errorMessage: '' } : row)
        const last = sections[sections.length - 1]
        const nextCategory = last.code ? this.data.categories[last.index + 1] : null
        this.setData({ sections, loading: false, errorMessage: '', nextCategoryName: last.complete && nextCategory ? nextCategory.name : '', endReached: last.complete && !nextCategory })
      } catch (error) {
        if (!this._current(task)) continue
        const errorMessage = messageFor(error, '商品暂时无法加载，请稍后重试。')
        this.setData({ loading: false, errorMessage, sections: this.data.sections.map((section, index) => index === task.sectionIndex ? { ...section, errorMessage } : section) })
      } finally {
        this._activeProduct = null
      }
    }
  },

  jumpCategory(code) {
    const index = this.data.categories.findIndex((category) => category.code === code)
    const targetIndex = code === '' || index < 0 ? 0 : index
    const category = this.data.categories[targetIndex] || { code: '', name: '全部商品' }
    const loaded = this.data.sections.find((section) => section.code === category.code)
    this._scrollSequence += 1
    this.setData({ selectedCategory: category.code, railIntoView: `catalog-category-${targetIndex}` })
    if (loaded) {
      this.setData({ productIntoView: '' }, () => {
        if (this._alive) this.setData({ productIntoView: loaded.anchor })
      })
      return this._productWorker
    }
    this._generation += 1
    this._queuedProduct = null
    this.setData({ sections: [sectionView(category, targetIndex)], productIntoView: `catalog-section-${targetIndex}`, errorMessage: '', nextCategoryName: '', endReached: false })
    return this._queuePage(0, 1)
  },

  selectCategory(event) {
    const code = event.currentTarget.dataset.code || ''
    if (this._loadingCategories || !this.data.sections.length) {
      this._requestedCategory = code
      return this._reloadWorker
    }
    return this.jumpCategory(code)
  },

  toggleRail() {
    this.setData({ railCollapsed: !this.data.railCollapsed })
  },

  loadMore() {
    if (this.data.loading || this.data.errorMessage || !this._alive) return this._productWorker
    const sections = this.data.sections
    const last = sections[sections.length - 1]
    if (!last) return
    if (!last.complete) return this._queuePage(sections.length - 1, last.nextPage)
    const category = last.code ? this.data.categories[last.index + 1] : null
    if (!category) return
    this.setData({ sections: [...sections, sectionView(category, last.index + 1)], nextCategoryName: '', endReached: false })
    return this._queuePage(sections.length, 1)
  },

  retrySection() {
    const index = this.data.sections.length - 1
    const section = this.data.sections[index]
    if (!section || this.data.loading) return
    return this._queuePage(index, section.nextPage)
  },

  retry() {
    return this.loadInitialData(this.data.selectedCategory)
  },

  onProductScroll() {
    if (this._measuringScroll || typeof wx.createSelectorQuery !== 'function' || !this.data.sections.length) return
    this._measuringScroll = true
    const task = { generation: this._generation, origin: getApiBaseUrl(), sequence: this._scrollSequence }
    wx.createSelectorQuery().in(this).select('#catalog-products').boundingClientRect().selectAll('.catalog-section').boundingClientRect().exec((results) => {
      this._measuringScroll = false
      if (!this._current(task) || task.sequence !== this._scrollSequence) return
      const viewport = results && results[0]
      const rectangles = results && results[1]
      if (!viewport || !Array.isArray(rectangles)) return
      const entered = rectangles.filter((rectangle) => rectangle.top <= viewport.top + 24 && rectangle.bottom > viewport.top)
      const visible = entered[entered.length - 1] || rectangles.find((rectangle) => rectangle.top < viewport.bottom && rectangle.bottom > viewport.top)
      const section = visible && this.data.sections.find((item) => item.anchor === visible.id)
      if (section && section.code !== this.data.selectedCategory) this.setData({ selectedCategory: section.code, railIntoView: `catalog-category-${section.index}` })
    })
  },

  openSearch() {
    wx.navigateTo({ url: '/pages/search/search' })
  },

  openProduct(event) {
    const code = event.detail?.productCode || event.currentTarget?.dataset?.code
    if (code) wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },

  chooseProduct(event) {
    const code = event.currentTarget?.dataset?.code
    if (code) wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}&choose=1` })
  },
})
