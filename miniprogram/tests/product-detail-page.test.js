'use strict'

const fs = require('node:fs')
const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const CUSTOMER = { id: 17, nickname: '小港', avatar_url: '' }

function fixture(overrides = {}) {
  return {
    product_code: 'DRY-FISH', name: '香酥小黄鱼135克', subtitle: '厦门风味，酥香可口',
    category: { id: 3, code: 'DRIED-SNACKS', name: '鱼干肉脯' },
    base_price_cents: 1500, market_price_cents: 1800, currency: 'CNY', unit: '包',
    stock_status: 'in_stock', inventory_count: 5, featured: true,
    tags: ['厦门风味', '独立包装'], selling_points: ['酥脆口感', '方便分享'],
    description: '香酥小黄鱼，开袋即食。\n请置于阴凉干燥处。',
    ingredients: '小黄鱼、食用植物油、食盐', allergen_info: '含鱼类及其制品。',
    images: [
      { id: 4, image_type: 'detail', sort_order: 20, url: '/api/v1/media/detail-2', alt_text: '配料与包装' },
      { id: 2, image_type: 'gallery', sort_order: 30, url: '/api/v1/media/gallery', alt_text: '包装侧面' },
      { id: 1, image_type: 'cover', sort_order: 99, url: '/api/v1/media/cover', alt_text: '小黄鱼正面包装' },
      { id: 3, image_type: 'detail', sort_order: 10, url: '/api/v1/media/detail-1', alt_text: '商品图文介绍' },
      { id: 5, image_type: 'gallery', sort_order: 40, url: 'https://another.example.cn/not-shop-media' },
    ],
    specifications: [
      { code: 'PACK', name: '包装', selection_mode: 'single', required: true, min_select: 1, max_select: 1,
        options: [
          { code: 'SINGLE', name: '单包', is_default: true, price_delta_cents: 0 },
          { code: 'DOUBLE', name: '双包', price_delta_cents: 999 },
          { code: 'UNAVAILABLE', name: '暂停供应', price_delta_cents: 0 },
        ] },
      { code: 'EXTRA', name: '附加包装', selection_mode: 'multiple', required: false, min_select: 0, max_select: 1,
        options: [{ code: 'GIFT', name: '礼品包装', price_delta_cents: 200 }] },
    ],
    skus: [
      { id: 1, sku_code: 'FISH-SINGLE', name: '单包装', price_cents: 1500, market_price_cents: 1800,
        stock_quantity: 3, attributes: { PACK: 'SINGLE' }, is_default: true, is_active: true },
      { id: 2, sku_code: 'FISH-DOUBLE', name: '双包装', price_cents: 2800, market_price_cents: 3200,
        stock_quantity: 2, attributes: { PACK: 'DOUBLE' }, is_active: true },
      { id: 3, sku_code: 'FISH-OLD', name: '停用规格', price_cents: 100, market_price_cents: 100,
        stock_quantity: 800, attributes: { PACK: 'UNAVAILABLE' }, is_active: false },
    ],
    ...overrides,
  }
}

function success(options, data) { options.success({ statusCode: 200, data: { data } }) }

