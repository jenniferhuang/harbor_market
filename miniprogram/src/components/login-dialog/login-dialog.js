const {
  getAuthState,
  subscribe,
  loginWithWeChat,
  updateProfile,
} = require('../../state/auth-store')

function messageFor(error, fallback) {
  return error && typeof error.message === 'string' && error.message ? error.message : fallback
}

Component({
  properties: {
    visible: {
      type: Boolean,
      value: false,
    },
    mode: {
      type: String,
      value: 'login',
    },
  },

  data: {
    nickname: '',
    avatarPath: '',
    avatarPreview: '',
    authBusy: false,
    submitting: false,
    errorMessage: '',
  },

  observers: {
    'visible, mode'(visible) {
      if (visible) {
        this.resetDraft()
        this.hideTabBar()
      } else {
        this.restoreTabBar()
      }
    },
  },

  lifetimes: {
    attached() {
      this._attached = true
      this._unsubscribe = subscribe((state) => {
        if (this._attached) this.setData({ authBusy: state.busy || state.status === 'restoring' })
      })
      this.setData({ authBusy: getAuthState().busy || getAuthState().status === 'restoring' })
      if (this.properties.visible) {
        this.resetDraft()
        this.hideTabBar()
      }
    },

    detached() {
      this._attached = false
      if (this._unsubscribe) this._unsubscribe()
      this.restoreTabBar()
    },
  },

  pageLifetimes: {
    show() {
      if (this.properties.visible) this.hideTabBar()
    },
    hide() {
      this.restoreTabBar()
    },
  },

  methods: {
    hideTabBar() {
      if (this._tabBarHidden || typeof wx.hideTabBar !== 'function') return
      this._tabBarHidden = true
      wx.hideTabBar({
        animation: false,
        fail: () => {
          this._tabBarHidden = false
        },
      })
    },

    restoreTabBar() {
      if (!this._tabBarHidden) return
      this._tabBarHidden = false
      if (typeof wx.showTabBar === 'function') wx.showTabBar({ animation: false })
    },

    resetDraft() {
      if (this._submitInFlight) return
      const state = getAuthState()
      this.setData({
        nickname: state.customer ? state.customer.nickname || '' : '',
        avatarPath: '',
        avatarPreview: state.avatarPath || '',
        errorMessage: '',
        submitting: false,
        authBusy: state.busy || state.status === 'restoring',
      })
    },

    blockBackgroundTouch() {},

    close() {
      if (this._submitInFlight || this.data.authBusy) return
      this.triggerEvent('close')
    },

    onChooseAvatar(event) {
      if (this._submitInFlight || this.data.authBusy) return
      const avatarPath = event.detail && event.detail.avatarUrl
      if (typeof avatarPath !== 'string' || !avatarPath) return
      this.setData({ avatarPath, avatarPreview: avatarPath, errorMessage: '' })
    },

    onAvatarError() {
      this.setData({ avatarPreview: '', avatarPath: '', errorMessage: '头像暂时无法显示，请重新选择。' })
    },

    onNicknameInput(event) {
      if (this._submitInFlight || this.data.authBusy) return
      this.setData({ nickname: event.detail.value || '', errorMessage: '' })
    },

    async submit() {
      if (this._submitInFlight || this.data.authBusy) return
      this._submitInFlight = true
      this.setData({ submitting: true, errorMessage: '' })
      const mode = this.properties.mode
      const profile = {
        nickname: this.data.nickname.trim(),
        avatarPath: this.data.avatarPath,
      }
      try {
        if (mode === 'profile') await updateProfile(profile)
        else await loginWithWeChat(profile)
        const state = getAuthState()
        if (state.status !== 'authenticated' || !state.customer) {
          throw new Error('登录状态未能确认，请稍后重试。')
        }
        if (this._attached) {
          this.triggerEvent('success', {
            mode,
            customerId: state.customer.id,
            warningMessage: state.errorMessage || '',
          })
        }
      } catch (error) {
        if (this._attached) {
          this.setData({
            errorMessage: messageFor(
              error,
              mode === 'profile' ? '资料保存失败，请稍后重试。' : '微信登录失败，请稍后重试。',
            ),
          })
        }
      } finally {
        this._submitInFlight = false
        if (this._attached) this.setData({ submitting: false })
      }
    },
  },
})
