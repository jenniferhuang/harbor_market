<script setup lang="ts">
import { ArrowLeft, ImagePlus, LoaderCircle, LogOut, PackageSearch, Plus, RefreshCw, Save, Trash2 } from 'lucide-vue-next'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { catalogAdminApi, type Category, type Product } from '../api/catalog'
import { ApiError } from '../api/client'
import { chineseMessage } from '../api/messages'
import {
  shopAdminApi,
  SHOP_IMAGE_MAX_BYTES,
  SHOP_VIDEO_MAX_BYTES,
  type HistoricalOrder,
  type ShopCoupon,
  type ShopCustomer,
  type ShopMedia,
  type ShopMediaKind,
  type ShopStore,
  type ShopPage,
} from '../api/shop'
import { useAuth } from '../auth/useAuth'
import AppBrand from '../components/AppBrand.vue'

const tabs = [
  { id: 'store', label: '商家信息' }, { id: 'media', label: '首页资料' },
  { id: 'coupons', label: '优惠券' }, { id: 'orders', label: '已成交订单' },
] as const
type Tab = typeof tabs[number]['id']
type Resource = Tab | 'customers' | 'categories' | 'products'
const auth = useAuth()
const router = useRouter()
const activeTab = ref<Tab>('store')
const notice = ref<{ success: boolean; text: string } | null>(null)
const loading = ref(false)
const loadErrors = reactive<Partial<Record<Resource, string>>>({})
const loaded = reactive<Record<Resource, boolean>>({ store: false, media: false, coupons: false, orders: false, customers: false, categories: false, products: false })
const saving = reactive<Record<Tab, boolean>>({ store: false, media: false, coupons: false, orders: false })
const loggingOut = ref(false)
const storeForm = reactive({ name: '', phone: '', address: '', latitude: '', longitude: '', owner_customer_id: '' })
const customers = ref<ShopCustomer[]>([])
const categories = ref<Category[]>([])
const products = ref<Product[]>([])
const mediaRows = ref<ShopMedia[]>([])
const coupons = ref<ShopCoupon[]>([])
const orders = ref<HistoricalOrder[]>([])
const mediaForm = reactive({ kind: 'carousel' as ShopMediaKind, category_id: '', title: '', sort_order: '0', is_active: true })
const mediaFile = ref<File | null>(null)
const mediaInput = ref<HTMLInputElement | null>(null)
const editingMediaId = ref<number | null>(null)
const deletingMediaId = ref<number | null>(null)
const couponForm = reactive({ title: '', min_spend_yuan: '', discount_yuan: '', starts_at: '', expires_at: '', is_active: true })
const editingCouponId = ref<number | null>(null)
const voidingOrderId = ref<number | null>(null)
const orderForm = reactive({ external_reference: '', customer_id: '', completed_at: '' })
let nextItemId = 1
const orderItems = ref([{ id: nextItemId++, product_code: '', quantity: '1', price_yuan: '' }])
const categoryById = computed(() => new Map(categories.value.map((category) => [category.id, category.name])))
const customerById = computed(() => new Map(customers.value.map((customer) => [customer.id, customer.nickname])))
const acceptsMedia = computed(() => mediaForm.kind === 'carousel' ? 'image/jpeg,image/png,image/webp,video/mp4' : 'image/jpeg,image/png,image/webp')
const loadingError = computed(() => Object.values(loadErrors).join('；'))
const busy = computed(() => loading.value || Object.values(saving).some(Boolean) || deletingMediaId.value !== null || voidingOrderId.value !== null)

function message(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return [error.message, ...Object.values(error.fieldErrors)].join(' ')
  return error instanceof Error ? chineseMessage(error.message, fallback) : fallback
}

function success(text: string) { notice.value = { success: true, text } }
function failure(error: unknown, fallback: string) { notice.value = { success: false, text: message(error, fallback) } }

function setStore(store: ShopStore) {
  Object.assign(storeForm, {
    name: store.name, phone: store.phone, address: store.address,
    latitude: store.latitude === null ? '' : String(store.latitude),
    longitude: store.longitude === null ? '' : String(store.longitude),
    owner_customer_id: store.owner_customer_id === null ? '' : String(store.owner_customer_id),
  })
}

async function loadResource<T>(key: Resource, label: string, fetch: () => Promise<T>, apply: (value: T) => void) {
  try {
    apply(await fetch())
    loaded[key] = true
    delete loadErrors[key]
  } catch (error) {
    loaded[key] = false
    loadErrors[key] = `${label}加载失败：${message(error, '请稍后重试。')}`
  }
}

