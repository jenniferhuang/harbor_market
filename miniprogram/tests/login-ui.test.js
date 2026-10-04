'use strict'

const fs = require('node:fs')
const path = require('node:path')

const SOURCE_ROOT = path.resolve(__dirname, '../src')
const ORIGIN = 'https://shop.example.cn'
const CUSTOMER = { id: 17, nickname: '小港', avatar_url: '' }
const CART_KEY = 'harbor_market_cart'
const GUEST_CART = { version: 1, items: [{ productCode: 'APPLE', quantity: 2 }] }
const PRODUCT = {
  product_code: 'APPLE',
  name: '苹果美式',
  base_price_cents: 1600,
  stock_status: 'in_stock',
  skus: [{ is_active: true, stock_quantity: 10 }],
}

function success(options, data) {
  options.success({ statusCode: 200, data: { data } })
}

function setup(overrides = {}) {
  vi.resetModules()
  for (const modulePath of Object.keys(require.cache)) {
    if (modulePath.startsWith(`${SOURCE_ROOT}${path.sep}`)) delete require.cache[modulePath]
  }
  const storage = new Map([
    ['harbor_market_api_base_url', ORIGIN],
    [CART_KEY, structuredClone(GUEST_CART)],
  ])
  let customer = { ...CUSTOMER }
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    login: vi.fn((options) => options.success({ code: 'native-wechat-code' })),
    request: vi.fn((options) => {
      if (options.url.endsWith('/login')) {
        success(options, {
          token_type: 'Bearer',
          access_token: 'customer-token-abcdefghijklmnop',
          expires_at: new Date(Date.now() + 60_000).toISOString(),
          customer,
        })
      } else if (options.url.endsWith('/profile')) {
        customer = { ...customer, nickname: options.data.nickname }
        success(options, customer)
      } else if (options.url.endsWith('/categories')) {
        success(options, [{ code: 'COFFEE', name: '咖啡' }])
      } else if (options.url.includes('/catalog/products?')) {
        success(options, { items: [PRODUCT], total: 1, page: 1, page_size: 10 })
      } else {
        success(options, customer)
      }
    }),
    uploadFile: vi.fn((options) => {
      customer = { ...customer, avatar_url: '/api/v1/mini/auth/avatar?v=1' }
      options.success({ statusCode: 200, data: JSON.stringify({ data: customer }) })
    }),
    downloadFile: vi.fn((options) => options.success({
      statusCode: 200, tempFilePath: 'wxfile://tmp/verified-avatar.jpg',
    })),
    hideTabBar: vi.fn(),
    showTabBar: vi.fn(),
    showModal: vi.fn(),
    showToast: vi.fn(),
    navigateTo: vi.fn(),
    ...overrides,
  }
  global.wx = wxMock
  const store = require('../src/state/auth-store')

  function mountPage(name = 'home') {
    let definition
    global.Page = vi.fn((value) => { definition = value })
    require(path.join(SOURCE_ROOT, `pages/${name}/${name}.js`))
    const page = {
      ...definition,
      data: structuredClone(definition.data),
      setData: vi.fn(function (changes) {
        Object.assign(this.data, changes)
        if (this.dialog && ('loginVisible' in changes || 'loginMode' in changes)) {
          this.dialog.properties.visible = this.data.loginVisible
          this.dialog.properties.mode = this.data.loginMode
          this.dialog.definition.observers['visible, mode'].call(this.dialog, this.data.loginVisible)
        }
      }),
    }
    page.onLoad()
    page.onShow()
    return page
  }

  function mountDialog(page) {
    let definition
    global.Component = vi.fn((value) => { definition = value })
    require(path.join(SOURCE_ROOT, 'components/login-dialog/login-dialog.js'))
    const dialog = {
      ...definition.methods,
      definition,
      properties: { visible: page.data.loginVisible, mode: page.data.loginMode },
      data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }),
      triggerEvent: vi.fn((name, detail) => {
        if (name === 'close') page.closeLogin()
        if (name === 'success') page.onLoginSuccess({ detail })
      }),
    }
    page.dialog = dialog
    definition.lifetimes.attached.call(dialog)
    return dialog
  }

  return { store, wxMock, storage, mountPage, mountDialog }
}

