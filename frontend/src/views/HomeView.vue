<script setup lang="ts">
import { Check, LoaderCircle, LogOut, UserRound } from 'lucide-vue-next'
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/useAuth'
import AppBrand from '../components/AppBrand.vue'

const auth = useAuth()
const router = useRouter()
const isLoggingOut = ref(false)
const logoutError = ref('')

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
        : 'Sign out could not be completed. Please try again.'
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
        <button class="secondary-button" type="button" :disabled="isLoggingOut" @click="logout">
          <LoaderCircle v-if="isLoggingOut" class="spin" :size="17" aria-hidden="true" />
          <LogOut v-else :size="17" aria-hidden="true" />
          <span>{{ isLoggingOut ? 'Signing out...' : 'Sign out' }}</span>
        </button>
      </div>
    </header>

    <main class="home-page">
      <section class="welcome-section" aria-labelledby="welcome-title">
        <p class="eyebrow">Home</p>
        <h1 id="welcome-title">Welcome, {{ auth.user?.username }}</h1>
        <p class="welcome-section__intro">Your account is signed in and ready.</p>

        <div class="session-row">
          <span class="session-row__icon" aria-hidden="true"><UserRound :size="22" /></span>
          <div>
            <span class="session-row__label">Signed in as</span>
            <strong>{{ auth.user?.username }}</strong>
          </div>
          <span class="status-label"><Check :size="15" aria-hidden="true" /> Active session</span>
        </div>

        <p v-if="logoutError" class="notice notice--error home-page__error" role="alert">
          {{ logoutError }}
        </p>
      </section>
    </main>
  </div>
</template>
