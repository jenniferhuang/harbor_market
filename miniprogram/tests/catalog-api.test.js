'use strict'

function setup(responder) {
  vi.resetModules()
  global.wx = {
    getStorageSync: vi.fn(),
    setStorageSync: vi.fn(),
    request: vi.fn((options) => responder(options)),
  }
  return require('../src/api/catalog')
}

describe('public catalog API', () => {
  afterEach(() => {
    delete global.wx
    vi.restoreAllMocks()
  })

  it('fetches categories from the public API', async () => {
    const catalog = setup((options) => {
      expect(options.url).toBe('http://127.0.0.1:8080/api/v1/catalog/categories')
      options.success({ statusCode: 200, data: { data: [{ code: 'COFFEE' }] } })
    })

    await expect(catalog.fetchCategories()).resolves.toEqual([{ code: 'COFFEE' }])
  })

  it('encodes public list filters and absolutizes same-origin media', async () => {
    const catalog = setup((options) => {
      expect(options.url).toContain('/api/v1/catalog/products?')
      expect(options.url).toContain('q=%E6%B5%B7%E6%B8%AF')
      expect(options.url).toContain('category=COFFEE')
      expect(options.url).toContain('page=2')
      expect(options.url).toContain('page_size=8')
      options.success({
        statusCode: 200,
        data: {
          data: {
            items: [
              {
                product_code: 'LATTE',
                images: [{ url: '/api/v1/media/products/latte.webp' }],
              },
            ],
            total: 1,
            page: 2,
            page_size: 8,
          },
        },
      })
    })

    const page = await catalog.fetchProducts({
      q: ' 海港 ',
      category: 'COFFEE',
      page: 2,
      page_size: 8,
    })
    expect(page.items[0].images[0].url).toBe(
      'http://127.0.0.1:8080/api/v1/media/products/latte.webp',
    )
    expect(page).toMatchObject({ total: 1, page: 2, page_size: 8 })
  })

  it('normalizes product codes and rejects invalid list bounds before networking', async () => {
    const catalog = setup((options) => {
      expect(options.url).toContain('/api/v1/catalog/products/LATTE-1')
      options.success({
        statusCode: 200,
        data: { data: { product_code: 'LATTE-1', images: [], skus: [] } },
      })
    })

    await expect(catalog.fetchProduct(' latte-1 ')).resolves.toMatchObject({
      product_code: 'LATTE-1',
      specifications: [],
    })
    await expect(catalog.fetchProducts({ page_size: 101 })).rejects.toThrow(TypeError)
  })
})
