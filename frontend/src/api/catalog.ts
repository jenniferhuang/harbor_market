import { ApiError, apiClient } from './client'

export type ProductStatus = 'draft' | 'published' | 'archived'
export type StockStatus = 'in_stock' | 'out_of_stock' | 'preorder'
export type ProductImageType = 'cover' | 'gallery' | 'detail'
export type ObjectCleanupStatus = 'intent' | 'pending' | 'processing' | 'completed' | 'failed'

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

export interface ProductImageUpdate {
  alt_text?: string | null
  sort_order?: number
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
  job_id: number
  dry_run: boolean
  valid: boolean
  summary: Record<string, unknown>
  errors: ImportIssue[]
  promoted_staging_keys: string[]
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
  promoted_staging_keys: string[]
  created_at: string
  completed_at: string | null
}

export interface ObjectCleanupJob {
  id: number
  created_by: number | null
  object_key: string
  reason: string
  status: ObjectCleanupStatus
  attempts: number
  last_error: string | null
  not_before: string | null
  created_at: string
  updated_at: string
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

function invalidContract(label: string): never {
  throw new ApiError(502, `The server returned an invalid ${label} response.`)
}

function recordValue(value: unknown, label: string): Record<string, unknown> {
  return isRecord(value) ? value : invalidContract(label)
}

function stringField(value: Record<string, unknown>, key: string, label: string): string {
  return typeof value[key] === 'string' ? value[key] : invalidContract(label)
}

function integerField(value: Record<string, unknown>, key: string, label: string): number {
  const candidate = value[key]
  return typeof candidate === 'number' && Number.isSafeInteger(candidate)
    ? candidate
    : invalidContract(label)
}

function booleanField(value: Record<string, unknown>, key: string, label: string): boolean {
  return typeof value[key] === 'boolean' ? value[key] : invalidContract(label)
}

function nullableStringField(
  value: Record<string, unknown>,
  key: string,
  label: string,
): string | null {
  const candidate = value[key]
  return candidate === null || typeof candidate === 'string' ? candidate : invalidContract(label)
}

function nullableIntegerField(
  value: Record<string, unknown>,
  key: string,
  label: string,
): number | null {
  const candidate = value[key]
  return candidate === null || (typeof candidate === 'number' && Number.isSafeInteger(candidate))
    ? candidate
    : invalidContract(label)
}

function enumField<const T extends string>(
  value: Record<string, unknown>,
  key: string,
  allowed: readonly T[],
  label: string,
): T {
  const candidate = value[key]
  return typeof candidate === 'string' && allowed.includes(candidate as T)
    ? candidate as T
    : invalidContract(label)
}

function stringList(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    return invalidContract(label)
  }
  return value
}

function unwrapData(payload: unknown): unknown {
  if (!isRecord(payload)) return payload
  return 'data' in payload ? payload.data : payload
}

function categoryValue(value: unknown): Category {
  const item = recordValue(value, 'category')
  return {
    id: integerField(item, 'id', 'category'),
    code: stringField(item, 'code', 'category'),
    name: stringField(item, 'name', 'category'),
    description: nullableStringField(item, 'description', 'category'),
    parent_id: nullableIntegerField(item, 'parent_id', 'category'),
    sort_order: integerField(item, 'sort_order', 'category'),
    is_active: booleanField(item, 'is_active', 'category'),
    created_at: stringField(item, 'created_at', 'category'),
    updated_at: stringField(item, 'updated_at', 'category'),
  }
}

function specificationOptionValue(value: unknown): SpecificationOption {
  const item = recordValue(value, 'product specification option')
  return {
    code: stringField(item, 'code', 'product specification option'),
    name: stringField(item, 'name', 'product specification option'),
    price_delta_cents: integerField(item, 'price_delta_cents', 'product specification option'),
    sort: integerField(item, 'sort', 'product specification option'),
    is_default: booleanField(item, 'is_default', 'product specification option'),
  }
}

function productSpecificationValue(value: unknown): ProductSpecification {
  const item = recordValue(value, 'product specification')
  if (!Array.isArray(item.options)) invalidContract('product specification')
  return {
    code: stringField(item, 'code', 'product specification'),
    name: stringField(item, 'name', 'product specification'),
    selection_mode: enumField(item, 'selection_mode', ['single', 'multiple'], 'product specification'),
    required: booleanField(item, 'required', 'product specification'),
    min_select: integerField(item, 'min_select', 'product specification'),
    max_select: integerField(item, 'max_select', 'product specification'),
    options: item.options.map(specificationOptionValue),
  }
}

