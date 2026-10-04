import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import userEvent from '@testing-library/user-event'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AuthApi } from '../api/auth'
import type { Category, Product } from '../api/catalog'
import { ApiError } from '../api/client'
import type { HistoricalOrder, ShopCoupon, ShopMedia, ShopStore } from '../api/shop'
import { createAuthStore } from '../auth/store'
import { authKey } from '../auth/useAuth'
import StoreAdminView from '../views/StoreAdminView.vue'

const shop = vi.hoisted(() => ({
  getStore: vi.fn(), updateStore: vi.fn(), listCustomers: vi.fn(), listMedia: vi.fn(),
  uploadMedia: vi.fn(), updateMedia: vi.fn(), deleteMedia: vi.fn(), listCoupons: vi.fn(),
  createCoupon: vi.fn(), updateCoupon: vi.fn(), listOrders: vi.fn(), createOrder: vi.fn(), voidOrder: vi.fn(),
}))
const catalog = vi.hoisted(() => ({ listCategories: vi.fn(), listProducts: vi.fn() }))
vi.mock('../api/shop', async (original) => ({ ...await original<typeof import('../api/shop')>(), shopAdminApi: shop }))
vi.mock('../api/catalog', async (original) => ({ ...await original<typeof import('../api/catalog')>(), catalogAdminApi: catalog }))

const store: ShopStore = { name: '港湾咖啡', phone: '123456', address: '厦门市思明区', latitude: null, longitude: null, owner_customer_id: null }
const category: Category = { id: 3, code: 'coffee', name: '咖啡', description: '', parent_id: null, sort_order: 0, is_active: true }
const product: Product = {
  id: 9, product_code: 'LATTE-01', name: '生椰拿铁', subtitle: '', category_id: 3,
  status: 'published', base_price_cents: 1990, market_price_cents: null, currency: 'CNY',
  unit: '杯', stock_status: 'in_stock', inventory_count: null, featured: false, sort_order: 0,
  tags: [], selling_points: [], description: '', ingredients: null, allergen_info: null,
  specifications: [], skus: [], images: [],
}
const media: ShopMedia = { id: 8, kind: 'carousel', category_id: null, title: '店内环境', sort_order: 0, is_active: true, url: '/api/v1/shop/media/8', media_type: 'video' }
const coupon: ShopCoupon = { id: 7, title: '满30减5', min_spend_cents: 3000, discount_cents: 500, starts_at: '2020-01-01T00:00:00Z', expires_at: '2027-01-01T00:00:00Z', is_active: true }
const order: HistoricalOrder = { id: 12, external_reference: 'RECEIPT-001', customer_id: 11, completed_at: '2020-01-01T00:00:00Z', status: 'completed', total_cents: 2980, items: [{ product_code: 'LATTE-01', product_name: '生椰拿铁', quantity: 2, unit_price_cents: 1490 }] }

async function renderView() {
  const administrator = { id: 1, username: 'store-admin', is_admin: true }
  const api: AuthApi = { register: vi.fn(), login: vi.fn(async () => administrator), me: vi.fn(async () => administrator), logout: vi.fn() }
  const auth = createAuthStore(api)
  await auth.restore()
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'home', component: { template: '<p>首页</p>' } },
    { path: '/login', name: 'login', component: { template: '<p>登录</p>' } },
    { path: '/admin/products', name: 'admin-products', component: { template: '<p>商品管理</p>' } },
    { path: '/admin/shop', name: 'admin-shop', component: StoreAdminView },
  ] })
  await router.push('/admin/shop')
  render(StoreAdminView, { global: { plugins: [router], provide: { [authKey as symbol]: auth } } })
  await nextTick()
  await waitFor(() => expect(screen.getByRole('button', { name: '刷新数据' })).toBeEnabled())
  return userEvent.setup({ applyAccept: false })
}