describe('native customer login UI behavior', () => {
  afterEach(() => {
    delete global.wx
    delete global.Page
    delete global.Component
    vi.restoreAllMocks()
  })

  it('lets guests dismiss login and browse real catalog results without another automatic prompt', async () => {
    const { store, wxMock, storage, mountPage, mountDialog } = setup()
    const home = mountPage()
    const dialog = mountDialog(home)
    expect(home.data.loginVisible).toBe(true)
    await vi.waitFor(() => expect(home.data.loading).toBe(false))
    expect(home.data.products[0]).toMatchObject({ name: '苹果美式', productCode: 'APPLE' })

    dialog.close()
    expect(home.data.loginVisible).toBe(false)
    expect(store.getAuthState().status).toBe('guest')
    home.openProduct({ detail: { productCode: 'APPLE' } })
    expect(wxMock.navigateTo).toHaveBeenCalledWith({ url: '/pages/product/product?code=APPLE' })
    home.onHide()
    home.onShow()
    store.resetSessionForApiChange()
    expect(home.data.loginVisible).toBe(false)
    expect(storage.get(CART_KEY)).toEqual(GUEST_CART)
    expect(wxMock.login).not.toHaveBeenCalled()

    home.openAccountDialog()
    expect(home.data.loginVisible).toBe(true)
  })

  it('uses native avatar/nickname controls and submits their chosen values through verified login', async () => {
    const markup = fs.readFileSync(path.join(SOURCE_ROOT, 'components/login-dialog/login-dialog.wxml'), 'utf8')
    expect(markup).toMatch(/open-type="chooseAvatar"/)
    expect(markup).toMatch(/type="nickname"/)
    const { store, wxMock, storage, mountPage, mountDialog } = setup()
    const home = mountPage()
    const dialog = mountDialog(home)
    dialog.onChooseAvatar({ detail: { avatarUrl: 'wxfile://tmp/chosen-avatar.jpg' } })
    dialog.onNicknameInput({ detail: { value: ' 新昵称 ' } })
    expect(dialog.data.avatarPreview).toBe('wxfile://tmp/chosen-avatar.jpg')

    await dialog.submit()
    expect(wxMock.login).toHaveBeenCalledOnce()
    const requests = wxMock.request.mock.calls.map(([options]) => options)
    expect(requests.find((options) => options.url.endsWith('/login')).data).toEqual({ code: 'native-wechat-code' })
    expect(requests.find((options) => options.url.endsWith('/profile')).data).toEqual({ nickname: '新昵称' })
    expect(wxMock.uploadFile).toHaveBeenCalledWith(expect.objectContaining({ filePath: 'wxfile://tmp/chosen-avatar.jpg' }))
    expect(store.getAuthState()).toMatchObject({ status: 'authenticated', avatarPath: 'wxfile://tmp/verified-avatar.jpg' })
    expect(home.data).toMatchObject({ loginVisible: false, customerName: '新昵称' })
    expect(wxMock.showToast).toHaveBeenCalledWith({ title: '微信登录成功', icon: 'success' })
    expect(wxMock.showTabBar).toHaveBeenCalledOnce()
    expect(storage.get(CART_KEY)).toEqual(GUEST_CART)
  })

  it('keeps the dialog open with a Chinese error when native WeChat login fails', async () => {
    const { store, wxMock, mountPage, mountDialog } = setup({
      login: vi.fn((options) => options.fail({ errMsg: 'login:fail canceled' })),
    })
    const home = mountPage()
    const dialog = mountDialog(home)
    await dialog.submit()

    expect(dialog.data.errorMessage).toBe('微信登录未完成，请重试或稍后登录。')
    expect(dialog.data.submitting).toBe(false)
    expect(home.data.loginVisible).toBe(true)
    expect(dialog.triggerEvent).not.toHaveBeenCalledWith('success', expect.anything())
    expect(store.getAuthState().status).toBe('guest')
    expect(wxMock.showToast).not.toHaveBeenCalled()
    expect(wxMock.request.mock.calls.some(([options]) => options.url.endsWith('/login'))).toBe(false)
    dialog.close()
    expect(home.data.loginVisible).toBe(false)
    expect(wxMock.showTabBar).toHaveBeenCalledOnce()
  })

  it.each(['home', 'account'])('closes successful login on %s and explains an optional avatar save failure', async (pageName) => {
    const { store, wxMock, mountPage, mountDialog } = setup({
      uploadFile: vi.fn((options) => options.fail({ errMsg: 'uploadFile:fail offline' })),
    })
    const page = mountPage(pageName)
    if (pageName === 'account') page.openLogin()
    const dialog = mountDialog(page)
    dialog.onChooseAvatar({ detail: { avatarUrl: 'wxfile://tmp/chosen-avatar.jpg' } })
    await dialog.submit()

    expect(page.data).toMatchObject({ loginVisible: false, authStatus: 'authenticated' })
    expect(store.getAuthState().customer.id).toBe(CUSTOMER.id)
    expect(wxMock.showModal).toHaveBeenCalledWith(expect.objectContaining({
      title: '已登录，资料尚未保存', content: '头像暂未保存，可在“我的”页面重试。', showCancel: false,
    }))
    expect(wxMock.showToast).not.toHaveBeenCalled()
    expect(wxMock.showTabBar).toHaveBeenCalledOnce()
  })

  it('prevents duplicate login and closing while native login is pending', async () => {
    let nativeLogin
    const { wxMock, mountPage, mountDialog } = setup({
      login: vi.fn((options) => { nativeLogin = options }),
    })
    const home = mountPage()
    const dialog = mountDialog(home)
    const submitting = dialog.submit()
    await dialog.submit()
    dialog.close()
    expect(home.data.loginVisible).toBe(true)
    expect(wxMock.login).toHaveBeenCalledOnce()
    expect(wxMock.showTabBar).not.toHaveBeenCalled()
    nativeLogin.fail({ errMsg: 'login:fail canceled' })
    await submitting
    dialog.close()
    expect(home.data.loginVisible).toBe(false)
  })

  it.each(['close', 'page hide', 'detach'])('restores the native tab bar after modal %s', (action) => {
    const { wxMock, mountPage, mountDialog } = setup()
    const home = mountPage()
    const dialog = mountDialog(home)
    expect(wxMock.hideTabBar).toHaveBeenCalledOnce()
    expect(wxMock.showTabBar).not.toHaveBeenCalled()
    if (action === 'close') dialog.close()
    if (action === 'page hide') dialog.definition.pageLifetimes.hide.call(dialog)
    if (action === 'detach') dialog.definition.lifetimes.detached.call(dialog)
    expect(wxMock.showTabBar).toHaveBeenCalledWith({ animation: false })
    expect(wxMock.showTabBar).toHaveBeenCalledOnce()
    dialog.definition.lifetimes.detached.call(dialog)
    expect(wxMock.showTabBar).toHaveBeenCalledOnce()
  })
})
