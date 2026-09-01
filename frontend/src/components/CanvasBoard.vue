<!--
  Repertoire prototype — infinite canvas for sticky-note brainstorming.
  Vue 3 SFC (script setup). No extra UI libraries: pan/place/select live here.
-->
<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue' // Vue 3 reactivity + lifecycle
import StickyNoteCard from './StickyNoteCard.vue'
import StickyNoteIcon from './StickyNoteIcon.vue'

/** Pen nib hotspot — used while the connector tool is on. */
const PEN_CURSOR = `url("data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#fde68a" stroke="#16161d" stroke-width="1.2" d="M3.2 21.2 5 17.8 18.6 4.2a1.5 1.5 0 0 1 2.1 2.1L7.1 20l-3.9 1.2z"/></svg>',
)}") 3 21, crosshair`

/** Offset a point away from a note edge so the curve leaves the mag point cleanly. */
function outward(side, dist) {
  if (side === 'top') return { x: 0, y: -dist }
  if (side === 'right') return { x: dist, y: 0 }
  if (side === 'bottom') return { x: 0, y: dist }
  return { x: -dist, y: 0 }
}

/** Smooth cubic from one mag point toward another (or the cursor). */
function connectorPath(x1, y1, side1, x2, y2, side2 = 'left') {
  const a = outward(side1, 48)
  const b = outward(side2, 48)
  return `M ${x1} ${y1} C ${x1 + a.x} ${y1 + a.y}, ${x2 + b.x} ${y2 + b.y}, ${x2} ${y2}`
}

/** Default fills, cycled as notes are placed. */
const NOTE_COLORS = [
  '#FDE68A',
  '#FCA5A5',
  '#86EFAC',
  '#93C5FD',
  '#F9A8D4',
  '#C4B5FD',
]

const notes = ref([])
const connections = ref([]) // { id, fromId, fromSide, toId, toSide } — one mag point can own many
const metrics = ref({}) // noteId -> { width, height } from ResizeObserver
const selectedId = ref(null)
const activeTool = ref(null) // null | 'sticky' | 'connect'
const draft = ref(null) // in-progress path: { fromId, fromSide, x, y }
const pan = ref({ x: 0, y: 0 })
const scale = 1
const canvasRef = ref(null)

let nextId = 1
let nextConnId = 1
const panRef = { x: 0, y: 0 }
let connectMove = null
let connectUp = null

const stickyActive = computed(() => activeTool.value === 'sticky') // place-note tool
const connectActive = computed(() => activeTool.value === 'connect') // mag-point connector tool