beforeEach(() => {
  Object.values(shop).forEach((mock) => mock.mockReset())
  Object.values(catalog).forEach((mock) => mock.mockReset())
  shop.getStore.mockResolvedValue(store)
  shop.updateStore.mockImplementation(async (input) => input)
  shop.listCustomers.mockResolvedValue([{ id: 11, nickname: '海港顾客' }])
  shop.listMedia.mockResolvedValue([])
  shop.listCoupons.mockResolvedValue([])
  shop.listOrders.mockResolvedValue([])
  shop.deleteMedia.mockResolvedValue(undefined)
  catalog.listCategories.mockResolvedValue([category])
  catalog.listProducts.mockResolvedValue({ items: [product], total: 1, page: 1, page_size: 100 })
})

describe('shop administration view', () => {
  it('saves real store data and optional owner and coordinate values', async () => {
    const interaction = await renderView()
    expect(screen.getByLabelText('商家名字 *')).toHaveValue('港湾咖啡')
    await interaction.clear(screen.getByLabelText('商家名字 *'))
    await interaction.type(screen.getByLabelText('商家名字 *'), ' 港湾集市 ')
    await interaction.type(screen.getByLabelText('纬度（可选）'), '24.48')
    await interaction.click(screen.getByRole('button', { name: '保存商家信息' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('经度和纬度须一起填写或一起留空。')
    expect(shop.updateStore).not.toHaveBeenCalled()
    await interaction.type(screen.getByLabelText('经度（可选）'), '118.08')
    await interaction.selectOptions(screen.getByLabelText('商家微信账号（可选）'), '11')
    await interaction.click(screen.getByRole('button', { name: '保存商家信息' }))
    await waitFor(() => expect(shop.updateStore).toHaveBeenCalledWith({ ...store, name: '港湾集市', latitude: 24.48, longitude: 118.08, owner_customer_id: 11 }))
    await interaction.clear(screen.getByLabelText('纬度（可选）'))
    await interaction.clear(screen.getByLabelText('经度（可选）'))
    await interaction.selectOptions(screen.getByLabelText('商家微信账号（可选）'), '')
    await interaction.click(screen.getByRole('button', { name: '保存商家信息' }))
    expect(shop.updateStore).toHaveBeenLastCalledWith({ ...store, name: '港湾集市' })
  })

  it('keeps a failed store load disabled and can retry without overwriting it', async () => {
    shop.getStore.mockRejectedValueOnce(new ApiError(503, 'storage unavailable'))
    const interaction = await renderView()
    expect(screen.getByRole('alert')).toHaveTextContent('商家信息加载失败')
    expect(screen.getByRole('alert')).not.toHaveTextContent('storage unavailable')
    expect(screen.getByRole('button', { name: '保存商家信息' })).toBeDisabled()
    await interaction.click(screen.getByRole('button', { name: '保存商家信息' }))
    expect(shop.updateStore).not.toHaveBeenCalled()
    await interaction.click(screen.getByRole('button', { name: '刷新数据' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '保存商家信息' })).toBeEnabled())
    expect(screen.getByLabelText('商家名字 *')).toHaveValue('港湾咖啡')
  })

  it('loads later pages for customer selection, coupons, orders, and products', async () => {
    shop.listCustomers.mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => ({ id: index + 1, nickname: `顾客${index + 1}` }))).mockResolvedValueOnce([{ id: 101, nickname: '后页顾客' }])
    shop.listCoupons.mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => ({ ...coupon, id: index + 1 }))).mockResolvedValueOnce([])
    shop.listOrders.mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => ({ ...order, id: index + 1 }))).mockResolvedValueOnce([])
    shop.listMedia.mockResolvedValueOnce(Array.from({ length: 100 }, (_, index) => ({ ...media, id: index + 1 }))).mockResolvedValueOnce([])
    catalog.listProducts.mockResolvedValueOnce({ items: [product], total: 101, page: 1, page_size: 100 }).mockResolvedValueOnce({ items: [{ ...product, id: 10, name: '后页商品', product_code: 'LATTE-02' }], total: 101, page: 2, page_size: 100 })
    const interaction = await renderView()
    expect(shop.listCustomers).toHaveBeenLastCalledWith({ page: 2, page_size: 100 })
    expect(shop.listCoupons).toHaveBeenLastCalledWith({ page: 2, page_size: 100 })
    expect(shop.listOrders).toHaveBeenLastCalledWith({ page: 2, page_size: 100 })
    expect(shop.listMedia).toHaveBeenLastCalledWith({ page: 2, page_size: 100 })
    expect(screen.getByRole('option', { name: '后页顾客 · #101' })).toBeInTheDocument()
    await interaction.click(screen.getByRole('tab', { name: '已成交订单' }))
    expect(screen.getByRole('option', { name: '后页商品 · LATTE-02' })).toBeInTheDocument()
  })

  it('uploads video only for carousel and reloads after replacing a category image', async () => {
    const interaction = await renderView()
    await interaction.click(screen.getByRole('tab', { name: '首页资料' }))
    const video = new File(['video'], 'shop.mp4', { type: 'video/mp4' })
    shop.uploadMedia.mockResolvedValueOnce(media)
    shop.listMedia.mockResolvedValueOnce([media])
    await interaction.type(screen.getByLabelText('资料标题'), '店内环境')
    await interaction.upload(screen.getByLabelText('宣传文件 *'), video)
    await interaction.click(screen.getByRole('button', { name: '上传首页资料' }))
    await waitFor(() => expect(shop.uploadMedia).toHaveBeenCalledWith(video, { kind: 'carousel', category_id: null, title: '店内环境', sort_order: 0, is_active: true }))
    expect(await screen.findByText('首页资料已保存。')).toBeVisible()
    await interaction.selectOptions(screen.getByLabelText('资料用途'), 'category')
    await interaction.selectOptions(screen.getByLabelText('代表图类目 *'), '3')
    await interaction.upload(screen.getByLabelText('宣传文件 *'), video)
    await interaction.click(screen.getByRole('button', { name: '上传首页资料' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('分类代表图和公告须使用图片。')
    expect(shop.uploadMedia).toHaveBeenCalledTimes(1)
    const image = new File(['image'], 'coffee.webp', { type: 'image/webp' })
    const categoryMedia = { ...media, id: 9, kind: 'category', category_id: 3, media_type: 'image' }
    shop.uploadMedia.mockResolvedValueOnce(categoryMedia)
    shop.listMedia.mockResolvedValueOnce([media, categoryMedia])
    await interaction.upload(screen.getByLabelText('宣传文件 *'), image)
    await interaction.click(screen.getByRole('checkbox', { name: '在首页显示' }))
    await interaction.click(screen.getByRole('button', { name: '上传首页资料' }))
    await waitFor(() => expect(shop.uploadMedia).toHaveBeenLastCalledWith(image, { kind: 'category', category_id: 3, title: '', sort_order: 0, is_active: false }))
  })

  it('rejects oversized media and retains rows when deletion fails', async () => {
    shop.listMedia.mockResolvedValue([media])
    const interaction = await renderView()
    await interaction.click(screen.getByRole('tab', { name: '首页资料' }))
    const image = new File(['image'], 'large.png', { type: 'image/png' })
    Object.defineProperty(image, 'size', { value: 5 * 1024 * 1024 + 1 })
    await interaction.upload(screen.getByLabelText('宣传文件 *'), image)
    await interaction.click(screen.getByRole('button', { name: '上传首页资料' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('图片不能超过 5 MiB。')
    expect(shop.uploadMedia).not.toHaveBeenCalled()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    shop.deleteMedia.mockRejectedValueOnce(new Error('network unavailable'))
    await interaction.click(screen.getByRole('button', { name: '删除资料 8' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('首页资料删除失败。')
    expect(screen.getByText('店内环境')).toBeVisible()
    await interaction.click(screen.getByRole('button', { name: '删除资料 8' }))
    await waitFor(() => expect(screen.queryByText('店内环境')).not.toBeInTheDocument())
  })

  it('validates coupon limits and dates and converts yuan to integer cents and timezone timestamps', async () => {
    shop.createCoupon.mockImplementation(async (input) => ({ ...input, id: 7 }))
    const interaction = await renderView()
    await interaction.click(screen.getByRole('tab', { name: '优惠券' }))
    await interaction.type(screen.getByLabelText('优惠券名称 *'), ' 满30减5 ')
    await interaction.type(screen.getByLabelText('满减门槛（元）*'), '30.00')
    await interaction.type(screen.getByLabelText('优惠金额（元）*'), '35.50')
    await fireEvent.update(screen.getByLabelText('开始时间 *'), '2026-01-02T10:00')
    await fireEvent.update(screen.getByLabelText('结束时间 *'), '2026-01-01T10:00')
    await interaction.click(screen.getByRole('button', { name: '创建优惠券' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('优惠金额不能超过满减门槛。')
    await interaction.clear(screen.getByLabelText('优惠金额（元）*'))
    await interaction.type(screen.getByLabelText('优惠金额（元）*'), '5.50')
    await interaction.click(screen.getByRole('button', { name: '创建优惠券' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('结束时间须晚于开始时间。')
    expect(shop.createCoupon).not.toHaveBeenCalled()
    await fireEvent.update(screen.getByLabelText('结束时间 *'), '2026-02-01T10:00')
    await interaction.click(screen.getByRole('checkbox', { name: '启用优惠券' }))
    await interaction.click(screen.getByRole('button', { name: '创建优惠券' }))
    await waitFor(() => expect(shop.createCoupon).toHaveBeenCalledWith({ title: '满30减5', min_spend_cents: 3000, discount_cents: 550, starts_at: new Date('2026-01-02T10:00').toISOString(), expires_at: new Date('2026-02-01T10:00').toISOString(), is_active: false }))
    expect(await screen.findByRole('cell', { name: '满 ¥30.00 减 ¥5.50' })).toBeVisible()
  })

  it('records historical prices, displays the server total, and voids only confirmed records', async () => {
    shop.createOrder.mockResolvedValue(order)
    shop.voidOrder.mockResolvedValue({ ...order, status: 'voided' })
    const interaction = await renderView()
    await interaction.click(screen.getByRole('tab', { name: '已成交订单' }))
    await interaction.type(screen.getByLabelText('原始成交单号 *'), 'RECEIPT-001')
    await interaction.selectOptions(screen.getByLabelText('成交顾客（可选）'), '11')
    await fireEvent.update(screen.getByLabelText('成交时间 *'), '2020-01-01T10:00')
    await interaction.selectOptions(screen.getByLabelText('成交商品 1 *'), 'LATTE-01')
    expect(screen.getByLabelText('实际成交单价 1（元）*')).toHaveValue('19.90')
    await interaction.clear(screen.getByLabelText('实际成交单价 1（元）*'))
    await interaction.type(screen.getByLabelText('实际成交单价 1（元）*'), '14.90')
    await interaction.clear(screen.getByLabelText('成交数量 1 *'))
    await interaction.type(screen.getByLabelText('成交数量 1 *'), '2')
    await interaction.click(screen.getByRole('button', { name: '录入已成交订单' }))
    await waitFor(() => expect(shop.createOrder).toHaveBeenCalledWith({ external_reference: 'RECEIPT-001', customer_id: 11, completed_at: new Date('2020-01-01T10:00').toISOString(), items: [{ product_code: 'LATTE-01', quantity: 2, unit_price_cents: 1490 }] }))
    expect(await screen.findByRole('cell', { name: '¥29.80' })).toBeVisible()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await interaction.click(screen.getByRole('button', { name: '撤销成交记录 RECEIPT-001' }))
    expect(shop.voidOrder).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    await interaction.click(screen.getByRole('button', { name: '撤销成交记录 RECEIPT-001' }))
    expect(await screen.findByRole('cell', { name: '已撤销' })).toBeVisible()
    expect(shop.voidOrder).toHaveBeenCalledWith(12)
  })

  it('disables editing and refresh during a pending store save', async () => {
    let resolve: (value: ShopStore) => void = () => undefined
    shop.updateStore.mockImplementation(() => new Promise<ShopStore>((done) => { resolve = done }))
    const interaction = await renderView()
    await interaction.click(screen.getByRole('button', { name: '保存商家信息' }))
    expect(screen.getByLabelText('商家名字 *')).toBeDisabled()
    expect(screen.getByRole('button', { name: '刷新数据' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '正在保存…' })).toBeDisabled()
    resolve(store)
    await waitFor(() => expect(screen.getByRole('button', { name: '保存商家信息' })).toBeEnabled())
    expect(shop.updateStore).toHaveBeenCalledTimes(1)
  })
})
