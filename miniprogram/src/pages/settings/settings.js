const { fetchCategories } = require('../../api/catalog')
const { getApiBaseUrl, setApiBaseUrl } = require('../../api/client')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

Page({
  data: {
    savedBaseUrl: '',
    baseUrlInput: '',
    testing: false,
    saving: false,
    statusKind: '',
    statusMessage: '',
  },

  onShow() {
    const savedBaseUrl = getApiBaseUrl()
    this.setData({
      savedBaseUrl,
      baseUrlInput: savedBaseUrl,
      statusKind: '',
      statusMessage: '',
    })
  },

  onBaseUrlInput(event) {
    this.setData({
      baseUrlInput: event.detail.value,
      statusKind: '',
      statusMessage: '',
    })
  },

  async testConnection() {
    if (this.data.testing || this.data.saving) return
    const previousBaseUrl = getApiBaseUrl()
    this.setData({ testing: true, statusKind: '', statusMessage: '' })
    let candidateWasApplied = false
    try {
      setApiBaseUrl(this.data.baseUrlInput)
      candidateWasApplied = true
      const categories = await fetchCategories()
      this.setData({
        statusKind: 'success',
        statusMessage: `连接成功，目录返回 ${categories.length} 个类目。测试不会保存该地址。`,
      })
    } catch (error) {
      this.setData({
        statusKind: 'error',
        statusMessage: messageFor(error, '连接测试失败，请检查地址和微信合法域名配置。'),
      })
    } finally {
      if (candidateWasApplied) {
        try {
          setApiBaseUrl(previousBaseUrl)
        } catch {
          // The previously stored value was already accepted by the client.
        }
      }
      this.setData({ testing: false })
    }
  },

  saveBaseUrl() {
    if (this.data.testing || this.data.saving) return
    this.setData({ saving: true, statusKind: '', statusMessage: '' })
    try {
      const savedBaseUrl = setApiBaseUrl(this.data.baseUrlInput)
      this.setData({
        savedBaseUrl,
        baseUrlInput: savedBaseUrl,
        statusKind: 'success',
        statusMessage: 'API 地址已保存到当前设备。返回选购页即可重新加载目录。',
      })
    } catch (error) {
      this.setData({
        statusKind: 'error',
        statusMessage: messageFor(error, '地址保存失败，请输入有效的 HTTPS 地址。'),
      })
    } finally {
      this.setData({ saving: false })
    }
  },
})
