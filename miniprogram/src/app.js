const { restoreSession } = require('./state/auth-store')

App({
  onLaunch() {
    // Session restoration reports any recoverable failure through auth-store.
    restoreSession().catch(() => {})
  },

  globalData: {
    appName: '港湾集市',
  },
})
