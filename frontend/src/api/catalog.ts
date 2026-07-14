import { ApiError, apiClient } from './client'

export type ProductStatus = 'draft' | 'published' | 'archived'
export type StockStatus = 'in_stock' | 'out_of_stock' | 'preorder'
export type ProductImageType = 'cover' | 'gallery' | 'detail'

export interface SpecificationOption {
  code: string
  name: string
  price_delta_cents: number
  sort: number
  is_default: boolean
}

export interface ProductSpecification {
  code: string
  name: string
  selection_mode: 'single' | 'multiple'
  required: boolean
  min_select: number
  max_select: number
  options: SpecificationOption[]
}

export interface Category {
  id: number
  code: string
  name: string
  description: string | null
  parent_id: number | null
  sort_order: number
  is_active: boolean
  created_at?: string
  updated_at?: string
}

export interface CategoryInput {
  code: string
  name: string
  description: string | null
  parent_id: number | null
  sort_order: number
  is_active: boolean
}

export interface ProductSkuInput {
  sku_code: string
  name: string
  price_cents: number
  market_price_cents: number | null
  stock_quantity: number
  attributes: Record<string, string>
  is_default: boolean
  is_active: boolean
  sort_order: number
}

export interface ProductSku extends ProductSkuInput {
  id: number
}

export interface ProductImage {
  id: number
  product_id?: number
  object_key: string
  image_type: ProductImageType
  alt_text: string | null
  sort_order: number
  mime_type?: string
  size_bytes?: number
  width?: number
  height?: number
  url?: string
  media_url?: string
  created_at?: string
}

export interface Product {
  id: number
  product_code: string
  name: string
  subtitle: string | null
  category_id: number
  category?: Category | null
  status: ProductStatus
  base_price_cents: number
  market_price_cents: number | null
  currency: string
  unit: string
  stock_status: StockStatus
  inventory_count: number | null
  featured: boolean
  sort_order: number
  tags: string[]
  selling_points: string[]
  description: string
  ingredients: string | null
  allergen_info: string | null
  specifications: ProductSpecification[]
  skus?: ProductSku[]
  images?: ProductImage[]
  created_at?: string
  updated_at?: string
}

export interface ProductInput {
  product_code: string
  name: string
  subtitle: string | null
  category_id: number
  status: ProductStatus
  base_price_cents: number
  market_price_cents: number | null
  currency: 'CNY'
  unit: string
  stock_status: StockStatus
  inventory_count: number | null
  featured: boolean
  sort_order: number
  tags: string[]
  selling_points: string[]
  description: string
  ingredients: string | null
  allergen_info: string | null
  specifications: ProductSpecification[]
  skus: ProductSkuInput[]
}

export interface ProductFilters {
  q?: string
  status?: ProductStatus | ''
  category_id?: number
  page?: number
  page_size?: number
}

export interface ProductPage {
  items: Product[]
  total: number
  page: number
  page_size: number
}

export interface ImportIssue {
  sheet?: string
  row?: number
  field?: string
  message: string
}

export interface ImportResult {
  id?: number
  job_id?: number
  status?: string
  dry_run: boolean
  valid?: boolean
  summary: Record<string, unknown>
  errors: ImportIssue[]
}

export interface ImportJob {
  id: number
  status: string
  original_filename: string
  workbook_sha256: string
  idempotency_key: string | null
  dry_run: boolean
  summary: Record<string, unknown>
  errors: ImportIssue[]
  created_at: string
  completed_at: string | null
}

