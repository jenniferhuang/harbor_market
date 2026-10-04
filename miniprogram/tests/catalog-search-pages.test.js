'use strict'

const path = require('node:path')
const fs = require('node:fs')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const PRODUCT = {
  product_code: 'APPLE',
  name: '苹果美式',
  base_price_cents: 1600,
  stock_status: 'in_stock',
  category: { code: 'COFFEE', name: '咖啡' },
  images: [{ image_type: 'cover', url: '/api/v1/media/products/apple.webp' }],
  skus: [{ is_active: true, stock_quantity: 12 }],
}

function success(options, data) {
  options.success({ statusCode: 200, data: { data } })
}

function productList(items = [PRODUCT], overrides = {}) {
  return { items, total: items.length, page: 1, page_size: 10, ...overrides }
}

function setup(responder) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  const wxMock = {
    getStorageSync: vi.fn((key) => key === 'harbor_market_api_base_url' ? ORIGIN : undefined),
    setStorageSync: vi.fn(),
    login: vi.fn(),
    navigateTo: vi.fn(),
    stopPullDownRefresh: vi.fn(),
    request: vi.fn((options) => {
      if (responder && responder(options) === true) return
      if (options.url.endsWith('/categories')) success(options, [{ code: 'COFFEE', name: '咖啡' }])
      else if (options.url.endsWith('/hot-searches')) success(options, [{ product: PRODUCT, search_hit_count: 7 }])
      else success(options, productList())
    }),
  }
  global.wx = wxMock

  function mount(name) {
    let definition
    global.Page = vi.fn((value) => { definition = value })
    require(path.join(SOURCE_ROOT, `pages/${name}/${name}.js`))
    const page = {
      ...definition,
      data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
    }
    const loading = page.onLoad()
    const showing = page.onShow()
    return { page, ready: Promise.all([loading, showing]) }
  }

  return { wxMock, mount, navigation: require('../src/state/catalog-navigation'), client: require('../src/api/client') }
}

