const { absoluteMediaUrl } = require('../../api/client')
const { loadCart, updateQuantity, clearCart, cartSummary, selectedSummary, setItemSelected, maxQuantityForItem } = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && /[\u3400-\u9fff]/.test(error.message)
    ? error.message : fallback
}

function itemView(item, cart) {
  return {
    ...item,
    coverUrl: absoluteMediaUrl(item.coverUrl),
    displayPrice: formatCents(item.unitPriceCents),
    maxQuantity: maxQuantityForItem(cart, item.key),
  }
}

Component({
  properties: {
    insetBottom: { type: Number, value: 0 },
  },
  data: {
    items: [],
    totalQuantity: 0,
    selectedQuantity: 0,
    displayTotal: formatCents(0),
    expanded: false,
    errorMessage: '',
  },
  lifetimes: {
    attached() {
      this._alive = true
      this.refresh()
    },
    detached() {
      this._alive = false
    },
  },
  pageLifetimes: {
    show() { this.refresh() },
  },
  methods: {
    refresh() {
      try {
        this.applyCart(loadCart())
      } catch (error) {
        this.setData({ errorMessage: messageFor(error, '购物车暂时无法读取，请稍后重试。') })
      }
    },
    applyCart(cart) {
      const summary = cartSummary(cart)
      const selected = selectedSummary(cart)
      this.setData({
        items: cart.items.map((item) => itemView(item, cart)),
        totalQuantity: summary.totalQuantity,
        selectedQuantity: selected.totalQuantity,
        displayTotal: formatCents(selected.totalCents),
        errorMessage: '',
      })
    },
    toggle() {
      this.refresh()
      this.setData({ expanded: !this.data.expanded })
    },
    close() { this.setData({ expanded: false }) },
    stopTouch() {},
    changeQuantity(event) {
      try {
        // Read current storage so another page's additions are never overwritten.
        this.applyCart(updateQuantity(loadCart(), event.currentTarget.dataset.key, event.detail.value))
        this.triggerEvent('change', { totalQuantity: this.data.totalQuantity })
      } catch (error) {
        this.showError('数量更新失败', error)
      }
    },
    changeSelection(event) {
      try {
        const cart = loadCart()
        const key = event.currentTarget.dataset.key
        const item = cart.items.find((candidate) => candidate.key === key)
        if (!item) return
        this.applyCart(setItemSelected(cart, key, !item.selected))
        this.triggerEvent('change', { totalQuantity: this.data.totalQuantity })
      } catch (error) {
        this.showError('选择更新失败', error)
      }
    },
    clearAll() {
      if (!this.data.items.length || this._clearing) return
      this._clearing = true
      wx.showModal({
        title: '清空购物车',
        content: '确定移除购物车中的全部商品吗？',
        confirmText: '清空',
        confirmColor: '#a3342a',
        success: (result) => {
          if (!this._alive || !result.confirm) return
          try {
            this.applyCart(clearCart())
            this.triggerEvent('change', { totalQuantity: 0 })
          } catch (error) {
            this.showError('清空失败', error)
          }
        },
        complete: () => { this._clearing = false },
      })
    },
    showError(title, error) {
      wx.showModal({ title, content: messageFor(error, '请检查商品数量后重试。'), showCancel: false })
    },
    checkout() {
      this.refresh()
      if (!this.data.selectedQuantity || this.data.errorMessage) return
      wx.navigateTo({ url: '/pages/checkout/checkout' })
    },
  },
})