export interface StagedProductImage {
  object_key: string
  mime_type: string
  size_bytes: number
  width: number
  height: number
  expires_at: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function unwrapData(payload: unknown): unknown {
  if (!isRecord(payload)) return payload
  return 'data' in payload ? payload.data : payload
}

function unwrapEntity<T>(payload: unknown, label: string): T {
  const value = unwrapData(payload)
  if (!isRecord(value)) throw new ApiError(502, `The server returned an invalid ${label} response.`)
  return value as T
}

function unwrapItems<T>(payload: unknown): T[] {
  const value = unwrapData(payload)
  if (Array.isArray(value)) return value as T[]
  if (isRecord(value) && Array.isArray(value.items)) return value.items as T[]
  return []
}

function productValue(value: unknown): Product {
  if (!isRecord(value)) throw new ApiError(502, 'The server returned an invalid product response.')
  const category = isRecord(value.category) ? value.category : undefined
  const categoryId =
    typeof value.category_id === 'number'
      ? value.category_id
      : typeof category?.id === 'number'
        ? category.id
        : undefined
  if (categoryId === undefined) {
    throw new ApiError(502, 'The server returned a product without a category.')
  }
  return { ...value, category_id: categoryId } as unknown as Product
}

function unwrapProduct(payload: unknown): Product {
  return productValue(unwrapData(payload))
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function productPage(payload: unknown, requestedPage: number, requestedPageSize: number): ProductPage {
  const value = unwrapData(payload)
  if (Array.isArray(value)) {
    return {
      items: value.map(productValue),
      total: value.length,
      page: requestedPage,
      page_size: requestedPageSize,
    }
  }

  if (!isRecord(value)) {
    return { items: [], total: 0, page: requestedPage, page_size: requestedPageSize }
  }

  const items = Array.isArray(value.items) ? value.items.map(productValue) : []
  return {
    items,
    total: numberValue(value.total, items.length),
    page: numberValue(value.page, requestedPage),
    page_size: numberValue(value.page_size, requestedPageSize),
  }
}

function importResult(payload: unknown, dryRun: boolean): ImportResult {
  const value = unwrapData(payload)
  if (!isRecord(value)) {
    return { dry_run: dryRun, summary: {}, errors: [] }
  }

  const rawErrors = Array.isArray(value.errors) ? value.errors : []
  const errors = rawErrors.flatMap((issue): ImportIssue[] => {
    if (typeof issue === 'string') return [{ message: issue }]
    if (!isRecord(issue) || typeof issue.message !== 'string') return []
    return [
      {
        ...(typeof issue.sheet === 'string' ? { sheet: issue.sheet } : {}),
        ...(typeof issue.row === 'number' ? { row: issue.row } : {}),
        ...(typeof issue.field === 'string' ? { field: issue.field } : {}),
        message: issue.message,
      },
    ]
  })

  return {
    ...(typeof value.id === 'number' ? { id: value.id } : {}),
    ...(typeof value.job_id === 'number' ? { job_id: value.job_id } : {}),
    ...(typeof value.status === 'string' ? { status: value.status } : {}),
    dry_run: typeof value.dry_run === 'boolean' ? value.dry_run : dryRun,
    ...(typeof value.valid === 'boolean' ? { valid: value.valid } : {}),
    summary: isRecord(value.summary) ? value.summary : {},
    errors,
  }
}

function importJob(value: unknown): ImportJob | null {
  if (!isRecord(value) || typeof value.id !== 'number' || typeof value.status !== 'string') {
    return null
  }
  const parsed = importResult(value, Boolean(value.dry_run))
  return {
    id: value.id,
    status: value.status,
    original_filename:
      typeof value.original_filename === 'string' ? value.original_filename : 'products.xlsx',
    workbook_sha256: typeof value.workbook_sha256 === 'string' ? value.workbook_sha256 : '',
    idempotency_key: typeof value.idempotency_key === 'string' ? value.idempotency_key : null,
    dry_run: Boolean(value.dry_run),
    summary: parsed.summary,
    errors: parsed.errors,
    created_at: typeof value.created_at === 'string' ? value.created_at : '',
    completed_at: typeof value.completed_at === 'string' ? value.completed_at : null,
  }
}

function queryPath(base: `/api/${string}`, filters: ProductFilters): `/api/${string}` {
  const query = new URLSearchParams()
  if (filters.q?.trim()) query.set('q', filters.q.trim())
  if (filters.status) query.set('status', filters.status)
  if (filters.category_id !== undefined) query.set('category_id', String(filters.category_id))
  if (filters.page !== undefined) query.set('page', String(filters.page))
  if (filters.page_size !== undefined) query.set('page_size', String(filters.page_size))
  const suffix = query.toString()
  return `${base}${suffix ? `?${suffix}` : ''}` as `/api/${string}`
}

const base = '/api/v1/admin' as const

export const catalogAdminApi = {
  async listCategories(): Promise<Category[]> {
    return unwrapItems<Category>(await apiClient.get<unknown>(`${base}/categories`))
  },

  async createCategory(input: CategoryInput): Promise<Category> {
    return unwrapEntity<Category>(
      await apiClient.post<unknown>(`${base}/categories`, input),
      'category',
    )
  },

  async updateCategory(id: number, input: Partial<CategoryInput>): Promise<Category> {
    return unwrapEntity<Category>(
      await apiClient.patch<unknown>(`${base}/categories/${id}`, input),
      'category',
    )
  },

  async deleteCategory(id: number): Promise<void> {
    await apiClient.delete<unknown>(`${base}/categories/${id}`)
  },

  async listProducts(filters: ProductFilters = {}): Promise<ProductPage> {
    const page = filters.page ?? 1
    const pageSize = filters.page_size ?? 20
    const payload = await apiClient.get<unknown>(queryPath(`${base}/products`, filters))
    return productPage(payload, page, pageSize)
  },

  async createProduct(input: ProductInput): Promise<Product> {
    return unwrapProduct(await apiClient.post<unknown>(`${base}/products`, input))
  },

  async getProduct(id: number): Promise<Product> {
    return unwrapProduct(await apiClient.get<unknown>(`${base}/products/${id}`))
  },

  async updateProduct(id: number, input: Partial<ProductInput>): Promise<Product> {
    return unwrapProduct(await apiClient.patch<unknown>(`${base}/products/${id}`, input))
  },

  async deleteProduct(id: number): Promise<void> {
    await apiClient.delete<unknown>(`${base}/products/${id}`)
  },

  async uploadProductImage(
    productId: number,
    input: { file: File; image_type: ProductImageType; alt_text: string; sort_order: number },
  ): Promise<Product> {
    const form = new FormData()
    form.append('file', input.file)
    form.append('image_type', input.image_type)
    form.append('alt_text', input.alt_text)
    form.append('sort_order', String(input.sort_order))
    return unwrapProduct(
      await apiClient.postForm<unknown>(`${base}/products/${productId}/images`, form),
    )
  },

  async deleteProductImage(productId: number, imageId: number): Promise<Product> {
    return unwrapProduct(
      await apiClient.delete<unknown>(`${base}/products/${productId}/images/${imageId}`),
    )
  },

  async uploadStagedProductImage(productCode: string, file: File): Promise<StagedProductImage> {
    const form = new FormData()
    form.append('product_code', productCode)
    form.append('file', file)
    return unwrapEntity<StagedProductImage>(
      await apiClient.postForm<unknown>(`${base}/product-images/staging`, form),
      'staged product image',
    )
  },

  async deleteStagedProductImage(objectKey: string): Promise<void> {
    const encodedKey = objectKey.split('/').map(encodeURIComponent).join('/')
    await apiClient.delete<unknown>(`${base}/product-images/staging/${encodedKey}`)
  },

  downloadTemplate(): Promise<Blob> {
    return apiClient.getBlob(`${base}/products/template.xlsx`)
  },

  exportProducts(): Promise<Blob> {
    return apiClient.getBlob(`${base}/products/export.xlsx`)
  },

  async importProducts(
    file: File,
    dryRun: boolean,
    idempotencyKey?: string,
  ): Promise<ImportResult> {
    const form = new FormData()
    form.append('file', file)
    const payload = await apiClient.postForm<unknown>(
      `${base}/products/import?dry_run=${String(dryRun)}`,
      form,
      idempotencyKey ? { 'X-Idempotency-Key': idempotencyKey } : undefined,
    )
    return importResult(payload, dryRun)
  },

  async listImportJobs(limit = 20): Promise<ImportJob[]> {
    const values = unwrapData(
      await apiClient.get<unknown>(`${base}/import-jobs?limit=${encodeURIComponent(String(limit))}`),
    )
    return Array.isArray(values) ? values.flatMap((value) => importJob(value) ?? []) : []
  },
}

export type CatalogAdminApi = typeof catalogAdminApi