function setup(config = {}) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(SOURCE_ROOT + path.sep)) delete require.cache[modulePath]
  }
  const storage = new Map([['harbor_market_api_base_url', ORIGIN]])
  let savedFavorite = false
  const payload = config.product || fixture()
  const dock = { refresh: vi.fn() }
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'real-wx-code' })),
    request: vi.fn((options) => {
      if (config.intercept && config.intercept(options)) return
      if (options.url.endsWith('/auth/login')) success(options, {
        token_type: 'Bearer', access_token: 'customer-token-abcdefghijklmnop',
        expires_at: new Date(Date.now() + 300_000).toISOString(), customer: CUSTOMER,
      })
      else if (options.url.endsWith('/auth/logout')) success(options, null)
      else if (options.url.includes('/catalog/products/')) success(options, payload)
      else if (options.url.includes('/mini/shop/favorites/')) {
        if (options.method === 'PUT') savedFavorite = true
        if (options.method === 'DELETE') savedFavorite = false
        success(options, { is_favorite: savedFavorite })
      } else throw new Error('Unexpected test request: ' + options.url)
    }),
    previewImage: vi.fn(), showToast: vi.fn(), showModal: vi.fn(),
    navigateTo: vi.fn(), switchTab: vi.fn(), setNavigationBarTitle: vi.fn(), stopPullDownRefresh: vi.fn(),
  }
  global.wx = wxMock
  const auth = require('../src/state/auth-store')
  const cart = require('../src/state/cart-store')
  const client = require('../src/api/client')
  let definition
  global.Page = vi.fn((value) => { definition = value })
  require('../src/pages/product/product')
  function mount(options = { code: 'DRY-FISH' }) {
    const page = {
      ...definition, data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
    }
    if (config.withDock) page.selectComponent = vi.fn(() => dock)
    page.onLoad(options)
    page.onShow()
    return page
  }
  return { mount, auth, cart, client, dock, wxMock, storage, payload }
}

async function ready(page) { await vi.waitFor(() => expect(page.data.loading).toBe(false)) }
function choose(page, specificationCode, optionCode) {
  page.selectOption({ currentTarget: { dataset: { specificationCode, optionCode } } })
}

