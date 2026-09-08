<!--
  Compact label text with a hover card of the full phrase.
  Does not change stored wording; overlay is visual only.
-->
<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { compactLabelDisplay } from '../api/rationale'

const props = defineProps({
  text: { type: String, default: '' },
  prefer: { type: String, default: 'right' }, // right | below
  disabled: { type: Boolean, default: false },
})

const display = computed(() => compactLabelDisplay(props.text))
const open = ref(false)
const pos = ref({ left: '0px', top: '0px' })
const triggerRef = ref(null)
let leaveTimer = 0

const show = computed(() => open.value && !props.disabled && display.value.compact && display.value.full)

function place() {
  const el = triggerRef.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const gap = 8
  const maxW = 260
  let left = props.prefer === 'below' ? r.left : r.right + gap
  let top = props.prefer === 'below' ? r.bottom + gap : r.top
  if (left + maxW > window.innerWidth - 8) {
    left = Math.max(8, props.prefer === 'below' ? r.left : r.left - maxW - gap)
  }
  if (top + 96 > window.innerHeight - 8) {
    top = Math.max(8, r.top - 8)
  }
  if (left < 8) left = 8
  if (top < 8) top = 8
  pos.value = { left: `${left}px`, top: `${top}px` }
}

function onEnter() {
  window.clearTimeout(leaveTimer)
  if (props.disabled || !display.value.compact) return
  place()
  open.value = true
}

function onLeave() {
  window.clearTimeout(leaveTimer)
  leaveTimer = window.setTimeout(() => {
    open.value = false
  }, 60)
}

onUnmounted(() => {
  window.clearTimeout(leaveTimer)
})
</script>

<template>
  <span
    ref="triggerRef"
    class="label-peek"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
  >
    {{ display.preview }}
    <Teleport to="body">
      <div
        v-if="show"
        class="label-peek-card"
        :style="pos"
      >{{ display.full }}</div>
    </Teleport>
  </span>
</template>

<style scoped>
.label-peek {
  min-width: 0;
  display: block;
  overflow: visible;
  overflow-wrap: anywhere;
}
</style>

<style>
.label-peek-card {
  position: fixed;
  z-index: 240;
  box-sizing: border-box;
  max-width: min(260px, calc(100vw - 16px));
  padding: 8px 10px;
  background: #fffdf8;
  border: 1px solid rgba(44, 40, 31, 0.18);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(44, 40, 31, 0.16);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.45;
  color: #16161d;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  pointer-events: none;
}
</style>
