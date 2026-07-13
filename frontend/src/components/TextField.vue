<script setup lang="ts">
defineProps<{
  id: string
  label: string
  modelValue: string
  autocomplete: string
  error?: string
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

function updateValue(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <div class="field">
    <label :for="id">{{ label }}</label>
    <input
      :id="id"
      class="field__input"
      type="text"
      :value="modelValue"
      :autocomplete="autocomplete"
      :disabled="disabled"
      :aria-invalid="Boolean(error)"
      :aria-describedby="`${id}-error`"
      autocapitalize="none"
      spellcheck="false"
      @input="updateValue"
    />
    <p :id="`${id}-error`" class="field__error" aria-live="polite">{{ error ?? '' }}</p>
  </div>
</template>
