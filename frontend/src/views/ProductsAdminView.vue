<script setup lang="ts">
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  FileCheck2,
  FileSpreadsheet,
  ImagePlus,
  LayoutGrid,
  LoaderCircle,
  LogOut,
  PackagePlus,
  Pencil,
  RefreshCw,
  Save,
  Search,
  Tags,
  Trash2,
  Upload,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  catalogAdminApi,
  type Category,
  type CategoryInput,
  type ImportJob,
  type ImportResult,
  type ObjectCleanupJob,
  type ObjectCleanupStatus,
  type Product,
  type ProductImage,
  type ProductImageType,
  type ProductImageUpdate,
  type ProductInput,
  type ProductSkuInput,
  type ProductSpecification,
  type ProductStatus,
  type StagedProductImage,
  type StockStatus,
} from '../api/catalog'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/useAuth'
import AppBrand from '../components/AppBrand.vue'

type WorkspaceTab = 'products' | 'categories' | 'excel'
type NoticeKind = 'success' | 'error'

const POSTGRES_INTEGER_MIN = -(2 ** 31)
const POSTGRES_INTEGER_MAX = 2 ** 31 - 1

interface NoticeState {
  kind: NoticeKind
  text: string
}

interface ProductFormState {
  product_code: string
  name: string
  subtitle: string
  category_id: string
  status: ProductStatus
  base_price_yuan: string
  market_price_yuan: string
  unit: string
  stock_status: StockStatus
  inventory_count: string
  featured: boolean
  sort_order: string
  tags: string
  selling_points: string
  description: string
  ingredients: string
  allergen_info: string
  specifications_json: string
  skus_json: string
}

interface CategoryFormState {
  code: string
  name: string
  description: string
  parent_id: string
  sort_order: string
  is_active: boolean
}

const auth = useAuth()
const router = useRouter()

const workspaceTabs: WorkspaceTab[] = ['products', 'categories', 'excel']
const activeTab = ref<WorkspaceTab>('products')
const notice = ref<NoticeState | null>(null)
const isLoggingOut = ref(false)

const categories = ref<Category[]>([])
const categoriesLoading = ref(false)
const categorySaving = ref(false)
const categoryDeletingId = ref<number | null>(null)
const editingCategoryId = ref<number | null>(null)

const products = ref<Product[]>([])
const productsLoading = ref(false)
const productSaving = ref(false)
const productDeletingId = ref<number | null>(null)
const editorOpen = ref(false)
const editingProductId = ref<number | null>(null)
const productDetail = ref<Product | null>(null)
const page = ref(1)
const pageSize = 20
const totalProducts = ref(0)

const filters = reactive({
  q: '',
  status: '' as ProductStatus | '',
  category_id: '',
})

const productForm = reactive<ProductFormState>(emptyProductForm())
const categoryForm = reactive<CategoryFormState>(emptyCategoryForm())

const imageFile = ref<File | null>(null)
const imageType = ref<ProductImageType>('cover')
const imageAltText = ref('')
const imageSortOrder = ref('0')
const imageUploading = ref(false)
const imageEditingId = ref<number | null>(null)
const imageUpdatingId = ref<number | null>(null)
const imageEditAltText = ref('')
const imageEditSortOrder = ref('0')

function handleWorkspaceTabKeydown(event: KeyboardEvent) {
  const currentIndex = workspaceTabs.indexOf(activeTab.value)
  let nextIndex: number | null = null
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % workspaceTabs.length
  if (event.key === 'ArrowLeft') {
    nextIndex = (currentIndex - 1 + workspaceTabs.length) % workspaceTabs.length
  }
  if (event.key === 'Home') nextIndex = 0
  if (event.key === 'End') nextIndex = workspaceTabs.length - 1
  if (nextIndex === null) return
  event.preventDefault()
  const nextTab = workspaceTabs[nextIndex]
  if (!nextTab) return
  activeTab.value = nextTab
  requestAnimationFrame(() => document.getElementById(`catalog-tab-${activeTab.value}`)?.focus())
}
const imageDeletingId = ref<number | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)

const downloadBusy = ref<'template' | 'export' | null>(null)
const importFile = ref<File | null>(null)
const importMode = ref<'dry-run' | 'commit' | null>(null)
const importResult = ref<ImportResult | null>(null)
const dryRunApprovedFile = ref<File | null>(null)
const recentImportJobs = ref<ImportJob[]>([])
const importHistoryLoading = ref(false)
const selectedImportJob = ref<ImportJob | null>(null)
const importJobLoadingId = ref<number | null>(null)
const importInput = ref<HTMLInputElement | null>(null)
const importIdempotencyKeys = reactive({ dryRun: '', commit: '' })
const stagingProductCode = ref('')
const stagingFile = ref<File | null>(null)
const stagingUploading = ref(false)
const stagedImages = ref<StagedProductImage[]>([])
const stagingDeletingKey = ref<string | null>(null)
const stagingInput = ref<HTMLInputElement | null>(null)

const cleanupStatus = ref<ObjectCleanupStatus | ''>('failed')
const cleanupJobs = ref<ObjectCleanupJob[]>([])
const cleanupJobsLoading = ref(false)
const cleanupRetryingId = ref<number | null>(null)
let cleanupRequestSequence = 0

const categoryById = computed(() => new Map(categories.value.map((item) => [item.id, item])))
const pageCount = computed(() => Math.max(1, Math.ceil(totalProducts.value / pageSize)))
const sortedImages = computed(() =>
  [...(productDetail.value?.images ?? [])].sort((left, right) => {
    if (left.image_type !== right.image_type) return left.image_type.localeCompare(right.image_type)
    return left.sort_order - right.sort_order
  }),
)
const importSummary = computed(() => Object.entries(importResult.value?.summary ?? {}))
const selectedImportSummary = computed(() => Object.entries(selectedImportJob.value?.summary ?? {}))
const canCommitImport = computed(
  () => importFile.value !== null && dryRunApprovedFile.value === importFile.value,
)

function importStatusLabel(job: ImportJob): string {
  const labels: Record<string, string> = {
    pending: '处理中',
    validated: '预检通过',
    completed: '已完成',
    failed: '失败',
  }
  return labels[job.status] ?? job.status
}

async function loadImportJobs() {
  importHistoryLoading.value = true
  try {
    recentImportJobs.value = await catalogAdminApi.listImportJobs(20)
  } catch (error) {
    showNotice('error', messageFor(error, '最近导入任务加载失败。'))
  } finally {
    importHistoryLoading.value = false
  }
}

async function loadImportJobDetail(job: ImportJob) {
  if (importJobLoadingId.value !== null) return
  importJobLoadingId.value = job.id
  clearNotice()
  try {
    selectedImportJob.value = await catalogAdminApi.getImportJob(job.id)
  } catch (error) {
    showNotice('error', messageFor(error, '导入任务详情加载失败。'))
  } finally {
    importJobLoadingId.value = null
  }
}

async function loadCleanupJobs() {
  const requestSequence = ++cleanupRequestSequence
  const requestedStatus = cleanupStatus.value
  cleanupJobsLoading.value = true
  try {
    const jobs = await catalogAdminApi.listObjectCleanupJobs(requestedStatus || undefined)
    if (requestSequence === cleanupRequestSequence && cleanupStatus.value === requestedStatus) {
      cleanupJobs.value = jobs
    }
  } catch (error) {
    if (requestSequence === cleanupRequestSequence) {
      showNotice('error', messageFor(error, '对象清理任务加载失败。'))
    }
  } finally {
    if (requestSequence === cleanupRequestSequence) cleanupJobsLoading.value = false
  }
}

