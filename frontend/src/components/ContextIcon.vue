<!-- Written-brief glyph for the design context well (inline SVG, no icon package). -->
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
    <rect x="4" y="4" width="16" height="16" rx="2.5" />
    <path d="M8.5 9.5h7" />
    <path d="M8.5 14h4.5" />
  </svg>
</template>
