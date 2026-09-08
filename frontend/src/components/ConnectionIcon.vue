<!-- Connector tool: two nodes + orthogonal path, with a four-point star. -->
<script setup>
defineProps({
  size: { type: Number, default: 28 },
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
    <g transform="translate(-0.6, -1.2)">
      <circle cx="18.5" cy="5" r="2.15" />
      <circle cx="5.5" cy="19" r="2.15" />
      <path d="M16.4 5 H9.2 a3 3 0 0 0 0 6 h5.6 a3 3 0 0 1 0 6 H7.6" />
    </g>
    <path
      fill="currentColor"
      stroke="none"
      d="M19.35 13.9 20.35 17.45 23.9 18.45 20.35 19.45 19.35 23 18.35 19.45 14.8 18.45 18.35 17.45 Z"
    />
  </svg>
</template>
