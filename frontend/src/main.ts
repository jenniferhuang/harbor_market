import { createApp } from 'vue'
import App from './App.vue'
import { authKey } from './auth/useAuth'
import { authStore } from './auth/store'
import { router } from './router'
import './styles/main.css'

createApp(App).provide(authKey, authStore).use(router).mount('#app')
