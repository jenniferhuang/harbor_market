'use strict'

const fs = require('node:fs')
const path = require('node:path')

const projectRoot = path.resolve(__dirname, '..')
const sourceRoot = path.join(projectRoot, 'src')

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'))
}

function filesUnder(directory, suffix) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) return filesUnder(entryPath, suffix)
    return entryPath.endsWith(suffix) ? [entryPath] : []
  })
}

function expectMiniProgramUnit(basePath) {
  for (const extension of ['.js', '.json', '.wxml', '.wxss']) {
    expect(fs.existsSync(`${basePath}${extension}`), `${basePath}${extension}`).toBe(true)
  }
}

describe('native Mini Program project structure', () => {
  it('declares importable project metadata and complete page units', () => {
    const project = readJson(path.join(projectRoot, 'project.config.json'))
    const app = readJson(path.join(sourceRoot, 'app.json'))

    expect(project).toMatchObject({
      appid: 'touristappid',
      compileType: 'miniprogram',
      miniprogramRoot: 'src/',
    })
    expect(app.pages[0]).toBe('pages/home/home')
    expect(app.pages).toEqual(expect.arrayContaining([
      'pages/home/home',
      'pages/catalog/catalog',
      'pages/search/search',
      'pages/product/product',
      'pages/cart/cart',
      'pages/account/account',
      'pages/settings/settings',
      'pages/favorites/favorites',
      'pages/coupons/coupons',
      'pages/orders/orders',
      'pages/merchant/merchant',
    ]))
    expect(new Set(app.pages).size).toBe(app.pages.length)
    expect(app.tabBar.list.map((tab) => tab.text)).toEqual(['首页', '商品选购', '购物车', '我'])
    for (const page of app.pages) expectMiniProgramUnit(path.join(sourceRoot, page))

    const declaredPages = new Set(app.pages)
    for (const tab of app.tabBar.list) {
      expect(declaredPages.has(tab.pagePath), tab.pagePath).toBe(true)
    }
  })

  it('resolves every declared component and relative CommonJS import', () => {
    for (const jsonPath of filesUnder(sourceRoot, '.json')) {
      const config = readJson(jsonPath)
      for (const componentPath of Object.values(config.usingComponents || {})) {
        const basePath = componentPath.startsWith('/')
          ? path.join(sourceRoot, componentPath.slice(1))
          : path.resolve(path.dirname(jsonPath), componentPath)
        expectMiniProgramUnit(basePath)
        expect(readJson(`${basePath}.json`)).toMatchObject({ component: true })
      }
    }

    for (const scriptPath of filesUnder(sourceRoot, '.js')) {
      const source = fs.readFileSync(scriptPath, 'utf8')
      for (const match of source.matchAll(/require\(['"](\.[^'"]+)['"]\)/g)) {
        const importedPath = path.resolve(path.dirname(scriptPath), match[1])
        expect(
          fs.existsSync(importedPath) || fs.existsSync(`${importedPath}.js`),
          `${scriptPath} -> ${match[1]}`,
        ).toBe(true)
      }
    }
  })

  it('uses native/custom WXML elements and balanced explicit tags', () => {
    const nativeElements = new Set([
      'block',
      'button',
      'image',
      'input',
      'scroll-view',
      'swiper',
      'swiper-item',
      'text',
      'view',
      'video',
    ])
    const voidElements = new Set(['image', 'input'])

    for (const wxmlPath of filesUnder(sourceRoot, '.wxml')) {
      const configPath = wxmlPath.replace(/\.wxml$/, '.json')
      const config = fs.existsSync(configPath) ? readJson(configPath) : {}
      const allowedElements = new Set([
        ...nativeElements,
        ...Object.keys(config.usingComponents || {}),
      ])
      const source = fs.readFileSync(wxmlPath, 'utf8')
      const stack = []

      for (const match of source.matchAll(/<\s*(\/?)\s*([a-z][a-z0-9-]*)([^>]*)>/gi)) {
        const [, closing, tag, tail] = match
        expect(allowedElements.has(tag), `${wxmlPath}: <${tag}>`).toBe(true)
        if (closing) {
          expect(stack.pop(), `${wxmlPath}: </${tag}>`).toBe(tag)
        } else if (!voidElements.has(tag) && !/\/\s*$/.test(tail)) {
          stack.push(tag)
        }
      }
      expect(stack, wxmlPath).toEqual([])
    }
  })

  it('keeps shopper code outside admin, mock-payment, and cashier APIs', () => {
    const shopperSource = filesUnder(sourceRoot, '.js')
      .map((filePath) => fs.readFileSync(filePath, 'utf8'))
      .join('\n')

    expect(shopperSource).not.toMatch(/\/api\/v1\/admin/)
    expect(shopperSource).not.toMatch(/MOCK-HMAC-SHA256/)
    expect(shopperSource).not.toMatch(/requestPayment/)
  })

  it('offers native optional avatar and nickname controls with an accessible guest exit', () => {
    const basePath = path.join(sourceRoot, 'components/login-dialog/login-dialog')
    expectMiniProgramUnit(basePath)
    expect(readJson(`${basePath}.json`)).toMatchObject({ component: true })
    const markup = fs.readFileSync(`${basePath}.wxml`, 'utf8')
    expect(markup).toMatch(/<button\b[^>]*open-type="chooseAvatar"[^>]*bindchooseavatar=/s)
    expect(markup).toMatch(/<input\b[^>]*type="nickname"/s)
    expect(markup).toContain('暂不登录，继续逛逛')
    expect(markup).toContain('aria-role="dialog"')
    const source = fs.readFileSync(`${basePath}.js`, 'utf8')
    expect(source).not.toMatch(/getUserInfo|getUserProfile|open-type="getUserInfo"/)
    const app = readJson(path.join(sourceRoot, 'app.json'))
    expect(app.tabBar.list).toContainEqual(expect.objectContaining({ pagePath: 'pages/account/account', text: '我' }))
  })
})
