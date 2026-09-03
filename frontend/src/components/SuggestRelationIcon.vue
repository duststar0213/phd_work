<!-- Same size as ConnectionIcon: dashed path, four-point star at the bottom. -->
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
    <g transform="translate(0, -2.2)">
      <circle cx="18.5" cy="5" r="2.15" />
      <circle cx="5.5" cy="19" r="2.15" />
      <path
        stroke-width="1.25"
        stroke-dasharray="1.8 3.2"
        d="M16.4 5 H9.2 a3 3 0 0 0 0 6 h5.6 a3 3 0 0 1 0 6 H7.6"
      />
    </g>
    <path
      fill="currentColor"
      stroke="none"
      d="M12 16.1 13.55 19.15 17.1 19.8 13.55 20.45 12 23.5 10.45 20.45 6.9 19.8 10.45 19.15 Z"
    />
  </svg>
</template>
