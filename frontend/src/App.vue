<script setup>
/** Root: canvas by default; ?ai=1 opens the standalone rationale-label module. */
import CanvasBoard from './components/CanvasBoard.vue'
import RationalePlayground from './components/RationalePlayground.vue'

const params = new URLSearchParams(window.location.search)
const showRationale = params.get('ai') === '1' || params.get('ai') === 'rationale'
</script>

<template>
  <RationalePlayground v-if="showRationale" />
  <CanvasBoard v-else />
</template>