function productSkuValue(value: unknown): ProductSku {
  const item = recordValue(value, 'product SKU')
  const attributes = recordValue(item.attributes, 'product SKU')
  if (Object.values(attributes).some((attribute) => typeof attribute !== 'string')) {
    invalidContract('product SKU')
  }
  return {
    id: integerField(item, 'id', 'product SKU'),
    sku_code: stringField(item, 'sku_code', 'product SKU'),
    name: stringField(item, 'name', 'product SKU'),
    price_cents: integerField(item, 'price_cents', 'product SKU'),
    market_price_cents: nullableIntegerField(item, 'market_price_cents', 'product SKU'),
    stock_quantity: integerField(item, 'stock_quantity', 'product SKU'),
    attributes: attributes as Record<string, string>,
    is_default: booleanField(item, 'is_default', 'product SKU'),
    is_active: booleanField(item, 'is_active', 'product SKU'),
    sort_order: integerField(item, 'sort_order', 'product SKU'),
  }
}

function productImageValue(value: unknown): ProductImage {
  const item = recordValue(value, 'product image')
  return {
    id: integerField(item, 'id', 'product image'),
    object_key: stringField(item, 'object_key', 'product image'),
    image_type: enumField(item, 'image_type', ['cover', 'gallery', 'detail'], 'product image'),
    alt_text: nullableStringField(item, 'alt_text', 'product image'),
    sort_order: integerField(item, 'sort_order', 'product image'),
    mime_type: nullableStringField(item, 'mime_type', 'product image') ?? undefined,
    size_bytes: nullableIntegerField(item, 'size_bytes', 'product image') ?? undefined,
    width: nullableIntegerField(item, 'width', 'product image') ?? undefined,
    height: nullableIntegerField(item, 'height', 'product image') ?? undefined,
    url: stringField(item, 'url', 'product image'),
    created_at: stringField(item, 'created_at', 'product image'),
  }
}

function productValue(value: unknown): Product {
  const item = recordValue(value, 'product')
  const category = categoryValue(item.category)
  if (!Array.isArray(item.specifications) || !Array.isArray(item.skus) || !Array.isArray(item.images)) {
    invalidContract('product')
  }
  return {
    id: integerField(item, 'id', 'product'),
    product_code: stringField(item, 'product_code', 'product'),
    name: stringField(item, 'name', 'product'),
    subtitle: nullableStringField(item, 'subtitle', 'product'),
    category_id: category.id,
    category,
    status: enumField(item, 'status', ['draft', 'published', 'archived'], 'product'),
    base_price_cents: integerField(item, 'base_price_cents', 'product'),
    market_price_cents: nullableIntegerField(item, 'market_price_cents', 'product'),
    currency: stringField(item, 'currency', 'product'),
    unit: stringField(item, 'unit', 'product'),
    stock_status: enumField(item, 'stock_status', ['in_stock', 'out_of_stock', 'preorder'], 'product'),
    inventory_count: nullableIntegerField(item, 'inventory_count', 'product'),
    featured: booleanField(item, 'featured', 'product'),
    sort_order: integerField(item, 'sort_order', 'product'),
    tags: stringList(item.tags, 'product'),
    selling_points: stringList(item.selling_points, 'product'),
    description: stringField(item, 'description', 'product'),
    ingredients: nullableStringField(item, 'ingredients', 'product'),
    allergen_info: nullableStringField(item, 'allergen_info', 'product'),
    specifications: item.specifications.map(productSpecificationValue),
    skus: item.skus.map(productSkuValue),
    images: item.images.map(productImageValue),
    created_at: stringField(item, 'created_at', 'product'),
    updated_at: stringField(item, 'updated_at', 'product'),
  }
}

function unwrapProduct(payload: unknown): Product {
  return productValue(unwrapData(payload))
}

function productPage(payload: unknown, requestedPage: number, requestedPageSize: number): ProductPage {
  const value = unwrapData(payload)
  const page = recordValue(value, 'product list')
  if (!Array.isArray(page.items)) invalidContract('product list')
  return {
    items: page.items.map(productValue),
    total: integerField(page, 'total', 'product list'),
    page: integerField(page, 'page', 'product list') || requestedPage,
    page_size: integerField(page, 'page_size', 'product list') || requestedPageSize,
  }
}

function summaryValue(value: unknown, label: string): Record<string, unknown> {
  const summary = recordValue(value, label)
  if (Object.values(summary).some((item) => typeof item !== 'number' || !Number.isFinite(item))) {
    invalidContract(label)
  }
  return summary
}

function importIssues(value: unknown, label: string): ImportIssue[] {
  if (!Array.isArray(value)) invalidContract(label)
  return value.map((issue) => {
    const item = recordValue(issue, label)
    return {
      sheet: stringField(item, 'sheet', label),
      row: integerField(item, 'row', label),
      field: stringField(item, 'field', label),
      message: stringField(item, 'message', label),
    }
  })
}

