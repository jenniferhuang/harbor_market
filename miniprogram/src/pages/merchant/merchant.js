const { fetchMerchantStore, updateMerchantStore, uploadMerchantAnnouncement } = require('../../api/customer-shop')
const { getApiBaseUrl } = require('../../api/client')
const { getAuthState, subscribe } = require('../../state/auth-store')

function formFor(store) {
  return {
    name: store.name || '', phone: store.phone || '', address: store.address || '',
    latitude: store.latitude == null ? '' : String(store.latitude),
    longitude: store.longitude == null ? '' : String(store.longitude),
  }
}

function coordinate(value, limit, label) {
  if (!value.trim()) return null
  const number = Number(value)
  if (!/^-?\d+(?:\.\d+)?$/.test(value.trim()) || !Number.isFinite(number) || Math.abs(number) > limit) {
    throw new Error(`请填写有效的${label}。`)
  }
  return number
}

Page({
  data: {
    loading: true, allowed: false, saving: false, uploading: false,
    form: { name: '', phone: '', address: '', latitude: '', longitude: '' },
    announcementUrl: '', errorMessage: '', successMessage: '',
  },
  onLoad() {
    this._alive = true
    this._sequence = 0
    this._customerId = getAuthState().customer?.id || null
    this._unsubscribe = subscribe((state) => {
      const id = state.status === 'authenticated' ? state.customer.id : null
      if (id === this._customerId) return
      this._customerId = id
      this._sequence += 1
      if (this._alive) this.setData({ allowed: false, loading: false, announcementUrl: '', form: formFor({}), errorMessage: '账户已改变，请重新进入商家信息。', saving: false, uploading: false })
    })
    this.loadStore()
  },
  onUnload() { this._alive = false; this._sequence += 1; if (this._unsubscribe) this._unsubscribe() },
  isCurrent(sequence, origin) { return this._alive && sequence === this._sequence && origin === getApiBaseUrl() },
  async loadStore() {
    const sequence = ++this._sequence
    const origin = getApiBaseUrl()
    this.setData({ loading: true, errorMessage: '' })
    try {
      const store = await fetchMerchantStore()
      if (this.isCurrent(sequence, origin)) this.setData({ loading: false, allowed: true, form: formFor(store), announcementUrl: store.announcement_image_url || '' })
    } catch (error) {
      if (this.isCurrent(sequence, origin)) this.setData({ loading: false, allowed: false, errorMessage: error.status === 403 ? '当前微信账户没有商家编辑权限，请由后台管理员关联店主账户。' : error.message || '商家信息暂时无法加载。' })
    }
  },
  onFieldInput(event) {
    const field = event.currentTarget.dataset.field
    if (!['name', 'phone', 'address', 'latitude', 'longitude'].includes(field)) return
    this.setData({ form: { ...this.data.form, [field]: event.detail.value }, successMessage: '' })
  },
  async save() {
    if (!this.data.allowed || this.data.saving || this.data.uploading) return
    let values
    try {
      const form = this.data.form
      if (!form.name.trim()) throw new Error('请填写商家名字。')
      values = { name: form.name.trim(), phone: form.phone.trim(), address: form.address.trim(), latitude: coordinate(form.latitude, 90, '纬度'), longitude: coordinate(form.longitude, 180, '经度') }
      if ((values.latitude == null) !== (values.longitude == null)) throw new Error('导航经度和纬度需要一起填写，也可以一起留空。')
    } catch (error) {
      this.setData({ errorMessage: error.message, successMessage: '' })
      return
    }
    const sequence = ++this._sequence
    const origin = getApiBaseUrl()
    this.setData({ saving: true, errorMessage: '', successMessage: '' })
    try {
      const store = await updateMerchantStore(values)
      if (this.isCurrent(sequence, origin)) this.setData({ form: formFor(store), announcementUrl: store.announcement_image_url || '', successMessage: '商家信息已保存。' })
    } catch (error) {
      if (this.isCurrent(sequence, origin)) this.setData({ errorMessage: error.message || '商家信息保存失败。', allowed: error.status !== 403 })
    } finally {
      if (this.isCurrent(sequence, origin)) this.setData({ saving: false })
    }
  },
  chooseAnnouncement() {
    if (!this.data.allowed || this.data.saving || this.data.uploading || this._choosing) return
    this._choosing = true
    const sequence = this._sequence
    const origin = getApiBaseUrl()
    const success = (result) => {
      this._choosing = false
      if (!this.isCurrent(sequence, origin)) return
      const file = result.tempFiles?.[0]
      const path = file?.tempFilePath || result.tempFilePaths?.[0]
      if (path) this.uploadAnnouncement(path)
    }
    const fail = (error) => {
      this._choosing = false
      if (this.isCurrent(sequence, origin) && !String(error?.errMsg || '').includes('cancel')) this.setData({ errorMessage: '无法选择图片，请稍后重试。' })
    }
    try {
      if (typeof wx.chooseMedia === 'function') wx.chooseMedia({ count: 1, mediaType: ['image'], sourceType: ['album', 'camera'], success, fail })
      else if (typeof wx.chooseImage === 'function') wx.chooseImage({ count: 1, sizeType: ['compressed'], sourceType: ['album', 'camera'], success, fail })
      else fail({})
    } catch { fail({}) }
  },
  async uploadAnnouncement(path) {
    if (!this.data.allowed || this.data.saving || this.data.uploading) return
    const sequence = ++this._sequence
    const origin = getApiBaseUrl()
    this.setData({ uploading: true, errorMessage: '', successMessage: '' })
    try {
      const store = await uploadMerchantAnnouncement(path)
      if (this.isCurrent(sequence, origin)) this.setData({ announcementUrl: store.announcement_image_url || '', successMessage: '商家公告图片已更新。' })
    } catch (error) {
      if (this.isCurrent(sequence, origin)) this.setData({ errorMessage: error.message || '公告图片上传失败。', allowed: error.status !== 403 })
    } finally {
      if (this.isCurrent(sequence, origin)) this.setData({ uploading: false })
    }
  },
  previewAnnouncement() { if (this.data.announcementUrl) wx.previewImage({ current: this.data.announcementUrl, urls: [this.data.announcementUrl] }) },
})
