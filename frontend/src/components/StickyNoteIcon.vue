<!-- Lucide-style sticky-note-with-pen glyph (inline SVG, no icon package). -->
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
    <path d="M16 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8Z" />
    <path d="M15 3v4a2 2 0 0 0 2 2h4" />
    <path d="M6.5 18.5 7.4 14.9 12.9 9.5 15.5 12.1 10.1 17.6Z" />
    <path d="M7.4 14.9 10.1 17.6" />
  </svg>
</template>
