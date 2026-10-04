const { fetchHome } = require('../../api/shop')
const { claimCoupon, fetchMyCoupons } = require('../../api/customer-shop')
const { absoluteMediaUrl, getApiBaseUrl } = require('../../api/client')
const { formatCents } = require('../../utils/money')
const { getAuthState, subscribe, shouldPromptForLogin } = require('../../state/auth-store')
const { selectCatalogCategory } = require('../../state/catalog-navigation')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function productView(record) {
  const product = record.product
  const images = Array.isArray(product.images) ? product.images : []
  const cover = images.find((image) => image.image_type === 'cover') || images[0]
  const skus = Array.isArray(product.skus) ? product.skus.filter((sku) => sku.is_active !== false) : []
  const hasStock = skus.some((sku) => Number(sku.stock_quantity) > 0)
  const available = product.stock_status !== 'out_of_stock' && hasStock
  return {
    productCode: product.product_code,
    name: product.name,
    subtitle: product.subtitle || '',
    categoryName: product.category && product.category.name ? product.category.name : '商品',
    featured: Boolean(product.featured),
    coverUrl: cover ? absoluteMediaUrl(cover.url || cover.media_url || '') : '',
    displayPrice: formatCents(Number(product.base_price_cents) || 0),
    available,
    stockLabel: available ? (product.stock_status === 'preorder' ? '可预订' : '可选购') : (skus.length ? '暂时售罄' : '暂不可售'),
    soldQuantity: Number(record.sold_quantity) || 0,
    repeatPurchaseCount: Number(record.repeat_purchase_count) || 0,
  }
}

function storeView(store) {
  const source = store || {}
  return {
    name: typeof source.name === 'string' && source.name.trim() ? source.name.trim() : '港湾集市',
    phone: typeof source.phone === 'string' ? source.phone.trim() : '',
    address: typeof source.address === 'string' ? source.address.trim() : '',
    announcementImageUrl: absoluteMediaUrl(source.announcement_image_url || ''),
    announcementUnavailable: false,
    latitude: source.latitude,
    longitude: source.longitude,
    hasCoordinates: Number.isFinite(source.latitude) && Math.abs(source.latitude) <= 90 &&
      Number.isFinite(source.longitude) && Math.abs(source.longitude) <= 180,
  }
}

function expiryText(value) {
  const time = Date.parse(value)
  if (!Number.isFinite(time)) return ''
  // Store coupon dates are presented in China Standard Time on every device.
  const date = new Date(time + 8 * 60 * 60 * 1000)
  return '有效期至 ' + date.getUTCFullYear() + '.' + String(date.getUTCMonth() + 1).padStart(2, '0') + '.' + String(date.getUTCDate()).padStart(2, '0')
}

