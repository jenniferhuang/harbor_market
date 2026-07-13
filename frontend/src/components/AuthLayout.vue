<script setup lang="ts">
import AppBrand from './AppBrand.vue'

defineProps<{
  title: string
  description: string
}>()
</script>

<template>
  <main class="auth-page">
    <section class="auth-panel" :aria-labelledby="`${title.toLowerCase().replaceAll(' ', '-')}-title`">
      <AppBrand />

      <header class="auth-panel__header">
        <p class="eyebrow">Account access</p>
        <h1 :id="`${title.toLowerCase().replaceAll(' ', '-')}-title`">{{ title }}</h1>
        <p>{{ description }}</p>
      </header>

      <slot />
    </section>
  </main>
</template>
