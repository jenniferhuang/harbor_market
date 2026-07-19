import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthApi, User } from '../api/auth'
import type { Category, ObjectCleanupJob, Product } from '../api/catalog'
import { createAuthStore } from '../auth/store'
import { authKey } from '../auth/useAuth'
import ProductsAdminView from '../views/ProductsAdminView.vue'

const catalogMocks = vi.hoisted(() => ({
  listCategories: vi.fn(),
  createCategory: vi.fn(),
  updateCategory: vi.fn(),
  deleteCategory: vi.fn(),
  listProducts: vi.fn(),
  createProduct: vi.fn(),
  getProduct: vi.fn(),
  updateProduct: vi.fn(),
  deleteProduct: vi.fn(),
  uploadProductImage: vi.fn(),
  updateProductImage: vi.fn(),
  deleteProductImage: vi.fn(),
  uploadStagedProductImage: vi.fn(),
  deleteStagedProductImage: vi.fn(),
  downloadTemplate: vi.fn(),
  exportProducts: vi.fn(),
  importProducts: vi.fn(),
  listImportJobs: vi.fn(),
  getImportJob: vi.fn(),
  listObjectCleanupJobs: vi.fn(),
  retryObjectCleanupJob: vi.fn(),
}))

vi.mock('../api/catalog', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/catalog')>()
  return { ...actual, catalogAdminApi: catalogMocks }
})

const administrator: User = { id: 1, username: 'catalog-admin', is_admin: true }
const category: Category = {
  id: 3,
  code: 'coffee',
  name: '咖啡',
  description: '',
  parent_id: null,
  sort_order: 0,
  is_active: true,
}
const product: Product = {
  id: 9,
  product_code: 'LATTE-01',
  name: '生椰拿铁',
  subtitle: '椰香轻盈',
  category_id: 3,
  status: 'draft',
  base_price_cents: 1990,
  market_price_cents: 2990,
  currency: 'CNY',
  unit: '杯',
  stock_status: 'in_stock',
  inventory_count: 30,
  featured: false,
  sort_order: 0,
  tags: ['新品'],
  selling_points: ['现磨咖啡'],
  description: '',
  ingredients: null,
  allergen_info: null,
  specifications: [],
  skus: [],
  images: [],
}

function authApi(): AuthApi {
  return {
    register: vi.fn(async () => undefined),
    login: vi.fn(async () => administrator),
    me: vi.fn(async () => administrator),
    logout: vi.fn(async () => undefined),
  }
}

async function renderView() {
  const store = createAuthStore(authApi())
  await store.restore()
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: { template: '<p>Home</p>' } },
      { path: '/login', name: 'login', component: { template: '<p>Login</p>' } },
      { path: '/admin/products', name: 'admin-products', component: ProductsAdminView },
    ],
  })
  await router.push('/admin/products')

  return render(ProductsAdminView, {
    global: { plugins: [router], provide: { [authKey as symbol]: store } },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  catalogMocks.listCategories.mockResolvedValue([category])
  catalogMocks.listProducts.mockResolvedValue({ items: [product], total: 1, page: 1, page_size: 20 })
  catalogMocks.getProduct.mockResolvedValue(product)
  catalogMocks.createProduct.mockResolvedValue(product)
  catalogMocks.updateProduct.mockResolvedValue(product)
  catalogMocks.updateProductImage.mockResolvedValue(product)
  catalogMocks.importProducts.mockResolvedValue({
    job_id: 12,
    dry_run: true,
    valid: false,
    summary: { products: 1 },
    errors: [{ sheet: 'Products', row: 2, field: 'name', message: '商品名称不能为空' }],
    promoted_staging_keys: [],
  })
  catalogMocks.listImportJobs.mockResolvedValue([])
  catalogMocks.listObjectCleanupJobs.mockResolvedValue([])
})

