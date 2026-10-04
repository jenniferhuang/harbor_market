import { ApiError, apiClient } from './client'

export interface ShopStore {
  name: string
  phone: string
  address: string
  latitude: number | null
  longitude: number | null
  owner_customer_id: number | null
}

export interface ShopCustomer { id: number; nickname: string }
export interface ShopPage { page: number; page_size: number }
export const SHOP_IMAGE_MAX_BYTES = 5 * 1024 * 1024
export const SHOP_VIDEO_MAX_BYTES = 10 * 1024 * 1024
export type ShopMediaKind = 'carousel' | 'category' | 'announcement'
export interface ShopMedia {
  id: number
  kind: ShopMediaKind
  category_id: number | null
  title: string
  sort_order: number
  is_active: boolean
  url: string
  media_type: 'image' | 'video'
}
export interface ShopMediaInput {
  kind: ShopMediaKind
  category_id: number | null
  title: string
  sort_order: number
  is_active: boolean
}

export interface ShopCouponInput {
  title: string
  min_spend_cents: number
  discount_cents: number
  starts_at: string
  expires_at: string
  is_active: boolean
}
export interface ShopCoupon extends ShopCouponInput { id: number }

export interface HistoricalOrderItemInput {
  product_code: string
  quantity: number
  unit_price_cents: number
}
export interface HistoricalOrderInput {
  external_reference: string
  customer_id: number | null
  completed_at: string
  items: HistoricalOrderItemInput[]
}
export interface HistoricalOrderItem extends HistoricalOrderItemInput { product_name?: string }
export interface HistoricalOrder extends Omit<HistoricalOrderInput, 'items'> {
  id: number
  status: 'completed' | 'voided'
  total_cents: number
  items: HistoricalOrderItem[]
}

function invalidResponse(): never {
  throw new ApiError(502, '服务器返回的商家数据异常，请刷新后重试。')
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : invalidResponse()
}

function data(payload: unknown): unknown {
  const envelope = record(payload)
  return Object.hasOwn(envelope, 'data') ? envelope.data : invalidResponse()
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : invalidResponse()
}

function integer(value: unknown, minimum = 0): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum
    ? value : invalidResponse()
}

function nullableId(value: unknown): number | null {
  return value === null ? null : integer(value, 1)
}

function coordinate(value: unknown, limit: number): number | null {
  return value === null ? null : typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= limit
    ? value : invalidResponse()
}

function boolean(value: unknown): boolean {
  return typeof value === 'boolean' ? value : invalidResponse()
}

function date(value: unknown): string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value)) ? value : invalidResponse()
}

function list<T>(payload: unknown, parse: (value: unknown) => T): T[] {
  const value = data(payload)
  const items = Array.isArray(value) ? value : record(value).items
  return Array.isArray(items) ? items.map(parse) : invalidResponse()
}

function store(value: unknown): ShopStore {
  const item = record(value)
  return {
    name: text(item.name), phone: text(item.phone), address: text(item.address),
    latitude: coordinate(item.latitude, 90), longitude: coordinate(item.longitude, 180),
    owner_customer_id: nullableId(item.owner_customer_id),
  }
}

function customer(value: unknown): ShopCustomer {
  const item = record(value)
  return { id: integer(item.id, 1), nickname: text(item.nickname) }
}

function media(value: unknown): ShopMedia {
  const item = record(value)
  const kind = item.kind
  if (kind !== 'carousel' && kind !== 'category' && kind !== 'announcement') invalidResponse()
  const rawType = text(item.media_type)
  const mediaType = rawType === 'image' || rawType.startsWith('image/') ? 'image'
    : rawType === 'video' || rawType === 'video/mp4' ? 'video' : invalidResponse()
  return {
    id: integer(item.id, 1), kind, category_id: nullableId(item.category_id),
    title: text(item.title), sort_order: integer(item.sort_order, -(2 ** 31)),
    is_active: boolean(item.is_active), url: text(item.url), media_type: mediaType,
  }
}

