const { fetchProduct } = require('../../api/catalog')
const { absoluteMediaUrl } = require('../../api/client')
const {
  initialSelections,
  toggleSelection,
  validateSelections,
  resolveSku,
  calculateUnitPrice,
  selectionSummary,
} = require('../../domain/catalog')
const { loadCart, addItem, cartQuantityForSku } = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')
const { getApiBaseUrl } = require('../../api/client')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { getFavorite, setFavorite } = require('../../api/customer-shop')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message) ? error.message : fallback
}

function normalizedImages(product, role) {
  const images = Array.isArray(product.images) ? product.images : []
  return images.filter((image) => image && (role === 'detail'
    ? image.image_type === 'detail'
    : image.image_type === 'cover' || image.image_type === 'gallery' || !image.image_type))
    .slice().sort((left, right) => {
      if (role !== 'detail' && (left.image_type === 'cover') !== (right.image_type === 'cover')) return left.image_type === 'cover' ? -1 : 1
      return (left.sort_order || 0) - (right.sort_order || 0) || (left.id || 0) - (right.id || 0)
    }).map((image) => ({
    ...image,
    displayUrl: absoluteMediaUrl(image.url || image.media_url || ''),
  })).filter((image) => image.displayUrl)
}

function activeSkusFor(product) {
  return (Array.isArray(product.skus) ? product.skus : []).filter((sku) => sku && sku.is_active !== false)
    .slice().sort((left, right) => Number(Boolean(right.is_default)) - Number(Boolean(left.is_default)) ||
      (left.sort_order || 0) - (right.sort_order || 0) || (left.id || 0) - (right.id || 0))
}

function skuRows(product, unit) {
  return activeSkusFor(product).map((sku) => {
    const attributesLabel = Object.entries(sku.attributes || {}).map(([code, value]) => {
      const specification = (product.specifications || []).find((item) => item.code === code)
      const option = specification && (specification.options || []).find((item) => item.code === value)
      return `${specification ? specification.name : code}：${option ? option.name : value}`
    }).join(' / ')
    return {
      code: sku.sku_code,
      name: sku.name || attributesLabel || sku.sku_code || '规格',
      attributesLabel,
      displayPrice: formatCents(sku.price_cents),
      marketPrice: Number(sku.market_price_cents) > sku.price_cents ? formatCents(sku.market_price_cents) : '',
      stockText: Number.isSafeInteger(sku.stock_quantity) && sku.stock_quantity >= 0 ? `库存 ${sku.stock_quantity} ${unit}` : '库存暂未确认',
      available: product.stock_status !== 'out_of_stock' && Number.isSafeInteger(sku.stock_quantity) && sku.stock_quantity > 0,
    }
  })
}

function decoratedSpecifications(product, selections) {
  const specifications = Array.isArray(product.specifications) ? product.specifications : []
  const variantCodes = new Set(activeSkusFor(product).flatMap((sku) => Object.keys(sku.attributes || {})))
  return specifications.map((specification) => {
    const selectedCodes = selections[specification.code] || []
    return {
      ...specification,
      modeLabel: specification.selection_mode === 'multiple' ? '可多选' : '单选',
      requirementLabel: specification.required ? '必选' : '可选',
      options: (specification.options || []).map((option) => {
        const priceDelta = variantCodes.has(specification.code) ? 0 : Number(option.price_delta_cents) || 0
        return {
          ...option,
          selected: selectedCodes.includes(option.code),
          priceDeltaLabel:
            priceDelta > 0
              ? `+${formatCents(priceDelta)}`
              : priceDelta < 0
                ? formatCents(priceDelta)
                : '',
        }
      }),
    }
  })
}

function stockFor(product, sku) {
  if (product.stock_status === 'out_of_stock') return 0
  return sku && Number.isSafeInteger(sku.stock_quantity) && sku.stock_quantity >= 0 ? sku.stock_quantity : null
}