async function retryCleanupJob(job: ObjectCleanupJob) {
  if (cleanupRetryingId.value !== null || job.status !== 'failed') return
  cleanupRetryingId.value = job.id
  clearNotice()
  try {
    const updated = await catalogAdminApi.retryObjectCleanupJob(job.id)
    await loadCleanupJobs()
    if (updated.status === 'completed') {
      showNotice('success', `清理任务 #${job.id} 已完成。`)
    } else {
      showNotice('error', `清理任务 #${job.id} 未完成，当前状态：${updated.status}。`)
    }
  } catch (error) {
    showNotice('error', messageFor(error, `清理任务 #${job.id} 重试失败。`))
    await loadCleanupJobs()
  } finally {
    cleanupRetryingId.value = null
  }
}

function emptyProductForm(): ProductFormState {
  return {
    product_code: '',
    name: '',
    subtitle: '',
    category_id: '',
    status: 'draft',
    base_price_yuan: '',
    market_price_yuan: '',
    unit: '件',
    stock_status: 'in_stock',
    inventory_count: '0',
    featured: false,
    sort_order: '0',
    tags: '',
    selling_points: '',
    description: '',
    ingredients: '',
    allergen_info: '',
    specifications_json: '[]',
    skus_json: '[]',
  }
}

function emptyCategoryForm(): CategoryFormState {
  return {
    code: '',
    name: '',
    description: '',
    parent_id: '',
    sort_order: '0',
    is_active: true,
  }
}

function messageFor(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const fieldMessages = Object.values(error.fieldErrors)
    return fieldMessages.length ? `${error.message} ${fieldMessages.join('；')}` : error.message
  }
  return error instanceof Error && error.message ? error.message : fallback
}

function showNotice(kind: NoticeKind, text: string) {
  notice.value = { kind, text }
}

function clearNotice() {
  notice.value = null
}

function nullable(value: string): string | null {
  const normalized = value.trim()
  return normalized ? normalized : null
}

function parseInteger(
  value: string,
  label: string,
  minimum = 0,
  maximum = Number.MAX_SAFE_INTEGER,
): number {
  const normalized = value.trim()
  if (!/^-?\d+$/.test(normalized)) throw new Error(`${label}必须是整数。`)
  const parsed = Number(normalized)
  if (!Number.isSafeInteger(parsed) || parsed < minimum) {
    throw new Error(`${label}不能小于 ${minimum}。`)
  }
  if (parsed > maximum) throw new Error(`${label}不能大于 ${maximum}。`)
  return parsed
}

function yuanToCents(value: string, label: string, optional = false): number | null {
  const normalized = value.trim()
  if (optional && !normalized) return null
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) {
    throw new Error(`${label}请输入非负金额，最多保留两位小数。`)
  }
  const [yuan = '0', decimals = ''] = normalized.split('.')
  const cents = Number(yuan) * 100 + Number(decimals.padEnd(2, '0'))
  if (!Number.isSafeInteger(cents)) throw new Error(`${label}金额过大。`)
  return cents
}

function centsToYuan(value: number | null | undefined): string {
  if (value === null || value === undefined) return ''
  return (value / 100).toFixed(2)
}

