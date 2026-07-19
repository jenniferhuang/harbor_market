import { afterEach, describe, expect, it, vi } from 'vitest'
import { catalogAdminApi } from '../api/catalog'

const category = {
  id: 3,
  code: 'COFFEE',
  name: '咖啡',
  description: null,
  parent_id: null,
  sort_order: 0,
  is_active: true,
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:00Z',
}

const product = {
  id: 9,
  product_code: 'LATTE-01',
  name: '生椰拿铁',
  subtitle: null,
  category,
  status: 'draft',
  base_price_cents: 1990,
  market_price_cents: null,
  currency: 'CNY',
  unit: '杯',
  description: '',
  featured: false,
  stock_status: 'in_stock',
  inventory_count: null,
  tags: [],
  selling_points: [],
  specifications: [],
  ingredients: null,
  allergen_info: null,
  sort_order: 0,
  skus: [],
  images: [],
  created_at: '2026-07-14T00:00:00Z',
  updated_at: '2026-07-14T00:00:00Z',
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('catalog administration API', () => {
  it('uses product filters and derives category_id from the nested category response', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(
        JSON.stringify({ data: { items: [product], total: 1, page: 2, page_size: 20 } }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await catalogAdminApi.listProducts({
      q: '拿铁',
      status: 'draft',
      category_id: 3,
      page: 2,
      page_size: 20,
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/admin/products?q=%E6%8B%BF%E9%93%81&status=draft&category_id=3&page=2&page_size=20',
    )
    expect(result.items[0]).toMatchObject({ id: 9, category_id: 3 })
    expect(result.total).toBe(1)
  })

  it('sends the image multipart fields and unwraps the updated product', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(JSON.stringify({ data: product }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const image = new File(['image'], 'latte.webp', { type: 'image/webp' })

    const updated = await catalogAdminApi.uploadProductImage(9, {
      file: image,
      image_type: 'gallery',
      alt_text: '杯装拿铁',
      sort_order: 2,
    })

    const init = fetchMock.mock.calls[0]?.[1]
    const body = init?.body as FormData
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/admin/products/9/images')
    expect(body.get('file')).toBe(image)
    expect(body.get('image_type')).toBe('gallery')
    expect(body.get('alt_text')).toBe('杯装拿铁')
    expect(body.get('sort_order')).toBe('2')
    expect(updated.category_id).toBe(3)
  })

  it('normalizes Excel dry-run results and row-level errors', async () => {
    const fetchMock = vi.fn(async (...args: Parameters<typeof fetch>) => {
      void args
      return new Response(
        JSON.stringify({
          data: {
            job_id: 12,
            dry_run: true,
            valid: false,
            summary: { products: 1 },
            errors: [{ sheet: 'Products', row: 2, field: 'name', message: '商品名称不能为空' }],
            promoted_staging_keys: [],
          },
        }),
      )
    })
    vi.stubGlobal('fetch', fetchMock)
    const workbook = new File(['xlsx'], 'catalog.xlsx')

    const result = await catalogAdminApi.importProducts(workbook, true, 'catalog-test-key')

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/admin/products/import?dry_run=true')
    expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get('X-Idempotency-Key')).toBe(
      'catalog-test-key',
    )
    expect(result).toMatchObject({ job_id: 12, dry_run: true, valid: false })
    expect(result.promoted_staging_keys).toEqual([])
    expect(result.errors[0]).toMatchObject({ sheet: 'Products', row: 2, field: 'name' })
  })

  it('rejects malformed category, product-list, and import success contracts', async () => {
    const fetchMock = vi
      .fn(async () => new Response(JSON.stringify({ data: {} })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: null })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { items: [] } })))
    vi.stubGlobal('fetch', fetchMock)

    await expect(catalogAdminApi.listCategories()).rejects.toMatchObject({ status: 502 })
    await expect(catalogAdminApi.listProducts()).rejects.toMatchObject({ status: 502 })
    await expect(
      catalogAdminApi.importProducts(new File(['xlsx'], 'catalog.xlsx'), true),
    ).rejects.toMatchObject({ status: 502 })
  })

  it('rejects a malformed staged-image upload contract', async () => {
    const fetchMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          data: {
            object_key: 'products/staged/LATTE-01/image.webp',
            mime_type: 'image/webp',
            size_bytes: '100',
            width: 300,
            height: 300,
            expires_at: '2026-07-22T00:00:00Z',
          },
        }),
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      catalogAdminApi.uploadStagedProductImage(
        'LATTE-01',
        new File(['image'], 'latte.webp', { type: 'image/webp' }),
      ),
    ).rejects.toMatchObject({ status: 502 })
  })

  it('updates image metadata and wires cleanup reporting endpoints', async () => {
    const cleanupJob = {
      id: 31,
      created_by: 1,
      object_key: 'products/9/gallery/orphan.webp',
      reason: 'image_deleted',
      status: 'failed',
      attempts: 2,
      last_error: 'storage unavailable',
      not_before: null,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:01:00Z',
      completed_at: null,
    }
    const fetchMock = vi
      .fn(async (...args: Parameters<typeof fetch>) => {
        void args
        return new Response(JSON.stringify({ data: product }))
      })
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: product })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: [cleanupJob] })))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ data: { ...cleanupJob, status: 'completed' } })),
      )
    vi.stubGlobal('fetch', fetchMock)

    await catalogAdminApi.updateProductImage(9, 4, { alt_text: '新说明', sort_order: 3 })
    const jobs = await catalogAdminApi.listObjectCleanupJobs('failed')
    const retried = await catalogAdminApi.retryObjectCleanupJob(31)

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/admin/products/9/images/4')
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('PATCH')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/admin/object-cleanup-jobs?status=failed')
    expect(fetchMock.mock.calls[2]?.[0]).toBe('/api/v1/admin/object-cleanup-jobs/31/retry')
    expect(jobs[0]?.status).toBe('failed')
    expect(retried.status).toBe('completed')
  })
})
