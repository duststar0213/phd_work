<!-- Lasso-loop glyph for the freehand group tool (inline SVG, no icon package). -->
<script setup>
defineProps({
  size: { type: Number, default: 18 },
  color: { type: String, default: 'currentColor' },
})
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    :stroke="color"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path d="M8.2 19.2c-2.6-1.2-4.2-3.8-4.2-6.7C4 8.1 7.6 4.8 12 4.8c4.2 0 7.6 3 7.6 6.7 0 2.6-1.6 4.9-4 6.1" />
    <path d="M16.2 19.8c.8.7 1.8 1.2 3 1.2 1.6 0 2.8-1 2.8-2.4 0-2.2-3.4-2.6-3.4-4.6" />
  </svg>
</template>