function importResult(payload: unknown, dryRun: boolean): ImportResult {
  const value = recordValue(unwrapData(payload), 'catalog import')
  const responseDryRun = booleanField(value, 'dry_run', 'catalog import')
  if (responseDryRun !== dryRun) invalidContract('catalog import')
  return {
    job_id: integerField(value, 'job_id', 'catalog import'),
    dry_run: responseDryRun,
    valid: booleanField(value, 'valid', 'catalog import'),
    summary: summaryValue(value.summary, 'catalog import'),
    errors: importIssues(value.errors, 'catalog import'),
    promoted_staging_keys: stringList(value.promoted_staging_keys, 'catalog import'),
  }
}

function importJobValue(value: unknown): ImportJob {
  const item = recordValue(value, 'import job')
  return {
    id: integerField(item, 'id', 'import job'),
    status: stringField(item, 'status', 'import job'),
    original_filename: stringField(item, 'original_filename', 'import job'),
    workbook_sha256: stringField(item, 'workbook_sha256', 'import job'),
    idempotency_key: nullableStringField(item, 'idempotency_key', 'import job'),
    dry_run: booleanField(item, 'dry_run', 'import job'),
    summary: summaryValue(item.summary, 'import job'),
    errors: importIssues(item.errors, 'import job'),
    promoted_staging_keys: stringList(item.promoted_staging_keys, 'import job'),
    created_at: stringField(item, 'created_at', 'import job'),
    completed_at: nullableStringField(item, 'completed_at', 'import job'),
  }
}

function cleanupJobValue(value: unknown): ObjectCleanupJob {
  const item = recordValue(value, 'object cleanup job')
  return {
    id: integerField(item, 'id', 'object cleanup job'),
    created_by: nullableIntegerField(item, 'created_by', 'object cleanup job'),
    object_key: stringField(item, 'object_key', 'object cleanup job'),
    reason: stringField(item, 'reason', 'object cleanup job'),
    status: enumField(
      item,
      'status',
      ['intent', 'pending', 'processing', 'completed', 'failed'],
      'object cleanup job',
    ),
    attempts: integerField(item, 'attempts', 'object cleanup job'),
    last_error: nullableStringField(item, 'last_error', 'object cleanup job'),
    not_before: nullableStringField(item, 'not_before', 'object cleanup job'),
    created_at: stringField(item, 'created_at', 'object cleanup job'),
    updated_at: stringField(item, 'updated_at', 'object cleanup job'),
    completed_at: nullableStringField(item, 'completed_at', 'object cleanup job'),
  }
}

function stagedProductImageValue(value: unknown): StagedProductImage {
  const item = recordValue(value, 'staged product image')
  return {
    object_key: stringField(item, 'object_key', 'staged product image'),
    mime_type: stringField(item, 'mime_type', 'staged product image'),
    size_bytes: integerField(item, 'size_bytes', 'staged product image'),
    width: integerField(item, 'width', 'staged product image'),
    height: integerField(item, 'height', 'staged product image'),
    expires_at: stringField(item, 'expires_at', 'staged product image'),
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
    const values = unwrapData(await apiClient.get<unknown>(`${base}/categories`))
    if (!Array.isArray(values)) invalidContract('category list')
    return values.map(categoryValue)
  },

  async createCategory(input: CategoryInput): Promise<Category> {
    return categoryValue(
      unwrapData(await apiClient.post<unknown>(`${base}/categories`, input)),
    )
  },

  async updateCategory(id: number, input: Partial<CategoryInput>): Promise<Category> {
    return categoryValue(
      unwrapData(await apiClient.patch<unknown>(`${base}/categories/${id}`, input)),
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

  async updateProductImage(
    productId: number,
    imageId: number,
    input: ProductImageUpdate,
  ): Promise<Product> {
    return unwrapProduct(
      await apiClient.patch<unknown>(`${base}/products/${productId}/images/${imageId}`, input),
    )
  },

  async uploadStagedProductImage(productCode: string, file: File): Promise<StagedProductImage> {
    const form = new FormData()
    form.append('product_code', productCode)
    form.append('file', file)
    return stagedProductImageValue(
      unwrapData(await apiClient.postForm<unknown>(`${base}/product-images/staging`, form)),
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
    if (!Array.isArray(values)) invalidContract('import job list')
    return values.map(importJobValue)
  },

  async getImportJob(id: number): Promise<ImportJob> {
    return importJobValue(
      unwrapData(await apiClient.get<unknown>(`${base}/import-jobs/${id}`)),
    )
  },

  async listObjectCleanupJobs(status?: ObjectCleanupStatus): Promise<ObjectCleanupJob[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    const values = unwrapData(
      await apiClient.get<unknown>(`${base}/object-cleanup-jobs${query}`),
    )
    if (!Array.isArray(values)) invalidContract('object cleanup job list')
    return values.map(cleanupJobValue)
  },

  async retryObjectCleanupJob(id: number): Promise<ObjectCleanupJob> {
    return cleanupJobValue(
      unwrapData(await apiClient.post<unknown>(`${base}/object-cleanup-jobs/${id}/retry`)),
    )
  },
}

export type CatalogAdminApi = typeof catalogAdminApi
