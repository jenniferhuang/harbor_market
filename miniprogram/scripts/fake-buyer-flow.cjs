'use strict'

/* global fetch, structuredClone, queueMicrotask, URL, AbortController, setTimeout, clearTimeout, __dirname, console, process */

// TEST ONLY. Runs current application modules with real HTTP against the
// localhost-only fake_buyer_harness.py. WeChat platform facilities are simulated.
// The origin is intentionally fixed; this script accepts no URL/env override.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const crypto = require('node:crypto')

const BASE_URL = 'http://127.0.0.1:18080'
const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ARTIFACT_ROOT = path.resolve(__dirname, '../../.data/fake-buyer-ui')
const RESULT_PATH = path.join(ARTIFACT_ROOT, 'customer-flow-result.json')
const report = {
  passed: false, scope: 'Current Mini Program modules with real HTTP to an isolated live backend; no native rendering assertions.',
  backend_origin: BASE_URL, simulated_platform: ['wx.login code', 'storage', 'navigation', 'dialogs', 'page/component registration'],
  http_responses_mocked: false, persistent_development_database_used: false,
  checks: [], requests: [],
}
const activeRequests = new Set()
let runtime

function save() {
  fs.mkdirSync(ARTIFACT_ROOT, { recursive: true })
  fs.writeFileSync(RESULT_PATH, JSON.stringify(report, null, 2) + '\n')
}
function checked(name, details = {}) { report.checks.push({ name, passed: true, ...details }); save() }
async function waitFor(predicate, label, timeout = 15000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    if (predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 15))
  }
  throw new Error(`Timed out: ${label}`)
}
async function drain() {
  while (activeRequests.size) await Promise.allSettled([...activeRequests])
}
function permittedUrl(value) {
  const url = new URL(value)
  if (url.origin !== BASE_URL || !url.pathname.startsWith('/api/')) throw new Error('Only the isolated loopback API may be called.')
  return url
}

function clearSourceModules() {
  for (const filename of Object.keys(require.cache)) {
    if (filename.startsWith(SOURCE_ROOT + path.sep)) delete require.cache[filename]
  }
}

