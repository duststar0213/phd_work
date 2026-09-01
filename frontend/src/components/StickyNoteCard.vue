<!--
  One sticky note: select, drag, type, resize, recolor, connect.
  Vue 3 SFC. Native APIs: contenteditable, ResizeObserver. ColorWheel uses Canvas 2D.
-->
<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue' // Vue 3
import ColorWheel from './ColorWheel.vue'
import ConnectionIcon from './ConnectionIcon.vue'

const MIN_SIZE = 96 // smallest width/height from corner resize
const SIDES = ['top', 'right', 'bottom', 'left']
const PLACEHOLDER = 'define idea here' // grey hint on empty notes; hidden while typing

const props = defineProps({
  note: { type: Object, required: true },
  scale: { type: Number, default: 1 },
  selected: { type: Boolean, default: false },
  connectMode: { type: Boolean, default: false },
  magOutset: { type: Number, default: 18 }, // px from note edge to mag-point center
  drafting: { type: Boolean, default: false }, // true while dragging a connector from a mag point
})

const emit = defineEmits(['move', 'textChange', 'select', 'colorChange', 'resize', 'metrics', 'connectStart', 'connectEnd', 'requestConnect'])

const textRef = ref(null)
const noteRef = ref(null)
const colorOpen = ref(false)
const dragging = ref(false)
const resizing = ref(false)
const textFocused = ref(false) // hide placeholder while the caret is in the note
let didDrag = false // click vs drag: click focuses text, drag moves the note

const CORNERS = ['nw', 'ne', 'sw', 'se']

let resizeObserver = null

/** Sync initial text + watch the note box so mag points follow wrap/resize. */
onMounted(() => {
  if (textRef.value) {
    textRef.value.innerText = props.note.text
  }
  if (!noteRef.value) return
  resizeObserver = new ResizeObserver(() => {
    if (!noteRef.value) return
    emit('metrics', props.note.id, {
      width: noteRef.value.offsetWidth,
      height: noteRef.value.offsetHeight,
    })
  })
  resizeObserver.observe(noteRef.value)
})

onUnmounted(() => {
  resizeObserver?.disconnect()
})

watch(
  () => props.selected,
  (selected) => {
    if (!selected) colorOpen.value = false
  },
)

watch(
  () => props.drafting,
  (drafting) => {
    if (drafting) colorOpen.value = false
  },
)

/** Dark text on light fills, light text on dark fills. */
const textColor = computed(() => {
  const hex = props.note.color.replace('#', '')
  if (hex.length !== 6) return 'rgba(0,0,0,0.75)'
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
  return luminance > 0.55 ? 'rgba(0,0,0,0.75)' : 'rgba(255,255,255,0.88)'
})

/** Grey hint color, slightly faded vs body text so it reads as placeholder. */
const placeholderColor = computed(() => {
  const hex = props.note.color.replace('#', '')
  if (hex.length !== 6) return 'rgba(0,0,0,0.32)'
  const r = parseInt(hex.slice(0, 2), 16)
  const g = parseInt(hex.slice(2, 4), 16)
  const b = parseInt(hex.slice(4, 6), 16)
  const luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
  return luminance > 0.55 ? 'rgba(0,0,0,0.32)' : 'rgba(255,255,255,0.42)'
})

const showPlaceholder = computed(
  () => !String(props.note.text ?? '').trim() && !textFocused.value,
)

/** Caret in: drop the grey hint. Caret out + still empty: hint returns. */
function onTextFocus() {
  textFocused.value = true
}

function onTextBlur() {
  textFocused.value = false
}

/** Select this note so the floating toolbar appears. */
function activate() {
  emit('select', props.note.id)
}

/** Open / close the HSV popover from the color dot. */
function toggleColorPicker(e) {
  e.stopPropagation()
  activate()
  colorOpen.value = !colorOpen.value
}

/** Tell the canvas to arm the connector tool. */
function requestConnect(e) {
  e.stopPropagation()
  colorOpen.value = false
  emit('requestConnect')
}