describe('product administration view', () => {
  it('lists products and converts yuan fields to integer cents when creating', async () => {
    await renderView()
    const interaction = userEvent.setup()

    expect(await screen.findByText('生椰拿铁')).toBeVisible()
    await interaction.click(screen.getByRole('button', { name: '新建商品' }))
    await interaction.type(screen.getByLabelText('商品编码 *'), 'AMERICANO-01')
    await interaction.type(screen.getByLabelText('商品名称 *'), '美式咖啡')
    await interaction.selectOptions(screen.getByLabelText('类目 *'), '3')
    await interaction.type(screen.getByLabelText('基础价（元）*'), '12.50')
    await interaction.type(screen.getByLabelText('划线价（元）'), '18')
    await interaction.type(screen.getByLabelText('标签（逗号或换行分隔）'), '清爽, 新品')
    await interaction.click(screen.getByRole('button', { name: '保存商品' }))

    await waitFor(() => expect(catalogMocks.createProduct).toHaveBeenCalledOnce())
    expect(catalogMocks.createProduct).toHaveBeenCalledWith(
      expect.objectContaining({
        product_code: 'AMERICANO-01',
        category_id: 3,
        base_price_cents: 1250,
        market_price_cents: 1800,
        tags: ['清爽', '新品'],
        specifications: [],
        skus: [],
      }),
    )
  })

  it('runs an Excel dry-run and renders row-level validation errors', async () => {
    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')

    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    const workbook = new File(['xlsx'], 'catalog.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
    await interaction.upload(screen.getByLabelText('Excel 文件'), workbook)
    await interaction.click(screen.getByRole('button', { name: '预检（不写入）' }))

    expect(await screen.findByText('商品名称不能为空')).toBeVisible()
    expect(catalogMocks.importProducts).toHaveBeenCalledWith(
      workbook,
      true,
      expect.stringMatching(/^catalog-dry-/),
    )
    expect(screen.getByText('任务 #12')).toBeVisible()

    const firstKey = catalogMocks.importProducts.mock.calls[0]?.[2]
    await interaction.click(screen.getByRole('button', { name: '预检（不写入）' }))
    await waitFor(() => expect(catalogMocks.importProducts).toHaveBeenCalledTimes(2))
    const secondKey = catalogMocks.importProducts.mock.calls[1]?.[2]
    expect(secondKey).toMatch(/^catalog-dry-/)
    expect(secondKey).not.toBe(firstKey)
  })

  it('reuses the selected workbook idempotency key after a transport failure', async () => {
    catalogMocks.importProducts
      .mockResolvedValueOnce({
        job_id: 12,
        dry_run: true,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce({
        job_id: 13,
        dry_run: false,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    const workbook = new File(['xlsx'], 'catalog.xlsx')
    await interaction.upload(screen.getByLabelText('Excel 文件'), workbook)

    const dryRunButton = screen.getByRole('button', { name: '预检（不写入）' })
    const commitButton = screen.getByRole('button', { name: '正式导入' })
    expect(commitButton).toBeDisabled()
    await interaction.click(dryRunButton)
    await waitFor(() => expect(commitButton).toBeEnabled())
    await interaction.click(commitButton)
    await screen.findByText('connection lost')
    await interaction.click(commitButton)
    await screen.findByText('Excel 已成功导入。')

    const firstKey = catalogMocks.importProducts.mock.calls[1]?.[2]
    const secondKey = catalogMocks.importProducts.mock.calls[2]?.[2]
    expect(firstKey).toMatch(/^catalog-commit-/)
    expect(secondKey).toBe(firstKey)
  })

  it('requires a new dry-run and commit key after a rejected formal import', async () => {
    catalogMocks.importProducts
      .mockResolvedValueOnce({
        job_id: 31,
        dry_run: true,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
      .mockResolvedValueOnce({
        job_id: 32,
        dry_run: false,
        valid: false,
        summary: { products: 0 },
        errors: [{ sheet: 'Images', row: 2, field: 'object_key', message: '图片路径无效' }],
        promoted_staging_keys: [],
      })
      .mockResolvedValueOnce({
        job_id: 33,
        dry_run: true,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
      .mockResolvedValueOnce({
        job_id: 34,
        dry_run: false,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    const workbook = new File(['xlsx'], 'catalog.xlsx')
    await interaction.upload(screen.getByLabelText('Excel 文件'), workbook)

    const dryRunButton = screen.getByRole('button', { name: '预检（不写入）' })
    const commitButton = screen.getByRole('button', { name: '正式导入' })
    await interaction.click(dryRunButton)
    await waitFor(() => expect(commitButton).toBeEnabled())
    await interaction.click(commitButton)
    expect(await screen.findByText('图片路径无效')).toBeVisible()
    expect(commitButton).toBeDisabled()

    await interaction.click(dryRunButton)
    await waitFor(() => expect(commitButton).toBeEnabled())
    await interaction.click(commitButton)
    expect(await screen.findByText('Excel 已成功导入。')).toBeVisible()

    const rejectedCommitKey = catalogMocks.importProducts.mock.calls[1]?.[2]
    const successfulCommitKey = catalogMocks.importProducts.mock.calls[3]?.[2]
    expect(rejectedCommitKey).toMatch(/^catalog-commit-/)
    expect(successfulCommitKey).toMatch(/^catalog-commit-/)
    expect(successfulCommitKey).not.toBe(rejectedCommitKey)
  })

  it('requires a successful dry-run for the selected workbook and resets approval on file change', async () => {
    catalogMocks.importProducts.mockResolvedValue({
      job_id: 21,
      dry_run: true,
      valid: true,
      summary: { products: 1 },
      errors: [],
      promoted_staging_keys: [],
    })
    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))

    const input = screen.getByLabelText('Excel 文件')
    const commitButton = screen.getByRole('button', { name: '正式导入' })
    await interaction.upload(input, new File(['first'], 'first.xlsx'))
    expect(commitButton).toBeDisabled()

    await interaction.click(screen.getByRole('button', { name: '预检（不写入）' }))
    await waitFor(() => expect(commitButton).toBeEnabled())

    await interaction.upload(input, new File(['second'], 'second.xlsx'))
    expect(commitButton).toBeDisabled()
  })

  it('keeps multiple staged images and cancels each object independently', async () => {
    const first = {
      object_key: 'products/staged/LATTE-01/first.webp',
      mime_type: 'image/webp',
      size_bytes: 100,
      width: 300,
      height: 300,
      expires_at: '2026-07-22T00:00:00Z',
    }
    const second = { ...first, object_key: 'products/staged/LATTE-01/second.webp' }
    catalogMocks.uploadStagedProductImage
      .mockResolvedValueOnce(first)
      .mockResolvedValueOnce(second)
    catalogMocks.deleteStagedProductImage.mockResolvedValue(undefined)

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    await interaction.type(screen.getByLabelText('商品编码'), 'LATTE-01')

    const imageInput = screen.getByLabelText(/JPEG \/ PNG \/ WebP/)
    const stageButton = screen.getByRole('button', { name: '生成图片路径' })
    await interaction.upload(imageInput, new File(['first'], 'first.webp', { type: 'image/webp' }))
    await interaction.click(stageButton)
    expect(await screen.findByDisplayValue(first.object_key)).toBeVisible()

    await interaction.upload(imageInput, new File(['second'], 'second.webp', { type: 'image/webp' }))
    await interaction.click(stageButton)
    expect(await screen.findByDisplayValue(second.object_key)).toBeVisible()
    expect(screen.getByDisplayValue(first.object_key)).toBeVisible()
    expect(catalogMocks.deleteStagedProductImage).not.toHaveBeenCalled()

    await interaction.click(screen.getAllByRole('button', { name: '清理此图片' })[0]!)
    await waitFor(() => expect(screen.queryByDisplayValue(first.object_key)).not.toBeInTheDocument())
    expect(screen.getByDisplayValue(second.object_key)).toBeVisible()
    expect(catalogMocks.deleteStagedProductImage).toHaveBeenCalledWith(first.object_key)
  })

  it('clears promoted staged image paths after a successful formal import', async () => {
    const stagedImage = {
      object_key: 'products/staged/LATTE-01/promoted.webp',
      mime_type: 'image/webp',
      size_bytes: 100,
      width: 300,
      height: 300,
      expires_at: '2026-07-22T00:00:00Z',
    }
    const unreferencedImage = {
      ...stagedImage,
      object_key: 'products/staged/LATTE-01/next-import.webp',
    }
    catalogMocks.uploadStagedProductImage
      .mockResolvedValueOnce(stagedImage)
      .mockResolvedValueOnce(unreferencedImage)
    catalogMocks.importProducts
      .mockResolvedValueOnce({
        job_id: 41,
        dry_run: true,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [],
      })
      .mockResolvedValueOnce({
        job_id: 42,
        dry_run: false,
        valid: true,
        summary: { products: 1 },
        errors: [],
        promoted_staging_keys: [stagedImage.object_key],
      })

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    await interaction.type(screen.getByLabelText('商品编码'), 'LATTE-01')
    await interaction.upload(
      screen.getByLabelText(/JPEG \/ PNG \/ WebP/),
      new File(['image'], 'promoted.webp', { type: 'image/webp' }),
    )
    await interaction.click(screen.getByRole('button', { name: '生成图片路径' }))
    expect(await screen.findByDisplayValue(stagedImage.object_key)).toBeVisible()
    await interaction.upload(
      screen.getByLabelText(/JPEG \/ PNG \/ WebP/),
      new File(['next'], 'next-import.webp', { type: 'image/webp' }),
    )
    await interaction.click(screen.getByRole('button', { name: '生成图片路径' }))
    expect(await screen.findByDisplayValue(unreferencedImage.object_key)).toBeVisible()

    await interaction.upload(screen.getByLabelText('Excel 文件'), new File(['xlsx'], 'catalog.xlsx'))
    await interaction.click(screen.getByRole('button', { name: '预检（不写入）' }))
    const commitButton = screen.getByRole('button', { name: '正式导入' })
    await waitFor(() => expect(commitButton).toBeEnabled())
    await interaction.click(commitButton)

    expect(await screen.findByText('Excel 已成功导入。')).toBeVisible()
    expect(screen.queryByDisplayValue(stagedImage.object_key)).not.toBeInTheDocument()
    expect(screen.getByDisplayValue(unreferencedImage.object_key)).toBeVisible()
  })

  it('updates image alt text and sort order through the image PATCH API', async () => {
    const productWithImage: Product = {
      ...product,
      images: [
        {
          id: 4,
          object_key: 'products/9/gallery/image.webp',
          image_type: 'gallery',
          alt_text: '旧说明',
          sort_order: 1,
          url: '/api/v1/media/products/9/gallery/image.webp',
        },
      ],
    }
    const updatedProduct: Product = {
      ...productWithImage,
      images: [{ ...productWithImage.images![0]!, alt_text: '新说明', sort_order: -3 }],
    }
    catalogMocks.getProduct.mockResolvedValue(productWithImage)
    catalogMocks.updateProductImage.mockResolvedValue(updatedProduct)

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('button', { name: '编辑商品' }))
    await interaction.click(await screen.findByRole('button', { name: '编辑图片 4' }))
    await interaction.clear(screen.getByLabelText('图片 4 替代文本'))
    await interaction.type(screen.getByLabelText('图片 4 替代文本'), '新说明')
    await interaction.clear(screen.getByLabelText('图片 4 排序值'))
    await interaction.type(screen.getByLabelText('图片 4 排序值'), '-3')
    await interaction.click(screen.getByRole('button', { name: '保存图片 4 信息' }))

    await waitFor(() => expect(catalogMocks.updateProductImage).toHaveBeenCalledOnce())
    expect(catalogMocks.updateProductImage).toHaveBeenCalledWith(9, 4, {
      alt_text: '新说明',
      sort_order: -3,
    })
    expect(await screen.findByText('图片说明与排序已更新。')).toBeVisible()
  })

  it('does not apply a delayed image update to a different product editor', async () => {
    const productWithImage: Product = {
      ...product,
      images: [
        {
          id: 4,
          object_key: 'products/9/gallery/image.webp',
          image_type: 'gallery',
          alt_text: '旧说明',
          sort_order: 1,
          url: '/api/v1/media/products/9/gallery/image.webp',
        },
      ],
    }
    const secondProduct: Product = {
      ...product,
      id: 10,
      product_code: 'AMERICANO-01',
      name: '美式咖啡',
      images: [],
    }
    catalogMocks.listProducts.mockResolvedValue({
      items: [productWithImage, secondProduct],
      total: 2,
      page: 1,
      page_size: 20,
    })
    catalogMocks.getProduct.mockImplementation(async (id: number) =>
      id === productWithImage.id ? productWithImage : secondProduct,
    )
    let resolveUpdate: ((value: Product) => void) | undefined
    catalogMocks.updateProductImage.mockReturnValue(
      new Promise<Product>((resolve) => {
        resolveUpdate = resolve
      }),
    )

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[0]!)
    await interaction.click(await screen.findByRole('button', { name: '编辑图片 4' }))
    await interaction.click(screen.getByRole('button', { name: '保存图片 4 信息' }))
    await waitFor(() => expect(catalogMocks.updateProductImage).toHaveBeenCalledOnce())

    await interaction.click(screen.getByRole('button', { name: '关闭商品表单' }))
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[1]!)
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))

    resolveUpdate?.({ ...productWithImage, name: '不应覆盖当前表单' })
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))
    expect(screen.queryByText('图片说明与排序已更新。')).not.toBeInTheDocument()
  })

  it('keeps another image draft open when an earlier metadata update resolves', async () => {
    const firstImage = {
      id: 4,
      object_key: 'products/9/gallery/first.webp',
      image_type: 'gallery' as const,
      alt_text: '第一张',
      sort_order: 1,
      url: '/api/v1/media/products/9/gallery/first.webp',
    }
    const secondImage = {
      ...firstImage,
      id: 5,
      object_key: 'products/9/gallery/second.webp',
      alt_text: '第二张',
      sort_order: 2,
    }
    const productWithImages: Product = { ...product, images: [firstImage, secondImage] }
    catalogMocks.getProduct.mockResolvedValue(productWithImages)
    let resolveUpdate: ((value: Product) => void) | undefined
    catalogMocks.updateProductImage.mockReturnValue(
      new Promise<Product>((resolve) => {
        resolveUpdate = resolve
      }),
    )

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('button', { name: '编辑商品' }))
    await interaction.click(await screen.findByRole('button', { name: '编辑图片 4' }))
    await interaction.click(screen.getByRole('button', { name: '保存图片 4 信息' }))
    await waitFor(() => expect(catalogMocks.updateProductImage).toHaveBeenCalledOnce())

    await interaction.click(screen.getByRole('button', { name: '编辑图片 5' }))
    await interaction.clear(screen.getByLabelText('图片 5 替代文本'))
    await interaction.type(screen.getByLabelText('图片 5 替代文本'), '第二张草稿')
    resolveUpdate?.({
      ...productWithImages,
      images: [{ ...firstImage, alt_text: '第一张已保存' }, secondImage],
    })

    await waitFor(() =>
      expect(screen.getByLabelText('图片 5 替代文本')).toHaveValue('第二张草稿'),
    )
  })

  it('does not apply delayed image upload or delete responses to another product', async () => {
    const image = {
      id: 4,
      object_key: 'products/9/gallery/image.webp',
      image_type: 'gallery' as const,
      alt_text: '待删除',
      sort_order: 1,
      url: '/api/v1/media/products/9/gallery/image.webp',
    }
    const firstProduct: Product = { ...product, images: [image] }
    const secondProduct: Product = {
      ...product,
      id: 10,
      product_code: 'AMERICANO-01',
      name: '美式咖啡',
      images: [],
    }
    catalogMocks.listProducts.mockResolvedValue({
      items: [firstProduct, secondProduct],
      total: 2,
      page: 1,
      page_size: 20,
    })
    catalogMocks.getProduct.mockImplementation(async (id: number) =>
      id === firstProduct.id ? firstProduct : secondProduct,
    )
    let resolveUpload: ((value: Product) => void) | undefined
    let resolveDelete: ((value: Product) => void) | undefined
    catalogMocks.uploadProductImage.mockReturnValue(
      new Promise<Product>((resolve) => {
        resolveUpload = resolve
      }),
    )
    catalogMocks.deleteProductImage.mockReturnValue(
      new Promise<Product>((resolve) => {
        resolveDelete = resolve
      }),
    )
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[0]!)
    await interaction.upload(
      screen.getByLabelText('选择图片'),
      new File(['image'], 'new.webp', { type: 'image/webp' }),
    )
    await interaction.click(screen.getByRole('button', { name: '上传图片' }))
    await waitFor(() => expect(catalogMocks.uploadProductImage).toHaveBeenCalledOnce())
    await interaction.click(screen.getByRole('button', { name: '关闭商品表单' }))
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[1]!)
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))
    resolveUpload?.({ ...firstProduct, images: [...(firstProduct.images ?? []), image] })
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))

    await interaction.click(screen.getByRole('button', { name: '关闭商品表单' }))
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[0]!)
    await interaction.click(await screen.findByRole('button', { name: '删除图片 4' }))
    await waitFor(() => expect(catalogMocks.deleteProductImage).toHaveBeenCalledOnce())
    await interaction.click(screen.getByRole('button', { name: '关闭商品表单' }))
    await interaction.click(screen.getAllByRole('button', { name: '编辑商品' })[1]!)
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))
    resolveDelete?.({ ...firstProduct, images: [] })
    await waitFor(() => expect(screen.getByLabelText('商品名称 *')).toHaveValue('美式咖啡'))
  })

  it('ignores an older cleanup-filter response that arrives last', async () => {
    const failedJob: ObjectCleanupJob = {
      id: 51,
      created_by: 1,
      object_key: 'products/cleanup/failed.webp',
      reason: 'image_deleted',
      status: 'failed',
      attempts: 1,
      last_error: 'storage unavailable',
      not_before: null,
      created_at: '2026-07-15T00:00:00Z',
      updated_at: '2026-07-15T00:01:00Z',
      completed_at: null,
    }
    const pendingJob: ObjectCleanupJob = {
      ...failedJob,
      id: 52,
      object_key: 'products/cleanup/pending.webp',
      status: 'pending',
      attempts: 0,
      last_error: null,
    }
    let resolveFailed: ((jobs: ObjectCleanupJob[]) => void) | undefined
    let resolvePending: ((jobs: ObjectCleanupJob[]) => void) | undefined
    catalogMocks.listObjectCleanupJobs.mockImplementation((status?: string) =>
      new Promise<ObjectCleanupJob[]>((resolve) => {
        if (status === 'pending') resolvePending = resolve
        else resolveFailed = resolve
      }),
    )

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    await interaction.selectOptions(screen.getByLabelText('清理任务状态'), 'pending')
    resolvePending?.([pendingJob])
    expect(await screen.findByText(pendingJob.object_key)).toBeVisible()
    resolveFailed?.([failedJob])
    await waitFor(() => expect(screen.getByText(pendingJob.object_key)).toBeVisible())
    expect(screen.queryByText(failedJob.object_key)).not.toBeInTheDocument()
  })

  it('shows import details and retries failed object cleanup jobs', async () => {
    const importJob = {
      id: 24,
      status: 'failed',
      original_filename: 'broken.xlsx',
      workbook_sha256: 'abc123',
      idempotency_key: 'catalog-24',
      dry_run: false,
      summary: { products: 2, errors: 1 },
      errors: [{ sheet: 'Products', row: 3, field: 'name', message: '缺少商品名称' }],
      promoted_staging_keys: [],
      created_at: '2026-07-15T00:00:00Z',
      completed_at: '2026-07-15T00:01:00Z',
    }
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
    catalogMocks.listImportJobs.mockResolvedValue([importJob])
    catalogMocks.getImportJob.mockResolvedValue(importJob)
    const pendingCleanupJob = {
      ...cleanupJob,
      id: 32,
      object_key: 'products/10/gallery/pending.webp',
      status: 'pending',
      attempts: 0,
      last_error: null,
    }
    catalogMocks.listObjectCleanupJobs
      .mockResolvedValueOnce([cleanupJob, pendingCleanupJob])
      .mockResolvedValueOnce([])
    catalogMocks.retryObjectCleanupJob.mockResolvedValue({
      ...cleanupJob,
      status: 'completed',
      completed_at: '2026-07-15T00:02:00Z',
    })

    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    await interaction.click(await screen.findByRole('button', { name: '查看详情' }))

    expect(await screen.findByText('缺少商品名称')).toBeVisible()
    expect(catalogMocks.getImportJob).toHaveBeenCalledWith(24)
    expect(screen.getByText('products/9/gallery/orphan.webp')).toBeVisible()
    expect(screen.getAllByRole('button', { name: '重试' })).toHaveLength(1)
    await interaction.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(catalogMocks.retryObjectCleanupJob).toHaveBeenCalledWith(31))
    expect(await screen.findByText('清理任务 #31 已完成。')).toBeVisible()
    expect(screen.queryByText('products/9/gallery/orphan.webp')).not.toBeInTheDocument()
  })
})
