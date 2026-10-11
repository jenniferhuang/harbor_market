'use strict'

const path = require('node:path')
const SOURCE_ROOT = path.resolve(__dirname, '../src') + path.sep

function setup({ envVersion = 'release', sameCustomer = false, intercept } = {}) {
  for (const modulePath of Object.keys(require.cache)) if (modulePath.startsWith(SOURCE_ROOT)) delete require.cache[modulePath]
  const storage = new Map([['harbor_market_api_base_url', 'https://shop.example.cn'],
    ['harbor_market_cart', { version: 1, items: [{ productCode: 'TEA', quantity: 2 }] }]])
  let logins = 0
  let customer
  const wxMock = {
    getStorageSync: vi.fn((key) => storage.get(key)),
    setStorageSync: vi.fn((key, value) => storage.set(key, value)),
    removeStorageSync: vi.fn((key) => storage.delete(key)),
    getAccountInfoSync: vi.fn(() => ({ miniProgram: { envVersion } })),
    login: vi.fn((options) => options.success({ code: 'native-login-code' })),
    request: vi.fn((options) => {
      if (intercept?.(options) === true) return
      let data = null
      if (options.url.endsWith('/login')) {
        logins += 1
        customer = { id: sameCustomer ? 17 : 16 + logins, nickname: `顾客${logins}`, avatar_url: '' }
        data = { token_type: 'Bearer', access_token: `customer-token-abcdefghijklmnop-${logins}`,
          expires_at: new Date(Date.now() + 300_000).toISOString(), customer }
      } else if (options.url.endsWith('/profile')) {
        customer = { ...customer, nickname: options.data.nickname }
        data = customer
      } else if (options.url.endsWith('/me')) data = customer
      options.success({ statusCode: 200, data: { data } })
    }),
    showModal: vi.fn(), showToast: vi.fn(), navigateTo: vi.fn(), switchTab: vi.fn(),
  }
  global.wx = wxMock
  const auth = require('../src/state/auth-store')
  const sessions = require('../src/state/customer-session')
  const client = require('../src/api/client')
  function mount() {
    let definition
    global.Page = (value) => { definition = value }
    require('../src/pages/profile/profile')
    const page = { ...definition, data: structuredClone(definition.data),
      setData: vi.fn(function (changes) { Object.assign(this.data, changes) }) }
    page.onLoad()
    page.onShow()
    return page
  }
  const logoutRequests = () => wxMock.request.mock.calls.filter(([options]) => options.url.endsWith('/logout'))
  return { auth, sessions, client, wxMock, storage, mount, logoutRequests }
}

afterEach(() => { delete global.wx; delete global.Page; vi.restoreAllMocks() })