function coupon(value: unknown): ShopCoupon {
  const item = record(value)
  return {
    id: integer(item.id, 1), title: text(item.title), min_spend_cents: integer(item.min_spend_cents, 1),
    discount_cents: integer(item.discount_cents, 1), starts_at: date(item.starts_at),
    expires_at: date(item.expires_at), is_active: boolean(item.is_active),
  }
}

function order(value: unknown): HistoricalOrder {
  const item = record(value)
  if (item.status !== 'completed' && item.status !== 'voided') invalidResponse()
  if (!Array.isArray(item.items)) invalidResponse()
  return {
    id: integer(item.id, 1), external_reference: text(item.external_reference),
    customer_id: nullableId(item.customer_id), completed_at: date(item.completed_at),
    status: item.status, total_cents: integer(item.total_cents),
    items: item.items.map((value) => {
      const detail = record(value)
      return {
        product_code: text(detail.product_code), quantity: integer(detail.quantity, 1),
        unit_price_cents: integer(detail.unit_price_cents),
        ...(typeof detail.product_name === 'string' ? { product_name: detail.product_name } : {}),
      }
    }),
  }
}

const base = '/api/v1/admin/shop' as const

function pageQuery(page: ShopPage): string {
  return `?${new URLSearchParams({ page: String(page.page), page_size: String(page.page_size) })}`
}

export const shopAdminApi = {
  async getStore(): Promise<ShopStore> {
    return store(data(await apiClient.get<unknown>(`${base}/store`)))
  },
  async updateStore(input: ShopStore): Promise<ShopStore> {
    return store(data(await apiClient.patch<unknown>(`${base}/store`, input)))
  },
  async listCustomers(page: ShopPage = { page: 1, page_size: 100 }): Promise<ShopCustomer[]> {
    return list(await apiClient.get<unknown>(`${base}/customers${pageQuery(page)}`), customer)
  },
  async listMedia(page: ShopPage = { page: 1, page_size: 100 }): Promise<ShopMedia[]> {
    return list(await apiClient.get<unknown>(`${base}/media${pageQuery(page)}`), media)
  },
  async uploadMedia(file: File, input: ShopMediaInput): Promise<ShopMedia> {
    const form = new FormData()
    form.append('file', file)
    form.append('kind', input.kind)
    if (input.category_id !== null) form.append('category_id', String(input.category_id))
    form.append('title', input.title)
    form.append('sort_order', String(input.sort_order))
    form.append('is_active', String(input.is_active))
    return media(data(await apiClient.postForm<unknown>(`${base}/media`, form)))
  },
  async updateMedia(id: number, input: Pick<ShopMediaInput, 'title' | 'sort_order' | 'is_active'>): Promise<ShopMedia> {
    return media(data(await apiClient.patch<unknown>(`${base}/media/${id}`, input)))
  },
  async deleteMedia(id: number): Promise<void> {
    await apiClient.delete(`${base}/media/${id}`)
  },
  async listCoupons(page: ShopPage = { page: 1, page_size: 100 }): Promise<ShopCoupon[]> {
    return list(await apiClient.get<unknown>(`${base}/coupons${pageQuery(page)}`), coupon)
  },
  async createCoupon(input: ShopCouponInput): Promise<ShopCoupon> {
    return coupon(data(await apiClient.post<unknown>(`${base}/coupons`, input)))
  },
  async updateCoupon(id: number, input: ShopCouponInput): Promise<ShopCoupon> {
    return coupon(data(await apiClient.patch<unknown>(`${base}/coupons/${id}`, input)))
  },
  async listOrders(page: ShopPage = { page: 1, page_size: 100 }): Promise<HistoricalOrder[]> {
    return list(await apiClient.get<unknown>(`${base}/orders${pageQuery(page)}`), order)
  },
  async createOrder(input: HistoricalOrderInput): Promise<HistoricalOrder> {
    return order(data(await apiClient.post<unknown>(`${base}/orders`, input)))
  },
  async voidOrder(id: number): Promise<HistoricalOrder> {
    return order(data(await apiClient.post<unknown>(`${base}/orders/${id}/void`)))
  },
}
