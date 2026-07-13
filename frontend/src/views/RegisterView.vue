<script setup lang="ts">
import { LoaderCircle, UserPlus } from 'lucide-vue-next'
import { reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import { useAuth } from '../auth/useAuth'
import AuthLayout from '../components/AuthLayout.vue'
import PasswordField from '../components/PasswordField.vue'
import TextField from '../components/TextField.vue'

interface RegistrationErrors {
  username?: string
  password?: string
  confirmPassword?: string
}

const auth = useAuth()
const router = useRouter()
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const errors = reactive<RegistrationErrors>({})
const formError = ref('')
const isSubmitting = ref(false)

watch(username, () => {
  errors.username = undefined
  formError.value = ''
})

watch(password, () => {
  errors.password = undefined
  errors.confirmPassword = undefined
  formError.value = ''
})

watch(confirmPassword, () => {
  errors.confirmPassword = undefined
  formError.value = ''
})

function validate(): boolean {
  errors.username = username.value.trim() ? undefined : 'Choose a username.'
  errors.password = password.value ? undefined : 'Choose a password.'
  errors.confirmPassword = confirmPassword.value ? undefined : 'Confirm your password.'

  if (password.value && confirmPassword.value && password.value !== confirmPassword.value) {
    errors.confirmPassword = 'Passwords do not match.'
  }

  return !errors.username && !errors.password && !errors.confirmPassword
}

function applyApiError(error: unknown) {
  if (!(error instanceof ApiError)) {
    formError.value = 'Your account could not be created. Please try again.'
    return
  }

  if (error.status === 409) {
    errors.username = 'That username is already taken.'
    return
  }

  errors.username = error.fieldErrors.username
  errors.password = error.fieldErrors.password
  errors.confirmPassword = error.fieldErrors.confirmPassword
  if (!errors.username && !errors.password && !errors.confirmPassword) formError.value = error.message
}

async function submit() {
  if (isSubmitting.value || !validate()) return

  isSubmitting.value = true
  formError.value = ''

  try {
    await auth.register({ username: username.value.trim(), password: password.value })
    await router.replace({ name: 'login', query: { registered: '1' } })
  } catch (error) {
    applyApiError(error)
  } finally {
    isSubmitting.value = false
  }
}
</script>

<template>
  <AuthLayout title="Create account" description="Set up your username and password to get started.">
    <form class="auth-form" novalidate @submit.prevent="submit">
      <TextField
        id="register-username"
        v-model="username"
        label="Username"
        autocomplete="username"
        :error="errors.username"
        :disabled="isSubmitting"
      />
      <PasswordField
        id="register-password"
        v-model="password"
        label="Password"
        autocomplete="new-password"
        :error="errors.password"
        :disabled="isSubmitting"
      />
      <PasswordField
        id="register-confirm-password"
        v-model="confirmPassword"
        label="Confirm password"
        autocomplete="new-password"
        :error="errors.confirmPassword"
        :disabled="isSubmitting"
      />

      <p v-if="formError" class="notice notice--error" role="alert">{{ formError }}</p>

      <button class="primary-button" type="submit" :disabled="isSubmitting">
        <LoaderCircle v-if="isSubmitting" class="spin" :size="18" aria-hidden="true" />
        <UserPlus v-else :size="18" aria-hidden="true" />
        <span>{{ isSubmitting ? 'Creating account...' : 'Create account' }}</span>
      </button>
    </form>

    <p class="auth-panel__alternate">
      Already have an account?
      <RouterLink to="/login">Sign in</RouterLink>
    </p>
  </AuthLayout>
</template>
