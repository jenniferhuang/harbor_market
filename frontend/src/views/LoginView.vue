<script setup lang="ts">
import { LoaderCircle, LogIn } from 'lucide-vue-next'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/useAuth'
import AuthLayout from '../components/AuthLayout.vue'
import PasswordField from '../components/PasswordField.vue'
import TextField from '../components/TextField.vue'

interface LoginErrors {
  username?: string
  password?: string
}

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const username = ref('')
const password = ref('')
const errors = reactive<LoginErrors>({})
const formError = ref('')
const isSubmitting = ref(false)

const registrationSucceeded = computed(() => route.query.registered === '1')
const sessionCheckFailed = computed(() => route.query.auth_error === 'session_check_failed')

watch(username, () => {
  errors.username = undefined
  formError.value = ''
})

watch(password, () => {
  errors.password = undefined
  formError.value = ''
})

function validate(): boolean {
  errors.username = username.value.trim() ? undefined : 'Enter your username.'
  errors.password = password.value ? undefined : 'Enter your password.'
  return !errors.username && !errors.password
}

function destinationAfterLogin(): string {
  const redirect = route.query.redirect
  if (typeof redirect === 'string' && redirect.startsWith('/') && !redirect.startsWith('//')) {
    return redirect
  }
  return '/'
}

function applyApiError(error: unknown) {
  if (!(error instanceof ApiError)) {
    formError.value = 'Sign in could not be completed. Please try again.'
    return
  }

  if (error.status === 401) {
    formError.value = 'Username or password is incorrect.'
    return
  }

  errors.username = error.fieldErrors.username
  errors.password = error.fieldErrors.password
  if (!errors.username && !errors.password) formError.value = error.message
}

async function submit() {
  if (isSubmitting.value || !validate()) return

  isSubmitting.value = true
  formError.value = ''

  try {
    await auth.login({ username: username.value.trim(), password: password.value })
    await router.replace(destinationAfterLogin())
  } catch (error) {
    password.value = ''
    await nextTick()
    applyApiError(error)
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <AuthLayout title="Sign in" description="Welcome back. Enter your account details to continue.">
    <p v-if="registrationSucceeded" class="notice notice--success" role="status">
      Account created. You can sign in now.
    </p>
    <p
      v-if="sessionCheckFailed"
      class="notice notice--error"
      role="alert"
    >
      We could not verify your existing session. Check the service connection, then sign in again.
    </p>

    <form class="auth-form" novalidate @submit.prevent="submit">
      <TextField
        id="login-username"
        v-model="username"
        label="Username"
        autocomplete="username"
        :error="errors.username"
        :disabled="isSubmitting"
      />
      <PasswordField
        id="login-password"
        v-model="password"
        label="Password"
        autocomplete="current-password"
        :error="errors.password"
        :disabled="isSubmitting"
      />

      <p v-if="formError" class="notice notice--error" role="alert">{{ formError }}</p>

      <button class="primary-button" type="submit" :disabled="isSubmitting">
        <LoaderCircle v-if="isSubmitting" class="spin" :size="18" aria-hidden="true" />
        <LogIn v-else :size="18" aria-hidden="true" />
        <span>{{ isSubmitting ? 'Signing in...' : 'Sign in' }}</span>
      </button>
    </form>

    <p class="auth-panel__alternate">
      New to Harbor Market?
      <RouterLink to="/register">Create account</RouterLink>
    </p>
  </AuthLayout>
</template>