function createRuntime(storage, phase) {
  clearSourceModules()
  const pages = new Set()
  const dialogs = new Set()
  const navigation = []
  let currentPage = null
  let loginCalls = 0
  let app
  global.wx = {
    getStorageSync(key) { return storage.get(key) },
    setStorageSync(key, value) { storage.set(key, structuredClone(value)) },
    removeStorageSync(key) { storage.delete(key) },
    login(options) {
      loginCalls += 1
      queueMicrotask(() => options.success({ code: `real_http_buyer_${crypto.randomUUID()}` }))
    },
    request(options) {
      const url = permittedUrl(options.url)
      const method = options.method || 'GET'
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), options.timeout || 10000)
      const operation = (async () => {
        try {
          const response = await fetch(url, {
            method, headers: options.header, redirect: 'error', credentials: 'omit', signal: controller.signal,
            body: options.data === undefined ? undefined : JSON.stringify(options.data),
          })
          const data = await response.json()
          report.requests.push({ phase, method, path: url.pathname, status: response.status,
            authenticated: Boolean(options.header?.Authorization) })
          options.success({ statusCode: response.status, data })
        } catch {
          report.requests.push({ phase, method, path: url.pathname, status: 0, network_failure: true })
          options.fail({ errMsg: 'isolated HTTP request failed' })
        } finally {
          clearTimeout(timeout)
          if (options.complete) options.complete()
        }
      })()
      activeRequests.add(operation)
      operation.finally(() => activeRequests.delete(operation))
      return { abort() { controller.abort() } }
    },
    navigateTo(options) { navigation.push({ method: 'navigateTo', url: options.url }); options.success?.({}) },
    switchTab(options) { navigation.push({ method: 'switchTab', url: options.url }); options.success?.({}) },
    navigateBack(options = {}) { navigation.push({ method: 'navigateBack' }); options.success?.({}) },
    setNavigationBarTitle() {}, hideTabBar() {}, showTabBar() {}, stopPullDownRefresh() {},
    showToast() {},
    showModal(options) {
      const operation = (async () => {
        await Promise.resolve()
        await options.success?.({ confirm: options.title === '退出登录', cancel: options.title !== '退出登录' })
        options.complete?.({})
      })()
      activeRequests.add(operation)
      operation.finally(() => activeRequests.delete(operation))
    },
  }
  global.getCurrentPages = () => currentPage ? [currentPage] : []
  global.getApp = () => app
  const auth = require(path.join(SOURCE_ROOT, 'state/auth-store'))
  const cart = require(path.join(SOURCE_ROOT, 'state/cart-store'))
  const customerShop = require(path.join(SOURCE_ROOT, 'api/customer-shop'))

  function launch() {
    global.App = (definition) => { app = definition }
    require(path.join(SOURCE_ROOT, 'app'))
    app.onLaunch()
  }
  function enter(name, options = {}) {
    if (currentPage?.onHide) currentPage.onHide()
    let definition
    global.Page = (value) => { definition = value }
    const filename = require.resolve(path.join(SOURCE_ROOT, `pages/${name}/${name}`))
    delete require.cache[filename]
    require(filename)
    assert.ok(definition, 'Page must register a definition.')
    const page = {
      ...definition, data: structuredClone(definition.data),
      route: `pages/${name}/${name}`,
      setData(changes) {
        Object.assign(this.data, changes)
        if (this.dialog && ('loginVisible' in changes || 'loginMode' in changes)) {
          this.dialog.properties.visible = this.data.loginVisible
          this.dialog.properties.mode = this.data.loginMode || 'login'
          this.dialog.definition.observers['visible, mode'].call(this.dialog, this.data.loginVisible)
        }
      },
      selectComponent(selector) { return selector.includes('login-dialog') ? this.dialog : null },
    }
    currentPage = page
    pages.add(page)
    page.onLoad(options)
    page.onShow?.()
    return page
  }
  function mountLogin(page) {
    let definition
    global.Component = (value) => { definition = value }
    const filename = require.resolve(path.join(SOURCE_ROOT, 'components/login-dialog/login-dialog'))
    delete require.cache[filename]
    require(filename)
    const dialog = {
      ...definition.methods, definition,
      properties: { visible: page.data.loginVisible, mode: page.data.loginMode || 'login' },
      data: structuredClone(definition.data),
      setData(changes) { Object.assign(this.data, changes) },
      triggerEvent(name, detail) {
        if (name === 'close') page.closeLogin()
        if (name === 'success') {
          if (typeof page.onLoginSuccess === 'function') page.onLoginSuccess({ detail })
          else page.loginSucceeded()
        }
      },
    }
    page.dialog = dialog
    dialogs.add(dialog)
    definition.lifetimes.attached.call(dialog)
    return dialog
  }
  async function destroy() {
    for (const dialog of dialogs) dialog.definition.lifetimes.detached.call(dialog)
    for (const page of pages) page.onUnload?.()
    await drain()
  }
  return { auth, cart, customerShop, enter, launch, mountLogin, destroy, navigation,
    get loginCalls() { return loginCalls } }
}

async function loginThroughDialog(account) {
  account.openLogin()
  const dialog = runtime.mountLogin(account)
  await dialog.submit()
  assert.equal(dialog.data.errorMessage, '', 'Login dialog must not show a failure.')
  await waitFor(() => runtime.auth.getAuthState().status === 'authenticated' && !runtime.auth.getAuthState().busy, 'verified buyer login')
  assert.equal(account.data.loginVisible, false)
  assert.equal(account.data.authStatus, 'authenticated')
}

function choose(page, specificationCode, optionCode) {
  page.selectOption({ currentTarget: { dataset: { specificationCode, optionCode } } })
}

