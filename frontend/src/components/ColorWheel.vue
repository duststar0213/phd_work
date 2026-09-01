<!-- HSV color disc + brightness slider. Canvas 2D API only (no color-picker npm package). -->
<script setup>
import { computed, onMounted, ref, watch } from 'vue' // Vue 3

const props = defineProps({
  modelValue: { type: String, required: true }, // hex, e.g. #FDE68A
})

const emit = defineEmits(['update:modelValue'])

const SIZE = 132
const canvasRef = ref(null)
const hsv = ref({ h: 48, s: 0.45, v: 0.99 })

/** Keep a number in [min, max]. */
function clamp(n, min, max) {
  return Math.min(max, Math.max(min, n))
}

/** HSV → 0–255 RGB. */
function hsvToRgb(h, s, v) {
  const hue = ((h % 360) + 360) % 360
  const c = v * s
  const x = c * (1 - Math.abs(((hue / 60) % 2) - 1))
  const m = v - c
  let r = 0
  let g = 0
  let b = 0
  if (hue < 60) [r, g, b] = [c, x, 0]
  else if (hue < 120) [r, g, b] = [x, c, 0]
  else if (hue < 180) [r, g, b] = [0, c, x]
  else if (hue < 240) [r, g, b] = [0, x, c]
  else if (hue < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)]
}

/** RGB 0–255 → #RRGGBB. */
function rgbToHex(r, g, b) {
  return `#${[r, g, b].map((n) => n.toString(16).padStart(2, '0')).join('')}`
}

/** Parse #RRGGBB back to HSV for the marker / slider. */
function hexToHsv(hex) {
  const raw = hex.replace('#', '')
  if (raw.length !== 6) return hsv.value
  const r = parseInt(raw.slice(0, 2), 16) / 255
  const g = parseInt(raw.slice(2, 4), 16) / 255
  const b = parseInt(raw.slice(4, 6), 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  let h = 0
  if (d !== 0) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return { h, s: max === 0 ? 0 : d / max, v: max }
}

/** HSV object → hex string. */
function toHex({ h, s, v }) {
  return rgbToHex(...hsvToRgb(h, s, v))
}

/** Paint hue around the circle, saturation by radius, brightness from hsv.v. */
function drawWheel() {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.width = SIZE
  canvas.height = SIZE
  const ctx = canvas.getContext('2d')
  const image = ctx.createImageData(SIZE, SIZE)
  const data = image.data
  const cx = SIZE / 2
  const cy = SIZE / 2
  const radius = SIZE / 2 - 1
  const value = hsv.value.v

  for (let y = 0; y < SIZE; y += 1) {
    for (let x = 0; x < SIZE; x += 1) {
      const dx = x - cx
      const dy = y - cy
      const dist = Math.sqrt(dx * dx + dy * dy)
      const i = (y * SIZE + x) * 4
      if (dist > radius) {
        data[i + 3] = 0
        continue
      }
      let h = (Math.atan2(dy, dx) * 180) / Math.PI
      if (h < 0) h += 360
      const [r, g, b] = hsvToRgb(h, dist / radius, value)
      data[i] = r
      data[i + 1] = g
      data[i + 2] = b
      data[i + 3] = 255
    }
  }
  ctx.putImageData(image, 0, 0)
}

/** v-model: emit the current HSV as hex. */
function emitColor() {
  emit('update:modelValue', toHex(hsv.value))
}

/** Map pointer position on the disc to hue + saturation. */
function pickFromEvent(e) {
  const canvas = canvasRef.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const x = ((e.clientX - rect.left) / rect.width) * SIZE
  const y = ((e.clientY - rect.top) / rect.height) * SIZE
  const cx = SIZE / 2
  const cy = SIZE / 2
  const dx = x - cx
  const dy = y - cy
  const radius = SIZE / 2 - 1
  let h = (Math.atan2(dy, dx) * 180) / Math.PI
  if (h < 0) h += 360
  hsv.value = {
    h,
    s: clamp(Math.sqrt(dx * dx + dy * dy) / radius, 0, 1),
    v: hsv.value.v,
  }
  emitColor()
}

/** Pointer down on the disc: capture + pick hue/sat. */
function onWheelPointerDown(e) {
  e.preventDefault()
  e.currentTarget.setPointerCapture(e.pointerId)
  pickFromEvent(e)
}

/** Drag across the disc while captured. */
function onWheelPointerMove(e) {
  if (!e.currentTarget.hasPointerCapture(e.pointerId)) return
  pickFromEvent(e)
}

/** Brightness slider — redraws the disc at the new V. */
function onValueInput(e) {
  hsv.value = { ...hsv.value, v: Number(e.target.value) }
  drawWheel()
  emitColor()
}

/** Marker sits at the current hue/sat on the disc. */
const markerStyle = computed(() => {
  const radius = SIZE / 2 - 1
  const angle = (hsv.value.h * Math.PI) / 180
  const dist = hsv.value.s * radius
  return {
    left: `${SIZE / 2 + Math.cos(angle) * dist}px`,
    top: `${SIZE / 2 + Math.sin(angle) * dist}px`,
  }
})

/** Full-saturation preview color for the brightness slider track. */
const valueColor = computed(() => toHex({ h: hsv.value.h, s: hsv.value.s, v: 1 }))

watch(
  () => props.modelValue,
  (hex) => {
    const next = hexToHsv(hex)
    const same =
      Math.abs(next.h - hsv.value.h) < 0.5 &&
      Math.abs(next.s - hsv.value.s) < 0.01 &&
      Math.abs(next.v - hsv.value.v) < 0.01
    if (same) return
    hsv.value = next
    drawWheel()
  },
)

onMounted(() => {
  hsv.value = hexToHsv(props.modelValue)
  drawWheel()
})
</script>


<template>
  <div class="wheel" @mousedown.stop @click.stop @pointerdown.stop>
    <div class="wheel-disc">
      <canvas
        ref="canvasRef"
        :width="SIZE"
        :height="SIZE"
        @pointerdown="onWheelPointerDown"
        @pointermove="onWheelPointerMove"
      />
      <span class="marker" :style="markerStyle" />
    </div>
    <label class="value">
      <span class="sr">Brightness</span>
      <input
        type="range"
        min="0"
        max="1"
        step="0.01"
        :value="hsv.v"
        :style="{ '--value-color': valueColor }"
        @input="onValueInput"
      />
    </label>
  </div>
</template>

<style scoped>
.wheel {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
}

.wheel-disc {
  position: relative;
  width: 132px;
  height: 132px;
  flex-shrink: 0;
  cursor: crosshair;
}

.wheel-disc canvas {
  display: block;
  width: 132px;
  height: 132px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
}

.marker {
  position: absolute;
  width: 12px;
  height: 12px;
  border: 2px solid #fff;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, 0.45);
  transform: translate(-50%, -50%);
  pointer-events: none;
}

.value {
  position: relative;
  width: 18px;
  height: 132px;
  margin: 0;
  flex-shrink: 0;
}

.value input {
  position: absolute;
  width: 132px;
  height: 18px;
  left: 50%;
  top: 50%;
  margin: 0;
  transform: translate(-50%, -50%) rotate(-90deg);
  appearance: none;
  background: linear-gradient(to right, #000, var(--value-color));
  border-radius: 99px;
  cursor: pointer;
}

.value input::-webkit-slider-thumb {
  appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.25);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}

.value input::-moz-range-thumb {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  border: 1px solid rgba(0, 0, 0, 0.25);
}

.sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
}
</style>