describe('guest catalog and global product search', () => {
  afterEach(() => {
    delete global.wx
    delete global.Page
    vi.restoreAllMocks()
  })

  it('consumes category navigation once and loads that category without requiring login', async () => {
    const { mount, navigation, wxMock } = setup()
    navigation.selectCatalogCategory(' COFFEE ')
    const { page, ready } = mount('catalog')
    await ready
    expect(page.data).toMatchObject({ selectedCategory: 'COFFEE', loading: false, total: 1 })
    expect(page.data.products[0]).toMatchObject({
      productCode: 'APPLE', name: '苹果美式', displayPrice: '¥16.00',
      coverUrl: `${ORIGIN}/api/v1/media/products/apple.webp`,
    })
    expect(navigation.consumeCatalogCategory()).toBeNull()
    const productRequests = wxMock.request.mock.calls.filter(([options]) => options.url.includes('/catalog/products?'))
    expect(productRequests).toHaveLength(1)
    expect(productRequests[0][0].url).toContain('category=COFFEE')
    expect(productRequests[0][0].header).not.toHaveProperty('Authorization')
    expect(wxMock.login).not.toHaveBeenCalled()
    page.openProduct({ detail: { productCode: 'APPLE' } })
    expect(wxMock.navigateTo).toHaveBeenCalledWith({ url: '/pages/product/product?code=APPLE' })
    page.openSearch()
    expect(wxMock.navigateTo).toHaveBeenCalledWith({ url: '/pages/search/search' })
  })

  it('keeps selected category while returning from details and allows an explicit all-category navigation', async () => {
    const { mount, navigation, wxMock } = setup()
    navigation.selectCatalogCategory('COFFEE')
    const { page, ready } = mount('catalog')
    await ready
    const requestsBeforeShow = wxMock.request.mock.calls.length
    page.onShow()
    expect(page.data.selectedCategory).toBe('COFFEE')
    expect(wxMock.request.mock.calls.length).toBe(requestsBeforeShow)
    navigation.selectCatalogCategory('')
    await page.onShow()
    expect(page.data.selectedCategory).toBe('')
    expect(wxMock.request.mock.calls.at(-1)[0].url).not.toContain('category=')
    expect(navigation.consumeCatalogCategory()).toBeNull()
  })

  it('does not let an older catalog response overwrite a newly selected category', async () => {
    const requests = []
    const { mount } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      requests.push(options)
      return true
    })
    const { page, ready } = mount('catalog')
    const changing = page.selectCategory({ currentTarget: { dataset: { code: 'COFFEE' } } })
    success(requests[1], productList())
    await changing
    success(requests[0], productList([{ ...PRODUCT, product_code: 'TEA', name: '海港茶' }]))
    await ready
    expect(page.data).toMatchObject({ selectedCategory: 'COFFEE', loading: false })
    expect(page.data.products[0].productCode).toBe('APPLE')
  })

  it('displays real hot-search ranks and opens ranked products directly without counting a search', async () => {
    const { mount, wxMock } = setup()
    const { page, ready } = mount('search')
    await ready
    expect(page.data).toMatchObject({ inputFocused: true, searchSubmitted: false, hotLoading: false })
    expect(page.data.hotSearches).toEqual([expect.objectContaining({ productCode: 'APPLE', rank: 1, searchHitCount: 7 })])
    page.openProduct({ currentTarget: { dataset: { code: 'APPLE' } } })
    expect(wxMock.navigateTo).toHaveBeenCalledWith({ url: '/pages/product/product?code=APPLE' })
    expect(wxMock.request).toHaveBeenCalledOnce()
    expect(wxMock.request.mock.calls[0][0].method).toBe('GET')
    expect(wxMock.login).not.toHaveBeenCalled()
  })

  it('records one explicit fuzzy search and paginates with read-only catalog requests', async () => {
    const { mount, wxMock } = setup((options) => {
      if (options.url.endsWith('/shop/search')) {
        success(options, productList([PRODUCT], { total: 21 }))
        return true
      }
      if (options.url.includes('/catalog/products?')) {
        const page = Number(new URL(options.url).searchParams.get('page'))
        success(options, productList([PRODUCT], { total: 21, page }))
        return true
      }
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: ' 苹果 ' } })
    await page.submitSearch()
    expect(page.data).toMatchObject({ appliedQuery: '苹果', searchSubmitted: true, page: 1, totalPages: 3 })
    await page.nextPage()
    expect(page.data.page).toBe(2)
    await page.previousPage()
    expect(page.data.page).toBe(1)
    await page.retrySearch()

    const requests = wxMock.request.mock.calls.map(([options]) => options)
    const counting = requests.filter((options) => options.url.endsWith('/shop/search'))
    expect(counting).toHaveLength(1)
    expect(counting[0]).toMatchObject({ method: 'POST', data: { q: '苹果', page: 1, page_size: 10 } })
    const pagination = requests.filter((options) => options.url.includes('/catalog/products?'))
    expect(pagination).toHaveLength(3)
    for (const options of pagination) {
      expect(options.method).toBe('GET')
      expect(new URL(options.url).searchParams.get('q')).toBe('苹果')
      expect(options.header).not.toHaveProperty('Authorization')
    }
  })

  it('does not issue duplicate pending submissions for the same query', async () => {
    let searching
    const { mount, wxMock } = setup((options) => {
      if (!options.url.endsWith('/shop/search')) return
      searching = options
      return true
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    const first = page.submitSearch()
    page.submitSearch()
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/shop/search'))).toHaveLength(1)
    success(searching, productList())
    await first
  })

  it.each(['success', 'failure'])('ignores an older search %s after a newer query has finished', async (oldResponse) => {
    const requests = []
    const { mount } = setup((options) => {
      if (!options.url.endsWith('/shop/search')) return
      requests.push(options)
      return true
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    const first = page.submitSearch()
    page.onQueryInput({ detail: { value: '茶' } })
    const second = page.submitSearch()
    success(requests[1], productList([{ ...PRODUCT, product_code: 'TEA', name: '海港茶' }]))
    await second
    if (oldResponse === 'success') success(requests[0], productList())
    else requests[0].fail({ errMsg: 'old socket timeout' })
    await first
    expect(page.data).toMatchObject({ appliedQuery: '茶', loading: false, errorMessage: '' })
    expect(page.data.products[0]).toMatchObject({ productCode: 'TEA', name: '海港茶' })
  })

  it('discards an in-flight search after clearing and preserves the hot-search view', async () => {
    let searching
    const { mount, wxMock } = setup((options) => {
      if (!options.url.endsWith('/shop/search')) return
      searching = options
      return true
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    const searchingPromise = page.submitSearch()
    await page.clearSearch()
    success(searching, productList())
    await searchingPromise
    expect(page.data).toMatchObject({ searchSubmitted: false, appliedQuery: '', products: [], loading: false, inputFocused: true })
    page.onQueryInput({ detail: { value: '   ' } })
    await page.submitSearch()
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/shop/search'))).toHaveLength(1)
  })

  it('shows empty results and an empty hot-search list without inventing ranked products', async () => {
    const { mount } = setup((options) => {
      if (options.url.endsWith('/hot-searches')) { success(options, []); return true }
      if (options.url.endsWith('/shop/search')) { success(options, productList([])); return true }
    })
    const { page, ready } = mount('search')
    await ready
    expect(page.data).toMatchObject({ hotSearches: [], hotLoading: false, hotErrorMessage: '' })
    page.onQueryInput({ detail: { value: '没有的商品' } })
    await page.submitSearch()
    expect(page.data).toMatchObject({ products: [], total: 0, loading: false, errorMessage: '', searchSubmitted: true })
    const markup = fs.readFileSync(path.join(SOURCE_ROOT, 'pages/search/search.wxml'), 'utf8')
    expect(markup).toContain('暂无热搜商品')
    expect(markup).toContain('没有找到商品')
    expect(markup).toContain('focus="{{inputFocused}}"')
  })

  it('keeps a failed hot-search request distinguishable from an empty ranking', async () => {
    const { mount } = setup((options) => {
      if (!options.url.endsWith('/hot-searches')) return
      options.fail({ errMsg: 'private socket failure' })
      return true
    })
    const { page, ready } = mount('search')
    await ready
    expect(page.data).toMatchObject({ hotSearches: [], hotLoading: false, searchSubmitted: false })
    expect(page.data.hotErrorMessage).toBe('无法连接服务器，请检查网络后重试。')
  })

  it('shows an empty catalog and does not fabricate available products', async () => {
    const { mount } = setup((options) => {
      if (!options.url.includes('/catalog/products?')) return
      success(options, productList([]))
      return true
    })
    const { page, ready } = mount('catalog')
    await ready
    expect(page.data).toMatchObject({ products: [], total: 0, loading: false, errorMessage: '' })
    expect(fs.readFileSync(path.join(SOURCE_ROOT, 'pages/catalog/catalog.wxml'), 'utf8')).toContain('暂无在售商品')
  })

  it('localizes search failures and retries without recording an additional search', async () => {
    const { mount, wxMock } = setup((options) => {
      if (!options.url.endsWith('/shop/search')) return
      options.success({ statusCode: 503, data: { error: { message: 'private upstream host' } } })
      return true
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    await page.submitSearch()
    expect(page.data).toMatchObject({ loading: false, errorMessage: '服务暂时不可用，请稍后重试。' })
    await page.retrySearch()
    expect(page.data).toMatchObject({ loading: false, errorMessage: '', total: 1 })
    expect(wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/shop/search'))).toHaveLength(1)
  })

  it('retries a new query on page one after the previous query had reached a later page', async () => {
    const { mount, wxMock } = setup((options) => {
      if (options.url.endsWith('/shop/search')) {
        if (options.data.q === '苹果') success(options, productList([PRODUCT], { total: 21 }))
        else options.fail({ errMsg: 'request:fail timeout' })
        return true
      }
      if (options.url.includes('/catalog/products?')) {
        const pageNumber = Number(new URL(options.url).searchParams.get('page'))
        success(options, productList([PRODUCT], { total: 21, page: pageNumber }))
        return true
      }
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    await page.submitSearch()
    await page.nextPage()
    expect(page.data.page).toBe(2)
    page.onQueryInput({ detail: { value: '茶' } })
    await page.submitSearch()
    expect(page.data.page).toBe(1)
    await page.retrySearch()
    const retry = wxMock.request.mock.calls.at(-1)[0]
    expect(new URL(retry.url).searchParams.get('q')).toBe('茶')
    expect(new URL(retry.url).searchParams.get('page')).toBe('1')
    expect(retry.method).toBe('GET')
  })

  it('does not write page state after an outstanding request completes on an unloaded page', async () => {
    let searching
    const { mount } = setup((options) => {
      if (!options.url.endsWith('/shop/search')) return
      searching = options
      return true
    })
    const { page, ready } = mount('search')
    await ready
    page.onQueryInput({ detail: { value: '苹果' } })
    const searchingPromise = page.submitSearch()
    page.onUnload()
    page.setData.mockClear()
    success(searching, productList())
    await searchingPromise
    expect(page.setData).not.toHaveBeenCalled()
  })

  it.each([
    ['categories', 'success'], ['categories', 'failure'],
    ['catalog products', 'success'], ['catalog products', 'failure'],
    ['hot searches', 'success'], ['hot searches', 'failure'],
    ['search results', 'success'], ['search results', 'failure'],
  ])('discards old-origin %s %s and reloads the current origin on return', async (source, response) => {
    let pending
    const { mount, client } = setup((options) => {
      if (!options.url.startsWith(`${ORIGIN}/`)) return
      const matches = source === 'categories' ? options.url.endsWith('/categories')
        : source === 'catalog products' ? options.url.includes('/catalog/products?')
          : source === 'hot searches' ? options.url.endsWith('/hot-searches')
            : options.url.endsWith('/shop/search')
      if (!matches) return
      pending = options
      return true
    })
    const isCatalog = source === 'categories' || source === 'catalog products'
    const { page, ready } = mount(isCatalog ? 'catalog' : 'search')
    let loading = ready
    if (source === 'search results') {
      await ready
      page.onQueryInput({ detail: { value: '苹果' } })
      loading = page.submitSearch()
    }
    expect(pending).toBeDefined()
    // Let unrelated initial requests settle so only the delayed old request remains.
    for (let index = 0; index < 6; index += 1) await Promise.resolve()
    const nextOrigin = 'https://new-shop.example.cn'
    client.setApiBaseUrl(nextOrigin)
    page.setData.mockClear()
    if (response === 'failure') pending.fail({ errMsg: 'old server unreachable' })
    else {
      const oldProduct = { ...PRODUCT, product_code: 'OLD', name: '旧店商品' }
      const payload = source === 'categories' ? [{ code: 'OLD', name: '旧店品类' }]
        : source === 'hot searches' ? [{ product: oldProduct, search_hit_count: 99 }]
          : productList([oldProduct])
      success(pending, payload)
    }
    await loading
    expect(page.setData).not.toHaveBeenCalled()

    await page.onShow()
    if (isCatalog) {
      expect(page.data).toMatchObject({ loading: false, errorMessage: '', categoryErrorMessage: '' })
      expect(page.data.categories).toEqual([{ code: 'COFFEE', name: '咖啡' }])
      expect(page.data.products[0]).toMatchObject({ productCode: 'APPLE', coverUrl: `${nextOrigin}/api/v1/media/products/apple.webp` })
    } else {
      expect(page.data).toMatchObject({ loading: false, hotLoading: false, errorMessage: '', hotErrorMessage: '', searchSubmitted: false, products: [] })
      expect(page.data.hotSearches[0]).toMatchObject({ productCode: 'APPLE', coverUrl: `${nextOrigin}/api/v1/media/products/apple.webp` })
    }
  })
})
