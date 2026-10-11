'use strict'

const { quoteCart } = require('../../api/cart')
const { getApiBaseUrl } = require('../../api/client')
const { getAuthState, subscribe } = require('../../state/auth-store')
const { loadCart, selectedItems } = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')

function fingerprint(items) {
  return JSON.stringify(items.map((item) => ({ key: item.key, quantity: item.quantity })))
}

function requestLines(items) {
  return items.map((item) => ({
    product_code: item.productCode,
    sku_code: item.skuCode,
    selections: item.selections,
    quantity: item.quantity,
  }))
}

function couponView(row) {
  return {
    ...row,
    discountLabel: formatCents(row.discount_cents),
    thresholdLabel: formatCents(row.min_spend_cents),
    statusLabel: row.eligible ? `可减 ${formatCents(row.discount_cents)}` :
      row.reason === 'below_minimum' ? `再选 ${formatCents(row.shortfall_cents)} 可用` :
        row.reason === 'expired' ? '已过期' : row.reason === 'not_started' ? '尚未开始' : '暂不可用',
  }
}

Page({
  data: {
    loggedIn: false, loginVisible: false, loading: false, quote: null, items: [], coupons: [],
    couponId: null, displaySubtotal: '¥0.00', displayDiscount: '¥0.00', displayTotal: '¥0.00',
    errorMessage: '', empty: true, priceChanged: false,
  },
  onLoad(options = {}) {
    this._alive = true
    this._sequence = 0
    this._origin = getApiBaseUrl()
    const couponId = Number(options.coupon_id)
    this._requestedCouponId = Number.isSafeInteger(couponId) && couponId > 0 ? couponId : null
    this._unsubscribe = subscribe((state) => {
      const id = state.status === 'authenticated' ? state.customer.id : null
      if (id === this._customerId) return
      this._customerId = id
      ++this._sequence
      this.setData({ loggedIn: Boolean(id), quote: null, items: [], coupons: [], loading: false,
        couponId: null, displaySubtotal: '¥0.00', displayDiscount: '¥0.00', displayTotal: '¥0.00',
        errorMessage: '', priceChanged: false })
      if (!id) this._requestedCouponId = null
      if (id && this._visible) this.refreshQuote()
    })
  },
  onShow() {
    this._visible = true
    if (this._origin !== getApiBaseUrl()) {
      ++this._sequence
      this._origin = getApiBaseUrl()
      this._requestedCouponId = null
      this.setData({ quote: null, coupons: [], items: [], loading: false, couponId: null,
        displaySubtotal: '¥0.00', displayDiscount: '¥0.00', displayTotal: '¥0.00', errorMessage: '', priceChanged: false })
    }
    const items = selectedItems(loadCart())
    this.setData({ empty: items.length === 0 })
    if (!items.length) {
      ++this._sequence
      this.setData({ quote: null, items: [], coupons: [], loading: false, couponId: null,
        displaySubtotal: '¥0.00', displayDiscount: '¥0.00', displayTotal: '¥0.00', errorMessage: '', priceChanged: false })
    }
    if (getAuthState().status === 'authenticated' && items.length) this.refreshQuote()
  },
  onHide() { this._visible = false; ++this._sequence; this.setData({ loading: false }) },
  onUnload() { this._alive = false; ++this._sequence; if (this._unsubscribe) this._unsubscribe() },
  async refreshQuote() {
    if (getAuthState().status !== 'authenticated') { this.setData({ loginVisible: true }); return }
    const items = selectedItems(loadCart())
    const cartFingerprint = fingerprint(items)
    const sequence = ++this._sequence
    const origin = getApiBaseUrl()
    const customerId = this._customerId
    this.setData({ loading: Boolean(items.length), empty: items.length === 0, quote: null, items: [], coupons: [],
      errorMessage: '', priceChanged: false, couponId: this._requestedCouponId, displaySubtotal: '¥0.00', displayDiscount: '¥0.00', displayTotal: '¥0.00' })
    if (!items.length) return
    try {
      const quote = await quoteCart({ items: requestLines(items), coupon_id: this._requestedCouponId })
      if (!this._alive || !this._visible || sequence !== this._sequence || origin !== getApiBaseUrl() || customerId !== this._customerId) return
      if (fingerprint(selectedItems(loadCart())) !== cartFingerprint) {
        this.setData({ loading: false, errorMessage: '购物车已改变，请重新核对金额。' })
        return
      }
      this.setData({ quote, loading: false, couponId: quote.coupon_id,
        items: quote.items.map((row, index) => ({ ...row, key: items[index].key,
          selectionSummary: items[index].selectionSummary, unitPrice: formatCents(row.unit_price_cents), lineTotal: formatCents(row.line_total_cents) })),
        coupons: quote.coupon_evaluations.map(couponView), displaySubtotal: formatCents(quote.subtotal_cents),
        displayDiscount: formatCents(quote.discount_cents), displayTotal: formatCents(quote.total_cents),
        priceChanged: quote.items.some((row, index) => row.unit_price_cents !== items[index].unitPriceCents) })
    } catch (error) {
      if (this._alive && this._visible && sequence === this._sequence && origin === getApiBaseUrl() && customerId === this._customerId) {
        this.setData({ loading: false, errorMessage: error.message || '金额暂时无法确认，请重试。' })
      }
    }
  },
  chooseCoupon(event) {
    const id = Number(event.currentTarget.dataset.id)
    const row = this.data.coupons.find((coupon) => coupon.coupon_id === id)
    if (id > 0 && (!row || !row.eligible)) return
    this._requestedCouponId = id > 0 ? id : null
    this.refreshQuote()
  },
  withoutCoupon() { this._requestedCouponId = null; this.refreshQuote() },
  login() { this.setData({ loginVisible: true }) },
  closeLogin() { this.setData({ loginVisible: false }) },
  loginSucceeded() { this.setData({ loginVisible: false }); this.refreshQuote() },
  openCouponCenter() { wx.navigateTo({ url: '/pages/coupon-center/coupon-center?from=cart' }) },
  backToCart() { wx.switchTab({ url: '/pages/cart/cart' }) },
})
