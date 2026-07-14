import { createApp } from 'vue'
import App from './App.vue'
import { AUTH_REQUIRED_EVENT } from './api/client'
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

createApp(App).provide(authKey, authStore).use(router).mount('#app')
