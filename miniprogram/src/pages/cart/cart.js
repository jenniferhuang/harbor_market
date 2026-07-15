const { absoluteMediaUrl } = require('../../api/client')
const {
  loadCart,
  updateQuantity,
  removeItem,
  clearCart,
  cartSummary,
} = require('../../state/cart-store')
const { formatCents } = require('../../utils/money')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

function cartItemView(item) {
  const stockQuantity = Number.isInteger(item.stockQuantity) ? item.stockQuantity : 999
  return {
    ...item,
    coverUrl: absoluteMediaUrl(item.coverUrl || ''),
    displayUnitPrice: formatCents(item.unitPriceCents),
    displayLineTotal: formatCents(item.unitPriceCents * item.quantity),
    maxQuantity: Math.max(1, Math.min(99, stockQuantity)),
    stockLabel: stockQuantity >= 999 ? '' : `库存记录：${stockQuantity}`,
  }
}

Page({
  data: {
    cart: { version: 1, items: [] },
    items: [],
    summary: {
      lineCount: 0,
      totalQuantity: 0,
      totalCents: 0,
    },
    displayTotal: formatCents(0),
    errorMessage: '',
  },

  onShow() {
    this.refreshCart()
  },

  refreshCart() {
    try {
      const cart = loadCart()
      const summary = cartSummary(cart)
      this.setData({
        cart,
        items: cart.items.map(cartItemView),
        summary,
        displayTotal: formatCents(summary.totalCents),
        errorMessage: '',
      })
    } catch (error) {
      this.setData({
        errorMessage: messageFor(error, '购物车暂时无法读取，请稍后重试。'),
      })
    }
  },

  changeQuantity(event) {
    const itemKey = event.currentTarget.dataset.key
    try {
      const cart = updateQuantity(this.data.cart, itemKey, event.detail.value)
      this.applyCart(cart)
    } catch (error) {
      wx.showModal({
        title: '数量更新失败',
        content: messageFor(error, '请稍后重试。'),
        showCancel: false,
      })
    }
  },

  removeLine(event) {
    const itemKey = event.currentTarget.dataset.key
    const item = this.data.items.find((candidate) => candidate.key === itemKey)
    wx.showModal({
      title: '移除商品',
      content: `确定从购物车移除“${item ? item.productName : '该商品'}”吗？`,
      confirmText: '移除',
      confirmColor: '#a3342a',
      success: (result) => {
        if (!result.confirm) return
        try {
          this.applyCart(removeItem(this.data.cart, itemKey))
        } catch (error) {
          wx.showModal({
            title: '移除失败',
            content: messageFor(error, '请稍后重试。'),
            showCancel: false,
          })
        }
      },
    })
  },

  clearAll() {
    wx.showModal({
      title: '清空购物车',
      content: '确定移除购物车中的全部商品吗？',
      confirmText: '清空',
      confirmColor: '#a3342a',
      success: (result) => {
        if (!result.confirm) return
        try {
          this.applyCart(clearCart())
        } catch (error) {
          wx.showModal({
            title: '清空失败',
            content: messageFor(error, '请稍后重试。'),
            showCancel: false,
          })
        }
      },
    })
  },

  continueShopping() {
    wx.switchTab({ url: '/pages/home/home' })
  },

  explainCheckout() {
    wx.showModal({
      title: '订单结算尚未开放',
      content:
        '当前版本只保存本机购物车，不会创建订单或发起付款。订单、库存预留和微信支付后端完成后再开放结算。',
      showCancel: false,
      confirmText: '知道了',
    })
  },

  applyCart(cart) {
    const summary = cartSummary(cart)
    this.setData({
      cart,
      items: cart.items.map(cartItemView),
      summary,
      displayTotal: formatCents(summary.totalCents),
      errorMessage: '',
    })
  },
})