async function loadProducts(): Promise<Product[]> {
  const first = await catalogAdminApi.listProducts({ page: 1, page_size: 100 })
  const result = [...first.items]
  for (let page = 2; page <= Math.ceil(first.total / 100); page += 1) {
    const next = await catalogAdminApi.listProducts({ page, page_size: 100 })
    if (!next.items.length) break
    result.push(...next.items)
  }
  return result
}

async function loadPages<T extends { id: number }>(fetch: (page: ShopPage) => Promise<T[]>): Promise<T[]> {
  const result: T[] = []
  const ids = new Set<number>()
  for (let page = 1; ; page += 1) {
    const rows = await fetch({ page, page_size: 100 })
    if (rows.some((row) => ids.has(row.id))) throw new Error('分页数据重复，请刷新后重试。')
    rows.forEach((row) => ids.add(row.id))
    result.push(...rows)
    if (rows.length < 100) return result
  }
}

async function loadAll() {
  if (busy.value) return
  loading.value = true
  await Promise.all([
    loadResource('store', '商家信息', shopAdminApi.getStore, setStore),
    loadResource('customers', '微信顾客列表', () => loadPages(shopAdminApi.listCustomers), (value) => { customers.value = value }),
    loadResource('categories', '类目', catalogAdminApi.listCategories, (value) => { categories.value = value }),
    loadResource('products', '商品', loadProducts, (value) => { products.value = value }),
    loadResource('media', '首页资料', () => loadPages(shopAdminApi.listMedia), (value) => { mediaRows.value = value }),
    loadResource('coupons', '优惠券', () => loadPages(shopAdminApi.listCoupons), (value) => { coupons.value = value }),
    loadResource('orders', '已成交订单', () => loadPages(shopAdminApi.listOrders), (value) => { orders.value = value }),
  ])
  loading.value = false
}

function integer(value: string, label: string, min = 0, max = 2 ** 31 - 1): number {
  if (!/^-?\d+$/.test(value.trim())) throw new Error(`${label}必须是整数。`)
  const parsed = Number(value)
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) throw new Error(`${label}须介于 ${min} 和 ${max} 之间。`)
  return parsed
}

function cents(value: string, label: string): number {
  if (!/^\d+(?:\.\d{1,2})?$/.test(value.trim())) throw new Error(`${label}请输入非负金额，最多保留两位小数。`)
  const [yuan = '0', decimals = ''] = value.trim().split('.')
  return integer(String(Number(yuan) * 100 + Number(decimals.padEnd(2, '0'))), label)
}

function money(value: number): string { return (value / 100).toFixed(2) }
function required(value: string, label: string): string {
  if (!value.trim()) throw new Error(`请填写${label}。`)
  return value.trim()
}

function optionalCoordinate(value: string, label: string, limit: number): number | null {
  if (!value.trim()) return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || Math.abs(parsed) > limit) throw new Error(`${label}须介于 -${limit} 和 ${limit} 之间。`)
  return parsed
}

function isoDate(value: string, label: string): string {
  if (!value || !Number.isFinite(Date.parse(value))) throw new Error(`请选择有效的${label}。`)
  return new Date(value).toISOString()
}