/** Moves the notes layer with the canvas pan. */
const notesLayerStyle = computed(() => ({
  transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${scale})`,
}))

/** Distance from note edge to mag-point center (must match StickyNoteCard --mag-outset). */
const MAG_OUTSET = 18

/** Mag-point position in canvas space (uses live size so wrapped text is included). */
function magPos(noteId, side) {
  const note = notes.value.find((n) => n.id === noteId)
  if (!note) return { x: 0, y: 0 }
  const size = metrics.value[noteId]
  const w = size?.width ?? note.width ?? 168
  const h = size?.height ?? note.height ?? 168
  if (side === 'top') return { x: note.x + w / 2, y: note.y - MAG_OUTSET }
  if (side === 'right') return { x: note.x + w + MAG_OUTSET, y: note.y + h / 2 }
  if (side === 'bottom') return { x: note.x + w / 2, y: note.y + h + MAG_OUTSET }
  return { x: note.x - MAG_OUTSET, y: note.y + h / 2 }
}

/** Map a mouse event to canvas coordinates (accounts for pan). */
function clientToCanvas(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  return {
    x: (e.clientX - rect.left - panRef.x) / scale,
    y: (e.clientY - rect.top - panRef.y) / scale,
  }
}

/** SVG `d` for every saved connector, recomputed when notes move/resize. */
const renderedConnections = computed(() =>
  connections.value.map((c) => {
    const from = magPos(c.fromId, c.fromSide)
    const to = magPos(c.toId, c.toSide)
    return { ...c, d: connectorPath(from.x, from.y, c.fromSide, to.x, to.y, c.toSide) }
  }),
)

/** Dashed path that follows the cursor while dragging a connector. */
const draftPath = computed(() => {
  if (!draft.value) return ''
  const from = magPos(draft.value.fromId, draft.value.fromSide)
  return connectorPath(from.x, from.y, draft.value.fromSide, draft.value.x, draft.value.y, 'left')
})

/** Arm / disarm a tool. Sticky and connect are mutually exclusive. */
function toggleTool(name) {
  activeTool.value = activeTool.value === name ? null : name
  if (activeTool.value !== 'connect') draft.value = null
}

/** Store live width/height from a note's ResizeObserver. */
function setMetrics(id, size) {
  metrics.value = { ...metrics.value, [id]: size }
}

/** Keeps the dot grid visually pinned while panning. */
const patternOffset = computed(() => ({
  x: pan.value.x % 28,
  y: pan.value.y % 28,
}))

/** Drop window listeners used while rubber-banding a connector. */
function clearConnectDrag() {
  if (connectMove) window.removeEventListener('mousemove', connectMove)
  if (connectUp) window.removeEventListener('mouseup', connectUp)
  connectMove = null
  connectUp = null
}

/** Mag-point mousedown: start a draft path. */
function onConnectStart(noteId, side) {
  if (!connectActive.value) return
  clearConnectDrag()
  const from = magPos(noteId, side)
  draft.value = { fromId: noteId, fromSide: side, x: from.x, y: from.y }

  connectMove = (ev) => {
    if (!draft.value) return
    const p = clientToCanvas(ev)
    draft.value = { ...draft.value, x: p.x, y: p.y }
  }
  connectUp = () => {
    clearConnectDrag()
    draft.value = null
  }
  window.addEventListener('mousemove', connectMove)
  window.addEventListener('mouseup', connectUp)
}

/** Mag-point mouseup: commit a path (same point is ignored). */
function onConnectEnd(noteId, side) {
  if (!draft.value) return
  const same = draft.value.fromId === noteId && draft.value.fromSide === side
  if (!same) {
    connections.value.push({
      id: nextConnId++,
      fromId: draft.value.fromId,
      fromSide: draft.value.fromSide,
      toId: noteId,
      toSide: side,
    })
  }
  clearConnectDrag()
  draft.value = null
}

/** Idle canvas: no selection, no connector tool, no in-progress path. Sticky place-tool stays on. */
function resetToIdle() {
  clearConnectDrag()
  draft.value = null
  if (activeTool.value === 'connect') activeTool.value = null
  selectedId.value = null
  const el = document.activeElement
  if (el instanceof HTMLElement && el.isContentEditable) el.blur()
}

/** Empty-canvas press: return to idle, then pan if the pointer moves. Sticky tool still places on click. */
function onCanvasMouseDown(e) {
  if (stickyActive.value) return
  resetToIdle()

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

/** Place one note at the click (canvas coords), then disarm the tool. */
function onCanvasClick(e) {
  if (!stickyActive.value || !canvasRef.value) return

  const rect = canvasRef.value.getBoundingClientRect()
  const cx = (e.clientX - rect.left - panRef.x) / scale
  const cy = (e.clientY - rect.top - panRef.y) / scale
  const id = nextId++

  notes.value.push({
    id,
    x: cx - 84,
    y: cy - 84,
    text: '',
    color: NOTE_COLORS[(id - 1) % NOTE_COLORS.length],
    width: 168,
    height: 168,
  })
  selectedId.value = id
  activeTool.value = null
}

/** Nudge a note after a drag. */
function moveNote(id, dx, dy) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.x += dx
  note.y += dy
}

/** Persist contenteditable text. */
function changeText(id, text) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.text = text
}

/** Persist fill from the color wheel. */
function changeColor(id, color) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.color = color
}

/** Apply corner-resize result (x/y + width/height). */
function resizeNote(id, patch) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.x = patch.x
  note.y = patch.y
  note.width = patch.width
  note.height = patch.height
}

/** Mark this note as the selected one (toolbar / resize handles). */
function selectNote(id) {
  selectedId.value = id
}

/** Esc: drop draft, then cancel tool / deselect. N: toggle sticky tool. */
function onKeydown(e) {
  const editing = e.target instanceof HTMLElement && e.target.isContentEditable
  if (e.key === 'Escape') {
    if (draft.value) {
      draft.value = null
      return
    }
    activeTool.value = null
    selectedId.value = null
    if (editing) e.target.blur()
    return
  }
  if ((e.key === 'n' || e.key === 'N') && !editing && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault()
    toggleTool('sticky')
  }
}

/** Register / drop keyboard shortcuts (N, Esc). */
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  clearConnectDrag()
})
</script>

<template>
  <div class="board">
    <div
      ref="canvasRef"
      class="canvas"
      :class="{ placing: stickyActive, connecting: connectActive }"
      :style="connectActive ? { cursor: PEN_CURSOR } : undefined"
      @mousedown="onCanvasMouseDown"
      @click="onCanvasClick"
    >
      <!-- SVG pattern = infinite dot grid; offset follows pan -->
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
        <svg class="connectors" overflow="visible" aria-hidden="true">
          <path
            v-for="line in renderedConnections"
            :key="line.id"
            :d="line.d"
            fill="none"
            stroke="rgba(232,228,220,0.85)"
            stroke-width="2.5"
            stroke-linecap="round"
          />
          <path
            v-if="draftPath"
            :d="draftPath"
            fill="none"
            stroke="#fde68a"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-dasharray="6 5"
          />
        </svg>
        <StickyNoteCard
          v-for="note in notes"
          :key="note.id"
          :note="note"
          :scale="scale"
          :selected="selectedId === note.id"
          :connect-mode="connectActive"
          :mag-outset="MAG_OUTSET"
          :drafting="!!draft"
          @move="moveNote"
          @text-change="changeText"
          @select="selectNote"
          @color-change="changeColor"
          @resize="resizeNote"
          @metrics="setMetrics"
          @connect-start="onConnectStart"
          @connect-end="onConnectEnd"
          @request-connect="toggleTool('connect')"
        />
      </div>

      <!-- FigJam-style compact bar: width hugs content, not full screen -->
      <div class="bottom-bar" @mousedown.stop @click.stop>
        <div class="tool-slot">
          <div v-if="stickyActive" class="hint">click anywhere to place a note · esc to cancel</div>
          <button
            type="button"
            class="tool-btn"
            :class="{ active: stickyActive }"
            title="Sticky note (N)"
            @click="toggleTool('sticky')"
          >
            <StickyNoteIcon :size="28" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.board {
  width: 100%;
  height: 100%;
  background: #16161d;
}

.canvas {
  width: 100%;
  height: 100%;
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

.connectors {
  position: absolute;
  left: 0;
  top: 0;
  overflow: visible;
  pointer-events: none;
  width: 1px;
  height: 1px;
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

.bottom-bar {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 2px;
  width: max-content;
  height: 56px;
  padding: 6px 8px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.45);
  z-index: 30;
}

.tool-slot {
  position: relative;
}

.tool-btn {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  border: 1.5px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(255, 255, 255, 0.7);
  transition: all 0.14s ease;
  padding: 0;
}

.tool-btn:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.tool-btn.active {
  border-color: rgba(253, 230, 138, 0.6);
  background: rgba(253, 230, 138, 0.18);
  color: #fde68a;
}

.hint {
  position: absolute;
  bottom: calc(100% + 10px);
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

