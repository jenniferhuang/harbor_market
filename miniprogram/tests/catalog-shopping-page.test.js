'use strict'

const fs = require('node:fs')
const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const CATEGORIES = [{ code: 'WINE', name: '高粱酒' }, { code: 'CAKE', name: '糕点馅饼' }, { code: 'SNACK', name: '鱼干肉脯' }]
const PRODUCT = {
  product_code: 'WINE-01', name: '高粱酒', subtitle: '醇香甘冽', unit: '瓶',
  base_price_cents: 19000, market_price_cents: 26800, stock_status: 'in_stock',
  images: [{ image_type: 'cover', url: '/api/v1/media/products/wine.webp' }],
  skus: [{ is_active: true, stock_quantity: 12 }],
}
function success(options, data) { options.success({ statusCode: 200, data: { data } }) }
function list(items = [PRODUCT], overrides = {}) { return { items, total: items.length, page: 1, page_size: 20, ...overrides } }
function select(code) { return { currentTarget: { dataset: { code } } } }
async function tick() { for (let index = 0; index < 12; index += 1) await Promise.resolve() }

function setup(responder) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  const measurements = []
  const query = {
    in: vi.fn(function () { return this }), select: vi.fn(function () { return this }),
    selectAll: vi.fn(function () { return this }), boundingClientRect: vi.fn(function () { return this }),
    exec: vi.fn((callback) => measurements.push(callback)),
  }
  const wxMock = {
    getStorageSync: vi.fn((key) => key === 'harbor_market_api_base_url' ? ORIGIN : undefined),
    setStorageSync: vi.fn(), login: vi.fn(), navigateTo: vi.fn(), stopPullDownRefresh: vi.fn(),
    createSelectorQuery: vi.fn(() => query),
    request: vi.fn((options) => {
      if (responder && responder(options) === true) return
      if (options.url.endsWith('/categories')) success(options, CATEGORIES)
      else {
        const params = new URL(options.url).searchParams
        success(options, list([{ ...PRODUCT, product_code: `${params.get('category')}-01` }], { page: Number(params.get('page')) }))
      }
    }),
  }
  global.wx = wxMock
  let definition
  global.Page = vi.fn((value) => { definition = value })
  require(path.join(SOURCE_ROOT, 'pages/catalog/catalog.js'))
  const dock = { refresh: vi.fn() }
  const page = {
    ...definition, data: structuredClone(definition.data), selectComponent: vi.fn(() => dock),
    setData: vi.fn(function (changes, callback) { Object.assign(this.data, changes); if (callback) callback() }),
  }
  const navigation = require('../src/state/catalog-navigation')
  function mount(category) {
    if (category !== undefined) navigation.selectCatalogCategory(category)
    page.onLoad()
    return page.onShow()
  }
  return { page, mount, wxMock, navigation, dock, measurements, client: require('../src/api/client') }
}
function productRequests(wxMock) { return wxMock.request.mock.calls.map(([options]) => options).filter((options) => options.url.includes('/catalog/products?')) }

afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

