import {
  createRouter,
  createWebHistory,
  type RouterHistory,
  type RouteRecordRaw,
} from 'vue-router'
import { authStore } from '../auth/store'

export interface AuthGate {
  readonly isAuthenticated: boolean
  restore(force?: boolean): Promise<void>
}

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('../views/HomeView.vue'),
    meta: { requiresAuth: true },
  },
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { guestOnly: true },
  },
  {
    path: '/register',
    name: 'register',
    component: () => import('../views/RegisterView.vue'),
    meta: { guestOnly: true },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/',
  },
]

export function createAppRouter(
  history: RouterHistory = createWebHistory(),
  auth: AuthGate = authStore,
) {
  const router = createRouter({ history, routes })

  router.beforeEach(async (to) => {
    if (to.meta.requiresAuth || to.meta.guestOnly) await auth.restore()

    if (to.meta.requiresAuth && !auth.isAuthenticated) {
      return {
        name: 'login',
        query: { redirect: to.fullPath },
      }
    }

    if (to.meta.guestOnly && auth.isAuthenticated) return { name: 'home' }
    return true
  })

  return router
}

export const router = createAppRouter()
