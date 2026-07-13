<script setup lang="ts">
import { Eye, EyeOff } from 'lucide-vue-next'
import { ref } from 'vue'

defineProps<{
  id: string
  label: string
  modelValue: string
  autocomplete: 'current-password' | 'new-password'
  error?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const isVisible = ref(false)

function updateValue(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="field">
    <label :for="id">{{ label }}</label>
    <div class="password-control">
      <input
        :id="id"
        class="field__input field__input--password"
        :type="isVisible ? 'text' : 'password'"
        :value="modelValue"
        :autocomplete="autocomplete"
        :disabled="disabled"
        :aria-invalid="Boolean(error)"
        :aria-describedby="`${id}-error`"
        @input="updateValue"
      />
      <button
        class="icon-button password-control__toggle"
        type="button"
        :disabled="disabled"
        :aria-label="isVisible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`"
        :aria-pressed="isVisible"
        @click="isVisible = !isVisible"
      >
        <EyeOff v-if="isVisible" :size="19" aria-hidden="true" />
        <Eye v-else :size="19" aria-hidden="true" />
      </button>
    </div>
    <p :id="`${id}-error`" class="field__error" aria-live="polite">{{ error ?? '' }}</p>
  </div>
</template>
