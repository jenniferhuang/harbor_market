<script setup lang="ts">
import { Check, LoaderCircle, LogOut, PackageSearch, UserRound } from 'lucide-vue-next'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/useAuth'
import AppBrand from '../components/AppBrand.vue'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const isLoggingOut = ref(false)
const logoutError = ref('')
const accessChanged = computed(() => route.query.access_changed === '1')

async function logout() {
  if (isLoggingOut.value) return

  isLoggingOut.value = true
  logoutError.value = ''

  try {
    await auth.logout()
    await router.replace({ name: 'login' })
  } catch (error) {
    logoutError.value =
      error instanceof ApiError
        ? error.message
        : '退出登录失败，请稍后重试。'
  } finally {
    isLoggingOut.value = false
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <div class="app-header__inner">
        <AppBrand />
        <button
          class="secondary-button"
          type="button"
          :disabled="isLoggingOut"
          @click="logout"
        >
          <LoaderCircle
            v-if="isLoggingOut"
            class="spin"
            :size="17"
            aria-hidden="true"
          />
          <LogOut
            v-else
            :size="17"
            aria-hidden="true"
          />
          <span>{{ isLoggingOut ? '正在退出…' : '退出登录' }}</span>
        </button>
      </div>
    </header>

    <main class="home-page">
      <section
        class="welcome-section"
        aria-labelledby="welcome-title"
      >
        <p class="eyebrow">
          首页
        </p>
        <h1 id="welcome-title">
          欢迎，{{ auth.user?.username }}
        </h1>
        <p class="welcome-section__intro">
          您已成功登录。
        </p>

        <div class="session-row">
          <span
            class="session-row__icon"
            aria-hidden="true"
          ><UserRound :size="22" /></span>
          <div>
            <span class="session-row__label">当前账号</span>
            <strong>{{ auth.user?.username }}</strong>
          </div>
          <span class="status-label"><Check
            :size="15"
            aria-hidden="true"
          /> 已登录</span>
        </div>

        <RouterLink
          v-if="auth.isAdmin"
          class="admin-entry"
          :to="{ name: 'admin-products' }"
        >
          <span
            class="admin-entry__icon"
            aria-hidden="true"
          ><PackageSearch :size="23" /></span>
          <span>
            <strong>商品管理后台</strong>
            <small>维护商品、类目、图片与 Excel 批量数据</small>
          </span>
          <span aria-hidden="true">→</span>
        </RouterLink>

        <p
          v-if="accessChanged"
          class="notice notice--error home-page__error"
          role="alert"
        >
          您的管理员权限已变更，商品管理页面已关闭。
        </p>

        <p
          v-if="logoutError"
          class="notice notice--error home-page__error"
          role="alert"
        >
          {{ logoutError }}
        </p>
      </section>
    </main>
  </div>
</template>