function localDate(value: string): string {
  const date = new Date(value)
  const pad = (number: number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function saveStore() {
  if (!loaded.store || loading.value || saving.store) return
  saving.store = true
  notice.value = null
  try {
    const latitude = optionalCoordinate(storeForm.latitude, '纬度', 90)
    const longitude = optionalCoordinate(storeForm.longitude, '经度', 180)
    if ((latitude === null) !== (longitude === null)) throw new Error('经度和纬度须一起填写或一起留空。')
    setStore(await shopAdminApi.updateStore({
      name: required(storeForm.name, '商家名字'), phone: storeForm.phone.trim(), address: storeForm.address.trim(),
      latitude, longitude,
      owner_customer_id: storeForm.owner_customer_id ? integer(storeForm.owner_customer_id, '商家微信账号', 1) : null,
    }))
    success('商家信息已保存。')
  } catch (error) { failure(error, '商家信息保存失败。') } finally { saving.store = false }
}

function resetMedia() {
  editingMediaId.value = null
  mediaFile.value = null
  if (mediaInput.value) mediaInput.value.value = ''
  Object.assign(mediaForm, { kind: 'carousel', category_id: '', title: '', sort_order: '0', is_active: true })
}

function editMedia(row: ShopMedia) {
  if (saving.media || deletingMediaId.value !== null) return
  editingMediaId.value = row.id
  mediaFile.value = null
  Object.assign(mediaForm, { kind: row.kind, category_id: row.category_id === null ? '' : String(row.category_id), title: row.title, sort_order: String(row.sort_order), is_active: row.is_active })
}

function mediaKindChanged() {
  mediaFile.value = null
  if (mediaInput.value) mediaInput.value.value = ''
  if (mediaForm.kind !== 'category') mediaForm.category_id = ''
}

function selectMedia(event: Event) { mediaFile.value = (event.target as HTMLInputElement).files?.[0] ?? null }

function validateMedia(file: File) {
  const video = file.type === 'video/mp4' && /\.mp4$/i.test(file.name)
  const image = ['image/jpeg', 'image/png', 'image/webp'].includes(file.type)
  if (!image && !video) throw new Error('请选择 JPG、PNG、WebP 图片或 MP4 视频。')
  if (video && mediaForm.kind !== 'carousel') throw new Error('分类代表图和公告须使用图片。')
  if (file.size === 0) throw new Error('文件为空，请重新选择。')
  if (file.size > (video ? SHOP_VIDEO_MAX_BYTES : SHOP_IMAGE_MAX_BYTES)) throw new Error(video ? '视频不能超过 10 MiB。' : '图片不能超过 5 MiB。')
}

async function saveMedia() {
  if (!loaded.media || loading.value || saving.media || deletingMediaId.value !== null) return
  saving.media = true
  notice.value = null
  try {
    const input = { title: mediaForm.title.trim(), sort_order: integer(mediaForm.sort_order, '排序值', -(2 ** 31)), is_active: mediaForm.is_active }
    let result: ShopMedia
    if (editingMediaId.value !== null) result = await shopAdminApi.updateMedia(editingMediaId.value, input)
    else {
      const categoryId = mediaForm.kind === 'category' ? integer(mediaForm.category_id, '代表图类目', 1) : null
      if (categoryId !== null && !categories.value.some((category) => category.id === categoryId)) throw new Error('请选择有效的类目。')
      if (!mediaFile.value) throw new Error('请先选择宣传文件。')
      validateMedia(mediaFile.value)
      result = await shopAdminApi.uploadMedia(mediaFile.value, { ...input, kind: mediaForm.kind, category_id: categoryId })
    }
    mediaRows.value = [...mediaRows.value.filter((row) => row.id !== result.id), result]
    await loadResource('media', '首页资料', () => loadPages(shopAdminApi.listMedia), (value) => { mediaRows.value = value })
    resetMedia()
    success('首页资料已保存。')
  } catch (error) { failure(error, '首页资料保存失败。') } finally { saving.media = false }
}

async function deleteMedia(row: ShopMedia) {
  if (loading.value || deletingMediaId.value !== null || saving.media || !window.confirm(`确定删除“${row.title || mediaKindLabel(row.kind)}”吗？`)) return
  deletingMediaId.value = row.id
  notice.value = null
  try {
    await shopAdminApi.deleteMedia(row.id)
    mediaRows.value = mediaRows.value.filter((item) => item.id !== row.id)
    if (editingMediaId.value === row.id) resetMedia()
    success('首页资料已删除。')
  } catch (error) { failure(error, '首页资料删除失败。') } finally { deletingMediaId.value = null }
}

function mediaKindLabel(kind: ShopMediaKind): string {
  return { carousel: '店内宣传', category: '分类代表图', announcement: '商家公告' }[kind]
}

function previewUrl(row: ShopMedia): string {
  if (!row.is_active || (row.kind === 'category' && !categories.value.some((category) => category.id === row.category_id && category.is_active))) return ''
  try {
    const url = new URL(row.url, window.location.origin)
    return url.origin === window.location.origin && ['http:', 'https:'].includes(url.protocol) ? url.href : ''
  } catch { return '' }
}

function resetCoupon() {
  editingCouponId.value = null
  Object.assign(couponForm, { title: '', min_spend_yuan: '', discount_yuan: '', starts_at: '', expires_at: '', is_active: true })
}

function editCoupon(row: ShopCoupon) {
  if (saving.coupons) return
  editingCouponId.value = row.id
  Object.assign(couponForm, { title: row.title, min_spend_yuan: money(row.min_spend_cents), discount_yuan: money(row.discount_cents), starts_at: localDate(row.starts_at), expires_at: localDate(row.expires_at), is_active: row.is_active })
}

async function saveCoupon() {
  if (!loaded.coupons || loading.value || saving.coupons) return
  saving.coupons = true
  notice.value = null
  try {
    const input = {
      title: required(couponForm.title, '优惠券名称'),
      min_spend_cents: cents(couponForm.min_spend_yuan, '满减门槛'),
      discount_cents: cents(couponForm.discount_yuan, '优惠金额'),
      starts_at: isoDate(couponForm.starts_at, '开始时间'), expires_at: isoDate(couponForm.expires_at, '结束时间'),
      is_active: couponForm.is_active,
    }
    if (input.discount_cents === 0) throw new Error('优惠金额须大于零。')
    if (input.min_spend_cents === 0) throw new Error('满减门槛须大于零。')
    if (input.discount_cents > input.min_spend_cents) throw new Error('优惠金额不能超过满减门槛。')
    if (Date.parse(input.expires_at) <= Date.parse(input.starts_at)) throw new Error('结束时间须晚于开始时间。')
    const result = editingCouponId.value === null ? await shopAdminApi.createCoupon(input) : await shopAdminApi.updateCoupon(editingCouponId.value, input)
    coupons.value = [...coupons.value.filter((row) => row.id !== result.id), result]
    resetCoupon()
    success('优惠券已保存。')
  } catch (error) { failure(error, '优惠券保存失败。') } finally { saving.coupons = false }
}

function selectOrderProduct(index: number) {
  const line = orderItems.value[index]
  if (!line) return
  const product = products.value.find((item) => item.product_code === line.product_code)
  line.price_yuan = product ? money(product.base_price_cents) : ''
}

async function saveOrder() {
  if (!loaded.orders || !loaded.products || loading.value || saving.orders || voidingOrderId.value !== null) return
  saving.orders = true
  notice.value = null
  try {
    const input = {
      external_reference: required(orderForm.external_reference, '原始成交单号'),
      customer_id: orderForm.customer_id ? integer(orderForm.customer_id, '顾客', 1) : null,
      completed_at: isoDate(orderForm.completed_at, '成交时间'),
      items: orderItems.value.map((line) => {
        if (!products.value.some((product) => product.product_code === line.product_code)) throw new Error('请为每一行选择商品。')
        return { product_code: line.product_code, quantity: integer(line.quantity, '成交数量', 1, 100_000), unit_price_cents: cents(line.price_yuan, '实际成交单价') }
      }),
    }
    if (!input.items.length) throw new Error('请至少填写一件成交商品。')
    if (input.items.length > 100) throw new Error('每笔成交记录最多填写 100 行商品。')
    if (input.items.reduce((total, item) => total + item.quantity * item.unit_price_cents, 0) > 2 ** 31 - 1) throw new Error('成交总金额超过允许范围。')
    if (Date.parse(input.completed_at) > Date.now()) throw new Error('成交时间不能晚于当前时间。')
    const result = await shopAdminApi.createOrder(input)
    orders.value = [result, ...orders.value.filter((row) => row.id !== result.id)]
    Object.assign(orderForm, { external_reference: '', customer_id: '', completed_at: '' })
    orderItems.value = [{ id: nextItemId++, product_code: '', quantity: '1', price_yuan: '' }]
    success('已成交订单已录入，购买排行将按有效记录更新。')
  } catch (error) { failure(error, '成交记录保存失败。') } finally { saving.orders = false }
}

async function voidOrder(row: HistoricalOrder) {
  if (loading.value || voidingOrderId.value !== null || saving.orders || row.status !== 'completed' || !window.confirm(`确定撤销成交记录“${row.external_reference}”吗？撤销后不再计入购买排行。`)) return
  voidingOrderId.value = row.id
  notice.value = null
  try {
    const result = await shopAdminApi.voidOrder(row.id)
    orders.value = orders.value.map((item) => item.id === result.id ? result : item)
    success('成交记录已撤销。')
  } catch (error) { failure(error, '成交记录撤销失败。') } finally { voidingOrderId.value = null }
}

function tabKeydown(event: KeyboardEvent) {
  const index = tabs.findIndex((tab) => tab.id === activeTab.value)
  const next = event.key === 'ArrowRight' ? (index + 1) % tabs.length : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : null
  if (next === null) return
  event.preventDefault()
  activeTab.value = tabs[next]!.id
  requestAnimationFrame(() => document.getElementById(`shop-tab-${activeTab.value}`)?.focus())
}

async function logout() {
  if (loggingOut.value) return
  loggingOut.value = true
  try { await auth.logout(); await router.replace({ name: 'login' }) } catch (error) { failure(error, '退出登录失败。') } finally { loggingOut.value = false }
}

onMounted(loadAll)
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
          <RouterLink
            class="text-link"
            :to="{ name: 'admin-products' }"
          >
            <PackageSearch
              :size="16"
              aria-hidden="true"
            /> 商品管理
          </RouterLink>
          <button
            class="secondary-button"
            type="button"
            :disabled="loggingOut"
            @click="logout"
          >
            <LogOut
              :size="17"
              aria-hidden="true"
            /> 退出登录
          </button>
        </nav>
      </div>
    </header>
    <main class="admin-page shop-admin-page">
      <section class="admin-title-row">
        <div>
          <p class="eyebrow">
            商家管理后台
          </p><h1>商家与首页管理</h1><p>维护小程序首页的商家资料、宣传内容、优惠券和真实成交记录。</p>
        </div>
        <button
          class="filter-button"
          type="button"
          :disabled="busy"
          @click="loadAll"
        >
          <RefreshCw
            :size="17"
            :class="{ spin: loading }"
            aria-hidden="true"
          /> 刷新数据
        </button>
      </section>
      <div
        class="admin-tabs"
        role="tablist"
        aria-label="商家管理工作区"
        @keydown="tabKeydown"
      >
        <button
          v-for="tab in tabs"
          :id="`shop-tab-${tab.id}`"
          :key="tab.id"
          :class="['admin-tab', { 'admin-tab--active': activeTab === tab.id }]"
          type="button"
          role="tab"
          :aria-selected="activeTab === tab.id"
          :aria-controls="`shop-panel-${tab.id}`"
          :tabindex="activeTab === tab.id ? 0 : -1"
          @click="activeTab = tab.id"
        >
          {{ tab.label }}
        </button>
      </div>
      <p
        v-if="loading"
        class="section-help"
        role="status"
      >
        正在加载商家数据…
      </p>
      <p
        v-if="loadingError"
        class="notice notice--error"
        role="alert"
      >
        {{ loadingError }}
      </p>
      <p
        v-if="notice"
        :class="['notice', notice.success ? 'notice--success' : 'notice--error']"
        role="alert"
      >
        {{ notice.text }}
      </p>

      <section
        v-if="activeTab === 'store'"
        id="shop-panel-store"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="shop-tab-store"
      >
        <div class="workspace-heading">
          <div><h2>商家信息</h2><p>经纬度用于小程序的位置导航；微信商家账号可在“我”中维护商家资料。</p></div>
        </div>
        <form
          aria-label="商家信息表单"
          novalidate
          @submit.prevent="saveStore"
        >
          <fieldset
            class="shop-fieldset"
            :disabled="!loaded.store || loading || saving.store"
          >
            <div class="form-grid form-grid--two">
              <label class="admin-field"><span>商家名字 *</span><input
                v-model="storeForm.name"
                maxlength="160"
                autocomplete="organization"
              ></label>
              <label class="admin-field"><span>商家电话</span><input
                v-model="storeForm.phone"
                type="tel"
                maxlength="40"
                autocomplete="tel"
              ></label>
              <label class="admin-field admin-field--wide"><span>商家地址</span><input
                v-model="storeForm.address"
                maxlength="500"
                autocomplete="street-address"
              ></label>
              <label class="admin-field"><span>纬度（可选）</span><input
                v-model="storeForm.latitude"
                inputmode="decimal"
                placeholder="-90 至 90"
              ></label>
              <label class="admin-field"><span>经度（可选）</span><input
                v-model="storeForm.longitude"
                inputmode="decimal"
                placeholder="-180 至 180"
              ></label>
              <label class="admin-field admin-field--wide"><span>商家微信账号（可选）</span><select
                v-model="storeForm.owner_customer_id"
                :disabled="!loaded.customers"
              ><option value="">不绑定</option><option
                v-if="storeForm.owner_customer_id && !customerById.has(Number(storeForm.owner_customer_id))"
                :value="storeForm.owner_customer_id"
              >顾客 #{{ storeForm.owner_customer_id }}</option><option
                v-for="customer in customers"
                :key="customer.id"
                :value="String(customer.id)"
              >{{ customer.nickname }} · #{{ customer.id }}</option></select></label>
            </div>
            <button
              class="admin-primary-button"
              type="submit"
            >
              <LoaderCircle
                v-if="saving.store"
                class="spin"
                :size="17"
                aria-hidden="true"
              /><Save
                v-else
                :size="17"
                aria-hidden="true"
              /> {{ saving.store ? '正在保存…' : '保存商家信息' }}
            </button>
          </fieldset>
        </form>
      </section>

      <section
        v-else-if="activeTab === 'media'"
        id="shop-panel-media"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="shop-tab-media"
      >
        <div class="workspace-heading">
          <div><h2>首页宣传资料</h2><p>店内宣传支持图片与 MP4 视频混合轮播；分类代表图和商家公告使用静态图片。图片最大 5 MiB，视频最大 10 MiB、时长不超过 60 秒，实际限制以服务器设置为准。上传启用的公告或分类图会替换当前对应图片。</p></div>
        </div>
        <form
          class="editor-card"
          aria-label="首页资料表单"
          novalidate
          @submit.prevent="saveMedia"
        >
          <fieldset
            class="shop-fieldset"
            :disabled="!loaded.media || loading || saving.media || deletingMediaId !== null"
          >
            <div class="form-grid form-grid--three">
              <label class="admin-field"><span>资料用途</span><select
                v-model="mediaForm.kind"
                :disabled="editingMediaId !== null"
                @change="mediaKindChanged"
              ><option value="carousel">店内宣传</option><option value="category">分类代表图</option><option value="announcement">商家公告</option></select></label>
              <label
                v-if="mediaForm.kind === 'category'"
                class="admin-field"
              ><span>代表图类目 *</span><select
                v-model="mediaForm.category_id"
                :disabled="!loaded.categories || editingMediaId !== null"
              ><option value="">请选择类目</option><option
                v-for="category in categories"
                :key="category.id"
                :value="String(category.id)"
              >{{ category.name }}{{ category.is_active ? '' : '（已停用）' }}</option></select></label>
              <label class="admin-field"><span>资料标题</span><input
                v-model="mediaForm.title"
                maxlength="160"
              ></label>
              <label class="admin-field"><span>资料排序值</span><input
                v-model="mediaForm.sort_order"
                inputmode="numeric"
              ></label>
              <label
                v-if="editingMediaId === null"
                class="admin-field admin-field--file admin-field--wide"
              ><span>宣传文件 *</span><input
                ref="mediaInput"
                type="file"
                :accept="acceptsMedia"
                @change="selectMedia"
              ></label>
              <label class="admin-check"><input
                v-model="mediaForm.is_active"
                type="checkbox"
              ><span>在首页显示</span></label>
            </div>
            <div class="button-row">
              <button
                class="admin-primary-button"
                type="submit"
              >
                <LoaderCircle
                  v-if="saving.media"
                  class="spin"
                  :size="17"
                  aria-hidden="true"
                /><ImagePlus
                  v-else
                  :size="17"
                  aria-hidden="true"
                /> {{ editingMediaId === null ? '上传首页资料' : '保存资料信息' }}
              </button><button
                v-if="editingMediaId !== null"
                class="filter-button"
                type="button"
                @click="resetMedia"
              >
                取消编辑
              </button>
            </div>
          </fieldset>
        </form>
        <div
          v-if="mediaRows.length"
          class="shop-media-grid"
        >
          <article
            v-for="row in mediaRows"
            :key="row.id"
            class="shop-media-card"
          >
            <video
              v-if="row.media_type === 'video' && previewUrl(row)"
              :src="previewUrl(row)"
              controls
              preload="metadata"
              :aria-label="row.title || '店内宣传视频'"
            />
            <img
              v-else-if="previewUrl(row)"
              :src="previewUrl(row)"
              :alt="row.title || mediaKindLabel(row.kind)"
              loading="lazy"
            >
            <p
              v-else
              class="empty-inline"
            >
              {{ row.is_active ? '预览暂不可用' : '已停用，启用后可预览。' }}
            </p>
            <div class="shop-media-card__body">
              <strong>{{ row.title || mediaKindLabel(row.kind) }}</strong><p>{{ mediaKindLabel(row.kind) }}{{ row.category_id ? ` · ${categoryById.get(row.category_id) ?? '类目已删除'}` : '' }} · 排序 {{ row.sort_order }} · {{ row.is_active ? '显示中' : '已停用' }}</p><div class="button-row">
                <button
                  class="text-button"
                  type="button"
                  :disabled="saving.media || deletingMediaId !== null"
                  :aria-label="`编辑资料 ${row.id}`"
                  @click="editMedia(row)"
                >
                  编辑
                </button><button
                  class="text-button text-button--danger"
                  type="button"
                  :disabled="saving.media || deletingMediaId !== null"
                  :aria-label="`删除资料 ${row.id}`"
                  @click="deleteMedia(row)"
                >
                  删除
                </button>
              </div>
            </div>
          </article>
        </div>
        <p
          v-else-if="loaded.media && !loading"
          class="empty-inline"
        >
          暂无首页资料，请上传真实图片或视频。
        </p>
      </section>

      <section
        v-else-if="activeTab === 'coupons'"
        id="shop-panel-coupons"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="shop-tab-coupons"
      >
        <div class="workspace-heading">
          <div><h2>满减优惠券</h2><p>设置领取活动的门槛、优惠金额和有效时间。</p></div>
        </div>
        <form
          class="editor-card"
          aria-label="优惠券表单"
          novalidate
          @submit.prevent="saveCoupon"
        >
          <fieldset
            class="shop-fieldset"
            :disabled="!loaded.coupons || loading || saving.coupons"
          >
            <div class="form-grid form-grid--three">
              <label class="admin-field"><span>优惠券名称 *</span><input
                v-model="couponForm.title"
                maxlength="160"
              ></label>
              <label class="admin-field"><span>满减门槛（元）*</span><input
                v-model="couponForm.min_spend_yuan"
                inputmode="decimal"
                placeholder="例如 30.00"
              ></label>
              <label class="admin-field"><span>优惠金额（元）*</span><input
                v-model="couponForm.discount_yuan"
                inputmode="decimal"
                placeholder="例如 5.00"
              ></label>
              <label class="admin-field"><span>开始时间 *</span><input
                v-model="couponForm.starts_at"
                type="datetime-local"
              ></label>
              <label class="admin-field"><span>结束时间 *</span><input
                v-model="couponForm.expires_at"
                type="datetime-local"
              ></label>
              <label class="admin-check"><input
                v-model="couponForm.is_active"
                type="checkbox"
              ><span>启用优惠券</span></label>
            </div>
            <div class="button-row">
              <button
                class="admin-primary-button"
                type="submit"
              >
                <LoaderCircle
                  v-if="saving.coupons"
                  class="spin"
                  :size="17"
                  aria-hidden="true"
                /><Save
                  v-else
                  :size="17"
                  aria-hidden="true"
                /> {{ editingCouponId === null ? '创建优惠券' : '保存优惠券' }}
              </button><button
                v-if="editingCouponId !== null"
                class="filter-button"
                type="button"
                @click="resetCoupon"
              >
                取消编辑
              </button>
            </div>
          </fieldset>
        </form>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>优惠券</th><th>满减规则</th><th>有效时间</th><th>状态</th><th>操作</th></tr></thead><tbody>
              <tr
                v-for="row in coupons"
                :key="row.id"
              >
                <td>{{ row.title }}</td><td>满 ¥{{ money(row.min_spend_cents) }} 减 ¥{{ money(row.discount_cents) }}</td><td>{{ new Date(row.starts_at).toLocaleString('zh-CN') }}<br>至 {{ new Date(row.expires_at).toLocaleString('zh-CN') }}</td><td>{{ row.is_active ? '已启用' : '已停用' }}</td><td>
                  <button
                    class="text-button"
                    type="button"
                    :disabled="saving.coupons"
                    :aria-label="`编辑优惠券 ${row.id}`"
                    @click="editCoupon(row)"
                  >
                    编辑
                  </button>
                </td>
              </tr><tr v-if="!coupons.length && loaded.coupons && !loading">
                <td
                  colspan="5"
                  class="table-state"
                >
                  暂无优惠券。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section
        v-else
        id="shop-panel-orders"
        class="admin-workspace"
        role="tabpanel"
        aria-labelledby="shop-tab-orders"
      >
        <div class="workspace-heading">
          <div><h2>历史已成交订单</h2><p>录入已有真实成交凭证，用于购买排行。不会发起付款或扣减当前库存。</p></div>
        </div>
        <form
          class="editor-card"
          aria-label="已成交订单表单"
          novalidate
          @submit.prevent="saveOrder"
        >
          <fieldset
            class="shop-fieldset"
            :disabled="!loaded.orders || !loaded.products || loading || saving.orders || voidingOrderId !== null"
          >
            <div class="form-grid form-grid--three">
              <label class="admin-field"><span>原始成交单号 *</span><input
                v-model="orderForm.external_reference"
                maxlength="100"
                autocomplete="off"
              ></label>
              <label class="admin-field"><span>成交顾客（可选）</span><select
                v-model="orderForm.customer_id"
                :disabled="!loaded.customers"
              ><option value="">未关联微信顾客</option><option
                v-for="customer in customers"
                :key="customer.id"
                :value="String(customer.id)"
              >{{ customer.nickname }} · #{{ customer.id }}</option></select></label>
              <label class="admin-field"><span>成交时间 *</span><input
                v-model="orderForm.completed_at"
                type="datetime-local"
              ></label>
            </div>
            <div
              v-for="(line, index) in orderItems"
              :key="line.id"
              class="shop-order-line"
            >
              <label class="admin-field"><span>成交商品 {{ index + 1 }} *</span><select
                v-model="line.product_code"
                @change="selectOrderProduct(index)"
              ><option value="">请选择商品</option><option
                v-for="product in products"
                :key="product.id"
                :value="product.product_code"
              >{{ product.name }} · {{ product.product_code }}</option></select></label>
              <label class="admin-field"><span>成交数量 {{ index + 1 }} *</span><input
                v-model="line.quantity"
                inputmode="numeric"
              ></label>
              <label class="admin-field"><span>实际成交单价 {{ index + 1 }}（元）*</span><input
                v-model="line.price_yuan"
                inputmode="decimal"
              ></label>
              <button
                class="filter-button"
                type="button"
                :disabled="orderItems.length === 1"
                :aria-label="`移除成交商品 ${index + 1}`"
                @click="orderItems.splice(index, 1)"
              >
                <Trash2
                  :size="16"
                  aria-hidden="true"
                />
              </button>
            </div>
            <p class="section-help">
              选择商品后会填入当前价格，请按原始凭证修改为实际成交单价。订单总额由服务端计算。
            </p>
            <div class="button-row">
              <button
                class="filter-button"
                type="button"
                :disabled="orderItems.length >= 100"
                @click="orderItems.push({ id: nextItemId++, product_code: '', quantity: '1', price_yuan: '' })"
              >
                <Plus
                  :size="17"
                  aria-hidden="true"
                /> 添加成交商品
              </button><button
                class="admin-primary-button"
                type="submit"
              >
                <LoaderCircle
                  v-if="saving.orders"
                  class="spin"
                  :size="17"
                  aria-hidden="true"
                /><Save
                  v-else
                  :size="17"
                  aria-hidden="true"
                /> 录入已成交订单
              </button>
            </div>
          </fieldset>
        </form>
        <div class="admin-table-wrap">
          <table class="admin-table">
            <thead><tr><th>成交单号</th><th>顾客</th><th>成交商品</th><th>成交总额</th><th>成交时间</th><th>状态</th><th>操作</th></tr></thead><tbody>
              <tr
                v-for="row in orders"
                :key="row.id"
              >
                <td>{{ row.external_reference }}</td><td>{{ row.customer_id ? customerById.get(row.customer_id) ?? `顾客 #${row.customer_id}` : '未关联' }}</td><td>
                  <small
                    v-for="(item, index) in row.items"
                    :key="index"
                  >{{ item.product_name || item.product_code }} × {{ item.quantity }}</small>
                </td><td>¥{{ money(row.total_cents) }}</td><td>{{ new Date(row.completed_at).toLocaleString('zh-CN') }}</td><td>{{ row.status === 'completed' ? '已成交' : '已撤销' }}</td><td>
                  <button
                    v-if="row.status === 'completed'"
                    class="text-button text-button--danger"
                    type="button"
                    :disabled="voidingOrderId !== null || saving.orders"
                    :aria-label="`撤销成交记录 ${row.external_reference}`"
                    @click="voidOrder(row)"
                  >
                    撤销
                  </button><span v-else>—</span>
                </td>
              </tr><tr v-if="!orders.length && loaded.orders && !loading">
                <td
                  colspan="7"
                  class="table-state"
                >
                  暂无已成交订单，请按真实成交凭证录入。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>
</template>

<style scoped>
.shop-fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.shop-fieldset:disabled { opacity: 0.65; }
.shop-media-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 18px; }
.shop-media-card { overflow: hidden; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
.shop-media-card img, .shop-media-card video { display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: #e9eeeb; }
.shop-media-card__body { padding: 14px; }
.shop-media-card__body p { color: var(--muted); font-size: 0.8rem; line-height: 1.5; }
.shop-order-line { display: grid; grid-template-columns: minmax(180px, 2fr) 1fr 1.3fr auto; align-items: end; gap: 12px; margin-bottom: 16px; }
@media (max-width: 700px) {
  .shop-order-line { grid-template-columns: 1fr; }
  .shop-admin-page .admin-tab { padding: 8px 5px; }
  .admin-header__actions { flex-wrap: wrap; justify-content: flex-end; gap: 10px; }
}
</style>
