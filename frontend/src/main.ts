import { createApp } from 'vue'
import App from './App.vue'
import { ADMIN_PERMISSION_CHANGED_EVENT, AUTH_REQUIRED_EVENT } from './api/client'
import { authKey } from './auth/useAuth'
import { authStore } from './auth/store'
import { router } from './router'
import './styles/main.css'

window.addEventListener(AUTH_REQUIRED_EVENT, () => {
  const redirect = router.currentRoute.value.fullPath
  authStore.clearSession()
  if (router.currentRoute.value.name !== 'login') {
    void router.replace({ name: 'login', query: { redirect } })
  }
})

window.addEventListener(ADMIN_PERMISSION_CHANGED_EVENT, () => {
  const redirect = router.currentRoute.value.fullPath
  void authStore
    .restore(true)
    .then(async () => {
      if (router.currentRoute.value.fullPath !== redirect) return
      if (!authStore.isAuthenticated) {
        await router.replace({ name: 'login', query: { redirect } })
        return
      }
      await router.replace({ name: 'home', query: { access_changed: '1' } })
    })
    .catch(async () => {
      if (router.currentRoute.value.fullPath !== redirect) return
      await router.replace({
        name: 'login',
        query: { redirect, auth_error: 'session_check_failed' },
      })
    })
})

createApp(App).provide(authKey, authStore).use(router).mount('#app')
