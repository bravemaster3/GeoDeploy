<template>
  <DemoBanner />
  <RouterView />
</template>

<script setup>
import { RouterView } from 'vue-router'
import DemoBanner from '@/components/shared/DemoBanner.vue'
</script>
