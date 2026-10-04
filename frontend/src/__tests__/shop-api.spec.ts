import { afterEach, describe, expect, it, vi } from 'vitest'
import { shopAdminApi } from '../api/shop'

const store = { name: '港湾咖啡', phone: '123456', address: '厦门', latitude: null, longitude: null, owner_customer_id: null }
const media = { id: 8, kind: 'carousel', category_id: null, title: '店内环境', sort_order: 2, is_active: true, url: '/api/v1/shop/media/8', media_type: 'video' }
const coupon = { id: 7, title: '满30减5', min_spend_cents: 3000, discount_cents: 500, starts_at: '2026-01-01T00:00:00Z', expires_at: '2027-01-01T00:00:00Z', is_active: true }
const orderInput = { external_reference: 'RECEIPT-001', customer_id: 11, completed_at: '2020-01-01T00:00:00Z', items: [{ product_code: 'LATTE-01', quantity: 2, unit_price_cents: 1490 }] }
const order = { ...orderInput, id: 12, status: 'completed', total_cents: 2980 }

function response(data: unknown) { return new Response(JSON.stringify({ data })) }
function mockFetch(...responses: Response[]) {
  const fetchMock = vi.fn<(...args: Parameters<typeof fetch>) => Promise<Response>>()
  responses.forEach((item) => fetchMock.mockResolvedValueOnce(item))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('shop administration API', () => {
  it('uses the store contract and preserves explicit unbinding and cleared coordinates', async () => {
    const fetchMock = mockFetch(response({ ...store, announcement_image_url: null }), response(store))
    expect(await shopAdminApi.getStore()).toEqual(store)
    expect(await shopAdminApi.updateStore(store)).toEqual(store)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/v1/admin/shop/store')
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ method: 'PATCH', credentials: 'include', body: JSON.stringify(store) })
  })

  it('sends multipart metadata and lets the browser supply its boundary', async () => {
    const fetchMock = mockFetch(response(media), response({ ...media, kind: 'category', category_id: 3, media_type: 'image' }))
    const video = new File(['video'], 'shop.mp4', { type: 'video/mp4' })
    await shopAdminApi.uploadMedia(video, { kind: 'carousel', category_id: null, title: '店内环境', sort_order: 2, is_active: true })
    const init = fetchMock.mock.calls[0]?.[1]
    const form = init?.body as FormData
    expect(form.get('file')).toBe(video)
    expect(form.get('kind')).toBe('carousel')
    expect(form.has('category_id')).toBe(false)
    expect(form.get('sort_order')).toBe('2')
    expect(form.get('is_active')).toBe('true')
    expect(new Headers(init?.headers).has('Content-Type')).toBe(false)
    const image = new File(['image'], 'coffee.webp', { type: 'image/webp' })
    await shopAdminApi.uploadMedia(image, { kind: 'category', category_id: 3, title: '', sort_order: 0, is_active: false })
    expect((fetchMock.mock.calls[1]?.[1]?.body as FormData).get('category_id')).toBe('3')
    expect((fetchMock.mock.calls[1]?.[1]?.body as FormData).get('is_active')).toBe('false')
  })

  it('requests bounded pages for customers, coupons, orders, and media', async () => {
    const fetchMock = mockFetch(response([{ id: 11, nickname: '海港顾客' }]), response([coupon]), response([order]), response([media]))
    expect(await shopAdminApi.listCustomers({ page: 3, page_size: 100 })).toEqual([{ id: 11, nickname: '海港顾客' }])
    await shopAdminApi.listCoupons({ page: 2, page_size: 100 })
    await shopAdminApi.listOrders({ page: 4, page_size: 100 })
    await shopAdminApi.listMedia({ page: 5, page_size: 100 })
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      '/api/v1/admin/shop/customers?page=3&page_size=100',
      '/api/v1/admin/shop/coupons?page=2&page_size=100',
      '/api/v1/admin/shop/orders?page=4&page_size=100',
      '/api/v1/admin/shop/media?page=5&page_size=100',
    ])
  })

  it('keeps historical prices and server-calculated totals and uses the void action', async () => {
    const fetchMock = mockFetch(response(order), response({ ...order, status: 'voided' }))
    const created = await shopAdminApi.createOrder(orderInput)
    expect(created.total_cents).toBe(2980)
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual(orderInput)
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).not.toHaveProperty('total_cents')
    expect((await shopAdminApi.voidOrder(12)).status).toBe('voided')
    expect(fetchMock.mock.calls[1]?.[0]).toBe('/api/v1/admin/shop/orders/12/void')
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBeUndefined()
  })

  it('rejects malformed success data rather than enabling forms with empty records', async () => {
    mockFetch(response({ ...store, owner_customer_id: '11' }), response({ ...order, status: 'paid' }), response({ ...coupon, starts_at: 'not a date' }), response({ ...media, media_type: 'html' }))
    await expect(shopAdminApi.getStore()).rejects.toMatchObject({ status: 502, message: expect.stringMatching(/[\u3400-\u9fff]/) })
    await expect(shopAdminApi.createOrder(orderInput)).rejects.toMatchObject({ status: 502 })
    await expect(shopAdminApi.createCoupon(coupon)).rejects.toMatchObject({ status: 502 })
    await expect(shopAdminApi.uploadMedia(new File(['image'], 'image.png'), { kind: 'carousel', category_id: null, title: '', sort_order: 0, is_active: true })).rejects.toMatchObject({ status: 502 })
  })
})
