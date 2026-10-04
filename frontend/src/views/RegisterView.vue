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
  errors.username = username.value.trim() ? undefined : '请输入用户名。'
  errors.password = password.value ? undefined : '请输入密码。'
  errors.confirmPassword = confirmPassword.value ? undefined : '请再次输入密码。'

  if (password.value && confirmPassword.value && password.value !== confirmPassword.value) {
    errors.confirmPassword = '两次输入的密码不一致。'
  }

  return !errors.username && !errors.password && !errors.confirmPassword
}

function applyApiError(error: unknown) {
  if (!(error instanceof ApiError)) {
    formError.value = '账号创建失败，请稍后重试。'
    return
  }

  if (error.status === 409) {
    errors.username = '该用户名已被注册。'
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
  <AuthLayout title="创建账号" description="设置用户名和密码，开始使用港湾集市。">
    <form class="auth-form" novalidate @submit.prevent="submit">
      <TextField
        id="register-username"
        v-model="username"
        label="用户名"
        autocomplete="username"
        :error="errors.username"
        :disabled="isSubmitting"
      />
      <PasswordField
        id="register-password"
        v-model="password"
        label="密码"
        autocomplete="new-password"
        :error="errors.password"
        :disabled="isSubmitting"
      />
      <PasswordField
        id="register-confirm-password"
        v-model="confirmPassword"
        label="确认密码"
        autocomplete="new-password"
        :error="errors.confirmPassword"
        :disabled="isSubmitting"
      />

      <p v-if="formError" class="notice notice--error" role="alert">{{ formError }}</p>

      <button class="primary-button" type="submit" :disabled="isSubmitting">
        <LoaderCircle v-if="isSubmitting" class="spin" :size="18" aria-hidden="true" />
        <UserPlus v-else :size="18" aria-hidden="true" />
        <span>{{ isSubmitting ? '正在创建账号…' : '创建账号' }}</span>
      </button>
    </form>

    <p class="auth-panel__alternate">
      已有账号？
      <RouterLink to="/login">登录</RouterLink>
    </p>
  </AuthLayout>
</template>