Page({
  data: {
    productCode: '',
    product: null,
    images: [],
    detailImages: [],
    coverImageUrl: '',
    activeImageIndex: 0,
    activeSkuRows: [],
    hasAvailableSku: false,
    displayUnit: '件',
    stockStatusLabel: '',
    inventoryLabel: '',
    specSheetVisible: false,
    specifications: [],
    selections: {},
    selectionText: '',
    selectionHint: '',
    selectedSku: null,
    displayPrice: '',
    marketPrice: '',
    quantity: 1,
    maxQuantity: 1,
    stockLabel: '',
    canAddToCart: false,
    adding: false,
    loading: true,
    errorMessage: '',
    isFavorite: false,
    favoriteBusy: false,
    favoriteError: '',
  },

  onLoad(options = {}) {
    this._alive = true
    this._favoriteSequence = 0
    this._loadSequence = 0
    this._autoChoose = options.choose === '1'
    const rawCode = typeof options.code === 'string' ? options.code : ''
    let productCode = rawCode
    try {
      productCode = decodeURIComponent(rawCode)
    } catch {
      productCode = rawCode
    }
    this.setData({ productCode })
    this._unsubscribe = subscribe((state) => {
      const id = state.status === 'authenticated' ? state.customer.id : null
      if (id === this._customerId) return
      this._customerId = id
      this._favoriteSequence += 1
      if (this._alive) this.setData({ isFavorite: false, favoriteBusy: false, favoriteError: '' })
      if (id) this.loadFavorite()
    })
    this.loadProduct()
  },

  onShow() {
    this.refreshCartDock()
    this.refreshSelectionView()
    if (getAuthState().status === 'authenticated') this.loadFavorite()
  },
  onHide() { this.closeSpecSheet() },
  onUnload() {
    this._alive = false
    this._favoriteSequence += 1
    this._loadSequence += 1
    if (this._unsubscribe) this._unsubscribe()
  },

  async loadFavorite() {
    if (!this.data.productCode || this.data.favoriteBusy) return
    const sequence = ++this._favoriteSequence
    const origin = getApiBaseUrl()
    try {
      const result = await getFavorite(this.data.productCode)
      if (this._alive && sequence === this._favoriteSequence && origin === getApiBaseUrl()) this.setData({ isFavorite: result.is_favorite, favoriteError: '' })
    } catch (error) {
      if (this._alive && sequence === this._favoriteSequence && origin === getApiBaseUrl()) this.setData({ favoriteError: error.message || '收藏状态暂时无法加载。' })
    }
  },

  async toggleFavorite() {
    if (!this.data.product || this.data.favoriteBusy) return
    if (getAuthState().status !== 'authenticated') {
      wx.showModal({ title: '登录后收藏商品', content: '通过微信登录后，可把喜欢的商品保存到“我的收藏”。', confirmText: '去登录', cancelText: '继续浏览', success: (result) => { if (result.confirm) wx.switchTab({ url: '/pages/account/account' }) } })
      return
    }
    const sequence = ++this._favoriteSequence
    const origin = getApiBaseUrl()
    this.setData({ favoriteBusy: true, favoriteError: '' })
    try {
      const result = await setFavorite(this.data.productCode, !this.data.isFavorite)
      if (this._alive && sequence === this._favoriteSequence && origin === getApiBaseUrl()) {
        this.setData({ isFavorite: result.is_favorite })
        wx.showToast({ title: result.is_favorite ? '已收藏' : '已取消收藏', icon: 'success' })
      }
    } catch (error) {
      if (this._alive && sequence === this._favoriteSequence && origin === getApiBaseUrl()) this.setData({ favoriteError: error.message || '收藏保存失败，请重试。' })
    } finally {
      if (this._alive && sequence === this._favoriteSequence && origin === getApiBaseUrl()) this.setData({ favoriteBusy: false })
    }
  },

  async onPullDownRefresh() {
    await this.loadProduct()
    wx.stopPullDownRefresh()
  },

  async loadProduct() {
    const sequence = ++this._loadSequence
    const origin = getApiBaseUrl()
    if (!this.data.productCode) {
      this.setData({
        loading: false,
        errorMessage: '缺少商品编码，请返回商品列表后重试。',
      })
      return
    }
    this.setData({ loading: true, errorMessage: '' })
    try {
      const product = await fetchProduct(this.data.productCode)
      if (!this._alive || sequence !== this._loadSequence || origin !== getApiBaseUrl()) return
      const selections = initialSelections(product)
      const images = normalizedImages(product, 'gallery')
      const unit = product.unit || '件'
      const rows = skuRows(product, unit)
      this.setData({
        product,
        images,
        detailImages: normalizedImages(product, 'detail'),
        coverImageUrl: images[0] ? images[0].displayUrl : '',
        activeSkuRows: rows,
        hasAvailableSku: rows.some((row) => row.available),
        displayUnit: unit,
        stockStatusLabel: product.stock_status === 'preorder' ? '可预订' : product.stock_status === 'out_of_stock' ? '暂时缺货' : '现货',
        inventoryLabel: Number.isSafeInteger(product.inventory_count) && product.inventory_count >= 0 ? `${product.inventory_count} ${unit}` : '',
        activeImageIndex: 0,
        selections,
        quantity: 1,
        loading: false,
      })
      this.refreshSelectionView()
      wx.setNavigationBarTitle({ title: product.name || '商品详情' })
      if (this._autoChoose) {
        this._autoChoose = false
        this.openSpecSheet()
      }
    } catch (error) {
      if (!this._alive || sequence !== this._loadSequence || origin !== getApiBaseUrl()) return
      this.setData({
        loading: false,
        errorMessage: messageFor(error, '商品详情暂时无法加载，请稍后重试。'),
      })
    }
  },

  retry() {
    this.loadProduct()
  },

  onGalleryChange(event) {
    const index = Number(event.detail.current)
    if (Number.isInteger(index) && index >= 0 && index < this.data.images.length) this.setData({ activeImageIndex: index })
  },

  selectGalleryImage(event) {
    this.onGalleryChange({ detail: { current: Number(event.currentTarget.dataset.index) } })
  },

  previewImage(event) {
    const images = event.currentTarget.dataset.role === 'detail' ? this.data.detailImages : this.data.images
    const image = images[Number(event.currentTarget.dataset.index)]
    if (image && image.displayUrl) wx.previewImage({ current: image.displayUrl, urls: images.map((item) => item.displayUrl) })
  },

  openSpecSheet() {
    if (!this.data.product || this.data.loading) return
    this.refreshSelectionView()
    this.setData({ specSheetVisible: true })
  },

  closeSpecSheet() {
    if (this.data.adding) return
    this.setData({ specSheetVisible: false })
  },

  stopSheetTouch() {},

  onCartChange() { this.refreshSelectionView() },

  refreshCartDock() {
    if (typeof this.selectComponent !== 'function') return
    const dock = this.selectComponent('#cart-dock')
    if (dock && typeof dock.refresh === 'function') dock.refresh()
  },

  selectOption(event) {
    if (!this.data.product) return
    const { specificationCode, optionCode } = event.currentTarget.dataset
    const selections = toggleSelection(
      this.data.product,
      this.data.selections,
      specificationCode,
      optionCode,
    )
    this.setData({ selections })
    this.refreshSelectionView()
  },

  changeQuantity(event) {
    const value = event.detail.value
    if (!Number.isInteger(value)) return
    this.setData({ quantity: Math.max(1, Math.min(this.data.maxQuantity, value)) })
  },

  openCouponCenter() {
    wx.navigateTo({ url: '/pages/coupon-center/coupon-center' })
  },

  openCart() {
    wx.switchTab({ url: '/pages/cart/cart' })
  },

  addToCart() {
    if (!this.data.product || this.data.adding) return
    this.refreshSelectionView()
    if (!this.data.canAddToCart) { this.openSpecSheet(); return }
    this.setData({ adding: true })
    try {
      const cart = loadCart()
      addItem(cart, this.data.product, this.data.selections, this.data.quantity)
      this.setData({ specSheetVisible: false })
      this.refreshSelectionView()
      this.refreshCartDock()
      wx.showToast({ title: '已加入购物车', icon: 'success' })
    } catch (error) {
      wx.showModal({
        title: '未能加入购物车',
        content: messageFor(error, '请检查商品选项与库存后重试。'),
        showCancel: false,
      })
    } finally {
      this.setData({ adding: false })
    }
  },

  refreshSelectionView() {
    const product = this.data.product
    if (!product) return

    const validation = validateSelections(product, this.data.selections)
    const sku = validation.valid ? resolveSku(product, this.data.selections) : null
    const activeSkus = activeSkusFor(product)
    const skuResolved = Boolean(sku)
    const stockQuantity = stockFor(product, sku)
    const cartQuantity = sku ? cartQuantityForSku(loadCart(), product.product_code, sku.sku_code) : 0
    const remainingQuantity = stockQuantity === null ? 0 : Math.max(0, stockQuantity - cartQuantity)
    const available = validation.valid && skuResolved && remainingQuantity > 0
    const maxQuantity = Math.max(1, Math.min(99, remainingQuantity))
    const quantity = Math.min(this.data.quantity, maxQuantity)
    let unitPrice = Number(product.base_price_cents) || 0
    let validPrice = true
    try {
      unitPrice = calculateUnitPrice(product, this.data.selections, sku)
    } catch {
      validPrice = false
      // Keep the server-provided base price visible while the selection is incomplete.
    }

    let selectionHint = ''
    if (activeSkus.length === 0) selectionHint = '商品尚未配置可售规格。'
    else if (!validation.valid) selectionHint = validation.message
    else if (!skuResolved) selectionHint = '当前选项没有可用规格，请选择其他组合。'
    else if (stockQuantity === null) selectionHint = '当前规格库存暂未确认。'
    else if (stockQuantity === 0) selectionHint = '当前选项暂时无库存。'
    else if (!available) selectionHint = '购物车中该规格的数量已达到库存上限。'
    else if (!validPrice) selectionHint = '当前规格价格暂未确认。'

    this.setData({
      specifications: decoratedSpecifications(product, this.data.selections),
      selectedSku: sku,
      selectionText: selectionSummary(product, this.data.selections),
      selectionHint,
      displayPrice: formatCents(unitPrice),
      marketPrice:
        Number(sku && sku.market_price_cents) > unitPrice
          ? formatCents(Number(sku.market_price_cents))
          : Number(product.market_price_cents) > unitPrice
            ? formatCents(Number(product.market_price_cents))
            : '',
      quantity,
      maxQuantity,
      stockLabel: activeSkus.length === 0 ? '暂不可售' : !sku ? '请选择规格' : stockQuantity === null ? '库存暂未确认' :
        stockQuantity > 0 ? `库存 ${stockQuantity} ${this.data.displayUnit}${cartQuantity ? ` · 购物车已选 ${cartQuantity}` : ''}` : '暂时售罄',
      canAddToCart: available && validPrice,
    })
  },
})
