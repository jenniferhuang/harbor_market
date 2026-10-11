const { fetchAvailableCoupons } = require('../../api/cart')
const { claimCoupon, fetchMyCoupons } = require('../../api/customer-shop')
const { getApiBaseUrl } = require('../../api/client')
const { evaluateCoupons } = require('../../domain/coupons')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { readSession } = require('../../state/customer-session')
const cartStore = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')
const { formatChinaDate } = require('../../utils/date')

const PAGE_SIZE = 20

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function identityKey(state) {
  const session = readSession()
  return state.status === 'authenticated' && session && state.customer
    ? `${session.apiOrigin}|${state.customer.id}|${session.accessToken}` : ''
}

function selectedTotal() {
  const cart = cartStore.loadCart()
  const summary = typeof cartStore.selectedSummary === 'function'
    ? cartStore.selectedSummary(cart) : cartStore.cartSummary(cart)
  return summary.totalCents
}

Page({
  data: {
    coupons: [], loading: false, hasLoaded: false, page: 0, hasMore: false,
    errorMessage: '', claimsErrorMessage: '', claimsLoading: false,
    authStatus: 'guest', authBusy: false, loginVisible: false,
    cartContext: false, subtotalLabel: '',
  },

  onLoad(options = {}) {
    this._alive = true
    this._visible = false
    this._publicSequence = 0
    this._claimsGeneration = 0
    this._claimsFetchSequence = 0
    this._identityKey = ''
    this._offers = []
    this._owned = new Map()
    this._claiming = new Set()
    this._subtotalCents = 0
    this._cartAmountValid = true
    this.setData({ cartContext: options.from === 'cart' })
    this._unsubscribe = subscribe((state) => this.applyAuthState(state))
  },

  onShow() {
    this._visible = true
    try {
      this._subtotalCents = selectedTotal()
      this._cartAmountValid = true
      this.setData({ subtotalLabel: formatCents(this._subtotalCents) })
    } catch {
      this._subtotalCents = 0
      this._cartAmountValid = false
      this.setData({ subtotalLabel: '金额暂时无法计算' })
    }
    this.applyAuthState(getAuthState())
    this.loadOffers()
    if (this._identityKey && !this.data.claimsLoading) this.loadOwned()
  },

  onHide() {
    this._visible = false
    this._publicSequence += 1
    this._claimsGeneration += 1
    this._claimsFetchSequence += 1
    this._claiming.clear()
    this._owned.clear()
    this.setData({ loading: false, claimsLoading: false, loginVisible: false, claimsErrorMessage: '' })
    this.syncViews()
  },

  onUnload() {
    this._alive = false
    this._visible = false
    this._publicSequence += 1
    this._claimsGeneration += 1
    this._claimsFetchSequence += 1
    this._owned.clear()
    this._claiming.clear()
    if (this._unsubscribe) this._unsubscribe()
  },

  applyAuthState(state) {
    if (!this._alive) return
    const nextIdentity = identityKey(state)
    this.setData({ authStatus: state.status, authBusy: Boolean(state.busy || state.status === 'restoring') })
    if (nextIdentity === this._identityKey) return
    this._identityKey = nextIdentity
    this._claimsGeneration += 1
    this._claimsFetchSequence += 1
    this._owned.clear()
    this._claiming.clear()
    this.setData({ claimsErrorMessage: '', claimsLoading: false })
    this.syncViews()
    if (nextIdentity && this._visible) this.loadOwned()
  },

  publicIsCurrent(sequence, origin) {
    return this._alive && this._visible && sequence === this._publicSequence && origin === getApiBaseUrl()
  },

  privateIsCurrent(generation, identity, origin) {
    return this._alive && this._visible && generation === this._claimsGeneration &&
      identity === this._identityKey && identity === identityKey(getAuthState()) && origin === getApiBaseUrl()
  },

  async loadOffers(append = false) {
    append = append === true
    if (append && (this.data.loading || !this.data.hasMore)) return
    const page = append ? this.data.page + 1 : 1
    const sequence = ++this._publicSequence
    const origin = getApiBaseUrl()
    this._offerOrigin = origin
    if (!append) {
      this._offers = []
      this.setData({ page: 0, hasMore: false, hasLoaded: false })
      this.syncViews()
    }
    this.setData({ loading: true, errorMessage: '' })
    try {
      const rows = await fetchAvailableCoupons({ page, page_size: PAGE_SIZE })
      if (!this.publicIsCurrent(sequence, origin)) return
      const byId = new Map((append ? this._offers : []).map((item) => [item.id, item]))
      rows.forEach((item) => byId.set(item.id, item))
      this._offers = [...byId.values()]
      this.setData({ loading: false, hasLoaded: true, page, hasMore: rows.length === PAGE_SIZE })
      this.syncViews()
    } catch (error) {
      if (this.publicIsCurrent(sequence, origin)) {
        this.setData({ loading: false, errorMessage: messageFor(error, '优惠券暂时无法加载，请稍后重试。') })
      }
    }
  },

  async loadOwned() {
    if (!this._identityKey || !this._visible) return
    const sequence = ++this._claimsFetchSequence
    const generation = this._claimsGeneration
    const identity = this._identityKey
    const origin = getApiBaseUrl()
    this.setData({ claimsLoading: true, claimsErrorMessage: '' })
    try {
      const fetchedIds = new Set()
      for (let page = 1; page <= 100; page += 1) {
        const rows = await fetchMyCoupons({ page, page_size: 100 })
        if (sequence !== this._claimsFetchSequence || !this.privateIsCurrent(generation, identity, origin)) return
        const previousCount = fetchedIds.size
        rows.forEach((row) => { fetchedIds.add(row.coupon_id); this._owned.set(row.coupon_id, row) })
        this.syncViews()
        if (rows.length < 100) break
        if (fetchedIds.size === previousCount || page === 100) {
          throw new Error('已领取记录未完整确认，请重试；再次领取不会重复领取。')
        }
      }
      if (sequence === this._claimsFetchSequence && this.privateIsCurrent(generation, identity, origin)) {
        this.setData({ claimsLoading: false })
      }
    } catch (error) {
      if (sequence === this._claimsFetchSequence && this.privateIsCurrent(generation, identity, origin)) {
        this.setData({ claimsLoading: false, claimsErrorMessage: messageFor(error, '领取记录暂时无法加载，请稍后重试。') })
      }
    }
  },

  syncViews() {
    if (!this._alive) return
    const records = this._offers.map((offer) => ({ ...offer, claimed_at: this._owned.get(offer.id)?.claimed_at }))
    const evaluations = evaluateCoupons(records, this._subtotalCents)
    this.setData({
      coupons: evaluations.map((item) => {
        const claimed = this._owned.has(item.id)
        let contextText = ''
        if (this.data.cartContext && this._cartAmountValid) {
          contextText = item.shortfallCents > 0
            ? `再选 ${formatCents(item.shortfallCents)} 可满减`
            : claimed ? `本次可减 ${formatCents(item.appliedDiscountCents)}` : '已达到使用门槛，领取后可用'
        }
        const unavailable = ['invalid', 'inactive', 'not_started', 'expired'].includes(item.reason)
        if (unavailable) contextText = item.reason === 'expired' ? '已过期' : '当前不可领取'
        return {
          id: item.id, title: item.title,
          discountLabel: formatCents(item.discount_cents).replace('¥', ''),
          thresholdLabel: item.min_spend_cents === 0 ? '无门槛使用' : `满 ${formatCents(item.min_spend_cents)} 使用`,
          expiryLabel: item.expires_at ? `有效期至 ${formatChinaDate(item.expires_at)}` : '长期有效',
          contextText, claimed, claiming: this._claiming.has(item.id), unavailable,
        }
      }),
    })
  },

  async claim(event) {
    this.applyAuthState(getAuthState())
    const id = Number(event.currentTarget.dataset.id)
    const offer = this._offers.find((item) => item.id === id)
    if (!this._alive || !this._visible || !offer || this._offerOrigin !== getApiBaseUrl() ||
        this._owned.has(id) || this._claiming.has(id) || this.data.authBusy || this.data.claimsLoading) return
    const evaluation = evaluateCoupons([offer], this._subtotalCents)[0]
    if (evaluation.reason !== 'unclaimed') {
      wx.showToast({ title: '该优惠券当前不可领取', icon: 'none' })
      this.loadOffers()
      return
    }
    if (getAuthState().status !== 'authenticated') {
      this.setData({ loginVisible: true })
      return
    }
    const generation = this._claimsGeneration
    const identity = this._identityKey
    const origin = getApiBaseUrl()
    this._claiming.add(id)
    this.setData({ claimsErrorMessage: '' })
    this.syncViews()
    try {
      const row = await claimCoupon(id)
      if (!this.privateIsCurrent(generation, identity, origin)) return
      if (row.coupon_id !== id) throw new Error('领取结果暂时无法确认，请刷新后重试。')
      this._owned.set(id, row)
      wx.showToast({ title: '领取成功', icon: 'success' })
    } catch (error) {
      if (this.privateIsCurrent(generation, identity, origin)) {
        this.setData({ claimsErrorMessage: messageFor(error, '优惠券领取失败，请稍后重试。') })
      }
    } finally {
      if (this.privateIsCurrent(generation, identity, origin)) {
        this._claiming.delete(id)
        this.syncViews()
      }
    }
  },

  loadMore() { return this.loadOffers(true) },
  retry() { return this.loadOffers() },
  retryClaims() { return this.loadOwned() },
  closeLogin() { this.setData({ loginVisible: false }) },
  onLoginSuccess() {
    this.setData({ loginVisible: false })
    this.applyAuthState(getAuthState())
  },
  openWallet() {
    if (getAuthState().status !== 'authenticated') this.setData({ loginVisible: true })
    else wx.navigateTo({ url: '/pages/coupons/coupons' })
  },
})