describe('native product detail page', () => {
  afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

  it('separates ordered cover/gallery images from detail-role images and previews each collection independently', async () => {
    const { mount, wxMock } = setup()
    const page = mount()
    await ready(page)
    expect(page.data.images.map((image) => image.id)).toEqual([1, 2])
    expect(page.data.detailImages.map((image) => image.id)).toEqual([3, 4])
    expect(page.data.coverImageUrl).toBe(ORIGIN + '/api/v1/media/cover')
    page.previewImage({ currentTarget: { dataset: { role: 'detail', index: 1 } } })
    expect(wxMock.previewImage).toHaveBeenLastCalledWith({
      current: ORIGIN + '/api/v1/media/detail-2',
      urls: [ORIGIN + '/api/v1/media/detail-1', ORIGIN + '/api/v1/media/detail-2'],
    })
    page.previewImage({ currentTarget: { dataset: { role: 'gallery', index: 0 } } })
    expect(wxMock.previewImage).toHaveBeenLastCalledWith({
      current: ORIGIN + '/api/v1/media/cover',
      urls: [ORIGIN + '/api/v1/media/cover', ORIGIN + '/api/v1/media/gallery'],
    })
  })

  it('keeps detail-only media out of the carousel and specification preview', async () => {
    const { mount, wxMock } = setup({ product: fixture({ images: [{ id: 7, image_type: 'detail', url: '/api/v1/media/long-image' }] }) })
    const page = mount()
    await ready(page)
    expect(page.data.images).toEqual([])
    expect(page.data.coverImageUrl).toBe('')
    expect(page.data.detailImages).toHaveLength(1)
    page.previewImage({ currentTarget: { dataset: { role: 'gallery', index: 0 } } })
    expect(wxMock.previewImage).not.toHaveBeenCalled()
  })

  it('exposes the actual unit, category, tags, stock, active SKU prices and descriptive fields', async () => {
    const { mount, payload } = setup()
    const page = mount()
    await ready(page)
    expect(page.data).toMatchObject({
      displayPrice: '¥15.00', marketPrice: '¥18.00', displayUnit: '包',
      stockStatusLabel: '现货', stockLabel: '库存 3 包', inventoryLabel: '5 包', hasAvailableSku: true,
    })
    expect(page.data.product).toMatchObject({
      category: payload.category, tags: payload.tags, selling_points: payload.selling_points,
      description: payload.description, ingredients: payload.ingredients, allergen_info: payload.allergen_info,
    })
    expect(page.data.activeSkuRows).toEqual([
      expect.objectContaining({ code: 'FISH-SINGLE', name: '单包装', displayPrice: '¥15.00', marketPrice: '¥18.00', stockText: '库存 3 包', attributesLabel: '包装：单包', available: true }),
      expect.objectContaining({ code: 'FISH-DOUBLE', name: '双包装', displayPrice: '¥28.00', stockText: '库存 2 包', available: true }),
    ])
    expect(page.data.activeSkuRows.some((row) => row.code === 'FISH-OLD')).toBe(false)
  })

  it('uses a dedicated widthFix detail section and accessible specification sheet with shared cart dock', () => {
    const markup = fs.readFileSync(path.join(SOURCE_ROOT, 'pages/product/product.wxml'), 'utf8')
    const config = JSON.parse(fs.readFileSync(path.join(SOURCE_ROOT, 'pages/product/product.json'), 'utf8'))
    expect(markup).toMatch(/wx:for="{{detailImages}}"[^>]*mode="widthFix"/)
    expect(markup).toMatch(/aria-role="dialog"[^>]*aria-label="选择商品规格和数量"/)
    expect(markup).toContain('catchtouchmove="stopSheetTouch"')
    expect(markup).toContain('<cart-dock id="cart-dock" bindchange="onCartChange" />')
    expect(config.usingComponents['cart-dock']).toBe('/components/cart-dock/cart-dock')
    expect(markup).not.toMatch(/会员价|包邮|已售\d|商品评价|立即购买/)
  })

  it('opens and closes the spec sheet without changing guest cart or selected options', async () => {
    const { mount, cart, wxMock } = setup()
    const page = mount()
    await ready(page)
    expect(page.data.specSheetVisible).toBe(false)
    page.openSpecSheet()
    choose(page, 'PACK', 'DOUBLE')
    page.closeSpecSheet()
    expect(page.data.specSheetVisible).toBe(false)
    page.openSpecSheet()
    expect(page.data.selections.PACK).toEqual(['DOUBLE'])
    expect(cart.loadCart().items).toEqual([])
    expect(wxMock.login).not.toHaveBeenCalled()
    page.onHide()
    expect(page.data.specSheetVisible).toBe(false)
  })

  it('auto-opens choose=1 only after the product loads and only once', async () => {
    let pendingProduct
    let delay = true
    const { mount } = setup({ intercept(options) {
      if (!delay || !options.url.includes('/catalog/products/')) return false
      pendingProduct = options
      return true
    } })
    const page = mount({ code: 'DRY-FISH', choose: '1' })
    expect(page.data.specSheetVisible).toBe(false)
    success(pendingProduct, fixture())
    await ready(page)
    expect(page.data.specSheetVisible).toBe(true)
    page.closeSpecSheet()
    delay = false
    await page.loadProduct()
    expect(page.data.specSheetVisible).toBe(false)
  })

  it('uses SKU variant prices once and charges pure customization options separately', async () => {
    const { mount } = setup()
    const page = mount()
    await ready(page)
    page.openSpecSheet()
    choose(page, 'PACK', 'DOUBLE')
    expect(page.data).toMatchObject({ displayPrice: '¥28.00', marketPrice: '¥32.00', maxQuantity: 2 })
    expect(page.data.specifications[0].options[1].priceDeltaLabel).toBe('')
    choose(page, 'EXTRA', 'GIFT')
    expect(page.data.displayPrice).toBe('¥30.00')
    expect(page.data.specifications[1].options[0].priceDeltaLabel).toBe('+¥2.00')
    expect(page.data.selectionText).toBe('包装：双包 / 附加包装：礼品包装')
  })

  it('adds the chosen variant and quantity as a guest, closes the sheet and refreshes the cart dock', async () => {
    const { mount, cart, dock, wxMock } = setup({ withDock: true })
    const page = mount()
    await ready(page)
    expect(dock.refresh).toHaveBeenCalledOnce()
    dock.refresh.mockClear()
    page.openSpecSheet()
    choose(page, 'PACK', 'DOUBLE')
    choose(page, 'EXTRA', 'GIFT')
    page.changeQuantity({ detail: { value: 2 } })
    page.addToCart()
    expect(cart.loadCart().items).toEqual([
      expect.objectContaining({ skuCode: 'FISH-DOUBLE', quantity: 2, unitPriceCents: 3000,
        selections: { PACK: ['DOUBLE'], EXTRA: ['GIFT'] }, stockQuantity: 2 }),
    ])
    expect(page.data).toMatchObject({ specSheetVisible: false, adding: false })
    expect(dock.refresh).toHaveBeenCalledOnce()
    expect(wxMock.showToast).toHaveBeenCalledWith({ title: '已加入购物车', icon: 'success' })
    expect(wxMock.login).not.toHaveBeenCalled()
  })

  it('requires missing specification choices and rejects an unavailable variant without saving cart data', async () => {
    const product = fixture()
    product.specifications[0].options[0].is_default = false
    const { mount, cart } = setup({ product })
    const page = mount()
    await ready(page)
    expect(page.data).toMatchObject({ canAddToCart: false, selectionHint: '请选择包装' })
    page.addToCart()
    expect(page.data.specSheetVisible).toBe(true)
    choose(page, 'PACK', 'UNAVAILABLE')
    expect(page.data.canAddToCart).toBe(false)
    expect(page.data.selectionHint).toContain('暂不可售')
    page.addToCart()
    expect(cart.loadCart().items).toEqual([])
  })

  it('shares remaining SKU stock across customization choices and refreshes after a cart change', async () => {
    const { mount, cart } = setup()
    const page = mount()
    await ready(page)
    page.changeQuantity({ detail: { value: 2 } })
    page.addToCart()
    choose(page, 'EXTRA', 'GIFT')
    expect(page.data).toMatchObject({ maxQuantity: 1, canAddToCart: true })
    expect(page.data.stockLabel).toContain('购物车已选 2')
    page.changeQuantity({ detail: { value: 99 } })
    page.addToCart()
    expect(cart.loadCart().items.map((item) => item.quantity)).toEqual([2, 1])
    expect(page.data.canAddToCart).toBe(false)
    expect(page.data.selectionHint).toBe('购物车中该规格的数量已达到库存上限。')
    cart.clearCart()
    page.onCartChange()
    expect(page.data).toMatchObject({ maxQuantity: 3, canAddToCart: true, stockLabel: '库存 3 包' })
  })

  it.each(['no active SKU', 'zero SKU stock', 'product out of stock', 'unknown SKU stock'])('prevents adding when there is %s', async (condition) => {
    const product = fixture()
    if (condition === 'no active SKU') product.skus = product.skus.map((sku) => ({ ...sku, is_active: false }))
    if (condition === 'zero SKU stock') product.skus = product.skus.map((sku) => ({ ...sku, stock_quantity: 0 }))
    if (condition === 'product out of stock') product.stock_status = 'out_of_stock'
    if (condition === 'unknown SKU stock') product.skus = product.skus.map((sku) => ({ ...sku, stock_quantity: null }))
    const { mount, cart, wxMock } = setup({ product })
    const page = mount()
    await ready(page)
    page.openSpecSheet()
    page.addToCart()
    expect(page.data.canAddToCart).toBe(false)
    expect(page.data.hasAvailableSku).toBe(false)
    expect(page.data.maxQuantity).toBe(1)
    expect(page.data.selectionHint).toMatch(/[\u3400-\u9fff]/)
    expect(page.data.stockLabel).not.toContain('999')
    expect(cart.loadCart().items).toEqual([])
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('bounds quantities by real SKU stock and lowers a previous quantity when changing variants', async () => {
    const { mount } = setup()
    const page = mount()
    await ready(page)
    page.changeQuantity({ detail: { value: 100 } })
    expect(page.data.quantity).toBe(3)
    choose(page, 'PACK', 'DOUBLE')
    expect(page.data).toMatchObject({ maxQuantity: 2, quantity: 2 })
    page.changeQuantity({ detail: { value: 1.5 } })
    expect(page.data.quantity).toBe(2)
    page.changeQuantity({ detail: { value: -1 } })
    expect(page.data.quantity).toBe(1)
  })

  it('keeps the picker open and reports a failed local cart save without a success toast', async () => {
    const { mount, wxMock, cart } = setup()
    const page = mount()
    await ready(page)
    page.openSpecSheet()
    wxMock.setStorageSync.mockImplementation(() => { throw new Error('storage full') })
    page.addToCart()
    expect(page.data).toMatchObject({ specSheetVisible: true, adding: false })
    expect(wxMock.showModal).toHaveBeenCalledWith(expect.objectContaining({ title: '未能加入购物车', content: '购物车保存失败，请检查设备存储空间。' }))
    expect(wxMock.showToast).not.toHaveBeenCalled()
    expect(cart.loadCart().items).toEqual([])
  })

  it('keeps public details and the spec picker usable when a guest taps favorite', async () => {
    const { mount, wxMock } = setup()
    const page = mount()
    await ready(page)
    page.openSpecSheet()
    await page.toggleFavorite()
    expect(page.data.product.name).toBe('香酥小黄鱼135克')
    expect(page.data.specSheetVisible).toBe(true)
    expect(wxMock.showModal).toHaveBeenCalledWith(expect.objectContaining({ title: '登录后收藏商品' }))
    expect(wxMock.request.mock.calls.some(([options]) => options.url.includes('/mini/shop/favorites/'))).toBe(false)
  })

  it('waits for server favorite confirmation and suppresses duplicate taps', async () => {
    let pendingFavorite
    const { mount, auth, wxMock } = setup({ intercept(options) {
      if (options.method !== 'PUT' || !options.url.includes('/mini/shop/favorites/')) return false
      pendingFavorite = options
      return true
    } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    const saving = page.toggleFavorite()
    await page.toggleFavorite()
    expect(page.data).toMatchObject({ isFavorite: false, favoriteBusy: true })
    expect(wxMock.request.mock.calls.filter(([options]) => options.method === 'PUT')).toHaveLength(1)
    success(pendingFavorite, { is_favorite: true })
    await saving
    expect(page.data).toMatchObject({ isFavorite: true, favoriteBusy: false })
  })

  it('discards favorite success after logout without changing the visible guest state', async () => {
    let pendingFavorite
    const { mount, auth, wxMock } = setup({ intercept(options) {
      if (options.method !== 'PUT' || !options.url.includes('/mini/shop/favorites/')) return false
      pendingFavorite = options
      return true
    } })
    await auth.loginWithWeChat()
    const page = mount()
    await ready(page)
    const saving = page.toggleFavorite()
    await auth.logout()
    wxMock.showToast.mockClear()
    success(pendingFavorite, { is_favorite: true })
    await saving
    expect(page.data).toMatchObject({ isFavorite: false, favoriteBusy: false, favoriteError: '' })
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it.each(['origin change', 'unload'])('discards product data fetched before %s', async (action) => {
    let pendingProduct
    const { mount, auth, client } = setup({ intercept(options) {
      if (!options.url.includes('/catalog/products/')) return false
      pendingProduct = options
      return true
    } })
    const page = mount({ code: 'DRY-FISH', choose: '1' })
    if (action === 'unload') page.onUnload()
    else { client.setApiBaseUrl('https://other-shop.example.cn'); auth.resetSessionForApiChange() }
    page.setData.mockClear()
    success(pendingProduct, fixture())
    for (let index = 0; index < 6; index += 1) await Promise.resolve()
    expect(page.data.product).toBeNull()
    expect(page.data.specSheetVisible).toBe(false)
    expect(page.setData).not.toHaveBeenCalled()
  })
})