describe('category shopping page', () => {
  it('opens the home-selected category once, exposes real prices, and refreshes the cart dock', async () => {
    const { page, mount, wxMock, navigation, dock } = setup()
    await mount(' CAKE ')
    expect(page.data).toMatchObject({ selectedCategory: 'CAKE', loading: false, railIntoView: 'catalog-category-1' })
    expect(page.data.sections).toHaveLength(1)
    expect(page.data.sections[0]).toMatchObject({ code: 'CAKE', name: '糕点馅饼', complete: true, total: 1 })
    expect(page.data.sections[0].products[0]).toMatchObject({ productCode: 'CAKE-01', displayPrice: '¥190.00', marketPrice: '¥268.00', coverUrl: `${ORIGIN}/api/v1/media/products/wine.webp` })
    expect(navigation.consumeCatalogCategory()).toBeNull()
    expect(productRequests(wxMock)).toHaveLength(1)
    expect(productRequests(wxMock)[0].url).toContain('category=CAKE&page=1&page_size=20')
    expect(productRequests(wxMock)[0].header).not.toHaveProperty('Authorization')
    expect(wxMock.login).not.toHaveBeenCalled()
    await page.onShow()
    expect(productRequests(wxMock)).toHaveLength(1)
    expect(dock.refresh).toHaveBeenCalledTimes(2)
    expect(page.selectComponent).toHaveBeenCalledWith('#cart-dock')
  })

  it('loads bounded pages and enters the next category only after the current one completes', async () => {
    const { page, mount, wxMock, measurements } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      const params = new URL(options.url).searchParams
      const pageNumber = Number(params.get('page'))
      if (params.get('category') === 'WINE') {
        const count = pageNumber === 1 ? 20 : 1
        success(options, list(Array.from({ length: count }, (_, index) => ({ ...PRODUCT, product_code: `WINE-${(pageNumber - 1) * 20 + index}` })), { total: 21, page: pageNumber }))
      } else success(options, list([{ ...PRODUCT, product_code: 'CAKE-01' }]))
      return true
    })
    await mount()
    expect(productRequests(wxMock)).toHaveLength(1)
    expect(page.data.sections[0]).toMatchObject({ complete: false, nextPage: 2 })
    const loading = page.loadMore()
    page.loadMore()
    await loading
    expect(productRequests(wxMock)).toHaveLength(2)
    expect(page.data.sections[0].products).toHaveLength(21)
    expect(page.data).toMatchObject({ nextCategoryName: '糕点馅饼', selectedCategory: 'WINE' })
    await page.loadMore()
    expect(productRequests(wxMock)).toHaveLength(3)
    expect(page.data.sections.map((section) => section.code)).toEqual(['WINE', 'CAKE'])
    expect(productRequests(wxMock)[2].url).toContain('category=CAKE&page=1&page_size=20')
    page.onProductScroll()
    measurements.shift()([{ top: 100, bottom: 800 }, [{ id: 'catalog-section-0', top: -1200, bottom: 90 }, { id: 'catalog-section-1', top: 90, bottom: 1000 }]])
    expect(page.data).toMatchObject({ selectedCategory: 'CAKE', railIntoView: 'catalog-category-1' })
  })

  it('jumps to loaded dividers, resets to an unloaded category, and collapses the rail', async () => {
    const { page, mount, wxMock } = setup()
    await mount()
    await page.loadMore()
    const count = productRequests(wxMock).length
    await page.selectCategory(select('WINE'))
    expect(page.data.productIntoView).toBe('catalog-section-0')
    expect(productRequests(wxMock)).toHaveLength(count)
    await page.selectCategory(select('SNACK'))
    expect(page.data.sections.map((section) => section.code)).toEqual(['SNACK'])
    expect(page.data).toMatchObject({ selectedCategory: 'SNACK', productIntoView: 'catalog-section-2', endReached: true })
    page.toggleRail()
    expect(page.data.railCollapsed).toBe(true)
    page.toggleRail()
    expect(page.data.railCollapsed).toBe(false)
    await page.loadMore()
    expect(productRequests(wxMock)).toHaveLength(count + 1)
  })

  it('coalesces rapid category changes behind one pending request and discards its obsolete response', async () => {
    const pending = []
    const { page, mount, wxMock } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      pending.push(options)
      return true
    })
    const initial = mount()
    await tick()
    expect(pending).toHaveLength(1)
    const changing = page.selectCategory(select('CAKE'))
    page.selectCategory(select('SNACK'))
    expect(page.data.selectedCategory).toBe('SNACK')
    expect(pending).toHaveLength(1)
    success(pending[0], list([{ ...PRODUCT, product_code: 'OLD' }]))
    await tick()
    expect(pending).toHaveLength(2)
    expect(pending[1].url).toContain('category=SNACK')
    success(pending[1], list([{ ...PRODUCT, product_code: 'SNACK-NEW' }]))
    await Promise.all([initial, changing])
    expect(page.data.sections[0].products.map((item) => item.productCode)).toEqual(['SNACK-NEW'])
    expect(productRequests(wxMock)).toHaveLength(2)
    expect(page.data.loading).toBe(false)
  })

  it('ignores stale scroll measurements after a category click', async () => {
    const { page, mount, measurements } = setup()
    await mount()
    await page.loadMore()
    page.onProductScroll()
    await page.selectCategory(select('CAKE'))
    const count = page.setData.mock.calls.length
    measurements.shift()([{ top: 100, bottom: 800 }, [{ id: 'catalog-section-0', top: 100, bottom: 1500 }]])
    expect(page.data.selectedCategory).toBe('CAKE')
    expect(page.setData.mock.calls.length).toBe(count)
  })

  it('uses the latest home navigation during category loading and handles browse-all navigation', async () => {
    let categoriesRequest
    const { page, mount, navigation, wxMock } = setup((options) => {
      if (!options.url.endsWith('/categories')) return
      categoriesRequest = options
      return true
    })
    const initial = mount('WINE')
    await tick()
    navigation.selectCatalogCategory('CAKE')
    const showing = page.onShow()
    success(categoriesRequest, CATEGORIES)
    await Promise.all([initial, showing])
    expect(page.data.selectedCategory).toBe('CAKE')
    expect(productRequests(wxMock)).toHaveLength(1)
    navigation.selectCatalogCategory('')
    await page.onShow()
    expect(page.data.selectedCategory).toBe('WINE')
    expect(productRequests(wxMock)[1].url).toContain('category=WINE')
  })

  it('keeps a failed later page visible and retries that page without restarting prior products', async () => {
    let secondPageAttempts = 0
    const { page, mount, wxMock } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      const pageNumber = Number(new URL(options.url).searchParams.get('page'))
      if (pageNumber === 1) success(options, list([PRODUCT], { total: 21 }))
      else if (++secondPageAttempts === 1) options.fail({ errMsg: 'private socket error' })
      else success(options, list([{ ...PRODUCT, product_code: 'WINE-02' }], { total: 21, page: 2 }))
      return true
    })
    await mount()
    await page.loadMore()
    expect(page.data.sections[0].products).toHaveLength(1)
    expect(page.data).toMatchObject({ loading: false, errorMessage: '无法连接服务器，请检查网络后重试。' })
    await page.loadMore()
    expect(productRequests(wxMock)).toHaveLength(2)
    await page.retrySection()
    expect(productRequests(wxMock)[2].url).toContain('page=2')
    expect(page.data.sections[0].products).toHaveLength(2)
    expect(page.data.errorMessage).toBe('')
  })

  it('terminates empty pages with stale totals without silently skipping an empty category', async () => {
    const { page, mount, wxMock } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      success(options, list([], { total: 9999 }))
      return true
    })
    await mount()
    expect(page.data.sections[0]).toMatchObject({ products: [], total: 9999, complete: true })
    expect(productRequests(wxMock)).toHaveLength(1)
    await page.loadMore()
    expect(page.data.sections.map((section) => section.code)).toEqual(['WINE', 'CAKE'])
    expect(productRequests(wxMock)).toHaveLength(2)
    expect(page.data.nextCategoryName).toBe('鱼干肉脯')
  })

  it('falls back to real all-products data after category failure and retries categories', async () => {
    let attempts = 0
    const { page, mount, wxMock } = setup((options) => {
      if (!options.url.endsWith('/categories') || ++attempts > 1) return
      options.fail({ errMsg: 'old upstream host' })
      return true
    })
    await mount()
    expect(page.data.categoryErrorMessage).toBe('无法连接服务器，请检查网络后重试。')
    expect(page.data.sections[0]).toMatchObject({ code: '', name: '全部商品', complete: true })
    expect(productRequests(wxMock)[0].url).not.toContain('category=')
    await page.retry()
    expect(page.data).toMatchObject({ categoryErrorMessage: '', selectedCategory: 'WINE', loading: false })
  })

  it.each(['categories', 'products'])('never writes state when pending %s complete after unload', async (source) => {
    let pending
    const { page, mount } = setup((options) => {
      if (source === 'categories' ? options.url.endsWith('/categories') : options.url.includes('/catalog/products?')) { pending = options; return true }
    })
    const initial = mount()
    await tick()
    page.onUnload()
    page.setData.mockClear()
    success(pending, source === 'categories' ? CATEGORIES : list())
    await initial
    expect(page.setData).not.toHaveBeenCalled()
  })

  it.each(['categories', 'products'])('discards old-origin %s and reloads current-origin data on return', async (source) => {
    let pending
    const { page, mount, client } = setup((options) => {
      if (!options.url.startsWith(`${ORIGIN}/`)) return
      if (source === 'categories' ? options.url.endsWith('/categories') : options.url.includes('/catalog/products?')) { pending = options; return true }
    })
    const initial = mount()
    await tick()
    client.setApiBaseUrl('https://next.example.cn')
    page.setData.mockClear()
    success(pending, source === 'categories' ? [{ code: 'OLD', name: '旧店' }] : list([{ ...PRODUCT, product_code: 'OLD' }]))
    await initial
    expect(page.setData).not.toHaveBeenCalled()
    await page.onShow()
    expect(page.data).toMatchObject({ selectedCategory: 'WINE', loading: false, errorMessage: '', categoryErrorMessage: '' })
    expect(page.data.sections[0].products[0].coverUrl).toBe('https://next.example.cn/api/v1/media/products/wine.webp')
    expect(page.data.sections[0].products[0].productCode).not.toBe('OLD')
  })

  it('rejects duplicate pages so bottom events cannot create a pagination loop', async () => {
    const { page, mount } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      const pageNumber = Number(new URL(options.url).searchParams.get('page'))
      success(options, list([PRODUCT], { total: 99, page: pageNumber }))
      return true
    })
    await mount()
    await page.loadMore()
    expect(page.data.sections[0].products).toHaveLength(1)
    expect(page.data.errorMessage).toBe('商品分页数据重复，请重新加载。')
  })

  it('opens details and specification choice with encoded codes instead of quick-adding to cart', async () => {
    const { page, mount, wxMock } = setup()
    await mount()
    page.openProduct(select('WINE/A 01'))
    page.chooseProduct(select('WINE/A 01'))
    page.openSearch()
    expect(wxMock.navigateTo.mock.calls.map(([value]) => value.url)).toEqual(['/pages/product/product?code=WINE%2FA%2001', '/pages/product/product?code=WINE%2FA%2001&choose=1', '/pages/search/search'])
    const markup = fs.readFileSync(path.join(SOURCE_ROOT, 'pages/catalog/catalog.wxml'), 'utf8')
    const configuration = JSON.parse(fs.readFileSync(path.join(SOURCE_ROOT, 'pages/catalog/catalog.json'), 'utf8'))
    expect(markup).toContain('catchtap="chooseProduct"')
    expect(markup).toContain('bindscrolltolower="loadMore"')
    expect(markup).toContain('暂无在售商品')
    expect(markup).toContain('id="cart-dock"')
    expect(configuration.usingComponents['cart-dock']).toBe('/components/cart-dock/cart-dock')
  })
})