Page({
  data: {
    store: storeView(null),
    carousel: [],
    carouselIndex: 0,
    videoPlaying: false,
    coupons: [],
    categories: [],
    hotProducts: [],
    hotProductsSource: 'newest',
    hotProductsTitle: '新上架好物',
    hotProductsIntro: '看看最近上架的商品。',
    loading: true,
    hasLoaded: false,
    errorMessage: '',
    couponErrorMessage: '',
    authStatus: 'guest',
    authBusy: false,
    customerName: '',
    avatarPath: '',
    loginVisible: false,
    loginMode: 'login',
  },

  onLoad() {
    this._alive = true
    this._requestSequence = 0
    this._claimsGeneration = 0
    this._claimsFetchSequence = 0
    this._claimedCouponIds = new Set()
    this._claimingCouponIds = new Set()
    this._couponRecords = []
    this._claimIdentityKey = ''
    this._unsubscribeAuth = subscribe((state) => this.applyAuthState(state))
    this.applyAuthState(getAuthState())
    this.loadInitialData()
  },

  onShow() {
    this._isVisible = true
    this.applyAuthState(getAuthState())
    if (this._loadOrigin && this._loadOrigin !== getApiBaseUrl()) this.loadInitialData()
  },

  onHide() {
    this._isVisible = false
    this.stopCurrentVideo()
  },

  onUnload() {
    this._alive = false
    this._isVisible = false
    this._requestSequence += 1
    this._claimsGeneration += 1
    this.stopCurrentVideo()
    if (this._unsubscribeAuth) this._unsubscribeAuth()
  },

  applyAuthState(state) {
    if (!this._alive) return
    this.setData({
      authStatus: state.status,
      authBusy: state.busy || state.status === 'restoring',
      customerName: state.customer ? state.customer.nickname || '微信用户' : '',
      avatarPath: state.avatarPath || '',
    })
    const identityKey = state.status === 'authenticated' && state.customer
      ? getApiBaseUrl() + '|' + state.customer.id : ''
    if (identityKey !== this._claimIdentityKey) {
      this._claimIdentityKey = identityKey
      this._claimsGeneration += 1
      this._claimedCouponIds.clear()
      this._claimingCouponIds.clear()
      this.setData({ couponErrorMessage: '' })
      this.syncCouponViews()
      if (identityKey && this._couponRecords.length) this.loadClaimedCoupons()
    }
    if (this._isVisible && state.status === 'guest' && !state.busy &&
      !this.data.loginVisible && shouldPromptForLogin()) {
      this.openLoginDialog()
    }
  },

  openLoginDialog() {
    this.stopCurrentVideo()
    this.setData({ loginVisible: true, loginMode: 'login' })
  },

  openAccountDialog() {
    const state = getAuthState()
    if (state.busy || state.status === 'restoring') return
    this.stopCurrentVideo()
    this.setData({ loginVisible: true, loginMode: state.status === 'authenticated' ? 'profile' : 'login' })
  },

  closeLogin() {
    this.setData({ loginVisible: false })
  },

  onLoginSuccess(event) {
    this.setData({ loginVisible: false })
    this.applyAuthState(getAuthState())
    const { mode, warningMessage } = event.detail
    if (warningMessage) {
      wx.showModal({
        title: mode === 'profile' ? '部分资料尚未保存' : '已登录，资料尚未保存',
        content: warningMessage,
        showCancel: false,
        confirmText: '知道了',
      })
    } else {
      wx.showToast({ title: mode === 'profile' ? '资料已保存' : '微信登录成功', icon: 'success' })
    }
  },

  onAvatarError() {
    this.setData({ avatarPath: '' })
  },

  async onPullDownRefresh() {
    await this.loadInitialData()
    wx.stopPullDownRefresh()
  },

  async loadInitialData() {
    const sequence = ++this._requestSequence
    const origin = getApiBaseUrl()
    this._loadOrigin = origin
    this.stopCurrentVideo()
    this.setData({ loading: true, errorMessage: '', couponErrorMessage: '' })
    try {
      const result = await fetchHome()
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl()) return
      this._couponRecords = Array.isArray(result.coupons) ? result.coupons : []
      const source = ['sales', 'featured', 'newest'].includes(result.hot_products_source)
        ? result.hot_products_source : 'newest'
      const titles = { sales: '热销商品', featured: '店主推荐', newest: '新上架好物' }
      const intros = { sales: '按实际成交销量排序。', featured: '精选店内好物。', newest: '看看最近上架的商品。' }
      this.setData({
        store: storeView(result.store),
        carousel: (result.carousel || []).map((item) => ({ ...item, url: absoluteMediaUrl(item.url || '') })),
        carouselIndex: 0,
        videoPlaying: false,
        categories: (result.categories || []).map((item) => ({
          ...item, imageUrl: absoluteMediaUrl(item.image_url || ''), imageLabel: item.name ? item.name.slice(0, 1) : '品',
        })),
        hotProducts: (result.hot_products || []).map(productView),
        hotProductsSource: source,
        hotProductsTitle: titles[source],
        hotProductsIntro: intros[source],
        loading: false,
        hasLoaded: true,
      })
      this.syncCouponViews()
      if (this._claimIdentityKey) this.loadClaimedCoupons()
    } catch (error) {
      if (!this._alive || sequence !== this._requestSequence || origin !== getApiBaseUrl()) return
      this.setData({ loading: false, errorMessage: messageFor(error, '首页暂时无法加载，请稍后重试。') })
    }
  },

  retry() {
    this.loadInitialData()
  },

  syncCouponViews() {
    if (!this._alive) return
    this.setData({
      coupons: this._couponRecords.map((coupon) => ({
        ...coupon,
        displayDiscount: formatCents(Number(coupon.discount_cents) || 0).replace('¥', ''),
        thresholdText: '满 ' + formatCents(Number(coupon.min_spend_cents) || 0).replace('¥', '') + ' 元可用',
        validityText: expiryText(coupon.expires_at),
        claimed: this._claimedCouponIds.has(String(coupon.id)),
        claiming: this._claimingCouponIds.has(String(coupon.id)),
      })),
    })
  },

  claimsAreCurrent(generation, identityKey, origin) {
    return this._alive && generation === this._claimsGeneration &&
      identityKey === this._claimIdentityKey && origin === getApiBaseUrl()
  },

  async loadClaimedCoupons() {
    const sequence = ++this._claimsFetchSequence
    const generation = this._claimsGeneration
    const identityKey = this._claimIdentityKey
    const origin = getApiBaseUrl()
    if (!identityKey || !this._couponRecords.length) return
    try {
      const visibleIds = this._couponRecords.map((coupon) => String(coupon.id))
      for (let page = 1; ; page += 1) {
        const records = await fetchMyCoupons({ page, page_size: 100 })
        if (sequence !== this._claimsFetchSequence || !this.claimsAreCurrent(generation, identityKey, origin)) return
        for (const record of records) {
          if (typeof record.claimed_at === 'string' && Number.isFinite(Date.parse(record.claimed_at))) {
            this._claimedCouponIds.add(String(record.coupon_id))
          }
        }
        this.syncCouponViews()
        if (records.length < 100 || visibleIds.every((id) => this._claimedCouponIds.has(id))) return
      }
    } catch (error) {
      if (sequence === this._claimsFetchSequence && this.claimsAreCurrent(generation, identityKey, origin)) {
        this.setData({ couponErrorMessage: messageFor(error, '已领取记录暂时无法加载，请稍后重试。') })
      }
    }
  },

  async claimCoupon(event) {
    const id = String(event.currentTarget.dataset.id)
    const coupon = this._couponRecords.find((item) => String(item.id) === id)
    if (!coupon || this._claimedCouponIds.has(id) || this._claimingCouponIds.has(id)) return
    const auth = getAuthState()
    if (auth.busy || auth.status === 'restoring') return
    if (auth.status !== 'authenticated') {
      this.openLoginDialog()
      return
    }
    const generation = this._claimsGeneration
    const identityKey = this._claimIdentityKey
    const origin = getApiBaseUrl()
    this._claimingCouponIds.add(id)
    this.setData({ couponErrorMessage: '' })
    this.syncCouponViews()
    try {
      const record = await claimCoupon(coupon.id)
      if (!this.claimsAreCurrent(generation, identityKey, origin)) return
      if (!record || String(record.coupon_id) !== id) throw new Error('领取结果暂时无法确认，请刷新后重试。')
      this._claimedCouponIds.add(id)
      wx.showToast({ title: '领取成功', icon: 'success' })
    } catch (error) {
      if (this.claimsAreCurrent(generation, identityKey, origin)) {
        this.setData({ couponErrorMessage: messageFor(error, '优惠券领取失败，请稍后重试。') })
      }
    } finally {
      if (this.claimsAreCurrent(generation, identityKey, origin)) {
        this._claimingCouponIds.delete(id)
        this.syncCouponViews()
      }
    }
  },

  openSearch() {
    wx.navigateTo({ url: '/pages/search/search' })
  },

  openCatalog() {
    selectCatalogCategory('')
    wx.switchTab({ url: '/pages/catalog/catalog' })
  },

  selectCategory(event) {
    selectCatalogCategory(event.currentTarget.dataset.code || '')
    wx.switchTab({ url: '/pages/catalog/catalog' })
  },

  openCouponCenter() {
    wx.navigateTo({ url: '/pages/coupons/coupons' })
  },

  openProduct(event) {
    const productCode = event.detail.productCode
    if (productCode) wx.navigateTo({ url: '/pages/product/product?code=' + encodeURIComponent(productCode) })
  },

  onCarouselChange(event) {
    this.stopCurrentVideo()
    this.setData({ carouselIndex: Number(event.detail.current) || 0, videoPlaying: false })
  },

  onVideoPlay(event) {
    if (Number(event.currentTarget.dataset.index) !== this.data.carouselIndex) return
    if (this.data.loginVisible) this.stopCurrentVideo()
    else this.setData({ videoPlaying: true })
  },

  onVideoPause(event) {
    if (Number(event.currentTarget.dataset.index) === this.data.carouselIndex) this.setData({ videoPlaying: false })
  },

  stopCurrentVideo() {
    const item = this.data.carousel[this.data.carouselIndex]
    if (item && item.media_type === 'video' && typeof wx.createVideoContext === 'function') {
      wx.createVideoContext('home-promo-' + item.id, this).pause()
    }
    if (this._alive && this.data.videoPlaying) this.setData({ videoPlaying: false })
  },

  onCarouselMediaError(event) {
    const index = Number(event.currentTarget.dataset.index)
    this.setData({
      carousel: this.data.carousel.map((item, itemIndex) => itemIndex === index ? { ...item, unavailable: true } : item),
      videoPlaying: index === this.data.carouselIndex ? false : this.data.videoPlaying,
    })
  },

  onCategoryImageError(event) {
    const code = event.currentTarget.dataset.code
    this.setData({ categories: this.data.categories.map((item) => item.code === code ? { ...item, imageUrl: '' } : item) })
  },

  onAnnouncementImageError() {
    this.setData({ store: { ...this.data.store, announcementImageUrl: '', announcementUnavailable: true } })
  },

  previewAnnouncement() {
    const url = this.data.store.announcementImageUrl
    if (url) wx.previewImage({ current: url, urls: [url] })
  },

  callStore() {
    const phone = this.data.store.phone
    if (!phone) return
    wx.makePhoneCall({
      phoneNumber: phone,
      fail(error) {
        if (error && /cancel/i.test(error.errMsg || '')) return
        wx.showToast({ title: '电话暂时无法拨打，请稍后重试。', icon: 'none' })
      },
    })
  },

  navigateToStore() {
    const store = this.data.store
    if (!store.hasCoordinates) {
      this.copyStoreAddress()
      return
    }
    wx.openLocation({
      latitude: store.latitude,
      longitude: store.longitude,
      name: store.name,
      address: store.address,
      scale: 16,
      fail: () => {
        if (!store.address) {
          wx.showToast({ title: '地图暂时无法打开，请稍后重试。', icon: 'none' })
          return
        }
        wx.showModal({
          title: '地图暂时无法打开',
          content: '可复制商家地址后，在地图中查找。',
          confirmText: '复制地址',
          cancelText: '取消',
          success: (result) => { if (result.confirm) this.copyStoreAddress() },
        })
      },
    })
  },

  copyStoreAddress() {
    const address = this.data.store.address
    if (!address) return
    wx.setClipboardData({
      data: address,
      success: () => wx.showToast({ title: '地址已复制', icon: 'success' }),
      fail: () => wx.showToast({ title: '地址复制失败，请重试。', icon: 'none' }),
    })
  },
})
