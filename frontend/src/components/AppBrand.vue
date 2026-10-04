<script setup lang="ts">
import { Store } from 'lucide-vue-next'
</script>

<template>
  <div class="app-brand" aria-label="港湾集市">
    <span class="app-brand__mark" aria-hidden="true">
      <Store :size="20" :stroke-width="2" />
    </span>
    <span class="app-brand__name">港湾集市</span>
  </div>
</template>
