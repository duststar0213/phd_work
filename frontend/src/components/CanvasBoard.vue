<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import StickyNoteCard from './StickyNoteCard.vue'
import StickyNoteIcon from './StickyNoteIcon.vue'

const NOTE_COLORS = [
  '#FDE68A',
  '#FCA5A5',
  '#86EFAC',
  '#93C5FD',
  '#F9A8D4',
  '#C4B5FD',
]

const notes = ref([])
const toolActive = ref(false)
const pan = ref({ x: 0, y: 0 })
const scale = 1
const canvasRef = ref(null)

let nextId = 1
const panRef = { x: 0, y: 0 }

const notesLayerStyle = computed(() => ({
  transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${scale})`,
}))

const patternOffset = computed(() => ({
  x: pan.value.x % 28,
  y: pan.value.y % 28,
}))

function onCanvasMouseDown(e) {
  if (toolActive.value) return

  const drag = {
    startX: e.clientX,
    startY: e.clientY,
    px: panRef.x,
    py: panRef.y,
  }

  const move = (ev) => {
    pan.value = {
      x: drag.px + (ev.clientX - drag.startX),
      y: drag.py + (ev.clientY - drag.startY),
    }
    panRef.x = pan.value.x
    panRef.y = pan.value.y
  }

  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
  }

  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

function onCanvasClick(e) {
  if (!toolActive.value || !canvasRef.value) return

  const rect = canvasRef.value.getBoundingClientRect()
  const cx = (e.clientX - rect.left - panRef.x) / scale
  const cy = (e.clientY - rect.top - panRef.y) / scale
  const id = nextId++

  notes.value.push({
    id,
    x: cx - 90,
    y: cy - 14,
    text: '',
    color: NOTE_COLORS[(id - 1) % NOTE_COLORS.length],
  })
  toolActive.value = false
}

function moveNote(id, dx, dy) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.x += dx
  note.y += dy
}

function changeText(id, text) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.text = text
}

function deleteNote(id) {
  notes.value = notes.value.filter((n) => n.id !== id)
}

function onKeydown(e) {
  const editing = e.target instanceof HTMLElement && e.target.isContentEditable
  if (e.key === 'Escape') {
    toolActive.value = false
    return
  }
  if ((e.key === 'n' || e.key === 'N') && !editing && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault()
    toolActive.value = !toolActive.value
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div class="board">
    <aside class="toolbar">
      <button
        type="button"
        class="tool-btn"
        :class="{ active: toolActive }"
        title="Sticky note (N)"
        @click="toolActive = !toolActive"
      >
        <StickyNoteIcon :size="18" />
      </button>
    </aside>

    <div
      ref="canvasRef"
      class="canvas"
      :class="{ placing: toolActive }"
      @mousedown="onCanvasMouseDown"
      @click="onCanvasClick"
    >
      <svg class="grid" aria-hidden="true">
        <defs>
          <pattern
            id="dots"
            :x="patternOffset.x"
            :y="patternOffset.y"
            width="28"
            height="28"
            patternUnits="userSpaceOnUse"
          >
            <circle cx="1" cy="1" r="1" fill="rgba(255,255,255,0.08)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#dots)" />
      </svg>

      <div class="notes-layer" :style="notesLayerStyle">
        <StickyNoteCard
          v-for="note in notes"
          :key="note.id"
          :note="note"
          :scale="scale"
          @move="moveNote"
          @text-change="changeText"
          @delete="deleteNote"
        />
      </div>

      <div v-if="toolActive" class="hint">click anywhere to place a note · esc to cancel</div>

      <div v-if="notes.length === 0 && !toolActive" class="empty">
        <StickyNoteIcon :size="32" color="rgba(255,255,255,0.1)" />
        <p>select the sticky note tool to begin</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.board {
  display: flex;
  width: 100%;
  height: 100%;
  background: #16161d;
}

.toolbar {
  width: 52px;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.04);
  border-right: 1px solid rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding-top: 16px;
  gap: 8px;
  z-index: 10;
}

.tool-btn {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  border: 1.5px solid transparent;
  background: rgba(255, 255, 255, 0.06);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.45);
  transition: all 0.14s ease;
  padding: 0;
}

.tool-btn:hover {
  color: rgba(255, 255, 255, 0.75);
}

.tool-btn.active {
  border-color: rgba(253, 230, 138, 0.6);
  background: rgba(253, 230, 138, 0.15);
  color: #fde68a;
}

.canvas {
  flex: 1;
  position: relative;
  overflow: hidden;
  cursor: grab;
}

.canvas.placing {
  cursor: crosshair;
}

.grid {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.notes-layer {
  position: absolute;
  left: 0;
  top: 0;
  transform-origin: 0 0;
  width: 0;
  height: 0;
  overflow: visible;
}

.hint {
  position: absolute;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(253, 230, 138, 0.12);
  border: 1px solid rgba(253, 230, 138, 0.25);
  border-radius: 20px;
  padding: 6px 16px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  color: rgba(253, 230, 138, 0.7);
  pointer-events: none;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  pointer-events: none;
}

.empty p {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.18);
  margin: 0;
  letter-spacing: 0.04em;
}
</style>
