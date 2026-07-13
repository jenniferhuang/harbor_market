<script setup lang="ts">
import { Store } from 'lucide-vue-next'
</script>

<template>
  <div class="app-brand" aria-label="Harbor Market">
    <span class="app-brand__mark" aria-hidden="true">
      <Store :size="20" :stroke-width="2" />
    </span>
    <span class="app-brand__name">Harbor Market</span>
  </div>
</template>