/** Select + optional drag. Toolbar / resize handles are ignored here. */
function onNoteMouseDown(e) {
  if (e.button !== 0) return
  if (e.target.closest('.toolbar') || e.target.closest('.resize-handle') || e.target.closest('.mag-point')) return
  e.stopPropagation()
  activate()
  colorOpen.value = false
  didDrag = false

  let lastX = e.clientX
  let lastY = e.clientY
  const originX = e.clientX
  const originY = e.clientY

  const onMove = (ev) => {
    if (!didDrag) {
      if (Math.hypot(ev.clientX - originX, ev.clientY - originY) < 5) return
      didDrag = true
      dragging.value = true
      textRef.value?.blur()
    }
    emit(
      'move',
      props.note.id,
      (ev.clientX - lastX) / props.scale,
      (ev.clientY - lastY) / props.scale,
    )
    lastX = ev.clientX
    lastY = ev.clientY
  }

  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    dragging.value = false
    if (!didDrag) textRef.value?.focus()
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

/** Click (not drag) keeps the caret; a drag does not. */
function onNoteClick(e) {
  e.stopPropagation()
  if (didDrag || resizing.value) {
    e.preventDefault()
    textRef.value?.blur()
  }
}

/** Corner drag: updates x/y/width/height. Height is a min-height so text can still grow. */
function onResizeMouseDown(e, corner) {
  e.preventDefault()
  e.stopPropagation()
  activate()
  colorOpen.value = false
  resizing.value = true

  const start = {
    x: props.note.x,
    y: props.note.y,
    w: props.note.width ?? 168,
    h: noteRef.value?.offsetHeight ?? props.note.height ?? 168,
    cx: e.clientX,
    cy: e.clientY,
  }

  const onMove = (ev) => {
    const dx = (ev.clientX - start.cx) / props.scale
    const dy = (ev.clientY - start.cy) / props.scale
    let x = start.x
    let y = start.y
    let w = start.w
    let h = start.h

    if (corner.includes('e')) w = start.w + dx
    if (corner.includes('w')) {
      w = start.w - dx
      x = start.x + dx
    }
    if (corner.includes('s')) h = start.h + dy
    if (corner.includes('n')) {
      h = start.h - dy
      y = start.y + dy
    }

    if (w < MIN_SIZE) {
      if (corner.includes('w')) x = start.x + start.w - MIN_SIZE
      w = MIN_SIZE
    }
    if (h < MIN_SIZE) {
      if (corner.includes('n')) y = start.y + start.h - MIN_SIZE
      h = MIN_SIZE
    }

    emit('resize', props.note.id, { x, y, width: w, height: h })
  }

  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    resizing.value = false
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

/** Push wrapped text up to the canvas note model. */
function onTextInput(e) {
  emit('textChange', props.note.id, e.currentTarget.innerText)
}

/** Push hex fill from ColorWheel. */
function onColorChange(color) {
  emit('colorChange', props.note.id, color)
}

/** Mag-point press starts a path; release on another point finishes it. */
function onMagMouseDown(e, side) {
  e.preventDefault()
  e.stopPropagation()
  emit('connectStart', props.note.id, side)
}

/** Mag-point release finishes a path onto this side. */
function onMagMouseUp(e, side) {
  e.preventDefault()
  e.stopPropagation()
  emit('connectEnd', props.note.id, side)
}
</script>


<template>
  <div
    ref="noteRef"
    class="note"
    :class="{ selected, dragging, resizing }"
    :style="{
      left: `${note.x}px`,
      top: `${note.y}px`,
      width: `${note.width ?? 168}px`,
      minHeight: `${note.height ?? 168}px`,
      background: note.color,
      '--mag-outset': `${magOutset}px`,
    }"
    @mousedown="onNoteMouseDown"
    @click="onNoteClick"
  >
    <!-- Hidden while a mag-point drag is in progress so it does not cover the path. -->
    <div v-if="selected && !drafting" class="toolbar" @pointerdown.stop @mousedown.stop @click.stop>
      <button
        class="color-btn"
        type="button"
        :class="{ open: colorOpen }"
        title="Background color"
        @click="toggleColorPicker"
      >
        <span class="color-dot" :style="{ background: note.color }" />
        <svg class="caret" viewBox="0 0 12 12" aria-hidden="true">
          <path d="M2.5 4.5 L6 8 L9.5 4.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <div class="toolbar-item">
        <button
          class="color-btn"
          type="button"
          :class="{ open: connectMode }"
          title="Connector"
          @click="requestConnect"
        >
          <ConnectionIcon :size="18" />
        </button>
        <div v-if="connectMode" class="tool-hint">
          <span>drag to mag</span>
          <span>esc to cancel</span>
        </div>
      </div>

      <div v-if="colorOpen" class="color-popover">
        <ColorWheel :model-value="note.color" @update:model-value="onColorChange" />
      </div>
    </div>

    <template v-if="selected">
      <div
        v-for="corner in CORNERS"
        :key="corner"
        class="resize-handle"
        :class="corner"
        :title="`Resize ${corner}`"
        @mousedown="onResizeMouseDown($event, corner)"
      />
    </template>

    <template v-if="connectMode">
      <button
        v-for="side in SIDES"
        :key="side"
        class="mag-point"
        :class="side"
        type="button"
        :title="`Connect ${side}`"
        @mousedown="onMagMouseDown($event, side)"
        @mouseup="onMagMouseUp($event, side)"
      />
    </template>

    <!-- Native contenteditable; wraps and grows the note (no inner scroll) -->
    <span
      v-if="showPlaceholder"
      class="note-placeholder"
      :style="{ color: placeholderColor }"
    >{{ PLACEHOLDER }}</span>
    <div
      ref="textRef"
      class="note-text"
      :style="{ color: textColor }"
      contenteditable="true"
      spellcheck="false"
      @input="onTextInput"
      @focus="onTextFocus"
      @mousedown="onTextFocus"
      @blur="onTextBlur"
    />
  </div>
</template>

<style scoped>
.note {
  position: absolute;
  box-sizing: border-box;
  height: auto;
  border-radius: 2px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35), 0 1px 4px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  user-select: none;
  transform-origin: top left;
  z-index: 1;
  cursor: grab;
  overflow: visible;
}