describe('personal profile page', () => {
  it('offers optional guest login and browsing without attempting login automatically', () => {
    const { mount, wxMock } = setup()
    const page = mount()
    expect(page.data).toMatchObject({ authStatus: 'guest', customerId: '', nickname: '', avatarPath: '' })
    expect(wxMock.login).not.toHaveBeenCalled()
    page.openProfileDialog()
    expect(page.data).toMatchObject({ loginVisible: true, loginMode: 'login' })
    page.closeLogin()
    expect(page.data.loginVisible).toBe(false)
    page.continueShopping()
    expect(wxMock.switchTab).toHaveBeenCalledWith({ url: '/pages/catalog/catalog' })
  })

  it('renders customer ID and nickname, reuses profile editing, and keeps tokens out of view data', async () => {
    const { auth, sessions, mount, wxMock } = setup()
    await auth.loginWithWeChat()
    const page = mount()
    const token = sessions.readSession().accessToken
    expect(page.data).toMatchObject({ authStatus: 'authenticated', customerId: '17', nickname: '顾客1' })
    expect(JSON.stringify(page.data)).not.toContain(token)
    page.openProfileDialog()
    expect(page.data).toMatchObject({ loginVisible: true, loginMode: 'profile' })
    await auth.updateProfile({ nickname: '小黄' })
    page.onLoginSuccess({ detail: { mode: 'profile', customerId: 17 } })
    expect(page.data).toMatchObject({ nickname: '小黄', loginVisible: false })
    expect(wxMock.showToast).toHaveBeenCalledWith({ title: '资料已保存', icon: 'success' })
    page.setData({ avatarPath: 'wxfile://tmp/avatar.jpg' })
    page.onAvatarError()
    expect(page.data.avatarPath).toBe('')
  })

  it('accepts a matching login result and shows partial-profile warnings without losing the session', async () => {
    const { auth, mount, wxMock } = setup()
    const page = mount()
    page.openProfileDialog()
    await auth.loginWithWeChat()
    page.onLoginSuccess({ detail: { mode: 'login', customerId: 17, warningMessage: '头像暂未保存，可稍后重试。' } })
    expect(page.data).toMatchObject({ authStatus: 'authenticated', loginVisible: false })
    expect(wxMock.showModal).toHaveBeenCalledWith(expect.objectContaining({ title: '部分资料尚未保存' }))
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it.each([false, true])('dismisses an old profile edit after customer or session replacement (same customer: %s)', async (sameCustomer) => {
    const { auth, mount, wxMock } = setup({ sameCustomer })
    await auth.loginWithWeChat()
    const page = mount()
    page.openProfileDialog()
    await auth.loginWithWeChat()
    page.onLoginSuccess({ detail: { mode: 'profile', customerId: 17 } })
    expect(page.data).toMatchObject({ loginVisible: false, nickname: '顾客2' })
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('switches expired profiles to login before opening the editor', async () => {
    const { auth, mount } = setup()
    await auth.loginWithWeChat()
    const page = mount()
    const expired = Date.now() + 300_001
    vi.spyOn(Date, 'now').mockReturnValue(expired)
    page.openProfileDialog()
    expect(page.data).toMatchObject({ authStatus: 'guest', customerId: '', nickname: '', loginVisible: true, loginMode: 'login' })
  })

  it('requires confirmation, guards duplicate dialogs, and preserves the cart on logout', async () => {
    const { auth, mount, wxMock, storage, logoutRequests } = setup()
    await auth.loginWithWeChat()
    const page = mount()
    const cart = structuredClone(storage.get('harbor_market_cart'))
    page.confirmLogout()
    page.confirmLogout()
    expect(wxMock.showModal).toHaveBeenCalledOnce()
    await wxMock.showModal.mock.calls[0][0].success({ confirm: false })
    expect(logoutRequests()).toHaveLength(0)
    page.confirmLogout()
    await wxMock.showModal.mock.calls[1][0].success({ confirm: true })
    expect(logoutRequests()).toHaveLength(1)
    expect(page.data).toMatchObject({ authStatus: 'guest', authBusy: false, customerId: '', avatarPath: '' })
    expect(storage.get('harbor_market_cart')).toEqual(cart)
    expect(wxMock.showToast).toHaveBeenLastCalledWith({ title: '已退出登录', icon: 'success' })
  })

  it.each(['customer', 'token', 'origin', 'hidden', 'unloaded'])('rejects a late logout confirmation after %s changes', async (change) => {
    const { auth, mount, wxMock, client, logoutRequests } = setup({ sameCustomer: change === 'token' })
    await auth.loginWithWeChat()
    const page = mount()
    page.confirmLogout()
    const confirmation = wxMock.showModal.mock.calls[0][0]
    if (change === 'customer' || change === 'token') await auth.loginWithWeChat()
    else if (change === 'origin') client.setApiBaseUrl('https://another.example.cn')
    else if (change === 'hidden') { page.onHide(); page.onShow() }
    else page.onUnload()
    await confirmation.success({ confirm: true })
    expect(logoutRequests()).toHaveLength(0)
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('does not display an old logout result over a replacement account', async () => {
    let request
    const { auth, mount, wxMock } = setup({ intercept(options) {
      if (!options.url.endsWith('/logout')) return false
      request = options
      return true
    } })
    await auth.loginWithWeChat()
    const page = mount()
    page.confirmLogout()
    const pending = wxMock.showModal.mock.calls[0][0].success({ confirm: true })
    await auth.loginWithWeChat()
    request.success({ statusCode: 200, data: { data: null } })
    await pending
    expect(page.data).toMatchObject({ authStatus: 'authenticated', customerId: '18', nickname: '顾客2', authBusy: false })
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('stays signed out locally and preserves the cart when server revocation fails', async () => {
    const { auth, mount, wxMock, storage } = setup({ intercept(options) {
      if (!options.url.endsWith('/logout')) return false
      options.fail({ errMsg: 'network unavailable' })
      return true
    } })
    await auth.loginWithWeChat()
    const page = mount()
    const cart = structuredClone(storage.get('harbor_market_cart'))
    page.confirmLogout()
    await wxMock.showModal.mock.calls[0][0].success({ confirm: true })
    expect(page.data).toMatchObject({ authStatus: 'guest', authBusy: false })
    expect(page.data.errorMessage).toContain('服务器会话撤销失败')
    expect(storage.get('harbor_market_cart')).toEqual(cart)
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })

  it('ignores auth updates and success callbacks after unloading', async () => {
    const { auth, mount, wxMock } = setup()
    const page = mount()
    page.openProfileDialog()
    page.onUnload()
    page.setData.mockClear()
    await auth.loginWithWeChat()
    page.onLoginSuccess({ detail: { mode: 'login', customerId: 17 } })
    expect(page.setData).not.toHaveBeenCalled()
    expect(wxMock.showToast).not.toHaveBeenCalled()
  })
})

describe('development-only profile connection settings', () => {
  it.each(['develop', 'trial', 'release', '', null])('allows navigation only in develop (%s)', (envVersion) => {
    const { mount, wxMock } = setup({ envVersion })
    const page = mount()
    expect(page.data.showConnectionSettings).toBe(envVersion === 'develop')
    page.openConnectionSettings()
    expect(wxMock.navigateTo).toHaveBeenCalledTimes(envVersion === 'develop' ? 1 : 0)
  })

  it.each(['missing', 'throws'])('keeps settings hidden when account-info support %s', (failure) => {
    const { mount, wxMock } = setup()
    if (failure === 'missing') delete wxMock.getAccountInfoSync
    else wxMock.getAccountInfoSync.mockImplementation(() => { throw new Error('unsupported') })
    const page = mount()
    page.openConnectionSettings()
    expect(page.data.showConnectionSettings).toBe(false)
    expect(wxMock.navigateTo).not.toHaveBeenCalled()
  })

  it('rechecks the environment when opening settings rather than trusting old view data', () => {
    const { mount, wxMock } = setup({ envVersion: 'develop' })
    const page = mount()
    wxMock.getAccountInfoSync.mockReturnValue({ miniProgram: { envVersion: 'release' } })
    page.openConnectionSettings()
    expect(wxMock.navigateTo).not.toHaveBeenCalled()
    page.onShow()
    expect(page.data.showConnectionSettings).toBe(false)
  })
})