function parseList(value: string): string[] {
  return value
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function parseJsonArray(value: string, label: string): unknown[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error(`${label}不是有效的 JSON。`)
  }
  if (!Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 数组。`)
  return parsed
}

function buildProductInput(): ProductInput {
  const categoryId = parseInteger(productForm.category_id, '类目', 1)
  const specifications = parseJsonArray(
    productForm.specifications_json,
    '规格 JSON',
  ) as ProductSpecification[]
  const skus = parseJsonArray(productForm.skus_json, 'SKU JSON') as ProductSkuInput[]

  return {
    product_code: productForm.product_code.trim(),
    name: productForm.name.trim(),
    subtitle: nullable(productForm.subtitle),
    category_id: categoryId,
    status: productForm.status,
    base_price_cents: yuanToCents(productForm.base_price_yuan, '基础价') ?? 0,
    market_price_cents: yuanToCents(productForm.market_price_yuan, '划线价', true),
    currency: 'CNY',
    unit: productForm.unit.trim(),
    stock_status: productForm.stock_status,
    inventory_count: productForm.inventory_count.trim()
      ? parseInteger(productForm.inventory_count, '库存数量')
      : null,
    featured: productForm.featured,
    sort_order: parseInteger(
      productForm.sort_order,
      '排序值',
      POSTGRES_INTEGER_MIN,
      POSTGRES_INTEGER_MAX,
    ),
    tags: parseList(productForm.tags),
    selling_points: parseList(productForm.selling_points),
    description: productForm.description.trim(),
    ingredients: nullable(productForm.ingredients),
    allergen_info: nullable(productForm.allergen_info),
    specifications,
    skus,
  }
}

function fillProductForm(product: Product) {
  Object.assign(productForm, {
    product_code: product.product_code,
    name: product.name,
    subtitle: product.subtitle ?? '',
    category_id: String(product.category_id),
    status: product.status,
    base_price_yuan: centsToYuan(product.base_price_cents),
    market_price_yuan: centsToYuan(product.market_price_cents),
    unit: product.unit,
    stock_status: product.stock_status,
    inventory_count: product.inventory_count === null ? '' : String(product.inventory_count),
    featured: product.featured,
    sort_order: String(product.sort_order),
    tags: product.tags.join('\n'),
    selling_points: product.selling_points.join('\n'),
    description: product.description,
    ingredients: product.ingredients ?? '',
    allergen_info: product.allergen_info ?? '',
    specifications_json: JSON.stringify(product.specifications ?? [], null, 2),
    skus_json: JSON.stringify(
      (product.skus ?? []).map((sku) => ({
        sku_code: sku.sku_code,
        name: sku.name,
        price_cents: sku.price_cents,
        market_price_cents: sku.market_price_cents,
        stock_quantity: sku.stock_quantity,
        attributes: sku.attributes,
        is_default: sku.is_default,
        is_active: sku.is_active,
        sort_order: sku.sort_order,
      })),
      null,
      2,
    ),
  })
}

async function loadCategories() {
  categoriesLoading.value = true
  try {
    categories.value = await catalogAdminApi.listCategories()
  } catch (error) {
    showNotice('error', messageFor(error, '类目加载失败。'))
  } finally {
    categoriesLoading.value = false
  }
}

async function loadProducts(resetPage = false, preserveNotice = false) {
  if (resetPage) page.value = 1
  productsLoading.value = true
  if (!preserveNotice) clearNotice()
  try {
    const result = await catalogAdminApi.listProducts({
      q: filters.q,
      status: filters.status,
      ...(filters.category_id ? { category_id: Number(filters.category_id) } : {}),
      page: page.value,
      page_size: pageSize,
    })
    products.value = result.items
    totalProducts.value = result.total
    page.value = result.page
  } catch (error) {
    showNotice('error', messageFor(error, '商品加载失败。'))
  } finally {
    productsLoading.value = false
  }
}

function openCreateProduct() {
  clearNotice()
  Object.assign(productForm, emptyProductForm())
  editingProductId.value = null
  productDetail.value = null
  editorOpen.value = true
}

async function openEditProduct(product: Product) {
  clearNotice()
  editorOpen.value = true
  editingProductId.value = product.id
  productDetail.value = product
  fillProductForm(product)

  try {
    const detail = await catalogAdminApi.getProduct(product.id)
    if (editorOpen.value && editingProductId.value === product.id) {
      productDetail.value = detail
      fillProductForm(detail)
    }
  } catch (error) {
    if (editorOpen.value && editingProductId.value === product.id) {
      showNotice('error', messageFor(error, '商品详情加载失败。'))
    }
  }
}

function closeProductEditor() {
  editorOpen.value = false
  editingProductId.value = null
  productDetail.value = null
  imageFile.value = null
  imageEditingId.value = null
  if (imageInput.value) imageInput.value.value = ''
}

async function saveProduct() {
  if (productSaving.value) return
  clearNotice()

  let input: ProductInput
  try {
    input = buildProductInput()
    if (!input.product_code) throw new Error('商品编码不能为空。')
    if (!input.name) throw new Error('商品名称不能为空。')
    if (!input.unit) throw new Error('销售单位不能为空。')
  } catch (error) {
    showNotice('error', messageFor(error, '请检查商品表单。'))
    return
  }

  productSaving.value = true
  try {
    const saved = editingProductId.value
      ? await catalogAdminApi.updateProduct(editingProductId.value, input)
      : await catalogAdminApi.createProduct(input)
    editingProductId.value = saved.id
    productDetail.value = saved
    fillProductForm(saved)
    showNotice('success', '商品已保存。现在可以继续上传或管理图片。')
    await loadProducts(false, true)
    try {
      productDetail.value = await catalogAdminApi.getProduct(saved.id)
      fillProductForm(productDetail.value)
    } catch {
      // The save response already contains enough data to keep editing.
    }
  } catch (error) {
    showNotice('error', messageFor(error, '商品保存失败。'))
  } finally {
    productSaving.value = false
  }
}

async function deleteProduct(product: Product) {
  if (!window.confirm(`确认删除商品「${product.name}」？此操作无法撤销。`)) return
  productDeletingId.value = product.id
  clearNotice()
  try {
    await catalogAdminApi.deleteProduct(product.id)
    if (editingProductId.value === product.id) closeProductEditor()
    showNotice('success', '商品已删除。')
    await loadProducts(false, true)
  } catch (error) {
    if (error instanceof ApiError && error.code === 'cleanup_pending') {
      if (editingProductId.value === product.id) closeProductEditor()
      showNotice('error', '商品记录已删除，但图片清理待后台重试；请勿再次删除。')
      await loadProducts(false, true)
    } else {
      showNotice('error', messageFor(error, '商品删除失败。'))
    }
  } finally {
    productDeletingId.value = null
  }
}

async function changePage(nextPage: number) {
  if (nextPage < 1 || nextPage > pageCount.value || nextPage === page.value) return
  page.value = nextPage
  await loadProducts()
}

function selectImage(event: Event) {
  const target = event.target as HTMLInputElement
  imageFile.value = target.files?.[0] ?? null
}

async function uploadImage() {
  if (!editingProductId.value || !imageFile.value || imageUploading.value) return
  const productId = editingProductId.value
  const selectedFile = imageFile.value
  const selectedImageType = imageType.value
  const selectedAltText = imageAltText.value.trim()
  imageUploading.value = true
  clearNotice()
  try {
    const updatedProduct = await catalogAdminApi.uploadProductImage(productId, {
      file: selectedFile,
      image_type: selectedImageType,
      alt_text: selectedAltText,
      sort_order: parseInteger(
        imageSortOrder.value,
        '图片排序值',
        POSTGRES_INTEGER_MIN,
        POSTGRES_INTEGER_MAX,
      ),
    })
    if (editorOpen.value && editingProductId.value === productId) {
      productDetail.value = updatedProduct
      imageFile.value = null
      imageAltText.value = ''
      imageSortOrder.value = '0'
      if (imageInput.value) imageInput.value.value = ''
      showNotice('success', '图片已上传。')
    }
  } catch (error) {
    if (editorOpen.value && editingProductId.value === productId) {
      showNotice('error', messageFor(error, '图片上传失败。'))
    }
  } finally {
    imageUploading.value = false
  }
}

function beginImageEdit(image: ProductImage) {
  imageEditingId.value = image.id
  imageEditAltText.value = image.alt_text ?? ''
  imageEditSortOrder.value = String(image.sort_order)
}

function cancelImageEdit() {
  imageEditingId.value = null
  imageEditAltText.value = ''
  imageEditSortOrder.value = '0'
}

async function saveImageMetadata(image: ProductImage) {
  if (!editingProductId.value || imageUpdatingId.value !== null) return
  const productId = editingProductId.value
  let input: ProductImageUpdate
  try {
    input = {
      alt_text: nullable(imageEditAltText.value),
      sort_order: parseInteger(
        imageEditSortOrder.value,
        '图片排序值',
        POSTGRES_INTEGER_MIN,
        POSTGRES_INTEGER_MAX,
      ),
    }
  } catch (error) {
    showNotice('error', messageFor(error, '请检查图片信息。'))
    return
  }

  imageUpdatingId.value = image.id
  clearNotice()
  try {
    const updatedProduct = await catalogAdminApi.updateProductImage(
      productId,
      image.id,
      input,
    )
    if (editorOpen.value && editingProductId.value === productId) {
      productDetail.value = updatedProduct
      if (imageEditingId.value === image.id) cancelImageEdit()
      showNotice('success', '图片说明与排序已更新。')
    }
  } catch (error) {
    if (editorOpen.value && editingProductId.value === productId) {
      showNotice('error', messageFor(error, '图片信息更新失败。'))
    }
  } finally {
    imageUpdatingId.value = null
  }
}

async function deleteImage(image: ProductImage) {
  if (!editingProductId.value || !window.confirm('确认删除这张商品图片？')) return
  const productId = editingProductId.value
  imageDeletingId.value = image.id
  clearNotice()
  try {
    const updatedProduct = await catalogAdminApi.deleteProductImage(productId, image.id)
    if (editorOpen.value && editingProductId.value === productId) {
      productDetail.value = updatedProduct
      showNotice('success', '图片已删除。')
    }
  } catch (error) {
    if (error instanceof ApiError && error.code === 'cleanup_pending') {
      if (editorOpen.value && editingProductId.value === productId) {
        showNotice('error', '图片记录已删除，但对象清理待后台重试。')
        const refreshedProduct = await catalogAdminApi.getProduct(productId)
        if (editorOpen.value && editingProductId.value === productId) {
          productDetail.value = refreshedProduct
        }
      }
    } else if (editorOpen.value && editingProductId.value === productId) {
      showNotice('error', messageFor(error, '图片删除失败。'))
    }
  } finally {
    imageDeletingId.value = null
  }
}

function productImageUrl(image: ProductImage): string {
  if (image.url) return image.url
  if (image.media_url) return image.media_url
  const encodedKey = image.object_key.split('/').map(encodeURIComponent).join('/')
  return `/api/v1/media/${encodedKey}`
}

function editCategory(category: Category) {
  editingCategoryId.value = category.id
  Object.assign(categoryForm, {
    code: category.code,
    name: category.name,
    description: category.description ?? '',
    parent_id: category.parent_id === null ? '' : String(category.parent_id),
    sort_order: String(category.sort_order),
    is_active: category.is_active,
  })
}

function resetCategoryForm() {
  editingCategoryId.value = null
  Object.assign(categoryForm, emptyCategoryForm())
}

async function saveCategory() {
  if (categorySaving.value) return
  clearNotice()

  let input: CategoryInput
  try {
    if (!categoryForm.code.trim() || !categoryForm.name.trim()) {
      throw new Error('类目编码和名称不能为空。')
    }
    input = {
      code: categoryForm.code.trim(),
      name: categoryForm.name.trim(),
      description: nullable(categoryForm.description),
      parent_id: categoryForm.parent_id
        ? parseInteger(categoryForm.parent_id, '父类目', 1)
        : null,
      sort_order: parseInteger(
        categoryForm.sort_order,
        '排序值',
        POSTGRES_INTEGER_MIN,
        POSTGRES_INTEGER_MAX,
      ),
      is_active: categoryForm.is_active,
    }
  } catch (error) {
    showNotice('error', messageFor(error, '请检查类目表单。'))
    return
  }

  categorySaving.value = true
  try {
    if (editingCategoryId.value) {
      await catalogAdminApi.updateCategory(editingCategoryId.value, input)
    } else {
      await catalogAdminApi.createCategory(input)
    }
    showNotice('success', editingCategoryId.value ? '类目已更新。' : '类目已创建。')
    resetCategoryForm()
    await loadCategories()
  } catch (error) {
    showNotice('error', messageFor(error, '类目保存失败。'))
  } finally {
    categorySaving.value = false
  }
}

async function deleteCategory(category: Category) {
  if (!window.confirm(`确认删除类目「${category.name}」？`)) return
  categoryDeletingId.value = category.id
  clearNotice()
  try {
    await catalogAdminApi.deleteCategory(category.id)
    if (editingCategoryId.value === category.id) resetCategoryForm()
    showNotice('success', '类目已删除。')
    await loadCategories()
  } catch (error) {
    showNotice('error', messageFor(error, '类目删除失败；请确认该类目未被商品使用。'))
  } finally {
    categoryDeletingId.value = null
  }
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

async function downloadWorkbook(kind: 'template' | 'export') {
  if (downloadBusy.value) return
  downloadBusy.value = kind
  clearNotice()
  try {
    const blob =
      kind === 'template'
        ? await catalogAdminApi.downloadTemplate()
        : await catalogAdminApi.exportProducts()
    saveBlob(blob, kind === 'template' ? 'harbor-products-template.xlsx' : 'harbor-products-export.xlsx')
    showNotice('success', kind === 'template' ? '模板下载已开始。' : '商品导出已开始。')
  } catch (error) {
    showNotice('error', messageFor(error, '文件下载失败。'))
  } finally {
    downloadBusy.value = null
  }
}

function selectImportFile(event: Event) {
  const target = event.target as HTMLInputElement
  importFile.value = target.files?.[0] ?? null
  importResult.value = null
  dryRunApprovedFile.value = null
  selectedImportJob.value = null
  resetImportIdempotencyKeys()
}

function resetImportIdempotencyKeys() {
  const selectionId =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  importIdempotencyKeys.dryRun = `catalog-dry-${selectionId}`
  importIdempotencyKeys.commit = `catalog-commit-${selectionId}`
}

function selectStagingFile(event: Event) {
  const target = event.target as HTMLInputElement
  stagingFile.value = target.files?.[0] ?? null
}

async function cancelStagedImage(image: StagedProductImage) {
  if (stagingDeletingKey.value !== null) return
  stagingDeletingKey.value = image.object_key
  clearNotice()
  try {
    await catalogAdminApi.deleteStagedProductImage(image.object_key)
    stagedImages.value = stagedImages.value.filter((item) => item.object_key !== image.object_key)
    showNotice('success', '暂存图片已清理。')
  } catch (error) {
    showNotice('error', messageFor(error, '暂存图片清理失败，后台已保留可重试任务。'))
  } finally {
    stagingDeletingKey.value = null
  }
}

async function copyStagedImageKey(image: StagedProductImage) {
  try {
    await navigator.clipboard.writeText(image.object_key)
    showNotice('success', '图片路径已复制。')
  } catch {
    showNotice('error', '自动复制失败，请在路径框中全选复制。')
  }
}

async function uploadStagingImage() {
  const productCode = stagingProductCode.value.trim().toUpperCase()
  if (!productCode || !stagingFile.value || stagingUploading.value) return
  stagingUploading.value = true
  clearNotice()
  try {
    const stagedImage = await catalogAdminApi.uploadStagedProductImage(
      productCode,
      stagingFile.value,
    )
    stagedImages.value = [
      ...stagedImages.value.filter((item) => item.object_key !== stagedImage.object_key),
      stagedImage,
    ]
    stagingFile.value = null
    if (stagingInput.value) stagingInput.value.value = ''
    showNotice('success', '图片已安全暂存；请将下方路径填入 Images 工作表。')
  } catch (error) {
    showNotice('error', messageFor(error, '图片暂存失败。'))
  } finally {
    stagingUploading.value = false
  }
}

async function runImport(dryRun: boolean) {
  if (!importFile.value || importMode.value) return
  if (!dryRun && !canCommitImport.value) {
    showNotice('error', '请先对当前工作簿执行并通过预检。')
    return
  }
  const selectedFile = importFile.value
  importMode.value = dryRun ? 'dry-run' : 'commit'
  importResult.value = null
  clearNotice()
  try {
    const key = dryRun ? importIdempotencyKeys.dryRun : importIdempotencyKeys.commit
    importResult.value = await catalogAdminApi.importProducts(selectedFile, dryRun, key)
    if (dryRun) {
      dryRunApprovedFile.value = importResult.value.valid ? selectedFile : null
      if (!importResult.value.valid) resetImportIdempotencyKeys()
    } else if (importResult.value.valid) {
      dryRunApprovedFile.value = null
      const promotedKeys = new Set(importResult.value.promoted_staging_keys)
      stagedImages.value = stagedImages.value.filter(
        (image) => !promotedKeys.has(image.object_key),
      )
    } else {
      dryRunApprovedFile.value = null
      resetImportIdempotencyKeys()
    }
    if (importResult.value.valid === false) {
      showNotice('error', dryRun ? '预检完成，请处理下方错误后重试。' : '导入未完成，请处理下方错误。')
    } else if (importResult.value.errors.length > 0) {
      showNotice('success', '商品已成功导入；下方仅为对象清理待重试警告，请勿重复导入。')
      if (!dryRun) await loadProducts(true, true)
    } else {
      showNotice('success', dryRun ? '预检通过，未修改任何商品数据。' : 'Excel 已成功导入。')
      if (!dryRun) await loadProducts(true, true)
    }
    await loadImportJobs()
  } catch (error) {
    showNotice('error', messageFor(error, dryRun ? 'Excel 预检失败。' : 'Excel 导入失败。'))
  } finally {
    importMode.value = null
  }
}

async function logout() {
  if (isLoggingOut.value) return
  isLoggingOut.value = true
  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch (error) {
    showNotice('error', messageFor(error, '退出登录失败。'))
  } finally {
    isLoggingOut.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadCategories(), loadProducts(), loadImportJobs(), loadCleanupJobs()])
})
</script>

<template>
  <div class="app-shell admin-shell">
    <header class="app-header">
      <div class="app-header__inner admin-header__inner">
        <AppBrand />
        <nav
          class="admin-header__actions"
          aria-label="后台导航"
        >
          <RouterLink
            class="text-link"
            :to="{ name: 'home' }"
          >
            <ArrowLeft
              :size="16"
              aria-hidden="true"
            /> 返回首页
          </RouterLink>
          <button
            class="secondary-button"
            type="button"
            :disabled="isLoggingOut"
            @click="logout"
          >
            <LoaderCircle
              v-if="isLoggingOut"
              class="spin"
              :size="17"
              aria-hidden="true"
            />
            <LogOut
              v-else
              :size="17"
              aria-hidden="true"
            />
            <span>退出</span>
          </button>
        </nav>
      </div>
    </header>

    <main class="admin-page">
      <section class="admin-title-row">
        <div>
          <p class="eyebrow">
            Catalog Console
          </p>
          <h1>商品管理</h1>
          <p>维护微信小程序与 H5 共用的商品、图片、类目和批量数据。</p>
        </div>
        <span class="admin-user">管理员 · {{ auth.user?.username }}</span>
      </section>

      <div
        class="admin-tabs"
        role="tablist"
        aria-label="商品管理工作区"
        @keydown="handleWorkspaceTabKeydown"
      >
        <button
          id="catalog-tab-products"
          :class="['admin-tab', { 'admin-tab--active': activeTab === 'products' }]"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'products'"
          aria-controls="catalog-panel-products"
          :tabindex="activeTab === 'products' ? 0 : -1"
          @click="activeTab = 'products'"
        >
          <LayoutGrid
            :size="17"
            aria-hidden="true"
          /> 商品
        </button>
        <button
          id="catalog-tab-categories"
          :class="['admin-tab', { 'admin-tab--active': activeTab === 'categories' }]"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'categories'"
          aria-controls="catalog-panel-categories"
          :tabindex="activeTab === 'categories' ? 0 : -1"
          @click="activeTab = 'categories'"
        >
          <Tags
            :size="17"
            aria-hidden="true"
          /> 类目
        </button>
        <button
          id="catalog-tab-excel"
          :class="['admin-tab', { 'admin-tab--active': activeTab === 'excel' }]"
          type="button"
          role="tab"
          :aria-selected="activeTab === 'excel'"
          aria-controls="catalog-panel-excel"
          :tabindex="activeTab === 'excel' ? 0 : -1"
          @click="activeTab = 'excel'"
        >
          <FileSpreadsheet
            :size="17"
            aria-hidden="true"
          /> Excel
        </button>
      </div>

      <p
        v-if="notice"
        :class="['notice', notice.kind === 'success' ? 'notice--success' : 'notice--error']"
        role="alert"
      >
        {{ notice.text }}
      </p>

      <section
        v-if="activeTab === 'products'"
        id="catalog-panel-products"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="catalog-tab-products"
      >
        <div class="workspace-heading">
          <div>
            <h2 id="products-title">
              商品列表
            </h2>
            <p>共 {{ totalProducts }} 件商品，价格按人民币元显示。</p>
          </div>
          <button
            class="admin-primary-button"
            type="button"
            @click="openCreateProduct"
          >
            <PackagePlus
              :size="18"
              aria-hidden="true"
            /> 新建商品
          </button>
        </div>

        <form
          class="catalog-filters"
          aria-label="筛选商品"
          @submit.prevent="loadProducts(true)"
        >
          <label class="admin-field admin-field--search">
            <span>搜索</span>
            <span class="input-with-icon">
              <Search
                :size="17"
                aria-hidden="true"
              />
              <input
                v-model="filters.q"
                type="search"
                placeholder="商品名称或编码"
              >
            </span>
          </label>
          <label class="admin-field">
            <span>状态</span>
            <select v-model="filters.status">
              <option value="">全部状态</option>
              <option value="draft">草稿</option>
              <option value="published">已上架</option>
              <option value="archived">已归档</option>
            </select>
          </label>
          <label class="admin-field">
            <span>类目</span>
            <select v-model="filters.category_id">
              <option value="">全部类目</option>
              <option
                v-for="category in categories"
                :key="category.id"
                :value="String(category.id)"
              >
                {{ category.name }}
              </option>
            </select>
          </label>
          <button
            class="filter-button"
            type="submit"
            :disabled="productsLoading"
          >
            <LoaderCircle
              v-if="productsLoading"
              class="spin"
              :size="17"
              aria-hidden="true"
            />
            <Search
              v-else
              :size="17"
              aria-hidden="true"
            /> 查询
          </button>
        </form>

        <form
          v-if="editorOpen"
          class="editor-card"
          aria-label="商品编辑表单"
          @submit.prevent="saveProduct"
        >
          <div class="editor-card__header">
            <div>
              <p class="eyebrow">
                {{ editingProductId ? 'Edit product' : 'New product' }}
              </p>
              <h3>{{ editingProductId ? '编辑商品' : '新建商品' }}</h3>
            </div>
            <button
              class="icon-button"
              type="button"
              aria-label="关闭商品表单"
              @click="closeProductEditor"
            >
              <X
                :size="20"
                aria-hidden="true"
              />
            </button>
          </div>

          <details open>
            <summary>基础信息</summary>
            <div class="form-grid form-grid--three">
              <label class="admin-field">
                <span>商品编码 *</span>
                <input
                  v-model.trim="productForm.product_code"
                  required
                  autocomplete="off"
                >
              </label>
              <label class="admin-field">
                <span>商品名称 *</span>
                <input
                  v-model.trim="productForm.name"
                  required
                  autocomplete="off"
                >
              </label>
              <label class="admin-field">
                <span>副标题</span>
                <input
                  v-model="productForm.subtitle"
                  autocomplete="off"
                >
              </label>
              <label class="admin-field">
                <span>类目 *</span>
                <select
                  v-model="productForm.category_id"
                  required
                >
                  <option
                    value=""
                    disabled
                  >请选择类目</option>
                  <option
                    v-for="category in categories"
                    :key="category.id"
                    :value="String(category.id)"
                  >
                    {{ category.name }}{{ category.is_active ? '' : '（已停用）' }}
                  </option>
                </select>
              </label>
              <label class="admin-field">
                <span>状态</span>
                <select v-model="productForm.status">
                  <option value="draft">草稿</option>
                  <option value="published">已上架</option>
                  <option value="archived">已归档</option>
                </select>
              </label>
              <label class="admin-field">
                <span>销售单位 *</span>
                <input
                  v-model.trim="productForm.unit"
                  required
                  placeholder="杯 / 件 / 盒"
                >
              </label>
              <label class="admin-field">
                <span>基础价（元）*</span>
                <input
                  v-model="productForm.base_price_yuan"
                  required
                  inputmode="decimal"
                  placeholder="19.90"
                >
              </label>
              <label class="admin-field">
                <span>划线价（元）</span>
                <input
                  v-model="productForm.market_price_yuan"
                  inputmode="decimal"
                  placeholder="29.90"
                >
              </label>
              <label class="admin-field">
                <span>库存状态</span>
                <select v-model="productForm.stock_status">
                  <option value="in_stock">有货</option>
                  <option value="out_of_stock">售罄</option>
                  <option value="preorder">预售</option>
                </select>
              </label>
              <label class="admin-field">
                <span>库存数量</span>
                <input
                  v-model="productForm.inventory_count"
                  inputmode="numeric"
                >
              </label>
              <label class="admin-field">
                <span>排序值</span>
                <input
                  v-model="productForm.sort_order"
                  inputmode="numeric"
                >
              </label>
              <label class="admin-check">
                <input
                  v-model="productForm.featured"
                  type="checkbox"
                >
                <span>推荐商品</span>
              </label>
            </div>
          </details>

          <details open>
            <summary>小程序展示内容</summary>
            <div class="form-grid form-grid--two">
              <label class="admin-field">
                <span>标签（逗号或换行分隔）</span>
                <textarea
                  v-model="productForm.tags"
                  rows="3"
                  placeholder="新品&#10;人气推荐"
                />
              </label>
              <label class="admin-field">
                <span>卖点（逗号或换行分隔）</span>
                <textarea
                  v-model="productForm.selling_points"
                  rows="3"
                  placeholder="现磨咖啡&#10;轻盈口感"
                />
              </label>
              <label class="admin-field admin-field--wide">
                <span>商品说明</span>
                <textarea
                  v-model="productForm.description"
                  rows="4"
                />
              </label>
              <label class="admin-field">
                <span>配料</span>
                <textarea
                  v-model="productForm.ingredients"
                  rows="3"
                />
              </label>
              <label class="admin-field">
                <span>过敏原信息</span>
                <textarea
                  v-model="productForm.allergen_info"
                  rows="3"
                />
              </label>
            </div>
          </details>

          <details>
            <summary>规格与 SKU JSON</summary>
            <div class="form-grid form-grid--two">
              <label class="admin-field admin-field--code">
                <span>规格 JSON（数组）</span>
                <textarea
                  v-model="productForm.specifications_json"
                  rows="12"
                  spellcheck="false"
                  placeholder="[{&quot;code&quot;:&quot;temperature&quot;,&quot;name&quot;:&quot;温度&quot;,&quot;selection_mode&quot;:&quot;single&quot;,&quot;required&quot;:true,&quot;min_select&quot;:1,&quot;max_select&quot;:1,&quot;options&quot;:[{&quot;code&quot;:&quot;iced&quot;,&quot;name&quot;:&quot;冰&quot;,&quot;price_delta_cents&quot;:0,&quot;sort&quot;:0,&quot;is_default&quot;:true}]}]"
                />
              </label>
              <label class="admin-field admin-field--code">
                <span>SKU JSON（数组）</span>
                <textarea
                  v-model="productForm.skus_json"
                  rows="12"
                  spellcheck="false"
                  placeholder="[{&quot;sku_code&quot;:&quot;LATTE-L&quot;,&quot;name&quot;:&quot;大杯&quot;,&quot;price_cents&quot;:1990}]"
                />
              </label>
            </div>
          </details>

          <details
            v-if="editingProductId"
            open
          >
            <summary>商品图片</summary>
            <p class="section-help">
              JPEG / PNG / WebP，单张不超过 5 MiB。封面 1 张、轮播最多 8 张、详情最多 20 张。
            </p>
            <div class="image-uploader">
              <label class="admin-field admin-field--file">
                <span>选择图片</span>
                <input
                  ref="imageInput"
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  @change="selectImage"
                >
              </label>
              <label class="admin-field">
                <span>图片类型</span>
                <select v-model="imageType">
                  <option value="cover">封面</option>
                  <option value="gallery">轮播</option>
                  <option value="detail">详情</option>
                </select>
              </label>
              <label class="admin-field">
                <span>替代文本</span>
                <input
                  v-model="imageAltText"
                  placeholder="描述图片内容"
                >
              </label>
              <label class="admin-field">
                <span>排序值</span>
                <input
                  v-model="imageSortOrder"
                  inputmode="numeric"
                >
              </label>
              <button
                class="filter-button"
                type="button"
                :disabled="!imageFile || imageUploading"
                @click="uploadImage"
              >
                <LoaderCircle
                  v-if="imageUploading"
                  class="spin"
                  :size="17"
                  aria-hidden="true"
                />
                <ImagePlus
                  v-else
                  :size="17"
                  aria-hidden="true"
                /> 上传图片
              </button>
            </div>

            <div
              v-if="sortedImages.length"
              class="image-grid"
            >
              <article
                v-for="image in sortedImages"
                :key="image.id"
                class="image-card"
              >
                <img
                  :src="productImageUrl(image)"
                  :alt="image.alt_text || productForm.name"
                >
                <div class="image-card__meta">
                  <span class="image-type">{{ image.image_type }}</span>
                  <span>#{{ image.sort_order }}</span>
                </div>
                <template v-if="imageEditingId === image.id">
                  <label class="admin-field image-card__field">
                    <span>替代文本</span>
                    <input
                      v-model="imageEditAltText"
                      :aria-label="`图片 ${image.id} 替代文本`"
                    >
                  </label>
                  <label class="admin-field image-card__field">
                    <span>排序值</span>
                    <input
                      v-model="imageEditSortOrder"
                      inputmode="numeric"
                      :aria-label="`图片 ${image.id} 排序值`"
                    >
                  </label>
                  <div class="image-card__actions">
                    <button
                      class="filter-button image-card__button"
                      type="button"
                      :disabled="imageUpdatingId === image.id"
                      :aria-label="`保存图片 ${image.id} 信息`"
                      @click="saveImageMetadata(image)"
                    >
                      <LoaderCircle
                        v-if="imageUpdatingId === image.id"
                        class="spin"
                        :size="15"
                        aria-hidden="true"
                      />
                      <Save
                        v-else
                        :size="15"
                        aria-hidden="true"
                      /> 保存
                    </button>
                    <button
                      class="text-button"
                      type="button"
                      @click="cancelImageEdit"
                    >
                      取消
                    </button>
                  </div>
                </template>
                <template v-else>
                  <p>{{ image.alt_text || '无替代文本' }}</p>
                  <div class="image-card__actions">
                    <button
                      class="text-button"
                      type="button"
                      :aria-label="`编辑图片 ${image.id}`"
                      @click="beginImageEdit(image)"
                    >
                      <Pencil
                        :size="15"
                        aria-hidden="true"
                      /> 编辑
                    </button>
                    <button
                      class="danger-text-button"
                      type="button"
                      :disabled="imageDeletingId === image.id"
                      :aria-label="`删除图片 ${image.id}`"
                      @click="deleteImage(image)"
                    >
                      <LoaderCircle
                        v-if="imageDeletingId === image.id"
                        class="spin"
                        :size="15"
                        aria-hidden="true"
                      />
                      <Trash2
                        v-else
                        :size="15"
                        aria-hidden="true"
                      /> 删除
                    </button>
                  </div>
                </template>
              </article>
            </div>
            <p
              v-else
              class="empty-inline"
            >
              暂未上传图片。
            </p>
          </details>

          <div class="editor-actions">
            <button
              class="secondary-button editor-cancel"
              type="button"
              @click="closeProductEditor"
            >
              取消
            </button>
            <button
              class="admin-primary-button"
              type="submit"
              :disabled="productSaving"
            >
              <LoaderCircle
                v-if="productSaving"
                class="spin"
                :size="18"
                aria-hidden="true"
              />
              <Save
                v-else
                :size="18"
                aria-hidden="true"
              />
              {{ productSaving ? '保存中…' : '保存商品' }}
            </button>
          </div>
        </form>

        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead>
              <tr>
                <th>商品</th>
                <th>类目</th>
                <th>状态</th>
                <th>价格</th>
                <th>库存</th>
                <th class="table-actions">
                  操作
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="productsLoading">
                <td
                  colspan="6"
                  class="table-state"
                >
                  <LoaderCircle
                    class="spin"
                    :size="20"
                    aria-hidden="true"
                  /> 正在加载商品…
                </td>
              </tr>
              <tr v-else-if="!products.length">
                <td
                  colspan="6"
                  class="table-state"
                >
                  未找到商品。可调整筛选条件或新建商品。
                </td>
              </tr>
              <tr
                v-for="product in products"
                v-else
                :key="product.id"
              >
                <td>
                  <strong>{{ product.name }}</strong>
                  <small>{{ product.product_code }}{{ product.featured ? ' · 推荐' : '' }}</small>
                </td>
                <td>{{ product.category?.name ?? categoryById.get(product.category_id)?.name ?? '—' }}</td>
                <td><span :class="['status-chip', `status-chip--${product.status}`]">{{ product.status }}</span></td>
                <td>¥{{ centsToYuan(product.base_price_cents) }}</td>
                <td>{{ product.stock_status }} · {{ product.inventory_count }}</td>
                <td class="table-actions">
                  <button
                    class="table-icon-button"
                    type="button"
                    aria-label="编辑商品"
                    @click="openEditProduct(product)"
                  >
                    <Pencil
                      :size="16"
                      aria-hidden="true"
                    />
                  </button>
                  <button
                    class="table-icon-button table-icon-button--danger"
                    type="button"
                    aria-label="删除商品"
                    :disabled="productDeletingId === product.id"
                    @click="deleteProduct(product)"
                  >
                    <LoaderCircle
                      v-if="productDeletingId === product.id"
                      class="spin"
                      :size="16"
                    />
                    <Trash2
                      v-else
                      :size="16"
                      aria-hidden="true"
                    />
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div
          class="pagination"
          aria-label="商品分页"
        >
          <button
            type="button"
            :disabled="page <= 1 || productsLoading"
            @click="changePage(page - 1)"
          >
            <ChevronLeft
              :size="17"
              aria-hidden="true"
            /> 上一页
          </button>
          <span>第 {{ page }} / {{ pageCount }} 页</span>
          <button
            type="button"
            :disabled="page >= pageCount || productsLoading"
            @click="changePage(page + 1)"
          >
            下一页 <ChevronRight
              :size="17"
              aria-hidden="true"
            />
          </button>
        </div>
      </section>

      <section
        v-else-if="activeTab === 'categories'"
        id="catalog-panel-categories"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="catalog-tab-categories"
      >
        <div class="workspace-heading">
          <div>
            <h2 id="categories-title">
              类目管理
            </h2>
            <p>类目编码用于 API 与 Excel 关联，创建后建议保持稳定。</p>
          </div>
          <button
            class="filter-button"
            type="button"
            :disabled="categoriesLoading"
            @click="loadCategories"
          >
            <RefreshCw
              :class="{ spin: categoriesLoading }"
              :size="17"
              aria-hidden="true"
            /> 刷新
          </button>
        </div>

        <div class="category-layout">
          <form
            class="editor-card category-form"
            aria-label="类目编辑表单"
            @submit.prevent="saveCategory"
          >
            <div class="editor-card__header">
              <h3>{{ editingCategoryId ? '编辑类目' : '新建类目' }}</h3>
              <button
                v-if="editingCategoryId"
                class="icon-button"
                type="button"
                aria-label="取消编辑类目"
                @click="resetCategoryForm"
              >
                <X
                  :size="19"
                  aria-hidden="true"
                />
              </button>
            </div>
            <label class="admin-field">
              <span>类目编码 *</span>
              <input
                v-model.trim="categoryForm.code"
                required
                autocomplete="off"
                :disabled="editingCategoryId !== null"
                :title="editingCategoryId !== null ? '类目编码是 Excel 稳定外键，创建后不可修改' : ''"
              >
            </label>
            <label class="admin-field">
              <span>类目名称 *</span>
              <input
                v-model.trim="categoryForm.name"
                required
                autocomplete="off"
              >
            </label>
            <label class="admin-field">
              <span>说明</span>
              <textarea
                v-model="categoryForm.description"
                rows="3"
              />
            </label>
            <div class="form-grid form-grid--two">
              <label class="admin-field">
                <span>父类目</span>
                <select v-model="categoryForm.parent_id">
                  <option value="">无</option>
                  <option
                    v-for="category in categories.filter((item) => item.id !== editingCategoryId)"
                    :key="category.id"
                    :value="String(category.id)"
                  >
                    {{ category.name }}
                  </option>
                </select>
              </label>
              <label class="admin-field">
                <span>排序值</span>
                <input
                  v-model="categoryForm.sort_order"
                  inputmode="numeric"
                >
              </label>
            </div>
            <label class="admin-check">
              <input
                v-model="categoryForm.is_active"
                type="checkbox"
              >
              <span>启用类目</span>
            </label>
            <button
              class="admin-primary-button"
              type="submit"
              :disabled="categorySaving"
            >
              <LoaderCircle
                v-if="categorySaving"
                class="spin"
                :size="18"
                aria-hidden="true"
              />
              <Save
                v-else
                :size="18"
                aria-hidden="true"
              /> {{ editingCategoryId ? '保存修改' : '创建类目' }}
            </button>
          </form>

          <div class="admin-table-wrap">
            <table class="admin-table">
              <thead>
                <tr>
                  <th>类目</th><th>层级</th><th>排序</th><th>状态</th><th class="table-actions">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="categoriesLoading">
                  <td
                    colspan="5"
                    class="table-state"
                  >
                    正在加载类目…
                  </td>
                </tr>
                <tr v-else-if="!categories.length">
                  <td
                    colspan="5"
                    class="table-state"
                  >
                    暂无类目，请先创建。
                  </td>
                </tr>
                <tr
                  v-for="category in categories"
                  v-else
                  :key="category.id"
                >
                  <td><strong>{{ category.name }}</strong><small>{{ category.code }}</small></td>
                  <td>{{ category.parent_id ? categoryById.get(category.parent_id)?.name ?? '—' : '顶级' }}</td>
                  <td>{{ category.sort_order }}</td>
                  <td><span :class="['status-chip', category.is_active ? 'status-chip--published' : 'status-chip--archived']">{{ category.is_active ? '启用' : '停用' }}</span></td>
                  <td class="table-actions">
                    <button
                      class="table-icon-button"
                      type="button"
                      aria-label="编辑类目"
                      @click="editCategory(category)"
                    >
                      <Pencil :size="16" />
                    </button>
                    <button
                      class="table-icon-button table-icon-button--danger"
                      type="button"
                      aria-label="删除类目"
                      :disabled="categoryDeletingId === category.id"
                      @click="deleteCategory(category)"
                    >
                      <LoaderCircle
                        v-if="categoryDeletingId === category.id"
                        class="spin"
                        :size="16"
                      />
                      <Trash2
                        v-else
                        :size="16"
                      />
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section
        v-else
        id="catalog-panel-excel"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="catalog-tab-excel"
      >
        <div class="workspace-heading">
          <div>
            <h2 id="excel-title">
              Excel 批量导入与导出
            </h2>
            <p>先下载模板并执行预检；正式导入采用整表校验，失败时不会部分写入。</p>
          </div>
        </div>

        <div class="excel-actions-grid">
          <article class="excel-card">
            <span class="excel-card__icon"><Download
              :size="24"
              aria-hidden="true"
            /></span>
            <h3>模板与导出</h3>
            <p>模板包含 Products、SKUs、Images 和 Dictionary 工作表及填写说明。</p>
            <div class="button-row">
              <button
                class="filter-button"
                type="button"
                :disabled="downloadBusy !== null"
                @click="downloadWorkbook('template')"
              >
                <LoaderCircle
                  v-if="downloadBusy === 'template'"
                  class="spin"
                  :size="17"
                />
                <Download
                  v-else
                  :size="17"
                /> 下载模板
              </button>
              <button
                class="filter-button"
                type="button"
                :disabled="downloadBusy !== null"
                @click="downloadWorkbook('export')"
              >
                <LoaderCircle
                  v-if="downloadBusy === 'export'"
                  class="spin"
                  :size="17"
                />
                <FileSpreadsheet
                  v-else
                  :size="17"
                /> 导出商品
              </button>
            </div>
          </article>

          <article class="excel-card">
            <span class="excel-card__icon"><ImagePlus
              :size="24"
              aria-hidden="true"
            /></span>
            <h3>为新商品暂存图片</h3>
            <p>输入 Excel 中的商品编码并上传图片，系统会生成受控的 staging 路径。</p>
            <label class="admin-field">
              <span>商品编码</span>
              <input
                v-model.trim="stagingProductCode"
                autocomplete="off"
                placeholder="例如 LATTE-01"
              >
            </label>
            <label class="admin-field admin-field--file">
              <span>JPEG / PNG / WebP（最大 5 MiB）</span>
              <input
                ref="stagingInput"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                @change="selectStagingFile"
              >
            </label>
            <button
              class="filter-button"
              type="button"
              :disabled="!stagingProductCode.trim() || !stagingFile || stagingUploading"
              @click="uploadStagingImage"
            >
              <LoaderCircle
                v-if="stagingUploading"
                class="spin"
                :size="17"
              />
              <ImagePlus
                v-else
                :size="17"
              /> 生成图片路径
            </button>
            <div
              v-if="stagedImages.length"
              class="staged-image-list"
            >
              <article
                v-for="image in stagedImages"
                :key="image.object_key"
                class="staged-image-item"
              >
                <label class="admin-field">
                  <span>Images.object_key</span>
                  <input
                    class="staged-image-path"
                    readonly
                    :value="image.object_key"
                    :aria-label="`暂存图片路径 ${image.object_key}`"
                  >
                </label>
                <p class="selected-file">
                  自动过期：{{ new Date(image.expires_at).toLocaleString('zh-CN') }}
                </p>
                <div class="button-row staged-image-actions">
                  <button
                    class="text-button"
                    type="button"
                    @click="copyStagedImageKey(image)"
                  >
                    <Copy
                      :size="15"
                      aria-hidden="true"
                    /> 复制路径
                  </button>
                  <button
                    class="text-button text-button--danger"
                    type="button"
                    :disabled="stagingDeletingKey === image.object_key"
                    @click="cancelStagedImage(image)"
                  >
                    <LoaderCircle
                      v-if="stagingDeletingKey === image.object_key"
                      class="spin"
                      :size="15"
                      aria-hidden="true"
                    />
                    <Trash2
                      v-else
                      :size="15"
                      aria-hidden="true"
                    /> 清理此图片
                  </button>
                </div>
              </article>
            </div>
          </article>

          <article class="excel-card">
            <span class="excel-card__icon"><Upload
              :size="24"
              aria-hidden="true"
            /></span>
            <h3>上传工作簿</h3>
            <p>只接受 .xlsx。图片工作表填写已存在的对象路径，服务端会验证对象是否存在。</p>
            <label class="admin-field admin-field--file">
              <span>Excel 文件</span>
              <input
                ref="importInput"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                @change="selectImportFile"
              >
            </label>
            <p
              v-if="importFile"
              class="selected-file"
            >
              已选择：{{ importFile.name }} ·
              {{ canCommitImport ? '预检已通过，可正式导入' : '正式导入前必须先通过预检' }}
            </p>
            <div class="button-row">
              <button
                class="filter-button"
                type="button"
                :disabled="!importFile || importMode !== null"
                @click="runImport(true)"
              >
                <LoaderCircle
                  v-if="importMode === 'dry-run'"
                  class="spin"
                  :size="17"
                />
                <FileCheck2
                  v-else
                  :size="17"
                /> 预检（不写入）
              </button>
              <button
                class="admin-primary-button"
                type="button"
                :disabled="!canCommitImport || importMode !== null"
                @click="runImport(false)"
              >
                <LoaderCircle
                  v-if="importMode === 'commit'"
                  class="spin"
                  :size="17"
                />
                <Upload
                  v-else
                  :size="17"
                /> 正式导入
              </button>
            </div>
          </article>
        </div>

        <section
          v-if="importResult"
          class="import-result"
          aria-labelledby="import-result-title"
        >
          <div class="workspace-heading workspace-heading--compact">
            <div>
              <p class="eyebrow">
                Import result
              </p>
              <h3 id="import-result-title">
                {{ importResult.dry_run ? '预检结果' : '导入结果' }}
              </h3>
            </div>
            <span
              v-if="importResult.job_id"
              class="job-id"
            >任务 #{{ importResult.job_id }}</span>
          </div>
          <dl
            v-if="importSummary.length"
            class="summary-grid"
          >
            <div
              v-for="[label, value] in importSummary"
              :key="label"
            >
              <dt>{{ label }}</dt><dd>{{ value }}</dd>
            </div>
          </dl>
          <div
            v-if="importResult.errors.length"
            class="admin-table-wrap"
          >
            <table class="admin-table import-errors">
              <thead><tr><th>工作表</th><th>行</th><th>字段</th><th>错误说明</th></tr></thead>
              <tbody>
                <tr
                  v-for="(issue, index) in importResult.errors"
                  :key="`${issue.sheet}-${issue.row}-${issue.field}-${index}`"
                >
                  <td>{{ issue.sheet ?? '—' }}</td><td>{{ issue.row ?? '—' }}</td><td>{{ issue.field ?? '—' }}</td><td>{{ issue.message }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p
            v-else
            class="empty-inline empty-inline--success"
          >
            未发现错误。
          </p>
        </section>

        <section class="import-result" aria-labelledby="recent-import-title">
          <div class="workspace-heading workspace-heading--compact">
            <div>
              <p class="eyebrow">Import history</p>
              <h3 id="recent-import-title">最近导入任务</h3>
            </div>
            <button
              class="text-button"
              type="button"
              :disabled="importHistoryLoading"
              @click="loadImportJobs"
            >
              <RefreshCw :class="{ spin: importHistoryLoading }" :size="15" /> 刷新
            </button>
          </div>
          <div v-if="recentImportJobs.length" class="admin-table-wrap">
            <table class="admin-table">
              <thead><tr><th>任务</th><th>文件</th><th>模式</th><th>状态</th><th>时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="job in recentImportJobs" :key="job.id">
                  <td>#{{ job.id }}</td>
                  <td>{{ job.original_filename }}</td>
                  <td>{{ job.dry_run ? '预检' : '正式导入' }}</td>
                  <td>{{ importStatusLabel(job) }}</td>
                  <td>{{ new Date(job.created_at).toLocaleString('zh-CN') }}</td>
                  <td>
                    <button
                      class="text-button"
                      type="button"
                      :disabled="importJobLoadingId !== null"
                      @click="loadImportJobDetail(job)"
                    >
                      <LoaderCircle
                        v-if="importJobLoadingId === job.id"
                        class="spin"
                        :size="15"
                        aria-hidden="true"
                      /> 查看详情
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="empty-inline">暂无导入任务。</p>
        </section>

        <section
          v-if="selectedImportJob"
          class="import-result"
          aria-labelledby="import-job-detail-title"
        >
          <div class="workspace-heading workspace-heading--compact">
            <div>
              <p class="eyebrow">
                Import job detail
              </p>
              <h3 id="import-job-detail-title">
                任务 #{{ selectedImportJob.id }} · {{ importStatusLabel(selectedImportJob) }}
              </h3>
            </div>
            <button
              class="text-button"
              type="button"
              @click="selectedImportJob = null"
            >
              关闭
            </button>
          </div>
          <p class="section-help">
            {{ selectedImportJob.original_filename }} ·
            {{ selectedImportJob.dry_run ? '预检' : '正式导入' }} ·
            完成时间：{{ selectedImportJob.completed_at ? new Date(selectedImportJob.completed_at).toLocaleString('zh-CN') : '尚未完成' }}
          </p>
          <dl
            v-if="selectedImportSummary.length"
            class="summary-grid"
          >
            <div
              v-for="[label, value] in selectedImportSummary"
              :key="label"
            >
              <dt>{{ label }}</dt><dd>{{ value }}</dd>
            </div>
          </dl>
          <div
            v-if="selectedImportJob.errors.length"
            class="admin-table-wrap"
          >
            <table class="admin-table import-errors">
              <thead><tr><th>工作表</th><th>行</th><th>字段</th><th>错误说明</th></tr></thead>
              <tbody>
                <tr
                  v-for="(issue, index) in selectedImportJob.errors"
                  :key="`${issue.sheet}-${issue.row}-${issue.field}-${index}`"
                >
                  <td>{{ issue.sheet ?? '—' }}</td>
                  <td>{{ issue.row ?? '—' }}</td>
                  <td>{{ issue.field ?? '—' }}</td>
                  <td>{{ issue.message }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p
            v-else
            class="empty-inline empty-inline--success"
          >
            此任务未记录错误。
          </p>
        </section>

        <section
          class="import-result"
          aria-labelledby="cleanup-jobs-title"
        >
          <div class="workspace-heading workspace-heading--compact">
            <div>
              <p class="eyebrow">
                Object cleanup
              </p>
              <h3 id="cleanup-jobs-title">
                对象清理任务
              </h3>
            </div>
            <div class="cleanup-toolbar">
              <label class="admin-field">
                <span>状态</span>
                <select
                  v-model="cleanupStatus"
                  aria-label="清理任务状态"
                  @change="loadCleanupJobs"
                >
                  <option value="">全部</option>
                  <option value="failed">失败</option>
                  <option value="pending">待处理</option>
                  <option value="processing">处理中</option>
                  <option value="intent">意图已记录</option>
                  <option value="completed">已完成</option>
                </select>
              </label>
              <button
                class="text-button"
                type="button"
                :disabled="cleanupJobsLoading"
                @click="loadCleanupJobs"
              >
                <RefreshCw
                  :class="{ spin: cleanupJobsLoading }"
                  :size="15"
                  aria-hidden="true"
                /> 刷新
              </button>
            </div>
          </div>
          <div
            v-if="cleanupJobs.length"
            class="admin-table-wrap"
          >
            <table class="admin-table cleanup-jobs-table">
              <thead><tr><th>任务</th><th>对象</th><th>原因</th><th>状态</th><th>尝试</th><th>最后错误</th><th>操作</th></tr></thead>
              <tbody>
                <tr
                  v-for="job in cleanupJobs"
                  :key="job.id"
                >
                  <td>#{{ job.id }}</td>
                  <td><code>{{ job.object_key }}</code></td>
                  <td>{{ job.reason }}</td>
                  <td>{{ job.status }}</td>
                  <td>{{ job.attempts }}</td>
                  <td>{{ job.last_error || '—' }}</td>
                  <td>
                    <button
                      v-if="job.status === 'failed'"
                      class="text-button"
                      type="button"
                      :disabled="cleanupRetryingId !== null"
                      @click="retryCleanupJob(job)"
                    >
                      <LoaderCircle
                        v-if="cleanupRetryingId === job.id"
                        class="spin"
                        :size="15"
                        aria-hidden="true"
                      /> 重试
                    </button>
                    <span v-else>—</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p
            v-else
            class="empty-inline"
          >
            当前筛选下没有对象清理任务。
          </p>
        </section>
      </section>
    </main>
  </div>
</template>