.note.selected {
  z-index: 20;
  box-shadow:
    0 0 0 1.5px #8ec8ff,
    0 8px 24px rgba(0, 0, 0, 0.4);
}

.note.dragging,
.note.dragging .note-text {
  cursor: grabbing;
}

.toolbar {
  position: absolute;
  top: 50%;
  right: calc(100% + var(--mag-outset) + 14px);
  left: auto;
  bottom: auto;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 36px;
  height: auto;
  padding: 6px 4px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  cursor: default;
}

.color-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  width: 28px;
  height: 28px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
}

.color-btn:hover,
.color-btn.open {
  background: rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
}

.toolbar-item {
  position: relative;
}

.tool-hint {
  position: absolute;
  right: calc(100% + 8px);
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
  width: max-content;
  max-width: none;
  padding: 3px 7px;
  background: rgba(253, 230, 138, 0.12);
  border: 1px solid rgba(253, 230, 138, 0.25);
  border-radius: 7px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1.3;
  color: rgba(253, 230, 138, 0.78);
  letter-spacing: 0.02em;
  text-align: right;
  white-space: nowrap;
  pointer-events: auto;
  z-index: 8;
}

.tool-hint:hover {
  padding: 5px 9px;
  font-size: 11px;
}

.color-dot {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.35);
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.12);
  flex-shrink: 0;
}

.caret {
  width: 8px;
  height: 8px;
  transform: rotate(90deg);
}

.color-popover {
  position: absolute;
  top: 0;
  left: calc(100% + 8px);
  right: auto;
  padding: 10px 12px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45);
}

.resize-handle {
  position: absolute;
  width: 9px;
  height: 9px;
  background: #fff;
  border: 1.5px solid #8ec8ff;
  border-radius: 1px;
  box-sizing: border-box;
  z-index: 4;
  pointer-events: auto;
}

.resize-handle.nw {
  left: -5px;
  top: -5px;
  cursor: nwse-resize;
}

.resize-handle.ne {
  right: -5px;
  top: -5px;
  cursor: nesw-resize;
}

.resize-handle.sw {
  left: -5px;
  bottom: -5px;
  cursor: nesw-resize;
}

.resize-handle.se {
  right: -5px;
  bottom: -5px;
  cursor: nwse-resize;
}

.note-placeholder {
  position: absolute;
  left: 14px;
  top: 14px;
  right: 14px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.55;
  pointer-events: none;
  user-select: none;
}

.note:focus-within .note-placeholder {
  display: none;
}

.note-text {
  padding: 14px;
  overflow: visible;
  outline: none;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  word-break: break-word;
  cursor: text;
  flex: 1;
  min-height: 100%;
}

.mag-point {
  position: absolute;
  width: 12px;
  height: 12px;
  padding: 0;
  border-radius: 50%;
  background: #fff;
  border: 2px solid #8ec8ff;
  box-sizing: border-box;
  z-index: 6;
  cursor: inherit;
  transform: translate(-50%, -50%);
}

.mag-point:hover {
  transform: translate(-50%, -50%) scale(1.25);
  background: #8ec8ff;
}

.mag-point.top {
  left: 50%;
  top: calc(-1 * var(--mag-outset));
}

.mag-point.right {
  left: calc(100% + var(--mag-outset));
  top: 50%;
}

.mag-point.bottom {
  left: 50%;
  top: calc(100% + var(--mag-outset));
}

.mag-point.left {
  left: calc(-1 * var(--mag-outset));
  top: 50%;
}
</style>
