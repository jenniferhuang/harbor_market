const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
const { fetchProducts } = require('../../api/catalog')
const { fetchMyCoupons } = require('../../api/customer-shop')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { readSession } = require('../../state/customer-session')
const {
  loadCart, updateQuantity, removeItem, clearCart, cartSummary, maxQuantityForItem,
  setItemSelected, setAllSelected, invertSelection, selectedSummary,
} = require('../../state/cart-store')
const { bestCoupon, evaluateCoupons } = require('../../domain/coupons')
const { formatCents } = require('../../utils/money')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function cartItemView(item, cart) {
  return {
    ...item,
    coverUrl: absoluteMediaUrl(item.coverUrl || ''),
    displayUnitPrice: formatCents(item.unitPriceCents),
    displayLineTotal: formatCents(item.unitPriceCents * item.quantity),
    maxQuantity: maxQuantityForItem(cart, item.key),
    stockLabel: Number.isSafeInteger(item.stockQuantity) ? `库存记录：${item.stockQuantity}` : '',
  }
}

function couponIdentity(state) {
  const session = readSession()
  return state.status === 'authenticated' && session && state.customer && session.customer.id === state.customer.id
    ? `${getApiBaseUrl()}|${session.customer.id}|${session.accessToken}` : ''
}

function couponHint(item) {
  if (item.eligible) return `满${formatCents(item.min_spend_cents).slice(1)}元减${formatCents(item.discount_cents).slice(1)}元`
  if (item.reason === 'below_minimum') return `差${formatCents(item.shortfallCents).slice(1)}元可减${formatCents(item.discount_cents).slice(1)}元`
  return ({ inactive: '已停用', not_started: '尚未开始', expired: '已过期', unclaimed: '尚未领取', invalid: '暂不可用' })[item.reason] || '暂不可用'
}

function recommendationView(product) {
  const images = Array.isArray(product.images) ? product.images : []
  const cover = images.find((image) => image.image_type === 'cover') || images[0]
  const skus = (Array.isArray(product.skus) ? product.skus : []).filter((sku) => sku && sku.is_active !== false)
  const available = product.stock_status !== 'out_of_stock' && skus.some((sku) => Number.isSafeInteger(sku.stock_quantity) && sku.stock_quantity > 0)
  return {
    productCode: product.product_code, name: product.name, subtitle: product.subtitle || '',
    categoryName: product.category?.name || '商品', featured: Boolean(product.featured),
    coverUrl: absoluteMediaUrl(cover?.url || ''), displayPrice: formatCents(product.base_price_cents),
    available, stockLabel: available ? (product.stock_status === 'preorder' ? '可预订' : '可选购') : '暂时售罄',
  }
}