async function run() {
  // Public fixture markers are checked before any customer write is allowed.
  const homeResponse = await fetch(permittedUrl(`${BASE_URL}/api/v1/shop/home`), { redirect: 'error' })
  assert.equal(homeResponse.status, 200)
  const home = (await homeResponse.json()).data
  assert.equal(home.store.name, '隔离测试店铺', 'The backend must be the isolated fixture.')
  checked('Isolated backend marker verified before any customer writes')
  const storage = new Map([['harbor_market_api_base_url', BASE_URL]])
  runtime = createRuntime(storage, 'initial')
  runtime.launch()
  await waitFor(() => runtime.auth.getAuthState().status === 'guest', 'initial guest')
  const account = runtime.enter('account')
  await loginThroughDialog(account)
  const customerId = runtime.auth.getAuthState().customer.id
  assert.equal(customerId, 1)
  checked('Buyer login dialog completed through the live HTTP auth service', { customer_id: customerId })

  const coffee = runtime.enter('product', { code: 'E2E-COFFEE' })
  await waitFor(() => coffee.data.product && !coffee.data.loading, 'coffee detail')
  await coffee.loadFavorite()
  if (coffee.data.isFavorite) await coffee.toggleFavorite()
  await coffee.toggleFavorite()
  assert.equal(coffee.data.isFavorite, true)
  checked('Favorite saved from the actual product page through HTTP')
  coffee.openSpecSheet()
  choose(coffee, 'cup', 'large')
  choose(coffee, 'addons', 'pearls')
  choose(coffee, 'addons', 'milk')
  coffee.changeQuantity({ detail: { value: 2 } })
  assert.equal(coffee.data.displayPrice, '¥7.00')
  coffee.addToCart()
  assert.equal(runtime.cart.loadCart().items.length, 1)
  checked('Large coffee SKU plus two addons added to local cart', { unit_price_cents: 700, quantity: 2 })
  const tea = runtime.enter('product', { code: 'E2E-TEA' })
  await waitFor(() => tea.data.product && !tea.data.loading, 'tea detail')
  tea.addToCart()
  let cart = runtime.enter('cart')
  await waitFor(() => !cart.data.couponLoading, 'cart coupon history')
  assert.equal(cart.data.selectedSummary.totalCents, 1650)
  assert.equal(cart.data.summary.totalQuantity, 3)
  checked('Cart retains both products with subtotal ¥16.50', { subtotal_cents: 1650, quantity: 3 })

  const center = runtime.enter('coupon-center', { from: 'cart' })
  await waitFor(() => center.data.hasLoaded && !center.data.loading && !center.data.claimsLoading, 'coupon center')
  const offer = center.data.coupons.find((row) => row.id === 1)
  assert.ok(offer, 'Seeded live coupon must be available.')
  const wasOwned = offer.claimed
  if (!wasOwned) await center.claim({ currentTarget: { dataset: { id: 1 } } })
  else await runtime.customerShop.claimCoupon(1)
  const firstClaim = await runtime.customerShop.claimCoupon(1)
  const repeatedClaim = await runtime.customerShop.claimCoupon(1)
  assert.equal(repeatedClaim.claimed_at, firstClaim.claimed_at)
  const owned = await runtime.customerShop.fetchMyCoupons({ page: 1, page_size: 100 })
  assert.equal(owned.filter((row) => row.coupon_id === 1).length, 1)
  checked('Coupon claim endpoint is idempotent with one owned claim', { coupon_id: 1, previously_claimed: wasOwned })

  cart = runtime.enter('cart')
  await waitFor(() => !cart.data.couponLoading, 'owned coupon estimate')
  assert.equal(cart.data.displayTotal, '¥14.50')
  const coffeeKey = cart.data.items.find((item) => item.productCode === 'E2E-COFFEE').key
  const teaKey = cart.data.items.find((item) => item.productCode === 'E2E-TEA').key
  cart.changeSelection({ currentTarget: { dataset: { key: coffeeKey } } })
  assert.equal(cart.data.selectedSummary.totalCents, 250)
  assert.equal(cart.data.discountCents, 0)
  assert.equal(cart.data.couponHint, '差7.50元可减2.00元')
  cart.invertSelected()
  assert.equal(cart.data.selectedSummary.totalCents, 1400)
  assert.equal(cart.data.displayTotal, '¥12.00')
  cart.toggleAll()
  assert.equal(cart.data.selectedSummary.totalCents, 1650)
  cart.changeQuantity({ currentTarget: { dataset: { key: teaKey } }, detail: { value: 2 } })
  assert.equal(cart.data.displayTotal, '¥17.00')
  cart.changeQuantity({ currentTarget: { dataset: { key: teaKey } }, detail: { value: 1 } })
  cart.toggleAll()
  assert.equal(cart.data.selectedSummary.totalQuantity, 0)
  const navigationBefore = runtime.navigation.length
  cart.checkout()
  assert.equal(runtime.navigation.length, navigationBefore)
  cart.invertSelected()
  assert.equal(cart.data.selectedSummary.totalCents, 1650)
  assert.equal(cart.data.displayTotal, '¥14.50')
  checked('Selection, inversion, select-all, quantity editing and threshold estimates are correct')
  cart.checkout()
  assert.equal(runtime.navigation.at(-1).url, '/pages/checkout/checkout?coupon_id=1')
  let checkout = runtime.enter('checkout', { coupon_id: '1' })
  await waitFor(() => checkout.data.quote || checkout.data.errorMessage, 'authoritative quote')
  assert.equal(checkout.data.errorMessage, '')
  assert.equal(checkout.data.quote.subtotal_cents, 1650)
  assert.equal(checkout.data.quote.discount_cents, 200)
  assert.equal(checkout.data.quote.total_cents, 1450)
  checked('Authoritative HTTP quote confirms ¥14.50', { subtotal_cents: 1650, discount_cents: 200, total_cents: 1450 })
  const cartSnapshot = structuredClone(runtime.cart.loadCart())
  await runtime.destroy()

  runtime = createRuntime(storage, 'restored')
  assert.equal(runtime.auth.getAuthState().status, 'restoring')
  runtime.launch()
  await waitFor(() => runtime.auth.getAuthState().status === 'authenticated', 'fresh-module session restoration')
  assert.equal(runtime.loginCalls, 0)
  assert.equal(runtime.auth.getAuthState().customer.id, customerId)
  assert.deepEqual(runtime.cart.loadCart(), cartSnapshot)
  const restoredProduct = runtime.enter('product', { code: 'E2E-COFFEE' })
  await waitFor(() => restoredProduct.data.product && !restoredProduct.data.loading, 'restored product detail')
  await restoredProduct.loadFavorite()
  assert.equal(restoredProduct.data.isFavorite, true)
  const restoredClaims = await runtime.customerShop.fetchMyCoupons({ page: 1, page_size: 100 })
  assert.equal(restoredClaims.find((row) => row.coupon_id === 1).claimed_at, firstClaim.claimed_at)
  checkout = runtime.enter('checkout', { coupon_id: '1' })
  await waitFor(() => checkout.data.quote || checkout.data.errorMessage, 'restored checkout')
  assert.equal(checkout.data.quote?.total_cents, 1450)
  checked('Fresh-module app launch re-verifies saved session and retains cart, favorite and coupon without wx.login')

  const reloginAccount = runtime.enter('account')
  await waitFor(() => !reloginAccount.data.authBusy, 'account ready for logout')
  reloginAccount.confirmLogout()
  await waitFor(() => runtime.auth.getAuthState().status === 'guest' && !reloginAccount.data.authBusy, 'logout through account dialog')
  assert.equal(storage.has('harbor_market_customer_session'), false)
  assert.deepEqual(runtime.cart.loadCart(), cartSnapshot)
  await loginThroughDialog(reloginAccount)
  assert.equal(runtime.auth.getAuthState().customer.id, customerId)
  assert.deepEqual(runtime.cart.loadCart(), cartSnapshot)
  assert.equal((await runtime.customerShop.getFavorite('E2E-COFFEE')).is_favorite, true)
  const reloginClaims = await runtime.customerShop.fetchMyCoupons({ page: 1, page_size: 100 })
  assert.equal(reloginClaims.find((row) => row.coupon_id === 1).claimed_at, firstClaim.claimed_at)
  checkout = runtime.enter('checkout', { coupon_id: '1' })
  await waitFor(() => checkout.data.quote || checkout.data.errorMessage, 'relogin checkout')
  assert.equal(checkout.data.quote?.total_cents, 1450)
  checked('Account logout and login retain local cart and server favorite/claim, with quote still ¥14.50')
  await runtime.destroy()
  runtime = null
  report.passed = true
  report.check_count = report.checks.length
  report.request_count = report.requests.length
  report.tokens_saved_or_printed = false
  save()
  console.log(JSON.stringify({ passed: true, checks: report.check_count, requests: report.request_count, result: RESULT_PATH }))
}

run().catch(async (error) => {
  if (runtime) await runtime.destroy().catch(() => {})
  report.failure = String(error.message).replace(/Bearer\s+[A-Za-z0-9._~-]+/g, 'Bearer [redacted]')
  save()
  console.error(JSON.stringify({ passed: false, error: report.failure, result: RESULT_PATH }))
  process.exitCode = 1
})
