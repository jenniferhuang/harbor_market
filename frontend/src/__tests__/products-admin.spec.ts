import { render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthApi, User } from '../api/auth'
import type { Category, Product } from '../api/catalog'
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
  deleteProductImage: vi.fn(),
  downloadTemplate: vi.fn(),
  exportProducts: vi.fn(),
  importProducts: vi.fn(),
  listImportJobs: vi.fn(),
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
  description: null,
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
  catalogMocks.importProducts.mockResolvedValue({
    job_id: 12,
    dry_run: true,
    valid: false,
    summary: { products: 1 },
    errors: [{ sheet: 'Products', row: 2, field: 'name', message: '商品名称不能为空' }],
  })
  catalogMocks.listImportJobs.mockResolvedValue([])
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
  })

  it('reuses the selected workbook idempotency key after a transport failure', async () => {
    catalogMocks.importProducts
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce({
        job_id: 13,
        dry_run: false,
        valid: true,
        summary: { products: 1 },
        errors: [],
      })
    await renderView()
    const interaction = userEvent.setup()
    await screen.findByText('生椰拿铁')
    await interaction.click(screen.getByRole('tab', { name: /Excel/ }))
    const workbook = new File(['xlsx'], 'catalog.xlsx')
    await interaction.upload(screen.getByLabelText('Excel 文件'), workbook)

    const commitButton = screen.getByRole('button', { name: '正式导入' })
    await interaction.click(commitButton)
    await screen.findByText('connection lost')
    await interaction.click(commitButton)
    await screen.findByText('Excel 已成功导入。')

    const firstKey = catalogMocks.importProducts.mock.calls[0]?.[2]
    const secondKey = catalogMocks.importProducts.mock.calls[1]?.[2]
    expect(firstKey).toMatch(/^catalog-commit-/)
    expect(secondKey).toBe(firstKey)
  })
})