Page({
  data: {
    cart: { version: 1, items: [] }, items: [],
    summary: { lineCount: 0, totalQuantity: 0, totalCents: 0 },
    selectedSummary: { lineCount: 0, totalQuantity: 0, totalCents: 0 },
    allSelected: false, displaySubtotal: formatCents(0), displayTotal: formatCents(0),
    displayDiscount: formatCents(0), discountCents: 0, errorMessage: '', editing: false,
    loggedIn: false, couponLoading: false, couponErrorMessage: '', couponRows: [],
    couponLimited: false,
    chosenCouponId: null, couponChoiceLabel: '暂未选择优惠券', couponHint: '领券后，购物更优惠',
    couponPickerVisible: false, noCouponSelected: false,
    recommendations: [], recommendationsLoading: false, recommendationsErrorMessage: '',
  },

  onLoad() {
    if (this._alive) return
    this._alive = true
    this._visible = false
    this._couponSequence = 0
    this._recommendationSequence = 0
    this._coupons = []
    this._couponChoice = undefined
    this._unsubscribe = subscribe((state) => this.applyAuthState(state))
  },

  onShow() {
    if (!this._alive) this.onLoad()
    this._visible = true
    this.refreshCart()
    this.applyAuthState(getAuthState())
    if (this._couponIdentity && !this._couponPending) this.loadCoupons()
    if (this._recommendationOrigin !== getApiBaseUrl() || !this._recommendationsLoaded) this.loadRecommendations()
  },

  onHide() {
    this._visible = false
    this._couponSequence += 1
    this._recommendationSequence += 1
    this._couponPending = false
    this._recommendationPending = false
    this.setData({ couponLoading: false, recommendationsLoading: false, couponPickerVisible: false })
  },

  onUnload() {
    this._alive = false
    this._visible = false
    this._couponSequence += 1
    this._recommendationSequence += 1
    if (this._unsubscribe) this._unsubscribe()
  },

  applyAuthState(state) {
    const identity = couponIdentity(state)
    if (identity === this._couponIdentity) return
    this._couponIdentity = identity
    this._couponSequence += 1
    this._couponPending = false
    this._coupons = []
    this._couponChoice = undefined
    this.setData({ loggedIn: Boolean(identity), couponLoading: false, couponErrorMessage: '', couponPickerVisible: false, couponLimited: false })
    this.refreshCouponPreview()
    if (identity && this._visible) this.loadCoupons()
  },

  couponRequestCurrent(sequence, identity) {
    if (!this._alive || !this._visible || sequence !== this._couponSequence) return false
    const activeIdentity = couponIdentity(getAuthState())
    if (activeIdentity !== this._couponIdentity) this.applyAuthState(getAuthState())
    return identity !== '' && identity === activeIdentity && identity === this._couponIdentity && sequence === this._couponSequence
  },

  async loadCoupons() {
    this.applyAuthState(getAuthState())
    const identity = this._couponIdentity
    if (!identity || !this._visible || this._couponPending) return
    this._couponPending = true
    const sequence = ++this._couponSequence
    this._coupons = []
    this.setData({ couponLoading: true, couponErrorMessage: '', couponLimited: false })
    this.refreshCouponPreview()
    try {
      const coupons = []
      const seen = new Set()
      let limited = false
      for (let page = 1; page <= 10; page += 1) {
        const rows = await fetchMyCoupons({ page, page_size: 100 })
        if (!this.couponRequestCurrent(sequence, identity)) return
        if (!Array.isArray(rows)) throw new Error('优惠券数据格式不正确。')
        for (const row of rows) {
          if (!row || seen.has(row.coupon_id)) continue
          seen.add(row.coupon_id)
          coupons.push(row)
        }
        if (rows.length < 100) break
        if (page === 10) limited = true
      }
      if (!this.couponRequestCurrent(sequence, identity)) return
      this._coupons = coupons
      this.setData({ couponLimited: limited })
    } catch (error) {
      if (!this.couponRequestCurrent(sequence, identity)) return
      this._coupons = []
      this.setData({ couponErrorMessage: messageFor(error, '优惠券暂时无法加载，请重试。'), couponLimited: false })
    } finally {
      if (sequence === this._couponSequence && this._alive) {
        this._couponPending = false
        this.setData({ couponLoading: false })
        this.refreshCouponPreview()
      }
    }
  },

  refreshCouponPreview() {
    const subtotal = this.data.selectedSummary.totalCents
    const evaluations = evaluateCoupons(this._coupons || [], subtotal)
    const chosen = this._couponChoice === undefined ? bestCoupon(evaluations)
      : evaluations.find((item) => item.coupon_id === this._couponChoice && item.eligible) || null
    const discount = chosen ? chosen.appliedDiscountCents : 0
    const nextOffer = evaluations.filter((item) => item.reason === 'below_minimum')
      .sort((left, right) => left.shortfallCents - right.shortfallCents || right.discount_cents - left.discount_cents)[0]
    let hint = !this.data.loggedIn ? '登录后可使用已领取的优惠券' : '去领券中心看看，有好券等你领取'
    if (this.data.couponLoading) hint = '正在加载已领取的优惠券'
    else if (this.data.couponErrorMessage) hint = '优惠券加载失败，暂不计入优惠'
    else if (chosen) hint = `已优惠${formatCents(discount)}`
    else if (this._couponChoice === null) hint = '本次不使用优惠券'
    else if (nextOffer) hint = couponHint(nextOffer)
    else if (evaluations.some((item) => item.eligible)) hint = '选择一张优惠券享受优惠'
    this.setData({
      couponRows: evaluations.map((item) => ({ coupon_id: item.coupon_id,
        title: typeof item.title === 'string' ? item.title.slice(0, 200) : '优惠券',
        eligible: item.eligible, reason: item.reason, hint: couponHint(item), selected: chosen?.coupon_id === item.coupon_id })),
      chosenCouponId: chosen?.coupon_id || null, noCouponSelected: this._couponChoice === null,
      couponChoiceLabel: chosen ? chosen.title || '已选优惠券' : this._couponChoice === null ? '不用券' : '选择优惠券',
      couponHint: hint, discountCents: discount, displayDiscount: formatCents(discount),
      displaySubtotal: formatCents(subtotal), displayTotal: formatCents(Math.max(0, subtotal - discount)),
    })
  },

  refreshCart() {
    try { this.applyCart(loadCart()) } catch (error) {
      this.setData({ errorMessage: messageFor(error, '购物车暂时无法读取，请稍后重试。') })
    }
  },

  applyCart(cart) {
    const summary = cartSummary(cart)
    const selected = selectedSummary(cart)
    this.setData({ cart, items: cart.items.map((item) => cartItemView(item, cart)), summary, selectedSummary: selected,
      allSelected: cart.items.length > 0 && cart.items.every((item) => item.selected), errorMessage: '' })
    this.refreshCouponPreview()
  },

  changeSelection(event) {
    const key = event.currentTarget.dataset.key
    const cart = loadCart()
    const item = cart.items.find((candidate) => candidate.key === key)
    if (item) this.mutateCart(() => setItemSelected(cart, key, !item.selected), '选择更新失败')
  },

  toggleAll() {
    const cart = loadCart()
    this.mutateCart(() => setAllSelected(cart, !cart.items.every((item) => item.selected)), '选择更新失败')
  },

  invertSelected() { this.mutateCart(() => invertSelection(loadCart()), '选择更新失败') },
  toggleEditing() { this.setData({ editing: !this.data.editing }) },

  mutateCart(operation, title) {
    try { this.applyCart(operation()) } catch (error) {
      wx.showModal({ title, content: messageFor(error, '请稍后重试。'), showCancel: false })
    }
  },

  changeQuantity(event) {
    this.mutateCart(() => updateQuantity(loadCart(), event.currentTarget.dataset.key, event.detail.value), '数量更新失败')
  },

  removeLine(event) {
    const key = event.currentTarget.dataset.key
    const item = this.data.items.find((candidate) => candidate.key === key)
    wx.showModal({ title: '移除商品', content: `确定从购物车移除“${item ? item.productName : '该商品'}”吗？`,
      confirmText: '移除', confirmColor: '#a3342a', success: (result) => {
        if (this._alive && result.confirm) this.mutateCart(() => removeItem(loadCart(), key), '移除失败')
      } })
  },

  clearAll() {
    if (!this.data.items.length) return
    wx.showModal({ title: '清空购物车', content: '确定移除购物车中的全部商品吗？',
      confirmText: '清空', confirmColor: '#a3342a', success: (result) => {
        if (this._alive && result.confirm) this.mutateCart(() => clearCart(), '清空失败')
      } })
  },

  continueShopping() { wx.switchTab({ url: '/pages/catalog/catalog' }) },
  openCouponCenter() { wx.navigateTo({ url: '/pages/coupon-center/coupon-center?from=cart' }) },
  login() { wx.switchTab({ url: '/pages/account/account' }) },
  openCouponPicker() {
    this.applyAuthState(getAuthState())
    this.refreshCouponPreview()
    if (!this.data.loggedIn) { this.login(); return }
    this.setData({ couponPickerVisible: true })
  },
  closeCouponPicker() { this.setData({ couponPickerVisible: false }) },
  stopTouch() {},
  chooseCoupon(event) {
    this.applyAuthState(getAuthState())
    this.refreshCouponPreview()
    const id = Number(event.currentTarget.dataset.id)
    if (!this.data.couponRows.some((item) => item.coupon_id === id && item.eligible)) return
    this._couponChoice = id
    this.setData({ couponPickerVisible: false })
    this.refreshCouponPreview()
  },
  chooseNoCoupon() {
    this._couponChoice = null
    this.setData({ couponPickerVisible: false })
    this.refreshCouponPreview()
  },

  checkout() {
    this.applyAuthState(getAuthState())
    this.refreshCart()
    if (!this.data.selectedSummary.totalQuantity || this.data.errorMessage) return
    const coupon = this.data.chosenCouponId ? `?coupon_id=${this.data.chosenCouponId}` : ''
    wx.navigateTo({ url: `/pages/checkout/checkout${coupon}` })
  },

  async loadRecommendations() {
    const origin = getApiBaseUrl()
    if (this._recommendationPending && origin === this._recommendationOrigin) return
    this._recommendationPending = true
    this._recommendationOrigin = origin
    this._recommendationsLoaded = false
    const sequence = ++this._recommendationSequence
    this.setData({ recommendations: [], recommendationsLoading: true, recommendationsErrorMessage: '' })
    try {
      const result = await fetchProducts({ page: 1, page_size: 4 })
      if (!this._alive || !this._visible || sequence !== this._recommendationSequence || origin !== getApiBaseUrl()) return
      const seen = new Set()
      const products = result.items.filter((product) => {
        if (product.status !== 'published' || product.category?.is_active !== true ||
            typeof product.product_code !== 'string' || !product.product_code ||
            !Number.isSafeInteger(product.base_price_cents) || product.base_price_cents < 0 || seen.has(product.product_code)) return false
        seen.add(product.product_code)
        return true
      }).slice(0, 4)
      this.setData({ recommendations: products.map(recommendationView) })
      this._recommendationsLoaded = true
    } catch (error) {
      if (this._alive && this._visible && sequence === this._recommendationSequence && origin === getApiBaseUrl()) {
        this.setData({ recommendations: [], recommendationsErrorMessage: messageFor(error, '推荐商品暂时无法加载。') })
      }
    } finally {
      if (this._alive && sequence === this._recommendationSequence) {
        this._recommendationPending = false
        this.setData({ recommendationsLoading: false })
      }
    }
  },

  openProduct(event) {
    const code = event.detail.productCode
    if (!this.data.recommendations.some((item) => item.productCode === code)) return
    wx.navigateTo({ url: `/pages/product/product?code=${encodeURIComponent(code)}` })
  },
})
