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
const { loadCart, addItem } = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

function normalizedImages(product) {
  const images = Array.isArray(product.images) ? product.images : []
  return images.map((image) => ({
    ...image,
    displayUrl: absoluteMediaUrl(image.url || image.media_url || ''),
  }))
}

function decoratedSpecifications(product, selections) {
  const specifications = Array.isArray(product.specifications) ? product.specifications : []
  return specifications.map((specification) => {
    const selectedCodes = selections[specification.code] || []
    return {
      ...specification,
      modeLabel: specification.selection_mode === 'multiple' ? '可多选' : '单选',
      requirementLabel: specification.required ? '必选' : '可选',
      options: (specification.options || []).map((option) => {
        const priceDelta = Number(option.price_delta_cents) || 0
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
  if (sku && Number.isInteger(sku.stock_quantity)) return Math.max(0, sku.stock_quantity)
  if (product.inventory_count === 0) return 0
  if (Number.isInteger(product.inventory_count)) return Math.max(0, product.inventory_count)
  return 999
}

Page({
  data: {
    productCode: '',
    product: null,
    images: [],
    activeImageIndex: 0,
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
  },

  onLoad(options) {
    const rawCode = typeof options.code === 'string' ? options.code : ''
    let productCode = rawCode
    try {
      productCode = decodeURIComponent(rawCode)
    } catch {
      productCode = rawCode
    }
    this.setData({ productCode })
    this.loadProduct()
  },

  async onPullDownRefresh() {
    await this.loadProduct()
    wx.stopPullDownRefresh()
  },

  async loadProduct() {
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
      const selections = initialSelections(product)
      this.setData({
        product,
        images: normalizedImages(product),
        activeImageIndex: 0,
        selections,
        quantity: 1,
        loading: false,
      })
      this.refreshSelectionView()
      wx.setNavigationBarTitle({ title: product.name || '商品详情' })
    } catch (error) {
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
    this.setData({ activeImageIndex: event.detail.current })
  },

  selectGalleryImage(event) {
    this.setData({ activeImageIndex: Number(event.currentTarget.dataset.index) || 0 })
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
    this.setData({ quantity: event.detail.value })
  },

  openCart() {
    wx.switchTab({ url: '/pages/cart/cart' })
  },

  addToCart() {
    if (!this.data.product || !this.data.canAddToCart || this.data.adding) return
    this.setData({ adding: true })
    try {
      const cart = loadCart()
      addItem(cart, this.data.product, this.data.selections, this.data.quantity)
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
    const activeSkus = Array.isArray(product.skus)
      ? product.skus.filter((item) => item.is_active !== false)
      : []
    const skuResolved = Boolean(sku)
    const stockQuantity = stockFor(product, sku)
    const available = validation.valid && skuResolved && stockQuantity > 0
    const maxQuantity = Math.max(1, Math.min(99, stockQuantity))
    const quantity = Math.min(this.data.quantity, maxQuantity)
    let unitPrice = Number(product.base_price_cents) || 0
    try {
      unitPrice = calculateUnitPrice(product, this.data.selections, sku)
    } catch {
      // Keep the server-provided base price visible while the selection is incomplete.
    }

    let selectionHint = ''
    if (activeSkus.length === 0) selectionHint = '商品尚未配置可售 SKU。'
    else if (!validation.valid) selectionHint = validation.message
    else if (!skuResolved) selectionHint = '当前选项没有可用 SKU，请选择其他组合。'
    else if (!available) selectionHint = '当前选项暂时无库存。'

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
      stockLabel:
        stockQuantity >= 999
          ? product.stock_status === 'preorder'
            ? '可预订'
            : '有货'
          : stockQuantity > 0
            ? `库存 ${stockQuantity}`
            : '暂时售罄',
      canAddToCart: available,
    })
  },
})
