<!--
  Repertoire prototype — infinite canvas for sticky-note brainstorming.
  Vue 3 SFC (script setup). No extra UI libraries: pan/place/select live here.
-->
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue' // Vue 3 reactivity + lifecycle
import { fetchCanvas, logEvent, logout, saveCanvas } from '../api/session'
import { patternStatsFromLabels, persistedRationaleLabels, embedTexts, cosine, asVector, suggestLinks, suggestGroupWhy, narrativeSharedWhy, clusterSimilarLabels, isSimilarLabel, askContextQuestions } from '../api/rationale'
import { importContextImage, imageFilesFrom, IMAGE_ACCEPT, MAX_CONTEXT_IMAGES } from '../contextImages'
import StickyNoteCard from './StickyNoteCard.vue'
import StickyNoteIcon from './StickyNoteIcon.vue'
import GroupIcon from './GroupIcon.vue'
import SearchIcon from './SearchIcon.vue'
import ContextIcon from './ContextIcon.vue'
import RationaleModule from './RationaleModule.vue'
import RelationMarker from './RelationMarker.vue'
import { SEARCH_TEST_PACKS, SEARCH_TEST_TOPIC } from '../data/searchTestPacks'
import { createCanvasHistory } from '../history'

const props = defineProps({
  username: { type: String, default: '' },
})
const emit = defineEmits(['signed-out'])

/** Pen nib hotspot — used while the connector tool is on. */
const PEN_CURSOR = `url("data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#fde68a" stroke="#2c281f" stroke-width="1.2" d="M3.2 21.2 5 17.8 18.6 4.2a1.5 1.5 0 0 1 2.1 2.1L7.1 20l-3.9 1.2z"/></svg>',
)}") 3 21, crosshair`

function setDrawingPenCursor(on) {
  document.documentElement.classList.toggle('drawing-pen', Boolean(on))
}

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

/** Point at t along the same cubic used by connectorPath (t=0.5 = visual midpoint). */
function connectorPoint(x1, y1, side1, x2, y2, side2, t = 0.5) {
  const a = outward(side1, 48)
  const b = outward(side2, 48)
  const p0x = x1
  const p0y = y1
  const p1x = x1 + a.x
  const p1y = y1 + a.y
  const p2x = x2 + b.x
  const p2y = y2 + b.y
  const p3x = x2
  const p3y = y2
  const u = 1 - t
  return {
    x: u * u * u * p0x + 3 * u * u * t * p1x + 3 * u * t * t * p2x + t * t * t * p3x,
    y: u * u * u * p0y + 3 * u * u * t * p1y + 3 * u * t * t * p2y + t * t * t * p3y,
  }
}

function emptyRelation() {
  return {
    rationaleOpen: false,
    rationaleText: '',
    rationaleLabels: [],
    pinnedLabels: [],
    abandoned: false,
    abandonOpen: false,
    abandonText: '',
    abandonLabels: [],
    abandonPinned: [],
  }
}

function emptyNoteMeta() {
  return {
    pinnedLabels: [],
    rationaleOpen: true,
    rationaleText: '',
    rationaleLabels: [],
    abandoned: false,
    abandonOpen: false,
    abandonText: '',
    abandonLabels: [],
    abandonPinned: [],
  }
}

const ABANDON_PRESET = { text: 'i do not know yet', kind: '', source: 'user' }

function isNoteEmpty(note) {
  return !String(note?.text || '').trim()
}

function isAbandoned(note) {
  return Boolean(note?.abandoned)
}

function isNoteFrozen(note) {
  return Boolean(note?.abandoned || note?.abandonOpen)
}

function isRelationEmpty(conn) {
  if (String(conn?.rationaleText || '').trim()) return false
  const labels = [...(conn?.rationaleLabels || []), ...(conn?.pinnedLabels || [])]
  return !labels.some((item) => String(item?.text || '').trim())
}

function isRelationAbandoned(conn) {
  return Boolean(conn?.abandoned)
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
const groups = ref([]) // kept lasso groups: { id, memberIds, rationaleText, rationaleLabels, pinnedLabels }
const metrics = ref({}) // noteId -> { width, height } from ResizeObserver
const selectedId = ref(null)
const selectedConnId = ref(null)
const activeTool = ref(null) // null | 'sticky' | 'connect' | 'group'
const groupStroke = ref([]) // canvas-space points while the lasso is being drawn
const groupHint = ref('')
const draft = ref(null) // in-progress path: { fromId, fromSide, x, y }
const pan = ref({ x: 0, y: 0 })
const scale = ref(1)
const canvasRef = ref(null)
// Free-form brief the designer keeps for themselves; the AI only ever reads it as background.
const designContext = ref('')
const contextOpen = ref(false)
const contextRef = ref(null)
// Pictures pinned to the brief: sketches, screenshots, whatever frames the project.
const contextImages = ref([]) // [{ id, name, dataUrl, w, h }]
const contextImageError = ref('')
const contextFileRef = ref(null)
const contextDropActive = ref(false)
// What the AI asks back once the brief changes. Bubbles live in canvas space.
const contextQuestions = ref([]) // [{ id, text, kind, x, y }]
const contextQuestionsLoading = ref(false)
const contextQuestionsError = ref('')
const contextDismissed = ref([]) // texts waved away; the AI must not raise them again
let contextSeed = '' // brief fingerprint at the last generation, so reopening is free
let contextAbort = null
const searchOpen = ref(false)
const searchQuery = ref('')
const searchHits = ref([]) // [{ id, score, title, why }]
const searchPicked = ref([]) // note ids the designer kept
const searchLoading = ref(false)
const searchError = ref('')
const searchInputRef = ref(null)
const searchVecCache = new Map() // blob -> vector; one embed per unique note text
const isDev = import.meta.env.DEV
const searchTestHint = ref('')
const clearArmed = ref(false)
const canUndo = ref(false)
const canRedo = ref(false)
const historyEpoch = ref(0)
const history = createCanvasHistory({
  onChange(undoable, redoable) {
    canUndo.value = undoable
    canRedo.value = redoable
  },
})
const suggestSourceId = ref(null)
const suggestedLinks = ref([]) // [{ key, fromId, fromSide, toId, toSide, why, picked }]
const suggestLoading = ref(false)
const suggestError = ref('')
const suggestPark = ref(null) // { sourceId, visitors: [{ id, from, to }] }
const suggestWhyHover = ref(null)
const suggestWhyOpen = ref(null)
let searchTestPackCursor = 0
let suggestAbort = null

let nextId = 1
let nextConnId = 1
let nextGroupId = 1
const panRef = { x: 0, y: 0 }
let connectMove = null
let connectUp = null

const GRID = 28
const BOARD_PAD = 28
const SEARCH_THRESHOLD = 0.28
const SEARCH_MAX = 16
/** Same fills as AI / human echo labels on sticky-note tabs. */
const AI_ECHO = '#93c5fd'
const AI_ECHO_STRONG = '#60a5fa'
const USER_ECHO = '#fde68a'

const stickyActive = computed(() => activeTool.value === 'sticky') // place-note tool
const connectActive = computed(() => activeTool.value === 'connect') // mag-point connector tool
const groupActive = computed(() => activeTool.value === 'group') // freehand lasso group tool
const magHoverId = ref(null)

/** Moves the notes layer with the canvas pan. Scale stays at 100%. */
const notesLayerStyle = computed(() => ({
  transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${scale.value})`,
}))

/** Distance from note edge to mag-point center (must match StickyNoteCard --mag-outset). */
const MAG_OUTSET = 18
/** Show another note's mag points when the pen is this close to its box. */
const MAG_HOVER_PX = 20
/** Click/press within this radius of a mag counts as grabbing it (canvas space). */
const MAG_GRAB_PX = 26
/** Gap under the note so the rationale box sits below the bottom mag point. */
const RATIONALE_GAP = 28
/** Room to paste a brief and some material, without letting the saved board grow unbounded. */
const MAX_DESIGN_CONTEXT = 4000
/** Short briefs say too little to ask anything specific back. */
const MIN_CONTEXT_FOR_QUESTIONS = 12
/** Question bubbles sit on a rail down the left of whatever the designer is looking at. */
const QUESTION_RAIL_INSET = 44
const QUESTION_RAIL_TOP = 64
const QUESTION_RAIL_STEP = 96
/** Cap the dismiss memory so the prompt stays small on a long session. */
const MAX_DISMISSED_QUESTIONS = 12
/** Used until a dock is measured, so the dashed frame grows on the same click as V. */
const RATIONALE_DOCK_FALLBACK = 220
const dockHeights = ref({})
const dockObservers = new Map()

function setDockHeight(id, height) {
  const h = Math.max(0, Math.round(height || 0))
  if (dockHeights.value[id] === h) return
  dockHeights.value = { ...dockHeights.value, [id]: h }
}

function bindDockRef(id, el) {
  const prev = dockObservers.get(id)
  if (prev?.el === el) return
  prev?.ro.disconnect()
  if (!el) {
    dockObservers.delete(id)
    if (dockHeights.value[id]) {
      const next = { ...dockHeights.value }
      delete next[id]
      dockHeights.value = next
    }
    return
  }
  const ro = new ResizeObserver(() => {
    setDockHeight(id, el.offsetHeight)
  })
  ro.observe(el)
  dockObservers.set(id, { el, ro })
  setDockHeight(id, el.offsetHeight)
}

function noteRationaleExtent(note) {
  if (!note || isAbandoned(note) || (!note.rationaleOpen && !note.abandonOpen)) return 0
  const measured = Number(dockHeights.value[note.id]) || 0
  return RATIONALE_GAP + (measured > 0 ? measured : RATIONALE_DOCK_FALLBACK)
}

/** Live note box (ResizeObserver), falling back to the stored size. */
function noteSize(note) {
  const size = metrics.value[note.id]
  return {
    width: size?.width ?? note.width ?? 168,
    height: size?.height ?? note.height ?? 168,
  }
}

function liveNotePos(note) {
  if (!note) return { x: 0, y: 0 }
  const gather = patternGather.value
  const visitor = gather?.visitors.find((item) => item.id === note.id)
  if (visitor) {
    if (gather.origin === 'suggest' && patternDragId.value !== note.id) {
      const memberNotes = gather.visitors
        .map((item) => notes.value.find((entry) => entry.id === item.id))
        .filter(Boolean)
      const slots = traySlotsForNotes(memberNotes)
      const index = gather.visitors.findIndex((item) => item.id === note.id)
      if (index >= 0 && slots[index]) return { x: slots[index].x, y: slots[index].y }
    }
    return { x: visitor.to.x, y: visitor.to.y }
  }
  const parked = suggestPark.value?.visitors.find((item) => item.id === note.id)
  if (parked) return { x: parked.to.x, y: parked.to.y }
  return { x: note.x, y: note.y }
}

/** Rationale panel sits under the note and matches its current width. */
function rationaleStyle(note) {
  const { width, height } = noteSize(note)
  const pos = liveNotePos(note)
  const inGroup = Boolean(patternGather.value && patternHitIds.value.has(note.id))
  return {
    left: `${pos.x}px`,
    top: `${pos.y + height + RATIONALE_GAP}px`,
    width: `${width}px`,
    zIndex: suggestParkedIds.value.has(note.id)
      ? selectedId.value === note.id ? 39 : 35
      : inGroup
        ? (selectedId.value === note.id ? 21 : 18)
        : selectedId.value === note.id ? 19 : 1,
  }
}
/** Mag-point position in canvas space (uses live size so wrapped text is included). */
function magPos(noteId, side) {
  const note = notes.value.find((n) => n.id === noteId)
  if (!note) return { x: 0, y: 0 }
  const pos = liveNotePos(note)
  const size = metrics.value[noteId]
  const w = size?.width ?? note.width ?? 168
  const h = size?.height ?? note.height ?? 168
  if (side === 'top') return { x: pos.x + w / 2, y: pos.y - MAG_OUTSET }
  if (side === 'right') return { x: pos.x + w + MAG_OUTSET, y: pos.y + h / 2 }
  if (side === 'bottom') return { x: pos.x + w / 2, y: pos.y + h + MAG_OUTSET }
  return { x: pos.x - MAG_OUTSET, y: pos.y + h / 2 }
}

/** Map a mouse event to canvas coordinates (accounts for pan). */
function clientToCanvas(e) {
  const rect = canvasRef.value.getBoundingClientRect()
  return {
    x: (e.clientX - rect.left - panRef.x) / scale.value,
    y: (e.clientY - rect.top - panRef.y) / scale.value,
  }
}

/** SVG `d` plus midpoint for every saved connector, recomputed when notes move/resize. */
const renderedConnections = computed(() =>
  connections.value.map((c) => {
    const from = magPos(c.fromId, c.fromSide)
    const to = magPos(c.toId, c.toSide)
    const fromNote = notes.value.find((n) => n.id === c.fromId)
    const toNote = notes.value.find((n) => n.id === c.toId)
    return {
      ...c,
      d: connectorPath(from.x, from.y, c.fromSide, to.x, to.y, c.toSide),
      mid: connectorPoint(from.x, from.y, c.fromSide, to.x, to.y, c.toSide, 0.5),
      faded: isAbandoned(fromNote) || isAbandoned(toNote) || isRelationAbandoned(c),
      abandoned: isRelationAbandoned(c),
    }
  }),
)

/** Dashed path that follows the cursor while dragging a connector. */
const draftPath = computed(() => {
  if (!draft.value) return ''
  const from = magPos(draft.value.fromId, draft.value.fromSide)
  return connectorPath(from.x, from.y, draft.value.fromSide, draft.value.x, draft.value.y, 'left')
})

const renderedSuggestions = computed(() =>
  suggestedLinks.value
    .filter((link) => !alreadyLinked(link.fromId, link.toId))
    .map((link) => {
      const from = magPos(link.fromId, link.fromSide)
      const to = magPos(link.toId, link.toSide)
      return {
        ...link,
        d: connectorPath(from.x, from.y, link.fromSide, to.x, to.y, link.toSide),
        mid: connectorPoint(from.x, from.y, link.fromSide, to.x, to.y, link.toSide, 0.5),
      }
    }),
)

const suggestPickedCount = computed(() => suggestedLinks.value.filter((link) => link.picked).length)
const suggestTargetIds = computed(() => new Set(suggestedLinks.value.map((link) => link.toId)))
const suggestParkedIds = computed(() => new Set((suggestPark.value?.visitors || []).map((item) => item.id)))
const suggestPulledIds = computed(() => {
  const park = suggestPark.value
  if (!park) return new Set()
  return new Set(park.visitors.filter((item) => item.id !== park.sourceId).map((item) => item.id))
})
const suggestDimActive = computed(() => suggestPulledIds.value.size > 0)

const searchHitIds = computed(() => new Set(searchHits.value.map((item) => item.id)))
const searchPickedIds = computed(() => new Set(searchPicked.value))
const searchDimActive = computed(() => searchHits.value.length > 0)

/** Arm / disarm a tool. Sticky, group, and connect are mutually exclusive. */
function toggleTool(name) {
  if ((name === 'sticky' || name === 'group') && searchOpen.value) closeSearch()
  const next = activeTool.value === name ? null : name
  if (activeTool.value === 'connect' && next !== 'connect') {
    clearSuggestions()
    draft.value = null
  }
  if (activeTool.value === 'group' && next !== 'group') {
    clearGroupStroke()
    if (patternGather.value?.origin === 'suggest') endPatternGather(false)
  }
  if (next === 'group') {
    if (patternGather.value) endPatternGather(false)
    groupHint.value = ''
    activeTool.value = 'group'
    if (activeTool.value !== 'connect') draft.value = null
    nextTick(() => {
      if (focusedSuggestSets.value.length) showSuggestSet(0, { morph: false })
    })
    return
  }
  activeTool.value = next
  if (activeTool.value !== 'connect') draft.value = null
}

function clearGroupStroke() {
  groupStroke.value = []
  window.removeEventListener('pointermove', onGroupPointerMove)
  window.removeEventListener('pointerup', onGroupPointerUp)
  window.removeEventListener('pointercancel', onGroupPointerUp)
}

function noteSearchBlob(note) {
  const kept = persistedRationaleLabels(note.rationaleLabels, note.pinnedLabels)
    .map((item) => String(item?.text || '').trim())
    .filter(Boolean)
  return [note.text, note.rationaleText, ...kept]
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .join('\n')
}

function lexicalScore(blob, query) {
  const terms = String(query || '')
    .toLowerCase()
    .split(/\s+/)
    .filter((term) => term.length > 1)
  if (!terms.length) return 0
  const hay = String(blob || '').toLowerCase()
  let hits = 0
  for (const term of terms) if (hay.includes(term)) hits += 1
  return hits / terms.length
}

function noteSearchWhy(note) {
  const pin = (note.pinnedLabels || []).map((item) => String(item?.text || '').trim()).find(Boolean)
  if (pin) return pin
  const reflection = String(note.rationaleText || '').trim()
  if (reflection) return reflection.length > 72 ? `${reflection.slice(0, 72)}…` : reflection
  const idea = String(note.text || '').trim()
  if (idea) return idea.length > 72 ? `${idea.slice(0, 72)}…` : idea
  return 'empty idea'
}

function closeSearch() {
  searchOpen.value = false
  searchQuery.value = ''
  searchHits.value = []
  searchPicked.value = []
  searchError.value = ''
  searchLoading.value = false
  searchTestHint.value = ''
}

function clearSuggestions() {
  suggestAbort?.abort()
  suggestAbort = null
  suggestSourceId.value = null
  suggestedLinks.value = []
  suggestLoading.value = false
  suggestError.value = ''
  suggestPark.value = null
  suggestWhyHover.value = null
  suggestWhyOpen.value = null
}

/** Drop a single AI suggestion after that pair is linked by hand (or already linked). */
function pruneSuggestedLinkBetween(a, b) {
  const before = suggestedLinks.value.length
  suggestedLinks.value = suggestedLinks.value.filter(
    (link) =>
      !((link.fromId === a && link.toId === b) || (link.fromId === b && link.toId === a)),
  )
  if (suggestedLinks.value.length === before) return
  // If this target was pulled in for that suggestion, send it home unless still suggested.
  const stillSuggested = new Set(suggestedLinks.value.map((link) => link.toId))
  for (const id of [a, b]) {
    if (id !== suggestSourceId.value && !stillSuggested.has(id)) releaseSuggestedTarget(id)
  }
  if (!suggestedLinks.value.length && !suggestLoading.value && !suggestError.value) {
    // All AI dashes resolved; leave the board quiet but keep selection.
    suggestSourceId.value = null
    suggestPark.value = null
    suggestWhyHover.value = null
    suggestWhyOpen.value = null
    if (activeTool.value === 'connect') activeTool.value = null
  }
}

function noteSuggestPayload(note) {
  return {
    id: String(note.id),
    text: String(note.text || '').trim().slice(0, 2000),
    labels: (note.pinnedLabels || [])
      .map((item) => String(item?.text || '').trim())
      .filter(Boolean)
      .slice(0, 3),
  }
}

function alreadyLinked(a, b) {
  return connections.value.some(
    (c) =>
      !isRelationAbandoned(c) &&
      ((c.fromId === a && c.toId === b) || (c.fromId === b && c.toId === a)),
  )
}

/** Pick mag-point sides so a suggested curve leaves toward the other note. */
function pickConnectSides(fromNote, toNote) {
  const fromPos = liveNotePos(fromNote)
  const toPos = liveNotePos(toNote)
  const from = noteSize(fromNote)
  const to = noteSize(toNote)
  const dx = toPos.x + to.width / 2 - (fromPos.x + from.width / 2)
  const dy = toPos.y + to.height / 2 - (fromPos.y + from.height / 2)
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? ['right', 'left'] : ['left', 'right']
  }
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
}

const SUGGEST_PULL_GAP = 64
const PULL_CLEAR_GAP = 24

function liveParkPos(noteId) {
  const parked = suggestPark.value?.visitors.find((item) => item.id === noteId)
  if (parked) return { x: parked.to.x, y: parked.to.y }
  const note = notes.value.find((item) => item.id === noteId)
  return note ? { x: note.x, y: note.y } : { x: 0, y: 0 }
}

/** Boxes of every other sticky (using parked pull positions when set). */
function occupiedBoxesForPull(ignoreId) {
  const boxes = []
  for (const note of notes.value) {
    if (note.id === ignoreId || isAbandoned(note)) continue
    boxes.push(noteVisualBox(note, liveParkPos(note.id)))
  }
  return boxes
}

/** Keep the preferred pull spot when free; otherwise spiral until nothing is covered. */
function clearPullSlot(note, preferred, ignoreId) {
  const gap = PULL_CLEAR_GAP
  const occupied = occupiedBoxesForPull(ignoreId)
  const tryPos = (x, y) => {
    const box = noteVisualBox(note, { x, y })
    if (occupied.some((other) => boxesOverlap(box, other, gap))) return null
    return { x, y }
  }
  const first = tryPos(preferred.x, preferred.y)
  if (first) return first

  for (let ring = 1; ring <= 28; ring++) {
    const radius = ring * 28
    const steps = Math.max(8, ring * 6)
    for (let i = 0; i < steps; i++) {
      const angle = (i / steps) * Math.PI * 2
      const hit = tryPos(
        preferred.x + Math.cos(angle) * radius,
        preferred.y + Math.sin(angle) * radius,
      )
      if (hit) return hit
    }
  }

  const right = occupied.reduce((max, box) => Math.max(max, box.x + box.w), preferred.x)
  return { x: right + gap + 8, y: preferred.y }
}

function pulledSlotForTarget(source, target, from) {
  const srcSize = noteSize(source)
  const cx = source.x + srcSize.width / 2
  const cy = source.y + srcSize.height / 2
  const size = noteSize(target)
  const origin = from || { x: target.x, y: target.y }
  const tx = origin.x + size.width / 2
  const ty = origin.y + size.height / 2
  const dist = Math.hypot(tx - cx, ty - cy) || 1
  const srcReach = Math.max(srcSize.width + tabClearX(source), srcSize.height + tabClearY(source)) / 2
  const reach = Math.max(size.width + tabClearX(target), size.height + tabClearY(target)) / 2
  const desired = srcReach + reach + SUGGEST_PULL_GAP
  const preferred = dist <= desired
    ? { x: origin.x, y: origin.y }
    : {
        x: cx + Math.cos(Math.atan2(ty - cy, tx - cx)) * desired - size.width / 2,
        y: cy + Math.sin(Math.atan2(ty - cy, tx - cx)) * desired - size.height / 2,
      }
  return clearPullSlot(target, preferred, target.id)
}

function ensureSuggestPark(source) {
  if (suggestPark.value?.sourceId === source.id) return
  suggestPark.value = {
    sourceId: source.id,
    visitors: [{ id: source.id, from: { x: source.x, y: source.y }, to: { x: source.x, y: source.y } }],
  }
}

function pullSuggestedTarget(targetId) {
  const source = notes.value.find((note) => note.id === suggestSourceId.value)
  const target = notes.value.find((note) => note.id === targetId)
  if (!source || !target) return
  ensureSuggestPark(source)
  const existing = suggestPark.value.visitors.find((item) => item.id === targetId)
  const from = existing?.from || { x: target.x, y: target.y }
  const to = pulledSlotForTarget(source, target, from)
  if (existing) existing.to = to
  else suggestPark.value.visitors.push({ id: targetId, from, to })
  refreshSuggestedSides(source)
  panToSuggestPark()
}

function releaseSuggestedTarget(targetId) {
  const park = suggestPark.value
  if (!park) return
  park.visitors = park.visitors.filter((item) => item.id !== targetId)
  if (park.visitors.every((item) => item.id === park.sourceId)) suggestPark.value = null
  const source = notes.value.find((note) => note.id === suggestSourceId.value)
  if (source) refreshSuggestedSides(source)
}

function panToSuggestPark() {
  const park = suggestPark.value
  if (!park?.visitors.length) return
  const slots = park.visitors.map((visitor) => {
    const note = notes.value.find((item) => item.id === visitor.id)
    return note
      ? slotForNote(note, visitor.to.x, visitor.to.y)
      : { x: visitor.to.x, y: visitor.to.y, w: 168, h: 168, vx: visitor.to.x, vy: visitor.to.y, vw: 168, vh: 168 }
  })
  panToSlots(slots)
}

function refreshSuggestedSides(source) {
  suggestedLinks.value = suggestedLinks.value.map((link) => {
    const target = notes.value.find((note) => note.id === link.toId)
    if (!target) return link
    const [fromSide, toSide] = pickConnectSides(source, target)
    return { ...link, fromSide, toSide }
  })
}

function commitSuggestPark(keepIds) {
  const park = suggestPark.value
  if (!park) return
  const keep = new Set(keepIds)
  keep.add(park.sourceId)
  for (const visitor of park.visitors) {
    if (!keep.has(visitor.id)) continue
    const note = notes.value.find((item) => item.id === visitor.id)
    if (!note) continue
    note.x = visitor.to.x
    note.y = visitor.to.y
  }
  suggestPark.value = null
}

/** One toolbar action: mag-point drawing plus AI dashed suggestions from this idea. */
function onRelationTool(id) {
  if (connectActive.value && suggestSourceId.value === id) {
    activeTool.value = null
    clearSuggestions()
    draft.value = null
    return
  }
  if (activeTool.value === 'sticky') activeTool.value = null
  activeTool.value = 'connect'
  selectedId.value = id
  selectedConnId.value = null
  suggestRelations(id)
}

async function suggestRelations(id) {
  const source = notes.value.find((n) => n.id === id)
  if (!source || isNoteFrozen(source)) return
  const payload = noteSuggestPayload(source)
  if (!payload.text && !payload.labels.length) {
    suggestSourceId.value = id
    suggestedLinks.value = []
    suggestError.value = 'write an idea or pin a label first'
    return
  }
  const candidates = notes.value.filter(
    (note) =>
      note.id !== id &&
      !isAbandoned(note) &&
      !note.abandonOpen &&
      !alreadyLinked(id, note.id) &&
      (String(note.text || '').trim() || (note.pinnedLabels || []).some((item) => String(item?.text || '').trim())),
  )
  if (!candidates.length) {
    suggestSourceId.value = id
    suggestedLinks.value = []
    suggestError.value = 'no other ideas to link yet'
    return
  }

  suggestAbort?.abort()
  suggestAbort = new AbortController()
  const signal = suggestAbort.signal
  suggestSourceId.value = id
  suggestedLinks.value = []
  suggestError.value = ''
  suggestLoading.value = true
  selectedId.value = id
  selectedConnId.value = null
  logEvent('suggest_relations', { noteId: id, candidates: candidates.length }).catch(() => {})

  try {
    const result = await suggestLinks(
      payload,
      candidates.slice(0, 40).map(noteSuggestPayload),
      { context: designContext.value, signal },
    )
    if (signal.aborted) return
    const byId = new Map(candidates.map((note) => [String(note.id), note]))
    suggestedLinks.value = result.links
      .map((link) => {
        const target = byId.get(String(link.id))
        if (!target) return null
        const [fromSide, toSide] = pickConnectSides(source, target)
        return {
          key: `${id}-${target.id}`,
          fromId: id,
          fromSide,
          toId: target.id,
          toSide,
          why: link.why || 'related ideas',
          picked: false,
        }
      })
      .filter(Boolean)
    if (!suggestedLinks.value.length) {
      suggestError.value = 'no close relations from this idea and its labels'
    }
  } catch (err) {
    if (signal.aborted || err?.code === 'cancelled') return
    suggestError.value = err?.message || "couldn't suggest relations"
  } finally {
    if (!signal.aborted) suggestLoading.value = false
  }
}

/** Click the blue line or the dashed frame: select the link AND pull the idea closer. */
function activateSuggestedLink(key) {
  // Never steal a hand-drawn yellow link in progress.
  if (draft.value) return
  const link = suggestedLinks.value.find((item) => item.key === key)
  if (!link) return
  const picked = !link.picked
  suggestedLinks.value = suggestedLinks.value.map((item) =>
    item.key === key ? { ...item, picked } : item,
  )
  if (picked) pullSuggestedTarget(link.toId)
  else releaseSuggestedTarget(link.toId)
}

/** Click the checkbox only: toggle the check mark, no position change. */
function toggleSuggestedLink(key) {
  suggestedLinks.value = suggestedLinks.value.map((item) =>
    item.key === key ? { ...item, picked: !item.picked } : item,
  )
}

function onSuggestFrameClick(key, event) {
  if (event.target.closest('.suggest-check')) return // handled by toggleSuggestedLink
  activateSuggestedLink(key)
  suggestWhyOpen.value = suggestWhyOpen.value === key ? null : key
}

function keepSuggestedLinks() {
  const source = notes.value.find((n) => n.id === suggestSourceId.value)
  const picked = suggestedLinks.value.filter((link) => link.picked)
  if (!source || !picked.length) return
  const additions = []
  for (const link of picked) {
    const target = notes.value.find((n) => n.id === link.toId)
    if (!target || isNoteFrozen(target) || alreadyLinked(link.fromId, link.toId)) continue
    additions.push({
      id: nextConnId++,
      fromId: link.fromId,
      fromSide: link.fromSide,
      toId: link.toId,
      toSide: link.toSide,
      ...emptyRelation(),
      rationaleText: link.why || '',
    })
  }
  if (additions.length) {
    connections.value = [...connections.value, ...additions]
    logEvent('suggest_relations_kept', {
      noteId: source.id,
      kept: additions.map((item) => item.toId),
    }).catch(() => {})
    commitSuggestPark(additions.map((item) => item.toId))
  }
  clearSuggestions()
}

function clearBoard() {
  clearArmed.value = false
  closeSearch()
  clearSuggestions()
  patternGather.value = null
  clearConnectDrag()
  notes.value = []
  connections.value = []
  groups.value = []
  metrics.value = {}
  selectedId.value = null
  selectedConnId.value = null
  activeTool.value = null
  draft.value = null
  searchVecCache.clear()
  searchTestHint.value = ''
  nextId = 1
  nextConnId = 1
  nextGroupId = 1
  panRef.x = 0
  panRef.y = 0
  pan.value = { x: 0, y: 0 }
  scale.value = 1
  clearTimeout(saveTimer)
  saveCanvas(canvasPayload()).catch(() => {})
  logEvent('canvas_cleared', {}).catch(() => {})
}

function armClearBoard() {
  if (clearArmed.value) {
    clearBoard()
    return
  }
  clearArmed.value = true
}

/** Pool labels for sample ideas — unique ids so they cannot collide with later AI chips. */
let sampleLabelSeq = Date.now()
function samplePoolLabels(pins) {
  return (Array.isArray(pins) ? pins : [])
    .map((text) => String(text || '').trim())
    .filter(Boolean)
    .map((text) => ({
      id: ++sampleLabelSeq,
      text,
      kind: '',
      source: 'user',
      rid: newRid(),
    }))
}

function noteBox(note) {
  return {
    x: note.x,
    y: note.y,
    w: note.width ?? 168,
    h: note.height ?? 168,
  }
}

function boxesOverlap(a, b, gap) {
  return !(a.x + a.w + gap < b.x || b.x + b.w + gap < a.x || a.y + a.h + gap < b.y || b.y + b.h + gap < a.y)
}

/** New sample cluster sits beside existing notes, never on a tidy grid. */
function sampleClusterOrigin() {
  const s = scale.value || 1
  if (!notes.value.length) {
    return {
      x: (BOARD_PAD - panRef.x) / s + 36 + Math.random() * 90,
      y: (BOARD_PAD - panRef.y) / s + 48 + Math.random() * 70,
    }
  }
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const note of notes.value) {
    const box = noteBox(note)
    minX = Math.min(minX, box.x)
    minY = Math.min(minY, box.y)
    maxX = Math.max(maxX, box.x + box.w)
    maxY = Math.max(maxY, box.y + box.h)
  }
  if (Math.random() < 0.55) {
    return { x: maxX + 80 + Math.random() * 90, y: minY + Math.random() * 140 }
  }
  return { x: minX + Math.random() * 160, y: maxY + 80 + Math.random() * 90 }
}

function placeSampleNotes(count) {
  const gap = 56
  const occupied = notes.value.map(noteBox)
  const origin = sampleClusterOrigin()
  const gold = Math.PI * (3 - Math.sqrt(5))
  const slots = []
  for (let i = 0; i < count; i++) {
    const w = 176 + Math.round(Math.random() * 40)
    const h = 150 + Math.round(Math.random() * 36)
    let slot = null
    for (let attempt = 0; attempt < 70; attempt++) {
      const k = i + attempt * 0.19
      const radius = 54 + Math.sqrt(k + 1) * (88 + Math.random() * 46)
      const angle = k * gold + (Math.random() - 0.5) * 0.9
      const next = {
        x: origin.x + Math.cos(angle) * radius + (Math.random() - 0.5) * 56,
        y: origin.y + Math.sin(angle) * radius * 0.7 + (Math.random() - 0.5) * 48,
        w,
        h,
      }
      const hit = occupied.concat(slots).some((box) => boxesOverlap(next, box, gap))
      if (!hit) {
        slot = next
        break
      }
    }
    if (!slot) {
      const right = occupied.concat(slots).reduce((max, box) => Math.max(max, box.x + box.w), origin.x)
      slot = { x: right + gap + Math.random() * 24, y: origin.y + (Math.random() - 0.5) * 110, w, h }
    }
    slots.push(slot)
  }
  return slots
}

/** DEV only: append the next sample pack. Loose scatter, no overlap with what is already there. */
function addSearchTestPack() {
  if (!isDev || !SEARCH_TEST_PACKS.length) return
  const pack = SEARCH_TEST_PACKS[searchTestPackCursor % SEARCH_TEST_PACKS.length]
  searchTestPackCursor += 1
  const slots = placeSampleNotes(pack.ideas.length)
  for (const [i, idea] of pack.ideas.entries()) {
    const id = nextId++
    const slot = slots[i]
    const pool = samplePoolLabels(idea.pins)
    const pinned = idea.pinTop ? pool.slice(0, Math.min(2, pool.length)).map((item) => ({ ...item })) : []
    notes.value.push(
      hydrateRids({
        id,
        x: slot.x,
        y: slot.y,
        text: idea.text,
        color: NOTE_COLORS[(id + Math.floor(Math.random() * NOTE_COLORS.length)) % NOTE_COLORS.length],
        width: slot.w,
        height: slot.h,
        ...emptyNoteMeta(),
        rationaleOpen: false,
        rationaleText: idea.rationale || '',
        rationaleLabels: pool,
        pinnedLabels: pinned,
      }),
    )
  }
  searchTestHint.value = `test samples · ${SEARCH_TEST_TOPIC} · added “${pack.id}” (${pack.ideas.length})`
  logEvent('search_test_pack', { pack: pack.id, topic: SEARCH_TEST_TOPIC, added: pack.ideas.length, total: notes.value.length }).catch(() => {})
}

function toggleSearch() {
  if (searchOpen.value) {
    closeSearch()
    return
  }
  activeTool.value = null
  draft.value = null
  clearGroupStroke()
  groupHint.value = ''
  searchOpen.value = true
  searchError.value = ''
  nextTick(() => searchInputRef.value?.focus())
}

function toggleSearchPick(id) {
  const has = searchPicked.value.includes(id)
  searchPicked.value = has ? searchPicked.value.filter((item) => item !== id) : [...searchPicked.value, id]
}

function clearSearchHits() {
  searchHits.value = []
  searchPicked.value = []
  searchError.value = ''
}

function jumpToSearchHit(id) {
  selectNote(id)
  const note = notes.value.find((item) => item.id === id)
  if (!note || !canvasRef.value) return
  const { w, h } = viewSize()
  const { width, height } = noteSize(note)
  const s = scale.value
  panRef.x = w / 2 - (note.x + width / 2) * s
  panRef.y = h / 2 - (note.y + height / 2) * s
  applyPan()
}

async function runBoardSearch() {
  const query = searchQuery.value.trim()
  if (!query || searchLoading.value) return
  const live = notes.value.filter((note) => !isAbandoned(note) && !note.abandonOpen)
  if (!live.length) {
    searchHits.value = []
    searchError.value = 'Place an idea first, then look it up.'
    return
  }
  searchLoading.value = true
  searchError.value = ''
  searchPicked.value = []
  try {
    const blobs = live.map((note) => ({ note, blob: noteSearchBlob(note) }))
    const missing = [...new Set(blobs.map((item) => item.blob).filter((blob) => blob && !asVector(searchVecCache.get(blob))))]
    const texts = missing.length ? [query, ...missing] : [query]
    let byText = new Map()
    try {
      byText = await embedTexts(texts)
      for (const [text, vec] of byText) searchVecCache.set(text, vec)
    } catch {
      byText = new Map()
    }
    const qVec = asVector(byText.get(query)) || asVector(searchVecCache.get(query))
    const ranked = []
    for (const { note, blob } of blobs) {
      const vec = asVector(searchVecCache.get(blob))
      const semantic = qVec && vec ? cosine(qVec, vec) : 0
      const lexical = lexicalScore(blob, query)
      const score = semantic > 0 ? semantic * 0.85 + lexical * 0.15 : lexical
      if (score < SEARCH_THRESHOLD && lexical < 0.34) continue
      ranked.push({
        id: note.id,
        score,
        title: String(note.text || '').trim() || 'untitled idea',
        why: noteSearchWhy(note),
      })
    }
    ranked.sort((a, b) => b.score - a.score)
    searchHits.value = ranked.slice(0, SEARCH_MAX)
    if (!searchHits.value.length) searchError.value = 'Nothing on the board is close to that yet.'
    logEvent('board_search', { q: query, hits: searchHits.value.map((item) => item.id) }).catch(() => {})
  } catch (err) {
    searchHits.value = []
    searchError.value = err?.message || "Couldn't look that up."
  } finally {
    searchLoading.value = false
  }
}

/** Store live width/height from a note's ResizeObserver. */
function setMetrics(id, size) {
  metrics.value = { ...metrics.value, [id]: size }
}

/** Dot grid follows pan so it stays locked to the canvas. */
const patternOffset = computed(() => {
  const size = GRID * scale.value
  return {
    x: pan.value.x % size,
    y: pan.value.y % size,
    size,
    r: Math.max(0.55, scale.value),
  }
})

function viewSize() {
  const el = canvasRef.value
  if (!el) return { w: window.innerWidth, h: window.innerHeight }
  const rect = el.getBoundingClientRect()
  return { w: rect.width, h: rect.height }
}

function applyPan() {
  pan.value = { x: panRef.x, y: panRef.y }
}

function onWindowResize() {
  applyPan()
}

/** Trackpad / wheel pans the board. Pinch-zoom is ignored so scale stays at 100%. */
function onWheel(e) {
  const t = e.target
  if (
    t instanceof HTMLElement &&
    (isTypingTarget(t) || t.closest('.rationale-dock textarea') || t.closest('.edge-tray') || t.closest('.relation-marker'))
  ) {
    return
  }
  e.preventDefault()
  panRef.x -= e.deltaX
  panRef.y -= e.deltaY
  applyPan()
}

function distanceToNoteEdge(px, py, note) {
  const pos = liveNotePos(note)
  const { width, height } = noteSize(note)
  const left = pos.x
  const top = pos.y
  const right = pos.x + width
  const bottom = pos.y + height
  const dx = Math.max(left - px, 0, px - right)
  const dy = Math.max(top - py, 0, py - bottom)
  return Math.hypot(dx, dy)
}

/** Closest other idea whose edge is within MAG_HOVER_PX of the pen. */
function nearestConnectNote(px, py, fromId) {
  let bestId = null
  let bestD = Infinity
  for (const note of notes.value) {
    if (note.id === fromId || isNoteFrozen(note) || isAbandoned(note)) continue
    const d = distanceToNoteEdge(px, py, note)
    if (d < bestD) {
      bestD = d
      bestId = note.id
    }
  }
  return bestD <= MAG_HOVER_PX ? bestId : null
}

function noteShowsMag(note) {
  if (isNoteFrozen(note) || isAbandoned(note)) return false
  // Selected sticky always shows mag points (no need to arm the suggest icon first).
  if (selectedId.value === note.id) return true
  // While drawing a yellow link, only reveal mag points on the sticky the pen is near.
  return Boolean(draft.value) && magHoverId.value === note.id
}

function noteSuggestArmed(note) {
  return connectActive.value && selectedId.value === note.id
}

/** Nearest visible mag point under/near the pointer — used when the click lands in the gap outside the note. */
function nearestMagGrab(px, py) {
  let best = null
  let bestD = Infinity
  for (const note of notes.value) {
    if (!noteShowsMag(note)) continue
    for (const side of ['top', 'right', 'bottom', 'left']) {
      const point = magPos(note.id, side)
      const d = Math.hypot(point.x - px, point.y - py)
      if (d < bestD) {
        bestD = d
        best = { noteId: note.id, side }
      }
    }
  }
  return bestD <= MAG_GRAB_PX ? best : null
}

/** Drop window listeners used while rubber-banding a connector. */
function clearConnectDrag() {
  if (connectMove) window.removeEventListener('mousemove', connectMove)
  if (connectUp) window.removeEventListener('mouseup', connectUp)
  connectMove = null
  connectUp = null
  magHoverId.value = null
  setDrawingPenCursor(false)
}

/** Mag-point mousedown: start a draft path. */
function onConnectStart(noteId, side) {
  const note = notes.value.find((n) => n.id === noteId)
  if (isNoteFrozen(note)) return
  // Allow drawing from a selected sticky, or while the suggest/connect tool is armed.
  if (!connectActive.value && selectedId.value !== noteId && draft.value?.fromId !== noteId) return
  clearConnectDrag()
  const from = magPos(noteId, side)
  draft.value = { fromId: noteId, fromSide: side, x: from.x, y: from.y }
  // Hand → pen immediately on grab (override mag grab cursor right away).
  setDrawingPenCursor(true)

  connectMove = (ev) => {
    if (!draft.value) return
    const p = clientToCanvas(ev)
    draft.value = { ...draft.value, x: p.x, y: p.y }
    magHoverId.value = nearestConnectNote(p.x, p.y, draft.value.fromId)
  }
  connectUp = () => {
    clearConnectDrag()
    draft.value = null
  }
  window.addEventListener('mousemove', connectMove)
  window.addEventListener('mouseup', connectUp)
}

/** Mag-point mouseup: commit a path. Same sticky (any mag point) is ignored. */
function onConnectEnd(noteId, side) {
  if (!draft.value) return
  const fromId = draft.value.fromId
  const fromSide = draft.value.fromSide
  const fromNote = notes.value.find((n) => n.id === fromId)
  const toNote = notes.value.find((n) => n.id === noteId)
  const sameNote = fromId === noteId
  if (!sameNote && !isNoteFrozen(fromNote) && !isNoteFrozen(toNote)) {
    connections.value.push({
      id: nextConnId++,
      fromId,
      fromSide,
      toId: noteId,
      toSide: side,
      ...emptyRelation(),
    })
    // Keep other AI dashes; only drop the pair that is now hand-linked.
    pruneSuggestedLinkBetween(fromId, noteId)
  }
  clearConnectDrag()
  draft.value = null
}

/**
 * Idle canvas: drop selection / draft.
 * AI suggested blues stay until dismiss, keep, suggest-icon toggle, or Esc (after draft).
 */
function resetToIdle() {
  clearArmed.value = false
  clearConnectDrag()
  draft.value = null
  selectedId.value = null
  selectedConnId.value = null
  if (patternGather.value) endPatternGather(false)
  const suggestionsOpen =
    suggestedLinks.value.length > 0 || suggestLoading.value || suggestError.value || suggestSourceId.value != null
  if (suggestionsOpen) {
    // Keep blues + suggest session; only leave other tools.
    if (activeTool.value === 'group') {
      clearGroupStroke()
      groupHint.value = ''
      activeTool.value = 'connect'
    } else if (activeTool.value && activeTool.value !== 'connect') {
      activeTool.value = 'connect'
    }
  } else {
    if (activeTool.value === 'connect') activeTool.value = null
    if (activeTool.value === 'group') {
      clearGroupStroke()
      groupHint.value = ''
      activeTool.value = null
    }
    clearSuggestions()
  }
  const el = document.activeElement
  if (el instanceof HTMLElement && (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
    el.blur()
  }
}

/** Empty-canvas press: return to idle, then pan if the pointer moves. Sticky / group tools still handle the click. */
function onCanvasMouseDown(e) {
  if (stickyActive.value || groupActive.value) return
  if (e.target.closest('.note') || e.target.closest('.relation-wrap') || e.target.closest('.rationale-dock') || e.target.closest('.pattern-frame-caption') || e.target.closest('.suggest-wrap') || e.target.closest('.context-bubble')) return
  // Mag points sit outside the note box. A near-miss used to hit the canvas, deselect,
  // and make all four mags vanish — treat that as grabbing the mag instead.
  const at = clientToCanvas(e)
  const grab = nearestMagGrab(at.x, at.y)
  if (grab) {
    e.preventDefault()
    onConnectStart(grab.noteId, grab.side)
    return
  }
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
    applyPan()
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
  if (groupActive.value) return
  if (e.target.closest('.context-bubble')) return
  if (!stickyActive.value || !canvasRef.value) return

  const rect = canvasRef.value.getBoundingClientRect()
  const cx = (e.clientX - rect.left - panRef.x) / scale.value
  const cy = (e.clientY - rect.top - panRef.y) / scale.value
  const id = nextId++

  for (const note of notes.value) {
    note.rationaleOpen = false
  }

  notes.value.push({
    id,
    x: cx - 84,
    y: cy - 84,
    text: '',
    color: NOTE_COLORS[(id - 1) % NOTE_COLORS.length],
    width: 168,
    height: 168,
    ...emptyNoteMeta(),
  })
  selectedId.value = id
  activeTool.value = null
  logEvent('note_created', { id }).catch(() => {})
}

/** Nudge a note after a drag. */
function moveNote(id, dx, dy) {
  if (patternGather.value) patternDragId.value = id
  const visitor = patternGather.value?.visitors.find((item) => item.id === id)
  if (visitor) {
    visitor.to = { x: visitor.to.x + dx, y: visitor.to.y + dy }
    return
  }
  const parked = suggestPark.value?.visitors.find((item) => item.id === id)
  if (parked) {
    parked.to = { x: parked.to.x + dx, y: parked.to.y + dy }
    return
  }
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  if (patternGather.value && (!patternAdmitFrom || patternAdmitFrom.id !== id)) {
    patternAdmitFrom = { id, x: note.x, y: note.y }
  }
  note.x += dx
  note.y += dy
}

/** Drop outside takes a gathered idea out; drop inside the frame puts one back in. */
function onNoteMoveEnd(id) {
  const gather = patternGather.value
  if (gather && gather.origin !== 'label' && gather.origin !== 'suggest' && patternDragId.value === id) {
    const member = gather.visitors.some((item) => item.id === id)
    if (member && isOutsidePatternGroup(id)) ejectPatternVisitor(id)
    else if (!member && isInsidePatternGroup(id)) admitPatternVisitor(id)
  }
  patternDragId.value = null
  patternAdmitFrom = null
  if (suggestPark.value && suggestSourceId.value != null) {
    const source = notes.value.find((note) => note.id === suggestSourceId.value)
    if (source) refreshSuggestedSides(source)
  }
}

/** Persist contenteditable text. */
function changeText(id, text) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.text = text
}

/** Persist fill from the color wheel. */
function changeColor(id, color) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.color = color
}

/** Apply corner-resize result (x/y + width/height). */
function resizeNote(id, patch) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.x = patch.x
  note.y = patch.y
  note.width = patch.width
  note.height = patch.height
}

/** Mark this note as the selected one (toolbar / resize handles). */
function selectNote(id) {
  const note = notes.value.find((n) => n.id === id)
  if (isAbandoned(note)) return
  selectedId.value = id
  selectedConnId.value = null
}

/** Tick the checkbox on a gathered idea. Looking at the card does not keep it. */
function toggleGatherPick(id) {
  const visitor = patternGather.value?.visitors.find((item) => item.id === id)
  if (visitor) visitor.picked = !visitor.picked
}

/**
 * A rid is a canvas-scoped rationale identity: two labels sharing one are the same
 * reasoning, whichever note they sit on. Random so any module can mint one on its own.
 */
function newRid() {
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
}

function withRids(labels) {
  return (Array.isArray(labels) ? labels : []).map((item) =>
    item?.rid ? item : { ...item, rid: newRid() },
  )
}

/** Pins are snapshots of pool labels, so they inherit the pool's rid rather than a new one. */
function linkPinRids(pins, pool) {
  return (Array.isArray(pins) ? pins : []).map((pin) => {
    if (pin?.rid) return pin
    const match = pool.find((item) => item.id === pin?.id || item.text === pin?.text)
    return { ...pin, rid: match?.rid || newRid() }
  })
}

/** Canvas records written before rids existed still need one. */
function hydrateRids(owner) {
  owner.rationaleLabels = withRids(owner.rationaleLabels)
  owner.abandonLabels = withRids(owner.abandonLabels)
  owner.pinnedLabels = linkPinRids(owner.pinnedLabels, owner.rationaleLabels)
  owner.abandonPinned = linkPinRids(owner.abandonPinned, owner.abandonLabels)
  owner.rationaleLabels = persistedRationaleLabels(owner.rationaleLabels, owner.pinnedLabels)
  owner.abandonLabels = persistedRationaleLabels(owner.abandonLabels, owner.abandonPinned)
  return owner
}

function keptRationaleLabels(owner) {
  return persistedRationaleLabels(owner?.rationaleLabels, owner?.pinnedLabels)
}

/** One entry per rationale identity — the list the model checks before coining a new label. */
const labelInventory = computed(() => {
  const byRid = new Map()
  const add = (label) => {
    const text = String(label?.text || '').trim()
    if (!label?.rid || !text || byRid.has(label.rid)) return
    byRid.set(label.rid, { ref: label.rid, text })
  }
  for (const note of notes.value) keptRationaleLabels(note).forEach(add)
  for (const conn of connections.value) keptRationaleLabels(conn).forEach(add)
  for (const group of groups.value) keptRationaleLabels(group).forEach(add)
  if (patternGather.value?.origin === 'lasso') keptRationaleLabels(patternGather.value).forEach(add)
  return [...byRid.values()]
})

/** How many distinct ideas / relations invoke each rationale; 2 or more is what the badge reports. */
const ridOwners = computed(() => {
  const owners = new Map()
  const track = (key, labels) => {
    for (const label of labels || []) {
      if (!label?.rid) continue
      if (!owners.has(label.rid)) owners.set(label.rid, new Set())
      owners.get(label.rid).add(key)
    }
  }
  for (const note of notes.value) track(`n${note.id}`, keptRationaleLabels(note))
  for (const conn of connections.value) track(`c${conn.id}`, keptRationaleLabels(conn))
  for (const group of groups.value) track(`g${group.id}`, keptRationaleLabels(group))
  if (patternGather.value?.origin === 'lasso') track('g-live', keptRationaleLabels(patternGather.value))
  const counts = {}
  for (const [rid, keys] of owners) counts[rid] = keys.size
  return counts
})

/** Every kept rationale phrasing on the canvas — top 3 plus yellow labels — for repeating patterns. */
const canvasLabels = computed(() => {
  const rows = []
  for (const note of notes.value) {
    for (const label of keptRationaleLabels(note)) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: `n${note.id}` })
    }
  }
  for (const conn of connections.value) {
    for (const label of keptRationaleLabels(conn)) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: `c${conn.id}` })
    }
  }
  for (const group of groups.value) {
    for (const label of keptRationaleLabels(group)) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: `g${group.id}` })
    }
  }
  if (patternGather.value?.origin === 'lasso') {
    for (const label of keptRationaleLabels(patternGather.value)) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: 'g-live' })
    }
  }
  return rows
})

const patternStats = computed(() => patternStatsFromLabels(canvasLabels.value))

/** Resolve n12 / c3 owner keys to titles so a pattern hint can list the ideas. */
const ownerDirectory = computed(() => {
  const dir = {}
  for (const note of notes.value) {
    dir[`n${note.id}`] = {
      key: `n${note.id}`,
      kind: 'note',
      id: note.id,
      title: String(note.text || '').trim() || 'untitled idea',
    }
  }
  for (const conn of connections.value) {
    dir[`c${conn.id}`] = {
      key: `c${conn.id}`,
      kind: 'relation',
      id: conn.id,
      title: relationIdea(conn),
    }
  }
  for (const group of groups.value) {
    dir[`g${group.id}`] = {
      key: `g${group.id}`,
      kind: 'group',
      id: group.id,
      title: 'your group',
    }
  }
  dir['g-live'] = { key: 'g-live', kind: 'group', id: 'live', title: 'your group' }
  return dir
})

/** Preview: compact related ideas. origin: pattern (AI group) | label (same rationale) | lasso. */
const patternGather = ref(null) // { sourceId, memberKey, visitors, origin, focusWhy, rationaleText, rationaleLabels, pinnedLabels }
const patternAnimatingIds = ref(new Set())
const patternDragId = ref(null)
let patternAdmitFrom = null
let patternAnimTimer = 0
const patternHitIds = computed(() => new Set((patternGather.value?.visitors || []).map((item) => item.id)))
const patternPickedIds = computed(
  () => new Set((patternGather.value?.visitors || []).filter((item) => item.picked).map((item) => item.id)),
)
const patternDimActive = computed(() => Boolean(patternGather.value && patternGather.value.origin !== 'suggest'))
const suggestedBrowseIndex = ref(0)
const patternPickedCount = computed(() => (patternGather.value?.visitors || []).filter((item) => item.picked).length)

/** Group focus: only members (plus a note being dragged in/out) stay on the empty stage. */
function noteOnGroupStage(id) {
  if (!patternGather.value) return true
  if (patternHitIds.value.has(id)) return true
  if (patternAnimatingIds.value.has(id)) return true
  if (patternDragId.value === id) return true
  return false
}

function connectionOnGroupStage(line) {
  if (!patternGather.value) return true
  if (patternGather.value.origin === 'suggest') return false
  return noteOnGroupStage(line.fromId) && noteOnGroupStage(line.toId)
}

function memberSetKey(ids) {
  return [...ids].map(Number).sort((a, b) => a - b).join(',')
}

function noteIdsFromOwnerKeys(keys) {
  return [...new Set(
    (keys || [])
      .filter((key) => String(key).startsWith('n'))
      .map((key) => Number(String(key).slice(1)))
      .filter((id) => Number.isFinite(id)),
  )]
}

function clipIdeaTitle(text) {
  const line = String(text || '').replace(/\s+/g, ' ').trim()
  if (!line) return 'untitled idea'
  return line.length > 48 ? `${line.slice(0, 46).trim()}…` : line
}

function sharedLabelTextsForNoteIds(ids) {
  const members = new Set([...ids].map((id) => `n${id}`))
  const rows = canvasLabels.value.filter((row) => members.has(row.owner))
  return clusterSimilarLabels(rows)
    .map((group) => {
      const owners = new Set(group.members.map((item) => item.owner).filter(Boolean))
      return { text: String(group.preview || '').trim(), owners: owners.size }
    })
    .filter((item) => item.text && item.owners >= 2)
    .sort((a, b) => b.owners - a.owners || a.text.localeCompare(b.text))
    .slice(0, 3)
    .map((item) => item.text)
}

/** Unique note-sets that share a rationale — listed in the group-tool holder. */
const suggestedGroupSets = computed(() => {
  const byKey = new Map()
  for (const [rid, stats] of Object.entries(patternStats.value?.byRid || {})) {
    const ids = noteIdsFromOwnerKeys(stats.ownerKeys).filter((id) => {
      const note = notes.value.find((item) => item.id === id)
      return note && !isNoteFrozen(note) && !isAbandoned(note)
    })
    if (ids.length < 2) continue
    const key = memberSetKey(ids)
    const owners = Number(stats?.owners) || ids.length
    const existing = byKey.get(key)
    if (existing && existing.owners >= owners) continue
    const memberSet = new Set(ids.map((id) => `n${id}`))
    const preview = String(
      canvasLabels.value.find((row) => row.rid === rid && memberSet.has(row.owner))?.text || '',
    ).trim()
    const memberNotes = ids.map((id) => notes.value.find((item) => item.id === id)).filter(Boolean)
    byKey.set(key, {
      key,
      ids,
      owners,
      titles: memberNotes.map((note) => clipIdeaTitle(note.text)),
      why: narrativeSharedWhy(preview ? [preview] : sharedLabelTextsForNoteIds(ids)),
    })
  }
  return [...byKey.values()]
    .sort((a, b) => b.ids.length - a.ids.length || String(a.why).localeCompare(String(b.why)))
})

/** Suggested groups that include the currently selected idea. */
const focusedSuggestSets = computed(() => {
  const id = selectedId.value
  if (id == null) return []
  return suggestedGroupSets.value
    .filter((set) => set.ids.some((memberId) => Number(memberId) === Number(id)))
    .slice(0, 5)
})

/** Right-edge tabs (and the count badge) sit outside the note box. */
const TAB_MAX_W = 160
const TAB_BADGE_PAD = 16
const TAB_PLUS_W = 28
const TAB_LINE_H = 31
const CLUSTER_GAP = 28
const RELATED_WHY_H = 42
const RELATED_GAP_X = 56

function noteTabCount(note) {
  const labels = Array.isArray(note.pinnedLabels) ? note.pinnedLabels : []
  return clusterSimilarLabels(labels).length
}

function tabClearX(note) {
  if (!noteTabCount(note)) return TAB_PLUS_W
  return Math.min(TAB_MAX_W, note.width ?? 168) + TAB_BADGE_PAD
}

function tabClearY(note) {
  const n = noteTabCount(note)
  const size = noteSize(note)
  const rightFit = Math.max(1, Math.floor((size.height - 20) / TAB_LINE_H))
  const overflow = Math.max(0, n - rightFit)
  return overflow ? overflow * TAB_LINE_H + 8 : 12
}

function noteBodyBox(note, pos) {
  const size = noteSize(note)
  const p = pos || { x: note.x, y: note.y }
  return { x: p.x, y: p.y, w: size.width, h: size.height }
}

function noteVisualBox(note, pos) {
  const body = noteBodyBox(note, pos)
  return {
    x: body.x,
    y: body.y - tabClearY(note),
    w: body.w + tabClearX(note),
    h: body.h + tabClearY(note),
  }
}

function slotForNote(note, x, y) {
  const size = noteSize(note)
  const extraX = tabClearX(note)
  const extraY = tabClearY(note)
  return {
    x,
    y,
    w: size.width,
    h: size.height,
    vx: x,
    vy: y - extraY,
    vw: size.width + extraX,
    vh: size.height + extraY,
  }
}

function clusterHasTabOverlap(memberNotes) {
  const boxes = memberNotes.map((note) => noteVisualBox(note))
  const slack = 8
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]
      const b = boxes[j]
      if (a.x < b.x + b.w - slack && a.x + a.w - slack > b.x && a.y < b.y + b.h - slack && a.y + a.h - slack > b.y) {
        return true
      }
    }
  }
  return false
}

function groupSpread(memberNotes) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const note of memberNotes) {
    const box = noteVisualBox(note)
    minX = Math.min(minX, box.x)
    minY = Math.min(minY, box.y)
    maxX = Math.max(maxX, box.x + box.w)
    maxY = Math.max(maxY, box.y + box.h)
  }
  return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY }
}

/** Compact grid centred on the group's current midpoint — not on whoever was clicked. */
function placeCluster(memberNotes, opts = {}) {
  const gap = opts.gap ?? CLUSTER_GAP
  const sizeScale = opts.sizeScale ?? 1
  const n = memberNotes.length
  const cols = opts.cols || (n <= 3 ? n : Math.ceil(Math.sqrt(n)))
  const rows = Math.ceil(n / cols)
  const sizes = memberNotes.map((note) => {
    const size = noteSize(note)
    return { width: size.width * sizeScale, height: size.height * sizeScale }
  })
  const padX = memberNotes.map((note) => tabClearX(note) * sizeScale)
  const padY = memberNotes.map((note) => tabClearY(note) * sizeScale)
  const colW = Array(cols).fill(0)
  const rowBody = Array(rows).fill(0)
  const rowTop = Array(rows).fill(0)
  for (let i = 0; i < n; i++) {
    const c = i % cols
    const r = Math.floor(i / cols)
    colW[c] = Math.max(colW[c], sizes[i].width + padX[i])
    rowBody[r] = Math.max(rowBody[r], sizes[i].height)
    rowTop[r] = Math.max(rowTop[r], padY[i])
  }
  const rowH = rowBody.map((body, r) => body + rowTop[r])
  const gridW = colW.reduce((sum, col) => sum + col, 0) + gap * Math.max(0, cols - 1)
  const gridH = rowH.reduce((sum, row) => sum + row, 0) + gap * Math.max(0, rows - 1)
  let x0
  let y0
  if (opts.topLeft) {
    x0 = opts.topLeft.x
    y0 = opts.topLeft.y
  } else {
    let cx = 0
    let cy = 0
    for (const note of memberNotes) {
      const size = noteSize(note)
      cx += note.x + size.width / 2
      cy += note.y + size.height / 2
    }
    cx /= n
    cy /= n
    x0 = cx - gridW / 2
    y0 = cy - gridH / 2
  }
  const slots = []
  let y = y0
  for (let r = 0; r < rows; r++) {
    let x = x0
    for (let c = 0; c < cols; c++) {
      const i = r * cols + c
      if (i >= n) break
      slots.push(slotForNote(memberNotes[i], x, y + rowTop[r]))
      x += colW[c] + gap
    }
    y += rowH[r] + gap
  }
  return slots
}

function clusterSlots(memberNotes) {
  const compact = placeCluster(memberNotes)
  const now = groupSpread(memberNotes)
  const compactW = Math.max(...compact.map((slot) => (slot.vx ?? slot.x) + (slot.vw ?? slot.w))) - Math.min(...compact.map((slot) => slot.vx ?? slot.x))
  const compactH = Math.max(...compact.map((slot) => (slot.vy ?? slot.y) + (slot.vh ?? slot.h))) - Math.min(...compact.map((slot) => slot.vy ?? slot.y))
  if (now.w <= compactW + 96 && now.h <= compactH + 96 && !clusterHasTabOverlap(memberNotes)) {
    return memberNotes.map((note) => slotForNote(note, note.x, note.y))
  }
  return compact
}

/** Compact related ideas around the clicked sticky note so it stays put. */
function clusterSlotsAround(memberNotes, sourceId) {
  const compact = placeCluster(memberNotes)
  const idx = memberNotes.findIndex((note) => note.id === sourceId)
  if (idx < 0) return compact
  const source = memberNotes[idx]
  const dx = source.x - compact[idx].x
  const dy = source.y - compact[idx].y
  return shiftSlotGrid(compact, dx, dy)
}

/** Keep the clicked idea still; stack related ideas to its right with room for a why caption. */
function clusterSlotsBeside(memberNotes, sourceId) {
  const source = memberNotes.find((note) => note.id === sourceId) || memberNotes[0]
  const byId = new Map()
  byId.set(source.id, slotForNote(source, source.x, source.y))
  const srcVis = noteVisualBox(source)
  const startX = srcVis.x + srcVis.w + RELATED_GAP_X
  let y = source.y
  for (const note of memberNotes) {
    if (note.id === source.id) continue
    const slot = slotForNote(note, startX, y)
    slot.vy -= RELATED_WHY_H
    slot.vh += RELATED_WHY_H
    byId.set(note.id, slot)
    y = slot.y + slot.h + noteRationaleExtent(note) + CLUSTER_GAP + RELATED_WHY_H
  }
  return memberNotes.map((note) => byId.get(note.id) || slotForNote(note, note.x, note.y))
}

function relatedWhyText(note, sourceNote, focusWhy) {
  const focus = String(focusWhy || '').trim()
  const sourceKept = keptRationaleLabels(sourceNote)
  const noteKept = keptRationaleLabels(note)
  const shared = []
  const seen = new Set()
  for (const label of noteKept) {
    const text = String(label?.text || '').trim()
    if (!text) continue
    const hit = sourceKept.some((other) => sameRationale(label, other) || isSimilarLabel(text, String(other?.text || '').trim()))
    if (!hit) continue
    const key = label.rid ? `rid:${label.rid}` : `t:${text.toLowerCase()}`
    if (seen.has(key)) continue
    seen.add(key)
    shared.push(text)
  }
  const main = shared.find((text) => text === focus || isSimilarLabel(text, focus)) || focus || shared[0]
  const extras = shared.filter((text) => text !== main && !(focus && isSimilarLabel(text, focus)))
  if (!main) return 'shares a related rationale'
  if (extras.length) return `also uses “${main}” · also “${extras[0]}”`
  return `also uses “${main}”`
}

function shiftSlotGrid(slots, dx, dy) {
  return slots.map((slot) => ({
    ...slot,
    x: slot.x + dx,
    y: slot.y + dy,
    vx: (slot.vx ?? slot.x) + dx,
    vy: (slot.vy ?? slot.y) + dy,
  }))
}

const TRAY_NAV_H = 68
const TRAY_PAD = 12
const TRAY_BOTTOM_GAP = 12
const TOOLBAR_H = 88

function trayScaleForCount(n) {
  if (n <= 2) return 0.72
  if (n <= 4) return 0.6
  if (n <= 6) return 0.5
  return 0.42
}

const suggestNoteScale = computed(() => trayScaleForCount(patternGather.value?.visitors.length || 0))

function viewPointToCanvas(x, y) {
  const s = scale.value || 1
  return {
    x: (x - pan.value.x) / s,
    y: (y - pan.value.y) / s,
  }
}

/** Park a suggested set in a screen-fixed tray above the tool well. */
function traySlotsForNotes(memberNotes) {
  if (!memberNotes.length) return []
  const { w, h } = viewSize()
  const sizeScale = trayScaleForCount(memberNotes.length)
  const maxH = Math.min(200, h * 0.28)
  const maxW = w - 56
  let cols = Math.min(memberNotes.length, Math.max(2, Math.ceil(memberNotes.length / 2)))
  let compact = placeCluster(memberNotes, { sizeScale, cols, gap: 16 })
  const measure = (slots) => {
    const boxes = slots.map((slot, i) => {
      const note = memberNotes[i]
      const size = noteSize(note)
      return {
        x: slot.x,
        y: slot.y - tabClearY(note) * sizeScale,
        w: (size.width + tabClearX(note)) * sizeScale,
        h: (size.height + tabClearY(note)) * sizeScale,
      }
    })
    const minX = Math.min(...boxes.map((box) => box.x))
    const minY = Math.min(...boxes.map((box) => box.y))
    const maxX = Math.max(...boxes.map((box) => box.x + box.w))
    const maxY = Math.max(...boxes.map((box) => box.y + box.h))
    return { minX, minY, maxX, maxY, w: maxX - minX, h: maxY - minY }
  }
  let box = measure(compact)
  while ((box.h > maxH || box.w > maxW) && cols < memberNotes.length) {
    cols += 1
    compact = placeCluster(memberNotes, { sizeScale, cols, gap: 16 })
    box = measure(compact)
  }
  const left = (w - Math.min(box.w, maxW)) / 2
  const trayH = Math.min(box.h, maxH)
  const top = h - TOOLBAR_H - 44 - trayH - TRAY_NAV_H
  const origin = viewPointToCanvas(left, Math.max(24, top) + TRAY_NAV_H)
  return shiftSlotGrid(compact, origin.x - box.minX, origin.y - box.minY)
}

function memberNotesForSuggestSet(set) {
  return (Array.isArray(set?.ids) ? set.ids : [])
    .map((id) => notes.value.find((note) => note.id === id))
    .filter((note) => note && !isNoteFrozen(note) && !isAbandoned(note))
    .sort((a, b) => a.id - b.id)
}

function showSuggestSet(index, { morph = true } = {}) {
  const sets = focusedSuggestSets.value
  if (!sets.length) {
    if (patternGather.value?.origin === 'suggest') endPatternGather(false)
    suggestedBrowseIndex.value = 0
    return false
  }
  const i = ((index % sets.length) + sets.length) % sets.length
  suggestedBrowseIndex.value = i
  const set = sets[i]
  const memberNotes = memberNotesForSuggestSet(set)
  if (memberNotes.length < 2) return false
  const slots = traySlotsForNotes(memberNotes)
  const focusId = memberNotes.some((note) => note.id === selectedId.value)
    ? selectedId.value
    : memberNotes[0].id
  const gather = {
    sourceId: focusId,
    memberKey: memberSetKey(memberNotes.map((note) => note.id)),
    origin: 'suggest',
    focusWhy: set.why || '',
    rationaleText: '',
    rationaleLabels: [],
    pinnedLabels: [],
    aiWhy: '',
    visitors: memberNotes.map((note, slotIndex) => ({
      id: note.id,
      title: String(note.text || '').trim() || 'untitled idea',
      from: { x: note.x, y: note.y },
      to: { x: slots[slotIndex].x, y: slots[slotIndex].y },
      picked: false,
    })),
  }
  const prevIds = morph && patternGather.value?.origin === 'suggest'
    ? patternGather.value.visitors.map((item) => item.id)
    : []
  const nextIds = memberNotes.map((note) => note.id)
  patternGather.value = gather
  flashPatternAnim([...new Set([...prevIds, ...nextIds])])
  fillPatternGroupWhy(gather)
  logEvent('pattern_gather', { sourceId: gather.sourceId, visitors: nextIds, from: 'group_tray', index: i }).catch(() => {})
  return true
}

function shiftSuggestedBrowse(delta) {
  showSuggestSet(suggestedBrowseIndex.value + delta)
}

watch(selectedId, () => {
  if (!groupActive.value) return
  if (patternGather.value && patternGather.value.origin !== 'suggest') return
  if (!focusedSuggestSets.value.length) {
    if (patternGather.value?.origin === 'suggest') endPatternGather(false)
    suggestedBrowseIndex.value = 0
    return
  }
  showSuggestSet(0, { morph: true })
})

const suggestTrayChromeStyle = computed(() => {
  if (!groupActive.value) return null
  if (!focusedSuggestSets.value.length && patternGather.value?.origin !== 'suggest') return null
  pan.value
  scale.value
  const { w, h } = viewSize()
  const gather = patternGather.value
  const members = gather?.origin === 'suggest'
    ? gather.visitors.map((item) => notes.value.find((note) => note.id === item.id)).filter(Boolean)
    : []
  if (!members.length) {
    const width = Math.min(420, w - 40)
    const height = 92
    return {
      left: `${(w - width) / 2}px`,
      top: `${h - TOOLBAR_H - TRAY_BOTTOM_GAP - height}px`,
      width: `${width}px`,
      height: `${height}px`,
    }
  }
  const slots = traySlotsForNotes(members)
  const sizeScale = trayScaleForCount(members.length)
  const s = scale.value || 1
  const boxes = members.map((note, i) => {
    const slot = slots[i]
    const size = noteSize(note)
    return {
      x: slot.x,
      y: slot.y - tabClearY(note) * sizeScale,
      w: (size.width + tabClearX(note)) * sizeScale,
      h: (size.height + tabClearY(note)) * sizeScale,
    }
  })
  const minX = Math.min(...boxes.map((box) => box.x))
  const minY = Math.min(...boxes.map((box) => box.y))
  const maxX = Math.max(...boxes.map((box) => box.x + box.w))
  const maxY = Math.max(...boxes.map((box) => box.y + box.h))
  return {
    left: `${minX * s + pan.value.x - TRAY_PAD}px`,
    top: `${minY * s + pan.value.y - TRAY_NAV_H - TRAY_PAD}px`,
    width: `${(maxX - minX) * s + TRAY_PAD * 2}px`,
    height: `${(maxY - minY) * s + TRAY_NAV_H + TRAY_PAD * 2}px`,
  }
})

const groupLassoStyle = computed(() => {
  const chrome = suggestTrayChromeStyle.value
  if (!chrome) return undefined
  const { h } = viewSize()
  const top = parseFloat(String(chrome.top))
  if (!Number.isFinite(top)) return undefined
  return { bottom: `${Math.max(0, h - top)}px` }
})

const suggestNavStyle = computed(() => {
  const chrome = suggestTrayChromeStyle.value
  if (!chrome) return null
  return {
    left: chrome.left,
    top: chrome.top,
    width: chrome.width,
  }
})

function panToSlots(slots, extra = {}) {
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const slot of slots) {
    const x = slot.vx ?? slot.x
    const y = slot.vy ?? slot.y
    const w = slot.vw ?? slot.w
    const h = slot.vh ?? slot.h
    minX = Math.min(minX, x)
    minY = Math.min(minY, y)
    maxX = Math.max(maxX, x + w)
    maxY = Math.max(maxY, y + h)
  }
  maxY += Number(extra.bottom) || 0
  const { w, h } = viewSize()
  const s = scale.value
  panRef.x = w / 2 - ((minX + maxX) / 2) * s
  panRef.y = h / 2 - ((minY + maxY) / 2) * s
  applyPan()
}

function visitorBounds(visitor) {
  const note = notes.value.find((item) => item.id === visitor.id)
  if (!note) return null
  return noteBodyBox(note, liveNotePos(note))
}

function visitorVisualBounds(visitor) {
  const note = notes.value.find((item) => item.id === visitor.id)
  if (!note) return null
  const box = noteVisualBox(note, liveNotePos(note))
  box.h += noteRationaleExtent(note)
  return box
}

function patternMembersBox(exceptId) {
  const gather = patternGather.value
  if (!gather?.visitors.length) return null
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const visitor of gather.visitors) {
    if (exceptId != null && visitor.id === exceptId) continue
    const box = visitorVisualBounds(visitor)
    if (!box) continue
    minX = Math.min(minX, box.x)
    minY = Math.min(minY, box.y)
    maxX = Math.max(maxX, box.x + box.w)
    maxY = Math.max(maxY, box.y + box.h)
  }
  if (!Number.isFinite(minX)) return null
  return { minX, minY, maxX, maxY }
}

/** The note's centre sits inside the remaining cluster (with a little slack). */
function isInsidePatternGroup(id) {
  const others = patternMembersBox(id)
  if (!others) return false
  const visitor = patternGather.value?.visitors.find((item) => item.id === id)
  const note = notes.value.find((item) => item.id === id)
  const box = visitor ? visitorBounds(visitor) : note
    ? { ...liveNotePos(note), w: noteSize(note).width, h: noteSize(note).height }
    : null
  if (!box) return false
  const pad = 64
  const cx = box.x + box.w / 2
  const cy = box.y + box.h / 2
  return cx >= others.minX - pad && cx <= others.maxX + pad && cy >= others.minY - pad && cy <= others.maxY + pad
}

/** Far enough from the remaining cluster that this idea is no longer part of the group. */
function isOutsidePatternGroup(id) {
  if (!patternMembersBox(id)) return false
  return !isInsidePatternGroup(id)
}

const patternLeavingId = computed(() => {
  const id = patternDragId.value
  if (id == null) return null
  if (patternGather.value?.origin === 'label' || patternGather.value?.origin === 'suggest') return null
  if (!patternGather.value?.visitors.some((item) => item.id === id)) return null
  return isOutsidePatternGroup(id) ? id : null
})

const patternJoiningId = computed(() => {
  const id = patternDragId.value
  if (id == null || !patternGather.value) return null
  if (patternGather.value.origin === 'label' || patternGather.value.origin === 'suggest') return null
  if (patternGather.value.visitors.some((item) => item.id === id)) return null
  return isInsidePatternGroup(id) ? id : null
})

const patternFrameKicker = computed(() => {
  if (patternJoiningId.value) return 'drop to add'
  const origin = patternGather.value?.origin
  if (origin === 'lasso') return 'your group'
  if (origin === 'label') return 'same rationale'
  return 'suggested group'
})

const patternKeepCopy = computed(() => {
  const origin = patternGather.value?.origin
  if (origin === 'label') return 'keep nearby'
  if (patternPickedCount.value) return `keep ${patternPickedCount.value}`
  return 'keep group'
})

const patternGatherHint = computed(() => {
  const origin = patternGather.value?.origin
  if (origin === 'lasso') return 'your group · write why they belong together · enter suggests labels · keep group · esc cancels'
  if (origin === 'label') return 'related ideas moved beside this one · each caption says why · keep nearby or put back · esc cancels'
  if (origin === 'suggest') return 'groups for this idea · ← → see others · keep group or put back · or draw your own'
  return 'dashed frame = suggested group · the line on the frame is why they sit together · drag an idea out or drop one in · keep group · esc cancels'
})

const patternFrameStyle = computed(() => {
  const origin = patternGather.value?.origin
  if (origin === 'suggest' || origin === 'label') return null
  const box = patternMembersBox(patternDragId.value)
  if (!box) return null
  const pad = 20
  return {
    left: `${box.minX - pad}px`,
    top: `${box.minY - pad}px`,
    width: `${box.maxX - box.minX + pad * 2}px`,
    height: `${box.maxY - box.minY + pad * 2}px`,
  }
})

const labelGatherCaptions = computed(() => {
  const gather = patternGather.value
  if (gather?.origin !== 'label') return []
  return gather.visitors
})

function relatedWhyStyle(visitor) {
  const note = notes.value.find((item) => item.id === visitor.id)
  if (!note) return { display: 'none' }
  const pos = liveNotePos(note)
  const extraY = tabClearY(note)
  return {
    left: `${pos.x}px`,
    top: `${pos.y - extraY - 6}px`,
    width: `${Math.max(noteSize(note).width, 140)}px`,
  }
}

function sharedPatternLabelTexts(gather) {
  if (!gather?.visitors.length || gather.origin === 'lasso') return []
  return sharedLabelTextsForNoteIds(gather.visitors.map((item) => item.id))
}

/** Shared rationale among gathered notes — AI sentence when ready, else a short narrative from the labels. */
const patternGroupWhy = computed(() => {
  const gather = patternGather.value
  if (!gather?.visitors.length) return ''
  if (gather.origin === 'lasso') {
    return keptRationaleLabels(gather)
      .map((item) => String(item.text || '').trim())
      .filter(Boolean)
      .slice(0, 3)
      .join(' · ')
  }
  if (gather.origin === 'label') {
    return String(gather.focusWhy || '').trim() || sharedPatternLabelTexts(gather).join(' · ')
  }
  return String(gather.aiWhy || '').trim() || narrativeSharedWhy(sharedPatternLabelTexts(gather))
})

let groupWhyAbort = null

function fillPatternGroupWhy(gather) {
  if (!gather || (gather.origin !== 'pattern' && gather.origin !== 'suggest')) return
  groupWhyAbort?.abort()
  groupWhyAbort = new AbortController()
  const memberKey = gather.memberKey
  const ideas = gather.visitors.map((visitor) => {
    const note = notes.value.find((item) => item.id === visitor.id)
    return {
      id: String(visitor.id),
      text: visitor.title,
      labels: keptRationaleLabels(note).map((item) => item.text).filter(Boolean).slice(0, 3),
    }
  })
  suggestGroupWhy(
    { ideas, shared: sharedPatternLabelTexts(gather) },
    { context: designContext.value, signal: groupWhyAbort.signal },
  )
    .then((why) => {
      if (!why || patternGather.value?.memberKey !== memberKey) return
      patternGather.value.aiWhy = why
    })
    .catch((err) => {
      if (err?.code === 'cancelled' || err?.name === 'AbortError') return
    })
}

function flashPatternAnim(ids) {
  window.clearTimeout(patternAnimTimer)
  patternAnimatingIds.value = new Set(ids)
  patternAnimTimer = window.setTimeout(() => {
    patternAnimatingIds.value = new Set()
    patternAnimTimer = 0
  }, 420)
}

function endPatternGather(keepPicked) {
  const gather = patternGather.value
  if (!gather) return
  groupWhyAbort?.abort()
  groupWhyAbort = null
  patternDragId.value = null
  patternAdmitFrom = null
  const keptIds = new Set()
  if (keepPicked) {
    const keep = gather.visitors.filter((item) => item.picked)
    for (const visitor of keep) {
      const note = notes.value.find((item) => item.id === visitor.id)
      if (!note) continue
      note.x = visitor.to.x
      note.y = visitor.to.y
      keptIds.add(visitor.id)
    }
    if (keep.length) {
      logEvent('pattern_kept_nearby', {
        sourceId: gather.sourceId,
        kept: keep.map((item) => item.id),
      }).catch(() => {})
    }
  }
  const returning = gather.visitors.filter((item) => !keptIds.has(item.id)).map((item) => item.id)
  patternGather.value = null
  if (returning.length) flashPatternAnim(returning)
}

function ejectPatternVisitor(id) {
  const gather = patternGather.value
  if (!gather) return
  const index = gather.visitors.findIndex((item) => item.id === id)
  if (index < 0) return
  const visitor = gather.visitors[index]
  const note = notes.value.find((item) => item.id === id)
  if (note) {
    note.x = visitor.to.x
    note.y = visitor.to.y
  }
  gather.visitors.splice(index, 1)
  gather.memberKey = memberSetKey(gather.visitors.map((item) => item.id))
  logEvent('pattern_ejected', { sourceId: gather.sourceId, id }).catch(() => {})
  if (!gather.visitors.length) endPatternGather(false)
}

function admitPatternVisitor(id) {
  const gather = patternGather.value
  if (!gather || gather.visitors.some((item) => item.id === id)) return
  const note = notes.value.find((item) => item.id === id)
  if (!note || isNoteFrozen(note) || isAbandoned(note)) return
  const from = patternAdmitFrom?.id === id
    ? { x: patternAdmitFrom.x, y: patternAdmitFrom.y }
    : { x: note.x, y: note.y }
  gather.visitors.push({
    id: note.id,
    title: String(note.text || '').trim() || 'untitled idea',
    from,
    to: { x: note.x, y: note.y },
    picked: false,
  })
  gather.memberKey = memberSetKey(gather.visitors.map((item) => item.id))
  logEvent('pattern_admitted', { sourceId: gather.sourceId, id }).catch(() => {})
}

function clearPatternHits() {
  endPatternGather(false)
}

function pathLength(points) {
  let sum = 0
  for (let i = 1; i < points.length; i += 1) {
    const dx = points[i].x - points[i - 1].x
    const dy = points[i].y - points[i - 1].y
    sum += Math.hypot(dx, dy)
  }
  return sum
}

/** Even-odd ray test. The loop is treated as closed. */
function pointInPolygon(pt, polygon) {
  let inside = false
  const n = polygon.length
  for (let i = 0, j = n - 1; i < n; j = i, i += 1) {
    const xi = polygon[i].x
    const yi = polygon[i].y
    const xj = polygon[j].x
    const yj = polygon[j].y
    const crosses = yi > pt.y !== yj > pt.y
    if (crosses && pt.x < ((xj - xi) * (pt.y - yi)) / (yj - yi + 1e-9) + xi) inside = !inside
  }
  return inside
}

const groupStrokePath = computed(() => {
  const pts = groupStroke.value
  if (pts.length < 2) return ''
  return `M ${pts.map((pt) => `${pt.x} ${pt.y}`).join(' L ')}`
})

function onGroupPointerDown(e) {
  if (e.button !== 0) return
  e.preventDefault()
  e.stopPropagation()
  groupHint.value = ''
  groupStroke.value = [clientToCanvas(e)]
  try {
    e.currentTarget.setPointerCapture(e.pointerId)
  } catch {
    /* capture is optional; window listeners still follow the stroke */
  }
  window.addEventListener('pointermove', onGroupPointerMove)
  window.addEventListener('pointerup', onGroupPointerUp)
  window.addEventListener('pointercancel', onGroupPointerUp)
}

function onGroupPointerMove(e) {
  if (!groupStroke.value.length) return
  if (e.buttons !== undefined && e.buttons === 0) return
  const pt = clientToCanvas(e)
  const last = groupStroke.value[groupStroke.value.length - 1]
  const dx = pt.x - last.x
  const dy = pt.y - last.y
  if (dx * dx + dy * dy < 9) return
  groupStroke.value = [...groupStroke.value, pt]
}

function endGroupPointer() {
  window.removeEventListener('pointermove', onGroupPointerMove)
  window.removeEventListener('pointerup', onGroupPointerUp)
  window.removeEventListener('pointercancel', onGroupPointerUp)
}

function onGroupPointerUp() {
  if (!groupStroke.value.length) {
    endGroupPointer()
    return
  }
  const pts = groupStroke.value
  groupStroke.value = []
  endGroupPointer()
  if (pts.length < 3 || pathLength(pts) < 48) {
    groupHint.value = 'draw a loop around at least two ideas'
    return
  }
  const inside = notes.value.filter((note) => {
    if (isNoteFrozen(note) || isAbandoned(note)) return false
    const pos = liveNotePos(note)
    const size = noteSize(note)
    return pointInPolygon({ x: pos.x + size.width / 2, y: pos.y + size.height / 2 }, pts)
  })
  if (inside.length < 2) {
    groupHint.value = 'circle at least two ideas'
    return
  }
  beginGather(inside, { sourceId: inside[0].id, origin: 'lasso' })
  activeTool.value = null
  logEvent('group_lasso', { visitors: inside.map((item) => item.id) }).catch(() => {})
}

function groupIdeaText(gather) {
  const lines = (gather?.visitors || [])
    .map((item) => String(item.title || '').trim())
    .filter(Boolean)
  return lines.join('\n').slice(0, 3500)
}

function savedLassoGroup(memberKey) {
  return groups.value.find((group) => memberSetKey(group.memberIds) === memberKey) || null
}

function upsertKeptGroup(gather) {
  if (gather?.origin !== 'lasso') return
  const memberIds = (gather.visitors || [])
    .filter((item) => item.picked)
    .map((item) => item.id)
  if (memberIds.length < 2) return
  const hasWhy =
    Boolean(String(gather.rationaleText || '').trim()) ||
    keptRationaleLabels(gather).some((item) => String(item.text || '').trim())
  if (!hasWhy) return
  const key = memberSetKey(memberIds)
  const payload = {
    memberIds,
    rationaleText: gather.rationaleText || '',
    rationaleLabels: persistedRationaleLabels(gather.rationaleLabels, gather.pinnedLabels),
    pinnedLabels: Array.isArray(gather.pinnedLabels) ? gather.pinnedLabels.slice(0, 3) : [],
    origin: 'lasso',
  }
  const existing = savedLassoGroup(key)
  if (existing) Object.assign(existing, payload)
  else groups.value.push({ id: nextGroupId++, ...payload })
}

function setGroupPinned(pinned) {
  const gather = patternGather.value
  if (!gather || gather.origin !== 'lasso') return
  gather.pinnedLabels = Array.isArray(pinned) ? pinned.slice(0, 3) : []
  gather.rationaleLabels = persistedRationaleLabels(gather.rationaleLabels, gather.pinnedLabels)
}

function updateGroupRationale(payload) {
  const gather = patternGather.value
  if (!gather || gather.origin !== 'lasso') return
  gather.rationaleText = payload.input || ''
  gather.rationaleLabels = persistedRationaleLabels(payload.labels, gather.pinnedLabels)
}

const groupRationaleStyle = computed(() => {
  if (patternGather.value?.origin !== 'lasso') return null
  const box = patternMembersBox(patternDragId.value)
  if (!box) return null
  const pad = 20
  const width = Math.max(280, Math.min(420, box.maxX - box.minX + pad * 2))
  return {
    left: `${box.minX - pad}px`,
    top: `${box.maxY + pad + 10}px`,
    width: `${width}px`,
    zIndex: 19,
  }
})

function beginGather(memberNotes, { sourceId = null, origin = 'pattern', focusWhy = '' } = {}) {
  const sorted = [...memberNotes].sort((a, b) => a.id - b.id)
  if (sorted.length < 2) return false
  endPatternGather(false)
  const sid = sourceId ?? sorted[0].id
  const slots = origin === 'label' ? clusterSlotsBeside(sorted, sid) : clusterSlots(sorted)
  const memberKey = memberSetKey(sorted.map((note) => note.id))
  const saved = origin === 'lasso' ? savedLassoGroup(memberKey) : null
  const sourceNote = sorted.find((note) => note.id === sid) || sorted[0]
  const labelWhy = String(focusWhy || '').trim() || (origin === 'label' ? sharedLabelTextsForNoteIds(sorted.map((note) => note.id))[0] || '' : '')
  patternGather.value = {
    sourceId: sid,
    memberKey,
    origin,
    focusWhy: labelWhy,
    rationaleText: saved?.rationaleText || '',
    rationaleLabels: Array.isArray(saved?.rationaleLabels) ? saved.rationaleLabels.slice() : [],
    pinnedLabels: Array.isArray(saved?.pinnedLabels) ? saved.pinnedLabels.slice() : [],
    aiWhy: '',
    visitors: sorted.map((note, i) => ({
      id: note.id,
      title: String(note.text || '').trim() || 'untitled idea',
      from: { x: note.x, y: note.y },
      to: { x: slots[i].x, y: slots[i].y },
      picked: false,
      why: origin === 'label' && note.id !== sid ? relatedWhyText(note, sourceNote, labelWhy) : '',
    })),
  }
  selectedId.value = sid
  selectedConnId.value = null
  panToSlots(slots, origin === 'lasso' ? { bottom: 300 } : {})
  if (origin === 'pattern') fillPatternGroupWhy(patternGather.value)
  if (origin === 'lasso') {
    nextTick(() => {
      document.querySelector('.group-rationale-dock textarea')?.focus()
    })
  }
  return true
}

function inspectPattern(places, sourceId, focusWhy = '') {
  const incoming = Array.isArray(places) ? places : []
  const seedIds = [...new Set(
    incoming
      .filter((item) => item.kind === 'note')
      .map((item) => item.id)
      .concat(sourceId != null ? [sourceId] : []),
  )]
  if (!seedIds.length) {
    endPatternGather(false)
    return
  }
  const memberNotes = seedIds
    .map((id) => notes.value.find((note) => note.id === id))
    .filter((note) => note && !isNoteFrozen(note) && !isAbandoned(note))
    .sort((a, b) => a.id - b.id)
  if (memberNotes.length < 2) {
    endPatternGather(false)
    return
  }
  const key = memberSetKey(memberNotes.map((note) => note.id))
  if (patternGather.value?.origin === 'label' && patternGather.value.memberKey === key && patternGather.value.sourceId === sourceId) {
    endPatternGather(false)
    return
  }
  beginGather(memberNotes, { sourceId, origin: 'label', focusWhy })
  logEvent('label_gather', { sourceId, visitors: memberNotes.map((item) => item.id) }).catch(() => {})
}

function keepPatternGroup() {
  const gather = patternGather.value
  if (!gather) return
  if (gather.origin === 'label' || gather.origin === 'suggest' || !gather.visitors.some((item) => item.picked)) {
    for (const visitor of gather.visitors) visitor.picked = true
  }
  if (gather.origin === 'suggest') {
    const keep = gather.visitors.filter((item) => item.picked)
    const memberNotes = keep
      .map((item) => notes.value.find((note) => note.id === item.id))
      .filter(Boolean)
    const slots = placeCluster(memberNotes)
    memberNotes.forEach((note, i) => {
      note.x = slots[i].x
      note.y = slots[i].y
      keep[i].to = { x: slots[i].x, y: slots[i].y }
    })
    upsertKeptGroup(gather)
    endPatternGather(true)
    activeTool.value = null
    return
  }
  upsertKeptGroup(gather)
  endPatternGather(true)
}

/** Keep up to 3 labels attached to the side of a note. Unused pure-AI labels are dropped. */
function setPinnedLabels(id, pinned) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.pinnedLabels = Array.isArray(pinned) ? pinned.slice(0, 3) : []
  note.rationaleLabels = persistedRationaleLabels(note.rationaleLabels, note.pinnedLabels)
  logEvent('pin_change', { noteId: id, pinned: note.pinnedLabels }).catch(() => {})
}

function sameRationale(a, b) {
  if (!a || !b) return false
  if (a.rid && b.rid && a.rid === b.rid) return true
  const at = String(a.text || '').trim()
  const bt = String(b.text || '').trim()
  return Boolean(at && at === bt)
}

function updateLabels(id, labels) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.rationaleLabels = persistedRationaleLabels(Array.isArray(labels) ? labels : [], note.pinnedLabels)
  note.pinnedLabels = (note.pinnedLabels || [])
    .map((pin) => {
      const match = note.rationaleLabels.find((item) => sameRationale(item, pin))
      return match
        ? { id: match.id, text: match.text, kind: match.kind || '', source: match.source, rid: match.rid }
        : pin
    })
    .slice(0, 3)
}

/** Fold / unfold the rationale panel. New notes always start open. */
function toggleRationale(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isAbandoned(note) || note.abandonOpen) return
  note.rationaleOpen = !note.rationaleOpen
}

/** Clicking a side tab opens the rationale panel for that note. */
function openRationale(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isAbandoned(note) || note.abandonOpen) return
  note.rationaleOpen = true
}

function presetAbandonLabel(noteId) {
  return { id: `abandon-${noteId}-idk`, ...ABANDON_PRESET }
}

/** Empty notes are removed. Notes with an idea open the abandon rationale instead. */
function removeNote(id) {
  notes.value = notes.value.filter((n) => n.id !== id)
  connections.value = connections.value.filter((c) => c.fromId !== id && c.toId !== id)
  if (selectedId.value === id) selectedId.value = null
  if (metrics.value[id]) {
    const next = { ...metrics.value }
    delete next[id]
    metrics.value = next
  }
  logEvent('note_deleted', { noteId: id }).catch(() => {})
}

/** Same as the old Delete key: empty notes vanish; ideas with text open abandon. */
function requestDeleteNote(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isAbandoned(note) || note.abandonOpen) return
  if (isNoteEmpty(note)) removeNote(note.id)
  else beginAbandon(note.id)
}

function beginAbandon(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isAbandoned(note)) return
  note.frozenX = note.x
  note.frozenY = note.y
  note.frozenWidth = note.width ?? 168
  note.frozenHeight = note.height ?? 168
  for (const item of notes.value) {
    item.abandonOpen = item.id === id
    if (item.id === id) item.rationaleOpen = false
  }
  for (const conn of connections.value) {
    conn.rationaleOpen = false
    conn.abandonOpen = false
  }
  if (!Array.isArray(note.abandonLabels) || !note.abandonLabels.length) {
    note.abandonLabels = [presetAbandonLabel(id)]
  }
  note.abandonOpen = true
  selectedId.value = id
  selectedConnId.value = null
  logEvent('note_abandon_started', { noteId: id }).catch(() => {})
}

function cancelAbandon(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.abandonOpen = false
  note.frozenX = undefined
  note.frozenY = undefined
  note.frozenWidth = undefined
  note.frozenHeight = undefined
}

function confirmAbandon(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isAbandoned(note)) return
  note.abandoned = true
  note.abandonOpen = false
  note.rationaleOpen = false
  note.frozenX = note.x
  note.frozenY = note.y
  note.frozenWidth = note.width ?? 168
  note.frozenHeight = note.height ?? 168
  if (draft.value?.fromId === id) {
    clearConnectDrag()
    draft.value = null
  }
  if (selectedId.value === id) selectedId.value = null
  if (activeTool.value === 'connect') activeTool.value = null
  logEvent('note_abandoned', {
    noteId: id,
    abandonText: note.abandonText || '',
    abandonLabels: note.abandonLabels || [],
  }).catch(() => {})
}

/** Right-click → revive: the ghost comes back at the position it was frozen at. */
function reviveNote(id) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || !isAbandoned(note)) return
  if (typeof note.frozenX === 'number') {
    note.x = note.frozenX
    note.y = note.frozenY
  }
  if (typeof note.frozenWidth === 'number') {
    note.width = note.frozenWidth
    note.height = note.frozenHeight
  }
  note.abandoned = false
  note.abandonOpen = false
  note.frozenX = undefined
  note.frozenY = undefined
  note.frozenWidth = undefined
  note.frozenHeight = undefined
  selectedId.value = id
  selectedConnId.value = null
  logEvent('note_revived', { noteId: id, abandonText: note.abandonText || '' }).catch(() => {})
}

function setAbandonPinned(id, pinned) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.abandonPinned = Array.isArray(pinned) ? pinned.slice(0, 3) : []
  note.abandonLabels = persistedRationaleLabels(note.abandonLabels, note.abandonPinned)
}

function updateAbandonRationale(id, payload) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.abandonText = payload.input || ''
  note.abandonLabels = persistedRationaleLabels(payload.labels, note.abandonPinned)
}

function removeConnection(id) {
  connections.value = connections.value.filter((c) => c.id !== id)
  if (selectedConnId.value === id) selectedConnId.value = null
  logEvent('relation_deleted', { connectionId: id }).catch(() => {})
}

function beginAbandonRelation(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn || isRelationAbandoned(conn)) return
  for (const note of notes.value) note.abandonOpen = false
  for (const item of connections.value) {
    item.abandonOpen = item.id === id
    if (item.id === id) item.rationaleOpen = false
  }
  if (!Array.isArray(conn.abandonLabels) || !conn.abandonLabels.length) {
    conn.abandonLabels = [presetAbandonLabel(`rel-${id}`)]
  }
  conn.abandonOpen = true
  selectConnection(id)
  logEvent('relation_abandon_started', { connectionId: id }).catch(() => {})
}

function cancelAbandonRelation(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.abandonOpen = false
}

function confirmAbandonRelation(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn || isRelationAbandoned(conn)) return
  conn.abandoned = true
  conn.abandonOpen = false
  conn.rationaleOpen = false
  if (selectedConnId.value === id) selectedConnId.value = null
  logEvent('relation_abandoned', {
    connectionId: id,
    abandonText: conn.abandonText || '',
    abandonLabels: conn.abandonLabels || [],
  }).catch(() => {})
}

function setRelationAbandonPinned(id, pinned) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.abandonPinned = Array.isArray(pinned) ? pinned.slice(0, 3) : []
  conn.abandonLabels = persistedRationaleLabels(conn.abandonLabels, conn.abandonPinned)
}

function updateRelationAbandonRationale(id, payload) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.abandonText = payload.input || ''
  conn.abandonLabels = persistedRationaleLabels(payload.labels, conn.abandonPinned)
}

/** Clicking a note's rationale box selects that note (and does not pan / place). */
function onRationalePointer(noteId) {
  selectedId.value = noteId
  selectedConnId.value = null
}

function relationIdea(conn) {
  const from = notes.value.find((n) => n.id === conn.fromId)
  const to = notes.value.find((n) => n.id === conn.toId)
  const left = String(from?.text || '').trim() || 'this idea'
  const right = String(to?.text || '').trim() || 'this idea'
  return `${left} ↔ ${right}`
}

function relationRationaleStyle(line) {
  return {
    left: `${line.mid.x}px`,
    top: `${line.mid.y + 16}px`,
    width: '220px',
    transform: 'translateX(-50%)',
    zIndex: selectedConnId.value === line.id ? 19 : 8,
  }
}

function selectConnection(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn || isRelationAbandoned(conn)) return
  selectedConnId.value = id
  selectedId.value = null
}

function openRelationRationale(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn || isRelationAbandoned(conn) || conn.abandonOpen) return
  selectConnection(id)
  for (const item of connections.value) {
    item.rationaleOpen = item.id === id
  }
}

function toggleRelationRationale(id) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn || isRelationAbandoned(conn) || conn.abandonOpen) return
  selectConnection(id)
  conn.rationaleOpen = !conn.rationaleOpen
}

function setRelationPinned(id, pinned) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.pinnedLabels = Array.isArray(pinned) ? pinned.slice(0, 3) : []
  conn.rationaleLabels = persistedRationaleLabels(conn.rationaleLabels, conn.pinnedLabels)
  logEvent('pin_change', { connectionId: id, pinned: conn.pinnedLabels }).catch(() => {})
}

function updateRelationLabels(id, labels) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.rationaleLabels = persistedRationaleLabels(Array.isArray(labels) ? labels : [], conn.pinnedLabels)
  const ids = new Set(conn.rationaleLabels.map((item) => item.id))
  conn.pinnedLabels = (conn.pinnedLabels || [])
    .map((pin) => {
      const match = conn.rationaleLabels.find((item) => item.id === pin.id)
      return match
        ? { id: match.id, text: match.text, kind: match.kind || '', source: match.source, rid: match.rid }
        : pin
    })
    .filter((pin) => ids.has(pin.id))
    .slice(0, 3)
}

function updateRelationRationale(id, payload) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.rationaleText = payload.input || ''
  conn.rationaleLabels = persistedRationaleLabels(payload.labels, conn.pinnedLabels)
}

function onRelationPointer(id) {
  selectConnection(id)
}

function onConnectorPointer(id) {
  if (draft.value) return
  selectConnection(id)
  const el = document.activeElement
  if (el instanceof HTMLElement && isTypingTarget(el)) el.blur()
}

/** True while typing in a note or in a rationale field — skip canvas shortcuts. */
function isTypingTarget(el) {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
}

function historyState() {
  return {
    notes: notes.value,
    connections: connections.value,
    nextId,
    nextConnId,
  }
}

function applyHistory(snap) {
  notes.value = Array.isArray(snap.notes) ? snap.notes : []
  connections.value = Array.isArray(snap.connections) ? snap.connections : []
  nextId = Number(snap.nextId) > 0 ? Number(snap.nextId) : 1
  nextConnId = Number(snap.nextConnId) > 0 ? Number(snap.nextConnId) : 1
  if (!notes.value.some((n) => n.id === selectedId.value)) selectedId.value = null
  if (!connections.value.some((c) => c.id === selectedConnId.value)) selectedConnId.value = null
  draft.value = null
  metrics.value = {}
  patternGather.value = null
  patternDragId.value = null
  patternAnimatingIds.value = new Set()
  historyEpoch.value += 1
  searchVecCache.clear()
}

function undoCanvas() {
  history.undo(historyState, applyHistory)
}

function redoCanvas() {
  history.redo(historyState, applyHistory)
}

function onHistoryPointerDown() {
  history.pointerDown()
}

function onHistoryPointerUp() {
  history.pointerUp()
}

function isUndoRedoKey(e) {
  if (!(e.metaKey || e.ctrlKey) || e.altKey) return false
  const key = String(e.key || '').toLowerCase()
  return key === 'z' || key === 'y'
}

/** Esc: drop draft, then cancel tool / deselect. N: toggle sticky tool. ⌘Z / ⌘⇧Z undo/redo even while typing. */
function onKeydown(e) {
  if (isUndoRedoKey(e)) {
    e.preventDefault()
    const key = String(e.key || '').toLowerCase()
    if (key === 'y' || (key === 'z' && e.shiftKey)) redoCanvas()
    else undoCanvas()
    return
  }
  const typing = isTypingTarget(e.target)
  if (e.key === 'Escape') {
    if (clearArmed.value) {
      clearArmed.value = false
      return
    }
    if (contextOpen.value) {
      closeContext()
      if (typing && e.target instanceof HTMLElement) e.target.blur()
      return
    }
    if (searchOpen.value) {
      closeSearch()
      return
    }
    if (groupActive.value || groupStroke.value.length) {
      if (patternGather.value) endPatternGather(false)
      clearGroupStroke()
      groupHint.value = ''
      activeTool.value = null
      return
    }
    if (patternGather.value) {
      if (typing) {
        if (e.target instanceof HTMLElement) e.target.blur()
        return
      }
      endPatternGather(false)
      return
    }
    // First Esc cancels an in-progress yellow dash only; blues stay.
    if (draft.value) {
      clearConnectDrag()
      draft.value = null
      return
    }
    if (suggestedLinks.value.length || suggestLoading.value || suggestError.value || connectActive.value) {
      clearSuggestions()
      activeTool.value = null
      draft.value = null
      return
    }
    const pending = notes.value.find((n) => n.abandonOpen)
    if (pending && !typing) {
      cancelAbandon(pending.id)
      return
    }
    const pendingRel = connections.value.find((c) => c.abandonOpen)
    if (pendingRel && !typing) {
      cancelAbandonRelation(pendingRel.id)
      return
    }
    activeTool.value = null
    selectedId.value = null
    selectedConnId.value = null
    if (!typing) {
      for (const conn of connections.value) conn.rationaleOpen = false
    }
    if (typing && e.target instanceof HTMLElement) e.target.blur()
    return
  }
  if ((e.key === 'Backspace' || e.key === 'Delete') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    const conn = connections.value.find((c) => c.id === selectedConnId.value)
    if (conn && !isRelationAbandoned(conn) && !conn.abandonOpen && !typing) {
      e.preventDefault()
      if (isRelationEmpty(conn)) removeConnection(conn.id)
      else beginAbandonRelation(conn.id)
      return
    }
  }
  if (typing) return
  if (!e.metaKey && !e.ctrlKey && !e.altKey && patternGather.value?.origin === 'suggest') {
    if (e.key === 'ArrowLeft') {
      e.preventDefault()
      shiftSuggestedBrowse(-1)
      return
    }
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      shiftSuggestedBrowse(1)
      return
    }
  }
  if ((e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault()
    toggleTool('sticky')
  }
  if ((e.key === 'g' || e.key === 'G') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault()
    toggleTool('group')
  }
}

function updateRationale(id, payload) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.rationaleText = payload.input || ''
  note.rationaleLabels = persistedRationaleLabels(payload.labels, note.pinnedLabels)
}

function canvasPayload() {
  return {
    notes: notes.value.map((note) => ({
      id: note.id,
      x: note.x,
      y: note.y,
      text: note.text,
      color: note.color,
      width: note.width,
      height: note.height,
      pinnedLabels: note.pinnedLabels || [],
      rationaleOpen: Boolean(note.rationaleOpen),
      rationaleText: note.rationaleText || '',
      rationaleLabels: persistedRationaleLabels(note.rationaleLabels, note.pinnedLabels),
      abandoned: Boolean(note.abandoned),
      abandonOpen: Boolean(note.abandonOpen),
      abandonText: note.abandonText || '',
      abandonLabels: persistedRationaleLabels(note.abandonLabels, note.abandonPinned),
      abandonPinned: note.abandonPinned || [],
      frozenX: note.frozenX,
      frozenY: note.frozenY,
      frozenWidth: note.frozenWidth,
      frozenHeight: note.frozenHeight,
    })),
    connections: connections.value.map((c) => ({
      ...c,
      rationaleLabels: persistedRationaleLabels(c.rationaleLabels, c.pinnedLabels),
      abandonLabels: persistedRationaleLabels(c.abandonLabels, c.abandonPinned),
    })),
    groups: groups.value.map((group) => ({
      id: group.id,
      memberIds: Array.isArray(group.memberIds) ? group.memberIds : [],
      rationaleText: group.rationaleText || '',
      rationaleLabels: persistedRationaleLabels(group.rationaleLabels, group.pinnedLabels),
      pinnedLabels: group.pinnedLabels || [],
      origin: group.origin || 'lasso',
    })),
    pan: pan.value,
    scale: scale.value,
    nextId,
    nextConnId,
    nextGroupId,
    designContext: designContext.value,
    contextImages: contextImages.value,
    contextQuestions: contextQuestions.value,
    contextDismissed: contextDismissed.value,
    contextSeed,
  }
}

function toggleContext() {
  if (contextOpen.value) {
    closeContext()
    return
  }
  contextOpen.value = true
  contextImageError.value = ''
  nextTick(() => contextRef.value?.focus())
}

/** Closing the brief is what asks the AI to read it back; reopening an unchanged brief is free. */
function closeContext() {
  if (!contextOpen.value) return
  contextOpen.value = false
  contextDropActive.value = false
  refreshContextQuestions()
}

/** What the AI has already been shown. Same fingerprint means nothing new to read. */
function contextFingerprint() {
  return `${designContext.value.trim()}::${contextImages.value.map((item) => item.id).join(',')}`
}

async function addContextImages(files) {
  const list = Array.from(files || [])
  if (!list.length) return
  contextImageError.value = ''
  for (const file of list) {
    if (contextImages.value.length >= MAX_CONTEXT_IMAGES) {
      contextImageError.value = `${MAX_CONTEXT_IMAGES} images is the limit · remove one first`
      break
    }
    try {
      const image = await importContextImage(file)
      contextImages.value = [...contextImages.value, image]
    } catch (err) {
      contextImageError.value = err?.message || "That image couldn't be imported."
    }
  }
}

function onContextFilePick(event) {
  addContextImages(event.target.files)
  event.target.value = '' // so picking the same file twice still fires
}

function onContextPaste(event) {
  const files = imageFilesFrom(event.clipboardData)
  if (!files.length) return // a plain text paste belongs to the textarea
  event.preventDefault()
  addContextImages(files)
}

function onContextDrop(event) {
  contextDropActive.value = false
  const files = imageFilesFrom(event.dataTransfer)
  if (!files.length) return
  event.preventDefault()
  addContextImages(files)
}

function onContextDragOver(event) {
  if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return
  event.preventDefault()
  contextDropActive.value = true
}

function removeContextImage(id) {
  contextImages.value = contextImages.value.filter((item) => item.id !== id)
  contextImageError.value = ''
}

/** Lay the bubbles on a rail down the left of the current view, in canvas space. */
function questionRailSlots(count) {
  const origin = viewPointToCanvas(QUESTION_RAIL_INSET, QUESTION_RAIL_TOP)
  const { h } = viewSize()
  // Never run the rail into the tool well at the bottom of the screen.
  const room = Math.max(1, Math.floor((h - QUESTION_RAIL_TOP - 150) / QUESTION_RAIL_STEP) + 1)
  const step = count > room ? QUESTION_RAIL_STEP * (room / count) : QUESTION_RAIL_STEP
  return Array.from({ length: count }, (_, i) => ({
    x: origin.x,
    y: origin.y + (i * step) / (scale.value || 1),
  }))
}

/**
 * Read the brief back as questions. Each run replaces the bubbles, because the questions
 * are about the brief as it stands now; dismissed ones are sent along so they stay gone.
 */
async function refreshContextQuestions() {
  const seed = contextFingerprint()
  if (seed === contextSeed) return
  const brief = designContext.value.trim()
  if (brief.length < MIN_CONTEXT_FOR_QUESTIONS && !contextImages.value.length) {
    contextSeed = seed
    contextQuestions.value = []
    return
  }

  contextAbort?.abort()
  contextAbort = new AbortController()
  const signal = contextAbort.signal
  contextQuestionsLoading.value = true
  contextQuestionsError.value = ''
  logEvent('context_questions', { chars: brief.length, images: contextImages.value.length }).catch(() => {})

  try {
    const result = await askContextQuestions(
      brief,
      contextImages.value.map((item) => item.dataUrl),
      { asked: contextDismissed.value, signal },
    )
    if (signal.aborted) return
    contextSeed = seed
    const slots = questionRailSlots(result.questions.length)
    contextQuestions.value = result.questions.map((item, i) => ({
      id: `q-${Date.now()}-${i}`,
      text: item.text,
      kind: item.kind,
      x: slots[i].x,
      y: slots[i].y,
    }))
    if (!result.questions.length) contextQuestionsError.value = 'nothing new to ask about the brief'
  } catch (err) {
    if (signal.aborted || err?.code === 'cancelled') return
    contextQuestionsError.value = err?.message || "couldn't read the brief back"
  } finally {
    if (!signal.aborted) contextQuestionsLoading.value = false
  }
}

/** Dismiss is also a signal: the AI never raises that question again. */
function dismissContextQuestion(id) {
  const question = contextQuestions.value.find((item) => item.id === id)
  if (!question) return
  contextQuestions.value = contextQuestions.value.filter((item) => item.id !== id)
  contextDismissed.value = [...contextDismissed.value, question.text].slice(-MAX_DISMISSED_QUESTIONS)
  logEvent('context_question_dismissed', { kind: question.kind }).catch(() => {})
}

function clearContextQuestions() {
  contextAbort?.abort()
  contextAbort = null
  contextQuestions.value = []
  contextQuestionsLoading.value = false
  contextQuestionsError.value = ''
}

let saveTimer = null
let loaded = false
let wheelTarget = null

function scheduleSave() {
  if (!loaded) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveCanvas(canvasPayload()).catch(() => {})
  }, 1400)
}

async function signOut() {
  clearTimeout(saveTimer)
  try {
    await saveCanvas(canvasPayload())
  } catch {
    /* still log out */
  }
  try {
    await logout()
  } catch {
    /* cookie may already be gone */
  }
  emit('signed-out')
}

/** Register / drop keyboard shortcuts (N, Esc, undo). Load this participant's canvas. */
onMounted(async () => {
  window.addEventListener('keydown', onKeydown, true)
  window.addEventListener('pointerdown', onHistoryPointerDown, true)
  window.addEventListener('pointerup', onHistoryPointerUp, true)
  window.addEventListener('pointercancel', onHistoryPointerUp, true)
  wheelTarget = canvasRef.value
  wheelTarget?.addEventListener('wheel', onWheel, { passive: false })
  try {
    const data = await fetchCanvas()
    notes.value = (Array.isArray(data.notes) ? data.notes : []).map((note) => {
      const loaded = {
        id: note.id,
        x: note.x,
        y: note.y,
        text: note.text || '',
        color: note.color || NOTE_COLORS[0],
        width: note.width || 168,
        height: note.height || 168,
        pinnedLabels: Array.isArray(note.pinnedLabels) ? note.pinnedLabels : [],
        rationaleOpen: Boolean(note.rationaleOpen),
        rationaleText: note.rationaleText || '',
        rationaleLabels: Array.isArray(note.rationaleLabels) ? note.rationaleLabels : [],
        abandoned: Boolean(note.abandoned),
        abandonOpen: Boolean(note.abandonOpen),
        abandonText: note.abandonText || '',
        abandonLabels: Array.isArray(note.abandonLabels) ? note.abandonLabels : [],
        abandonPinned: Array.isArray(note.abandonPinned) ? note.abandonPinned : [],
        frozenX: typeof note.frozenX === 'number' ? note.frozenX : undefined,
        frozenY: typeof note.frozenY === 'number' ? note.frozenY : undefined,
        frozenWidth: typeof note.frozenWidth === 'number' ? note.frozenWidth : undefined,
        frozenHeight: typeof note.frozenHeight === 'number' ? note.frozenHeight : undefined,
      }
      if (loaded.abandoned && loaded.frozenX != null) {
        loaded.x = loaded.frozenX
        loaded.y = loaded.frozenY
        loaded.width = loaded.frozenWidth ?? loaded.width
        loaded.height = loaded.frozenHeight ?? loaded.height
      }
      return hydrateRids(loaded)
    })
    connections.value = (Array.isArray(data.connections) ? data.connections : []).map((c) => hydrateRids({
      id: c.id,
      fromId: c.fromId,
      fromSide: c.fromSide,
      toId: c.toId,
      toSide: c.toSide,
      rationaleOpen: Boolean(c.rationaleOpen),
      rationaleText: c.rationaleText || '',
      rationaleLabels: Array.isArray(c.rationaleLabels) ? c.rationaleLabels : [],
      pinnedLabels: Array.isArray(c.pinnedLabels) ? c.pinnedLabels : [],
      abandoned: Boolean(c.abandoned),
      abandonOpen: Boolean(c.abandonOpen),
      abandonText: c.abandonText || '',
      abandonLabels: Array.isArray(c.abandonLabels) ? c.abandonLabels : [],
      abandonPinned: Array.isArray(c.abandonPinned) ? c.abandonPinned : [],
    }))
    const savedPan = data.pan && typeof data.pan === 'object' ? data.pan : {}
    panRef.x = Number(savedPan.x) || 0
    panRef.y = Number(savedPan.y) || 0
    pan.value = { x: panRef.x, y: panRef.y }
    scale.value = 1
    nextId = Number(data.nextId) > 0 ? Number(data.nextId) : 1
    nextConnId = Number(data.nextConnId) > 0 ? Number(data.nextConnId) : 1
    groups.value = (Array.isArray(data.groups) ? data.groups : []).map((group) => hydrateRids({
      id: group.id,
      memberIds: Array.isArray(group.memberIds) ? group.memberIds : [],
      rationaleText: group.rationaleText || '',
      rationaleLabels: Array.isArray(group.rationaleLabels) ? group.rationaleLabels : [],
      pinnedLabels: Array.isArray(group.pinnedLabels) ? group.pinnedLabels : [],
      origin: group.origin || 'lasso',
    }))
    const maxGroupId = groups.value.reduce((max, group) => Math.max(max, Number(group.id) || 0), 0)
    nextGroupId = Number(data.nextGroupId) > 0 ? Number(data.nextGroupId) : maxGroupId + 1
    designContext.value = typeof data.designContext === 'string' ? data.designContext : ''
    contextImages.value = (Array.isArray(data.contextImages) ? data.contextImages : [])
      .filter((item) => item && String(item.dataUrl || '').startsWith('data:image/'))
      .slice(0, MAX_CONTEXT_IMAGES)
      .map((item) => ({
        id: String(item.id || `img-${Math.random().toString(36).slice(2, 10)}`),
        name: String(item.name || 'image'),
        dataUrl: item.dataUrl,
        w: Number(item.w) || 0,
        h: Number(item.h) || 0,
      }))
    contextQuestions.value = (Array.isArray(data.contextQuestions) ? data.contextQuestions : [])
      .filter((item) => item && String(item.text || '').trim())
      .map((item, i) => ({
        id: String(item.id || `q-restored-${i}`),
        text: String(item.text),
        kind: String(item.kind || 'scope'),
        x: Number(item.x) || 0,
        y: Number(item.y) || 0,
      }))
    contextDismissed.value = (Array.isArray(data.contextDismissed) ? data.contextDismissed : [])
      .map((item) => String(item || '').trim())
      .filter(Boolean)
      .slice(-MAX_DISMISSED_QUESTIONS)
    contextSeed = typeof data.contextSeed === 'string' ? data.contextSeed : contextFingerprint()
  } catch {
    notes.value = []
    groups.value = []
  }
  // A board with nothing on it and no brief means a first visit: open the brief once, unprompted.
  contextOpen.value = !designContext.value.trim() && !notes.value.length
  loaded = true
  history.seed(historyState())
  history.setReady(true)
  await nextTick()
  window.addEventListener('resize', onWindowResize)
  watch(
    [notes, connections, groups],
    () => {
      scheduleSave()
      history.noteChange(historyState())
    },
    { deep: true },
  )
  watch(pan, scheduleSave, { deep: true })
  watch(designContext, scheduleSave)
  watch([contextImages, contextQuestions, contextDismissed], scheduleSave, { deep: true })
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown, true)
  window.removeEventListener('pointerdown', onHistoryPointerDown, true)
  window.removeEventListener('pointerup', onHistoryPointerUp, true)
  window.removeEventListener('pointercancel', onHistoryPointerUp, true)
  window.removeEventListener('resize', onWindowResize)
  wheelTarget?.removeEventListener('wheel', onWheel)
  wheelTarget = null
  clearConnectDrag()
  clearGroupStroke()
  for (const item of dockObservers.values()) item.ro.disconnect()
  dockObservers.clear()
  clearTimeout(saveTimer)
  window.clearTimeout(patternAnimTimer)
  suggestAbort?.abort()
  contextAbort?.abort()
})
</script>

<template>
  <div class="board">
    <div
      ref="canvasRef"
      class="canvas"
      :class="{ placing: stickyActive, connecting: connectActive || !!draft, drawing: !!draft, grouping: groupActive, 'group-focus': patternDimActive }"
      :style="groupActive || connectActive || draft ? { cursor: PEN_CURSOR } : undefined"
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
            :width="patternOffset.size"
            :height="patternOffset.size"
            patternUnits="userSpaceOnUse"
          >
            <circle :cx="patternOffset.r" :cy="patternOffset.r" :r="patternOffset.r" fill="rgba(44, 40, 31, 0.16)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#dots)" />
      </svg>

      <div class="notes-layer" :style="notesLayerStyle">
        <svg
          class="connectors"
          :class="{ 'suggest-front': suggestDimActive, drawing: !!draft }"
          overflow="visible"
          aria-hidden="true"
        >
          <g v-for="line in renderedConnections" v-show="connectionOnGroupStage(line)" :key="line.id">
            <path
              class="connector-hit"
              :class="{ abandoned: line.abandoned }"
              :d="line.d"
              fill="none"
              stroke="transparent"
              stroke-width="16"
              stroke-linecap="round"
              @mousedown.stop.prevent="onConnectorPointer(line.id)"
            />
            <path
              class="connector-line"
              :class="{ selected: selectedConnId === line.id && !line.abandoned, faded: line.faded }"
              :d="line.d"
              fill="none"
              :stroke="line.faded ? 'rgba(44, 40, 31, 0.12)' : selectedConnId === line.id ? '#2563eb' : USER_ECHO"
              :stroke-width="selectedConnId === line.id && !line.abandoned ? 3 : 2.5"
              stroke-linecap="round"
            />
          </g>
          <path
            v-if="draftPath"
            class="draft-line"
            :d="draftPath"
            fill="none"
            :stroke="USER_ECHO"
            stroke-width="3"
            stroke-linecap="round"
            stroke-dasharray="6 5"
          />
          <g v-for="line in renderedSuggestions" v-show="!patternDimActive" :key="`sug-${line.key}`">
            <path
              class="connector-hit suggest-hit"
              :d="line.d"
              fill="none"
              stroke="transparent"
              stroke-width="18"
              stroke-linecap="round"
              @mousedown.stop.prevent="activateSuggestedLink(line.key); suggestWhyOpen = suggestWhyOpen === line.key ? null : line.key"
            />
            <path
              class="suggest-line"
              :class="{ picked: line.picked }"
              :d="line.d"
              fill="none"
              :stroke="line.picked ? AI_ECHO_STRONG : AI_ECHO"
              :stroke-width="line.picked ? 3 : 2.5"
              stroke-linecap="round"
              :stroke-dasharray="line.picked ? '9 5' : '7 6'"
            />
          </g>
        </svg>
        <div v-if="patternFrameStyle" class="pattern-frame" :class="{ joining: patternJoiningId != null }" :style="patternFrameStyle">
          <div class="pattern-frame-caption">
            <span class="pattern-frame-kicker">{{ patternFrameKicker }}</span>
            <span v-if="patternGroupWhy && !patternJoiningId" class="pattern-frame-why">{{ patternGroupWhy }}</span>
          </div>
        </div>
        <div
          v-for="visitor in labelGatherCaptions"
          :key="`why-${visitor.id}`"
          class="related-why"
          :class="{ source: visitor.id === patternGather.sourceId }"
          :style="relatedWhyStyle(visitor)"
        >
          <span class="related-why-kicker">{{ visitor.id === patternGather.sourceId ? 'this idea' : 'related because' }}</span>
          <span v-if="visitor.why" class="related-why-text">{{ visitor.why }}</span>
        </div>
        <svg v-if="groupStrokePath" class="group-lasso" overflow="visible" aria-hidden="true">
          <path
            class="group-lasso-line"
            :d="groupStrokePath"
            fill="none"
            stroke="#b45309"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-dasharray="7 5"
          />
        </svg>
        <div
          v-for="line in renderedConnections"
          v-show="connectionOnGroupStage(line)"
          :key="`rel-${line.id}`"
          class="relation-wrap"
          :class="{ abandoned: line.abandoned }"
          :style="{
            left: `${line.mid.x}px`,
            top: `${line.mid.y}px`,
            zIndex: selectedConnId === line.id || line.rationaleOpen || line.abandonOpen ? 18 : 8,
            opacity: line.faded || line.abandoned ? 0.22 : 1,
          }"
          @pointerdown.stop="onRelationPointer(line.id)"
          @mousedown.stop="onRelationPointer(line.id)"
        >
          <RelationMarker
            :connection="line"
            :open="Boolean(line.rationaleOpen)"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :selected="selectedConnId === line.id && !line.abandoned"
            :abandoned="Boolean(line.abandoned)"
            @open-rationale="openRelationRationale(line.id)"
            @toggle-rationale="toggleRelationRationale(line.id)"
            @pin-change="(pinned) => setRelationPinned(line.id, pinned)"
            @labels-change="(labels) => updateRelationLabels(line.id, labels)"
            @inspect-pattern="(places, focusWhy) => inspectPattern(places, line.fromId, focusWhy)"
          />
        </div>
        <div
          v-for="line in renderedSuggestions"
          v-show="!patternDimActive"
          :key="`sug-chip-${line.key}`"
          class="suggest-wrap"
          :class="{ open: suggestWhyOpen === line.key || suggestWhyHover === line.key, inert: !!draft }"
          :style="{
            left: `${line.mid.x}px`,
            top: `${line.mid.y}px`,
            zIndex: draft ? 0 : (suggestWhyOpen === line.key || suggestWhyHover === line.key ? 42 : 40),
            pointerEvents: draft ? 'none' : 'auto',
          }"
          @pointerdown.stop
          @mousedown.stop
        >
          <div
            class="suggest-pick"
            :class="{ on: line.picked }"
            @mouseenter="suggestWhyHover = line.key"
            @mouseleave="suggestWhyHover = null"
            @click="onSuggestFrameClick(line.key, $event)"
          >
            <button
              type="button"
              class="suggest-check"
              :class="{ on: line.picked }"
              :title="line.picked ? 'Unselect this suggested link' : 'Select this suggested link'"
              @click.stop="toggleSuggestedLink(line.key)"
            />
            <span class="suggest-why">{{ line.why }}</span>
          </div>
        </div>
        <!-- The AI reading the brief back. Not attached to any note: these are about the project. -->
        <div
          v-for="question in contextQuestions"
          :key="question.id"
          class="context-bubble"
          :class="[`kind-${question.kind}`, { inert: !!draft }]"
          :style="{
            left: `${question.x}px`,
            top: `${question.y}px`,
            pointerEvents: draft ? 'none' : 'auto',
          }"
          @pointerdown.stop
          @mousedown.stop
        >
          <span class="context-bubble-kind">{{ question.kind === 'goal' ? 'is this your goal?' : question.kind }}</span>
          <p class="context-bubble-text">{{ question.text }}</p>
          <button
            type="button"
            class="context-bubble-drop"
            title="Dismiss — the AI will not ask this again"
            @click="dismissContextQuestion(question.id)"
          >×</button>
        </div>
        <template v-for="note in notes" :key="note.id">
          <StickyNoteCard
            :note="note"
            :scale="scale"
            :selected="selectedId === note.id"
            :connect-mode="noteSuggestArmed(note)"
            :show-mag="noteShowsMag(note)"
            :mag-outset="MAG_OUTSET"
            :drafting="!!draft"
            :draft-from-id="draft?.fromId ?? null"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :display-x="liveNotePos(note).x"
            :display-y="liveNotePos(note).y"
            :gathering="patternAnimatingIds.has(note.id) || (patternHitIds.has(note.id) && patternGather?.origin !== 'suggest') || suggestParkedIds.has(note.id)"
            :gather-pick="Boolean(patternGather) && patternGather.origin !== 'label' && patternGather.origin !== 'suggest' && patternHitIds.has(note.id)"
            :leaving-group="patternLeavingId === note.id"
            :joining-group="patternJoiningId === note.id"
            :search-hit="searchHitIds.has(note.id) || (patternHitIds.has(note.id) && patternGather?.origin !== 'suggest') || patternJoiningId === note.id"
            :search-picked="searchPickedIds.has(note.id) || patternPickedIds.has(note.id)"
            :search-dim="searchDimActive || (suggestDimActive && !suggestParkedIds.has(note.id) && selectedId !== note.id && magHoverId !== note.id) || (patternGather?.origin === 'suggest' && !patternHitIds.has(note.id) && !patternAnimatingIds.has(note.id))"
            :preview-scale="patternGather?.origin === 'suggest' && patternHitIds.has(note.id) ? suggestNoteScale : 1"
            :stage-hidden="patternDimActive && !noteOnGroupStage(note.id)"
            :suggesting="suggestLoading && suggestSourceId === note.id"
            :suggest-source="suggestSourceId === note.id && suggestPulledIds.size > 0"
            :suggest-hit="suggestTargetIds.has(note.id)"
            :suggest-picked="suggestedLinks.some((link) => link.toId === note.id && link.picked)"
            @move="moveNote"
            @move-end="onNoteMoveEnd"
            @text-change="changeText"
            @select="selectNote"
            @toggle-gather="toggleGatherPick"
            @color-change="changeColor"
            @resize="resizeNote"
            @metrics="setMetrics"
            @connect-start="onConnectStart"
            @connect-end="onConnectEnd"
            @request-relation="onRelationTool"
            @request-delete="requestDeleteNote"
            @toggle-rationale="toggleRationale"
            @open-rationale="openRationale"
            @pin-change="(pinned) => setPinnedLabels(note.id, pinned)"
            @labels-change="(labels) => updateLabels(note.id, labels)"
            @inspect-pattern="(places, focusWhy) => inspectPattern(places, note.id, focusWhy)"
            @revive="reviveNote"
          />
          <div
            v-if="note.abandonOpen && !note.abandoned && noteOnGroupStage(note.id)"
            class="rationale-dock"
            :ref="(el) => bindDockRef(note.id, el)"
            :style="rationaleStyle(note)"
            @pointerdown.stop="onRationalePointer(note.id)"
            @mousedown.stop="onRationalePointer(note.id)"
            @click.stop
          >
            <RationaleModule
              :key="`abandon-${note.id}-${historyEpoch}`"
              compact
              target="note"
              :idea="note.text"
              :id-prefix="`abandon-${note.id}`"
              :pinned="note.abandonPinned || []"
              :saved-input="note.abandonText || ''"
              :saved-labels="note.abandonLabels || []"
              :known-labels="labelInventory"
              :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :context="designContext"
              :preset-labels="[presetAbandonLabel(note.id)]"
              placeholder="Tell me why abandon this idea…"
              action-label="just abandon it"
              complete-on-generate
              @pin-change="(pinned) => setAbandonPinned(note.id, pinned)"
              @rationale-change="(payload) => updateAbandonRationale(note.id, payload)"
              @labels="() => logEvent('abandon_labels_generated', { noteId: note.id })"
              @inspect-pattern="(places, focusWhy) => inspectPattern(places, note.id, focusWhy)"
              @action="confirmAbandon(note.id)"
              @complete="confirmAbandon(note.id)"
            />
          </div>
          <div
            v-show="note.rationaleOpen && !note.abandoned && !note.abandonOpen && noteOnGroupStage(note.id)"
            class="rationale-dock"
            :ref="(el) => bindDockRef(note.id, el)"
            :style="rationaleStyle(note)"
            @pointerdown.stop="onRationalePointer(note.id)"
            @mousedown.stop="onRationalePointer(note.id)"
            @click.stop
          >
            <RationaleModule
              :key="`${note.id}-${historyEpoch}`"
              compact
              target="note"
              :idea="note.text"
              :id-prefix="`note-${note.id}`"
              :pinned="note.pinnedLabels || []"
              :saved-input="note.rationaleText || ''"
              :saved-labels="note.rationaleLabels || []"
              :known-labels="labelInventory"
              :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :context="designContext"
              :active="Boolean(note.rationaleOpen)"
              @pin-change="(pinned) => setPinnedLabels(note.id, pinned)"
              @rationale-change="(payload) => updateRationale(note.id, payload)"
              @labels="() => logEvent('labels_generated', { noteId: note.id })"
              @inspect-pattern="(places, focusWhy) => inspectPattern(places, note.id, focusWhy)"
            />
          </div>
        </template>
        <template v-for="line in renderedConnections" :key="`rel-abandon-${line.id}`">
          <div
            v-if="line.abandonOpen && !line.abandoned && connectionOnGroupStage(line) && !patternGather"
            class="rationale-dock"
            :style="relationRationaleStyle(line)"
            @pointerdown.stop="onRelationPointer(line.id)"
            @mousedown.stop="onRelationPointer(line.id)"
            @click.stop
          >
          <RationaleModule
            :key="`rel-abandon-${line.id}-${historyEpoch}`"
            compact
            target="relation"
            :idea="relationIdea(line)"
            :id-prefix="`rel-abandon-${line.id}`"
            :pinned="line.abandonPinned || []"
            :saved-input="line.abandonText || ''"
            :saved-labels="line.abandonLabels || []"
            :known-labels="labelInventory"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :context="designContext"
            :preset-labels="[presetAbandonLabel(`rel-${line.id}`)]"
            placeholder="Tell me why abandon this relation…"
            action-label="just abandon it"
            complete-on-generate
            @pin-change="(pinned) => setRelationAbandonPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationAbandonRationale(line.id, payload)"
            @labels="() => logEvent('relation_abandon_labels_generated', { connectionId: line.id })"
            @inspect-pattern="(places, focusWhy) => inspectPattern(places, line.fromId, focusWhy)"
            @action="confirmAbandonRelation(line.id)"
            @complete="confirmAbandonRelation(line.id)"
          />
          </div>
        </template>
        <div
          v-for="line in renderedConnections"
          v-show="line.rationaleOpen && !line.abandoned && !line.abandonOpen && connectionOnGroupStage(line) && !patternGather"
          :key="`rel-dock-${line.id}`"
          class="rationale-dock"
          :style="relationRationaleStyle(line)"
          @pointerdown.stop="onRelationPointer(line.id)"
          @mousedown.stop="onRelationPointer(line.id)"
          @click.stop
        >
          <RationaleModule
            :key="`rel-${line.id}-${historyEpoch}`"
            compact
            target="relation"
            :idea="relationIdea(line)"
            :id-prefix="`rel-${line.id}`"
            :pinned="line.pinnedLabels || []"
            :saved-input="line.rationaleText || ''"
            :saved-labels="line.rationaleLabels || []"
            :known-labels="labelInventory"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :context="designContext"
            placeholder="Tell me about how they relates…"
            @pin-change="(pinned) => setRelationPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationRationale(line.id, payload)"
            @labels="() => logEvent('labels_generated', { connectionId: line.id })"
            @inspect-pattern="(places, focusWhy) => inspectPattern(places, line.fromId, focusWhy)"
          />
        </div>
        <div
          v-if="patternGather?.origin === 'lasso' && groupRationaleStyle"
          class="rationale-dock group-rationale-dock"
          :style="groupRationaleStyle"
          @pointerdown.stop
          @mousedown.stop
          @click.stop
        >
          <RationaleModule
            :key="`group-${patternGather.memberKey}-${historyEpoch}`"
            compact
            target="group"
            :idea="groupIdeaText(patternGather)"
            id-prefix="group"
            :pinned="patternGather.pinnedLabels || []"
            :saved-input="patternGather.rationaleText || ''"
            :saved-labels="patternGather.rationaleLabels || []"
            :known-labels="labelInventory"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :context="designContext"
            placeholder="tell me more why this is a group"
            @pin-change="setGroupPinned"
            @rationale-change="updateGroupRationale"
            @labels="() => logEvent('group_labels_generated', { members: patternGather.visitors.map((item) => item.id) })"
            @inspect-pattern="(places, focusWhy) => inspectPattern(places, patternGather.sourceId, focusWhy)"
          />
        </div>
      </div>

      <!-- DEV test seed — delete this block when the study no longer needs it. -->
      <div v-if="isDev" class="test-bar" @mousedown.stop @click.stop>
        <span class="test-tag">test only</span>
        <button
          type="button"
          class="test-btn"
          title="Temporary seed: same heat-neighbourhood topic, scattered, no overlap"
          @click="addSearchTestPack"
        >
          <span class="sample-plus">+</span>
          <span class="btn-label">add samples</span>
        </button>
        <span v-if="searchTestHint" class="test-hint">{{ searchTestHint }}</span>
      </div>

      <!-- Account: floating undo / redo / clear / logout -->
      <div class="account-bar" @mousedown.stop @click.stop>
        <button
          type="button"
          class="logout-btn"
          title="Undo (⌘Z)"
          :disabled="!canUndo"
          @click="undoCanvas"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M3 7v6h6" />
            <path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6.7 3L3 13" />
          </svg>
          <span class="btn-label">undo</span>
        </button>
        <button
          type="button"
          class="logout-btn"
          title="Redo (⌘⇧Z)"
          :disabled="!canRedo"
          @click="redoCanvas"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M21 7v6h-6" />
            <path d="M3 17a9 9 0 0 1 9-9 9 9 0 0 1 6.7 3L21 13" />
          </svg>
          <span class="btn-label">redo</span>
        </button>
        <button
          type="button"
          class="logout-btn"
          :class="{ armed: clearArmed }"
          :title="clearArmed ? 'Click again to clear the board' : 'Clear canvas'"
          @click="armClearBoard"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
          </svg>
          <span class="btn-label">{{ clearArmed ? 'confirm?' : 'clear' }}</span>
        </button>
        <button
          type="button"
          class="logout-btn"
          :title="props.username ? `Log out · ${props.username}` : 'Log out'"
          @click="signOut"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" x2="9" y1="12" y2="12" />
          </svg>
          <span class="btn-label">log-out</span>
        </button>
      </div>

      <div
        v-if="groupActive"
        class="group-lasso-layer"
        :style="groupLassoStyle"
        @pointerdown="onGroupPointerDown"
        @pointermove="onGroupPointerMove"
        @pointerup="onGroupPointerUp"
        @pointercancel="onGroupPointerUp"
      />

      <div
        v-if="groupActive && (patternGather?.origin === 'suggest' || focusedSuggestSets.length)"
        class="group-suggest-nav"
        :style="suggestNavStyle"
        @mousedown.stop
        @click.stop
      >
        <button
          type="button"
          class="group-suggest-step"
          :disabled="focusedSuggestSets.length < 2"
          title="Previous group for this idea"
          @click="shiftSuggestedBrowse(-1)"
        >←</button>
        <span class="group-suggest-kicker">{{ focusedSuggestSets.length ? `this idea · ${suggestedBrowseIndex + 1} / ${focusedSuggestSets.length}` : 'this idea' }}</span>
        <button
          type="button"
          class="group-suggest-step"
          :disabled="focusedSuggestSets.length < 2"
          title="Next group for this idea"
          @click="shiftSuggestedBrowse(1)"
        >→</button>
        <p v-if="patternGather?.origin === 'suggest' && patternGroupWhy" class="group-suggest-why">{{ patternGroupWhy }}</p>
      </div>
      <aside
        v-if="groupActive && (patternGather?.origin === 'suggest' || focusedSuggestSets.length)"
        class="group-suggest-tray"
        :class="{ empty: patternGather?.origin !== 'suggest' }"
        :style="suggestTrayChromeStyle"
      ></aside>

      <!-- FigJam-style tool well: sticky note only -->
      <div class="bottom-bar" @mousedown.stop @click.stop>
        <div v-if="stickyActive" class="hint">click anywhere to place a note · esc to cancel</div>
        <div v-else-if="groupActive && groupHint" class="hint">{{ groupHint }}</div>
        <div v-else-if="groupActive && patternGather?.origin === 'suggest'" class="hint">{{ patternGatherHint }}</div>
        <div v-else-if="groupActive && selectedId && !focusedSuggestSets.length" class="hint">no suggested group for this idea · draw around ideas to make your own · esc to cancel</div>
        <div v-else-if="groupActive" class="hint">draw around ideas to group them · esc to cancel</div>
        <div v-else-if="suggestError" class="hint">{{ suggestError }} · or drag a mag point to draw your own</div>
        <div v-else-if="suggestLoading" class="hint">looking for related ideas… · you can still drag a mag point</div>
        <div v-else-if="suggestedLinks.length" class="hint">blue dashed = AI · click blue to pull closer · drag a mag point to draw your own (blues stay) · dismiss / esc to clear</div>
        <div v-else-if="connectActive" class="hint">drag a mag point on this note · other notes show mag points when the pen is close</div>
        <div v-else-if="patternGather" class="hint">{{ patternGatherHint }}</div>
        <div v-else-if="searchOpen && !searchHits.length && !searchError" class="hint">press enter to look up · esc to close</div>
        <div v-else-if="contextQuestionsLoading" class="hint">reading your brief back…</div>
        <div v-else-if="contextQuestionsError" class="hint">{{ contextQuestionsError }}</div>
        <div v-else-if="contextQuestions.length" class="hint">the AI's questions about this project · × dismisses one for good</div>
        <button
          v-if="contextQuestions.length"
          type="button"
          class="tool-btn"
          title="Clear every question bubble"
          @click="clearContextQuestions"
        >
          <span class="btn-label">clear questions</span>
        </button>
        <button
          v-if="suggestedLinks.length"
          type="button"
          class="tool-btn"
          :disabled="!suggestPickedCount"
          title="Keep the selected suggested links"
          @click="keepSuggestedLinks"
        >
          <span class="sample-plus">✓</span>
          <span class="btn-label">keep {{ suggestPickedCount }}</span>
        </button>
        <button
          v-if="suggestedLinks.length || suggestError"
          type="button"
          class="tool-btn"
          title="Dismiss suggestions"
          @click="clearSuggestions"
        >
          <span class="btn-label">dismiss</span>
        </button>
        <button
          v-if="patternGather"
          type="button"
          class="tool-btn"
          :title="patternGather.origin === 'label' ? 'Keep these ideas nearby' : patternGather.origin === 'suggest' ? 'Keep this suggested group together' : patternPickedCount ? 'Keep the ticked ideas in the group' : 'Keep the suggested group together'"
          @click="keepPatternGroup"
        >
          <span class="sample-plus">✓</span>
          <span class="btn-label">{{ patternKeepCopy }}</span>
        </button>
        <button
          v-if="patternGather"
          type="button"
          class="tool-btn"
          title="Send remaining gathered ideas back"
          @click="endPatternGather(false)"
        >
          <span class="btn-label">put back</span>
        </button>
        <button
          type="button"
          class="tool-btn"
          :class="{ active: stickyActive }"
          title="Create idea (N)"
          @click="toggleTool('sticky')"
        >
          <StickyNoteIcon :size="28" />
          <span class="btn-label">create idea</span>
        </button>
        <button
          type="button"
          class="tool-btn"
          :class="{ active: groupActive }"
          title="Group ideas (G)"
          @click="toggleTool('group')"
        >
          <GroupIcon :size="28" />
          <span class="btn-label">group</span>
        </button>
        <div class="context-slot">
          <!-- Standing brief: the designer writes here whenever they like and the AI never answers it. -->
          <div
            v-if="contextOpen"
            class="context-panel"
            :class="{ dropping: contextDropActive }"
            @dragover="onContextDragOver"
            @dragleave="contextDropActive = false"
            @drop="onContextDrop"
          >
            <div class="context-head">
              <span class="context-title">what are you designing?</span>
              <button type="button" class="context-close" title="Close" @click="closeContext">×</button>
            </div>
            <textarea
              ref="contextRef"
              v-model="designContext"
              class="context-input"
              :maxlength="MAX_DESIGN_CONTEXT"
              placeholder="Describe the design goal, who it is for, constraints — or paste any material that frames this work."
              @paste="onContextPaste"
            />
            <div v-if="contextImages.length" class="context-shots">
              <div v-for="image in contextImages" :key="image.id" class="context-shot">
                <img :src="image.dataUrl" :alt="image.name" />
                <button
                  type="button"
                  class="context-shot-drop"
                  :title="`Remove ${image.name}`"
                  @click="removeContextImage(image.id)"
                >×</button>
              </div>
            </div>
            <div class="context-actions">
              <button
                type="button"
                class="context-add"
                :disabled="contextImages.length >= MAX_CONTEXT_IMAGES"
                title="Attach a sketch, screenshot or photo"
                @click="contextFileRef?.click()"
              >
                + image
              </button>
              <span class="context-count">{{ contextImages.length }}/{{ MAX_CONTEXT_IMAGES }} · or paste / drop</span>
              <input
                ref="contextFileRef"
                class="context-file"
                type="file"
                :accept="IMAGE_ACCEPT"
                multiple
                @change="onContextFilePick"
              />
            </div>
            <p v-if="contextImageError" class="context-warn">{{ contextImageError }}</p>
            <p class="context-hint">close this and the AI reads it back as questions on the canvas</p>
          </div>
          <button
            type="button"
            class="tool-btn"
            :class="{ active: contextOpen }"
            title="Design context the AI reads as background"
            @click="toggleContext"
          >
            <ContextIcon :size="28" />
            <span class="btn-label">{{ designContext.trim() ? 'context ✓' : 'context' }}</span>
          </button>
        </div>
        <div class="search-slot">
          <div v-if="searchHits.length || (searchOpen && searchError)" class="search-results">
            <div class="search-results-head">
              <p v-if="searchError" class="search-status">{{ searchError }}</p>
              <p v-else class="search-status">{{ searchHits.length }} close {{ searchHits.length === 1 ? 'idea' : 'ideas' }} · pick what matches</p>
              <button v-if="searchHits.length" type="button" class="search-clear" @click="clearSearchHits">clear</button>
            </div>
            <button
              v-for="hit in searchHits"
              :key="hit.id"
              type="button"
              class="search-hit-row"
              :class="{ picked: searchPickedIds.has(hit.id) }"
              @click="jumpToSearchHit(hit.id)"
            >
              <span
                class="search-check"
                :class="{ on: searchPickedIds.has(hit.id) }"
                @click.stop="toggleSearchPick(hit.id)"
              />
              <span class="search-hit-copy">
                <span class="search-hit-title">{{ hit.title }}</span>
                <span class="search-hit-why">{{ hit.why }}</span>
              </span>
            </button>
          </div>
          <input
            v-if="searchOpen"
            ref="searchInputRef"
            v-model="searchQuery"
            class="search-input"
            type="text"
            maxlength="200"
            placeholder="are you looking for an idea? a rationale"
            :disabled="searchLoading"
            @keydown.enter.prevent="runBoardSearch"
          />
          <button
            type="button"
            class="tool-btn"
            :class="{ active: searchOpen }"
            title="Look up ideas"
            @click="toggleSearch"
          >
            <SearchIcon :size="28" />
            <span class="btn-label">{{ searchLoading ? 'looking…' : 'look up' }}</span>
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
  background: var(--paper);
}

.canvas {
  width: 100%;
  height: 100%;
  position: relative;
  overflow: hidden;
  cursor: default;
  touch-action: none;
  overscroll-behavior: none;
}

.canvas.placing,
.canvas.grouping {
  cursor: crosshair;
}

.canvas.connecting .note {
  cursor: default;
}

.canvas.connecting .mag-point,
.canvas.connecting .mag-point:hover {
  cursor: grab;
}

.canvas.connecting .mag-point:active {
  cursor: grabbing;
}

/* Once a mag is grabbed, switch to the pen immediately for the whole drag. */
.canvas.drawing,
.canvas.drawing .note,
.canvas.drawing .note *,
.canvas.drawing .mag-point,
.canvas.drawing .mag-point:hover,
.canvas.drawing .mag-point:active,
.canvas.drawing .mag-point.blocked {
  cursor: inherit !important;
}

.group-lasso-layer {
  position: absolute;
  inset: 0;
  z-index: 25;
  touch-action: none;
  cursor: inherit;
}

.group-suggest-tray {
  position: absolute;
  box-sizing: border-box;
  border: 1.5px dashed #b45309;
  border-radius: 16px;
  background: rgba(255, 251, 235, 0.55);
  z-index: 12;
  pointer-events: none;
}

.group-suggest-tray.empty {
  background: var(--chrome);
}

.group-suggest-nav {
  position: absolute;
  box-sizing: border-box;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 8px 10px;
  padding: 8px 10px 0;
  z-index: 32;
}

.group-suggest-step {
  width: 28px;
  height: 28px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--chrome);
  color: #92400e;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
}

.group-suggest-step:disabled {
  opacity: 0.35;
  cursor: default;
}

.group-suggest-kicker {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #b45309;
}

.group-suggest-why {
  flex: 1 0 100%;
  margin: 0;
  max-width: 100%;
  text-align: center;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.35;
  color: #92400e;
  pointer-events: none;
}

.group-suggest-empty {
  flex: 1 0 100%;
  margin: 0;
  text-align: center;
  font-size: 13px;
  line-height: 1.4;
  color: var(--ink-muted);
  pointer-events: none;
}

.group-lasso {
  position: absolute;
  left: 0;
  top: 0;
  overflow: visible;
  pointer-events: none;
  width: 1px;
  height: 1px;
}

.grid {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.canvas.group-focus .grid {
  visibility: hidden;
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

.connectors.suggest-front {
  z-index: 34;
}

.connectors.drawing .suggest-hit {
  pointer-events: none;
}

.connector-hit {
  pointer-events: stroke;
  cursor: pointer;
}

.connector-hit.abandoned {
  pointer-events: none;
  cursor: default;
}

.suggest-hit {
  pointer-events: stroke;
  cursor: pointer;
}

.suggest-line {
  filter: drop-shadow(0 0 0.5px #1e3a5f);
}

.draft-line {
  filter: drop-shadow(0 0 0.5px #5b4a12);
}

/* Pale yellow needs an outline to stay readable on the paper background. */
.connector-line {
  filter: drop-shadow(0 0 0.5px #5b4a12);
}

.connector-line.faded,
.connector-line.selected {
  filter: none;
}

.suggest-wrap {
  position: absolute;
  width: max-content;
  max-width: 148px;
  transform: translate(-50%, calc(-100% - 8px));
  pointer-events: auto;
  z-index: 24;
}

.suggest-wrap.open {
  max-width: 240px;
}

.suggest-pick {
  display: inline-flex;
  align-items: flex-start;
  gap: 5px;
  box-sizing: border-box;
  width: max-content;
  max-width: 148px;
  margin: 0;
  padding: 3px 7px;
  border: 1px dashed #93c5fd;
  border-radius: 8px;
  background: var(--chrome);
  color: #1e3a5f;
  box-shadow: 0 4px 12px rgba(44, 40, 31, 0.1);
}

.suggest-wrap.open .suggest-pick {
  max-width: 240px;
}

.suggest-pick.on {
  border-style: solid;
  border-color: #60a5fa;
  background: #fff;
}

.suggest-check {
  flex: 0 0 auto;
  width: 13px;
  height: 13px;
  margin-top: 1px;
  padding: 0;
  border: 1.5px solid var(--line);
  border-radius: 3px;
  background: var(--paper);
  cursor: pointer;
}

.suggest-check.on {
  border-color: #60a5fa;
  background: #93c5fd;
  box-shadow: inset 0 0 0 2px var(--chrome);
}

.suggest-why {
  flex: 1 1 auto;
  min-width: 0;
  max-width: 124px;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1.3;
  color: var(--ink-muted);
  cursor: default;
}

.suggest-wrap.open .suggest-why {
  max-width: 214px;
  white-space: normal;
  overflow: visible;
  text-overflow: unset;
  overflow-wrap: break-word;
}

.suggest-pick.on .suggest-why {
  color: #1d4ed8;
}

.relation-wrap.abandoned {
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
  pointer-events: none;
  z-index: 20;
}

.pattern-frame {
  position: absolute;
  z-index: 0;
  pointer-events: none;
  border: 1.5px dashed #b45309;
  border-radius: 18px;
  background: transparent;
}

.pattern-frame-caption {
  position: absolute;
  top: 0;
  left: 12px;
  right: 12px;
  transform: translateY(calc(-100% - 6px));
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  max-width: calc(100% - 24px);
  pointer-events: auto;
}

.pattern-frame-kicker {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.02em;
  color: #b45309;
}

.pattern-frame-why {
  box-sizing: border-box;
  max-width: 100%;
  padding: 5px 8px;
  border: 1px dashed #b45309;
  border-radius: 8px;
  background: var(--chrome);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.35;
  color: #92400e;
  white-space: normal;
  overflow-wrap: break-word;
  word-break: normal;
}

.pattern-frame.joining {
  border-color: #92400e;
  background: transparent;
}

.pattern-frame.joining .pattern-frame-kicker {
  color: #92400e;
}

.related-why {
  position: absolute;
  z-index: 22;
  transform: translateY(-100%);
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  pointer-events: none;
  transition: left 0.38s ease, top 0.38s ease;
}

.related-why-kicker {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  letter-spacing: 0.02em;
  color: #2563eb;
}

.related-why.source .related-why-kicker {
  color: var(--ink-muted);
}

.related-why-text {
  box-sizing: border-box;
  max-width: 100%;
  padding: 4px 0 0;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.35;
  color: #1d4ed8;
  white-space: normal;
  overflow-wrap: break-word;
  word-break: normal;
}

.notes-layer > :deep(.note),
.relation-wrap,
.suggest-wrap,
.rationale-dock {
  pointer-events: auto;
}

.relation-wrap {
  position: absolute;
  transform: translate(-50%, -50%);
  pointer-events: auto;
}

.rationale-dock {
  position: absolute;
  box-sizing: border-box;
  min-width: 0;
  overflow: visible;
  padding: 10px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(44, 40, 31, 0.12);
  cursor: default;
}

.group-rationale-dock {
  overflow: visible;
}

.bottom-bar {
  position: absolute;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: max-content;
  padding: 6px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.12);
  z-index: 30;
}

.account-bar {
  position: absolute;
  top: 20px;
  right: 20px;
  display: flex;
  align-items: stretch;
  gap: 8px;
  z-index: 30;
}

.test-bar {
  position: absolute;
  top: 20px;
  left: 20px;
  display: flex;
  align-items: center;
  gap: 8px;
  z-index: 30;
  max-width: min(420px, calc(100% - 280px));
}

.test-tag {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #9a3412;
  background: #ffedd5;
  border: 1px dashed #c2410c;
  border-radius: 6px;
  padding: 3px 6px;
  white-space: nowrap;
}

.test-btn {
  min-width: 52px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 7px 10px 6px;
  border: 1px dashed #c2410c;
  border-radius: 10px;
  background: #fff7ed;
  color: #9a3412;
  cursor: pointer;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.12);
}

.test-btn:hover {
  background: #ffedd5;
  color: #7c2d12;
}

.test-hint {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1.35;
  color: var(--ink-muted);
}

.logout-btn {
  min-width: 52px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 7px 8px 6px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--chrome);
  color: var(--ink-muted);
  cursor: pointer;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.12);
}

.logout-btn:disabled {
  opacity: 0.38;
  cursor: default;
}

.logout-btn:disabled:hover {
  color: var(--ink-muted);
  background: var(--chrome);
}

.logout-btn:hover {
  color: var(--accent);
  background: var(--accent-soft);
}

.logout-btn.armed {
  color: #b91c1c;
  border-color: rgba(185, 28, 28, 0.45);
  background: #fee2e2;
}

.tool-btn {
  min-width: 60px;
  border-radius: 8px;
  border: 1.5px solid transparent;
  background: transparent;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  color: var(--ink-muted);
  transition: all 0.14s ease;
  padding: 7px 10px 6px;
}

.btn-label {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1;
  letter-spacing: 0.04em;
  white-space: nowrap;
  color: inherit;
}

.tool-btn:hover {
  background: var(--accent-soft);
  color: var(--ink);
}

.tool-btn.active {
  border-color: rgba(180, 83, 9, 0.45);
  background: var(--accent-soft);
  color: var(--accent);
}

.tool-btn:disabled {
  opacity: 0.38;
  cursor: default;
}

.tool-btn:disabled:hover {
  background: transparent;
  color: var(--ink-muted);
}

/* Same box as the 28px tool icons, so a glyph tool sits level with an SVG one. */
.sample-plus {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  font-size: 22px;
  line-height: 1;
  font-family: 'DM Mono', ui-monospace, monospace;
}

.hint {
  position: absolute;
  bottom: calc(100% + 10px);
  left: 50%;
  transform: translateX(-50%);
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 6px 16px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  color: var(--ink-muted);
  pointer-events: none;
  letter-spacing: 0.04em;
  white-space: nowrap;
}

.search-slot {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
}

.context-slot {
  position: relative;
  display: flex;
  align-items: center;
}

.context-panel {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 12px);
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(78vw, 460px);
  padding: 12px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 12px 32px rgba(44, 40, 31, 0.16);
}

.context-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.context-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
}

.context-close {
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  font-size: 15px;
  line-height: 20px;
}

.context-close:hover {
  background: var(--accent-soft);
  color: var(--ink);
}

.context-input {
  box-sizing: border-box;
  width: 100%;
  height: 180px;
  padding: 10px 12px;
  resize: none;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.55;
  outline: none;
}

.context-input:focus {
  border-color: rgba(180, 83, 9, 0.45);
}

.context-input::placeholder {
  color: var(--ink-faint);
}

.context-hint {
  margin: 0;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.03em;
  line-height: 1.4;
  color: var(--ink-faint);
}

.context-panel.dropping {
  border-color: rgba(180, 83, 9, 0.55);
  box-shadow: 0 12px 32px rgba(180, 83, 9, 0.22);
}

.context-shots {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.context-shot {
  position: relative;
  width: 64px;
  height: 64px;
  border: 1px solid var(--line);
  border-radius: 6px;
  overflow: hidden;
  background: var(--paper);
}

.context-shot img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.context-shot-drop {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: rgba(44, 40, 31, 0.66);
  color: #fff;
  font-size: 12px;
  line-height: 16px;
  cursor: pointer;
}

.context-shot-drop:hover {
  background: rgba(44, 40, 31, 0.88);
}

.context-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.context-add {
  padding: 4px 10px;
  border: 1px dashed var(--line);
  border-radius: 6px;
  background: transparent;
  color: var(--ink-muted);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  cursor: pointer;
}

.context-add:hover:not(:disabled) {
  border-color: rgba(180, 83, 9, 0.45);
  background: var(--accent-soft);
  color: var(--accent);
}

.context-add:disabled {
  opacity: 0.38;
  cursor: default;
}

.context-count {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  letter-spacing: 0.03em;
  color: var(--ink-faint);
}

.context-file {
  display: none;
}

.context-warn {
  margin: 0;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.4;
  color: var(--accent);
}

/* Questions the AI asks back about the whole project, parked on the canvas. */
.context-bubble {
  position: absolute;
  box-sizing: border-box;
  width: 230px;
  padding: 9px 24px 9px 11px;
  border: 1px solid rgba(180, 83, 9, 0.35);
  border-radius: 12px 12px 12px 3px;
  background: var(--chrome);
  box-shadow: 0 6px 18px rgba(44, 40, 31, 0.12);
  pointer-events: auto;
  z-index: 16;
}

.context-bubble.inert {
  opacity: 0.4;
}

/* The AI's reading of the design goal is the one worth answering first. */
.context-bubble.kind-goal {
  border-color: rgba(180, 83, 9, 0.75);
  border-width: 1.5px;
  background: #fffaf2;
}

.context-bubble-kind {
  display: block;
  margin-bottom: 3px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 8px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--ink-faint);
}

.context-bubble.kind-goal .context-bubble-kind {
  color: var(--accent);
}

.context-bubble-text {
  margin: 0;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.45;
  color: var(--ink);
  overflow-wrap: break-word;
}

.context-bubble-drop {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 17px;
  height: 17px;
  padding: 0;
  border: 0;
  border-radius: 5px;
  background: transparent;
  color: var(--ink-faint);
  font-size: 13px;
  line-height: 17px;
  cursor: pointer;
}

.context-bubble-drop:hover {
  background: var(--accent-soft);
  color: var(--accent);
}

.search-input {
  width: min(42vw, 320px);
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 12px;
  letter-spacing: 0.01em;
}

.search-input::placeholder {
  color: var(--ink-muted);
  opacity: 0.72;
}

.search-input:focus {
  outline: none;
  border-color: rgba(180, 83, 9, 0.45);
}

.search-results {
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(100% + 12px);
  max-height: 280px;
  overflow: auto;
  padding: 8px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.12);
  z-index: 31;
}

.search-results-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 4px 8px;
}

.search-status {
  margin: 0;
  flex: 1;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.35;
  color: var(--ink-muted);
}

.search-clear {
  border: 0;
  background: transparent;
  color: var(--accent);
  cursor: pointer;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  padding: 0;
}

.search-hit-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  width: 100%;
  margin: 0;
  padding: 7px 6px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  cursor: pointer;
}

.search-hit-row:hover,
.search-hit-row.picked {
  background: var(--accent-soft);
}

.search-check {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  margin-top: 2px;
  border: 1.5px solid var(--line);
  border-radius: 4px;
  background: var(--paper);
}

.search-check.on {
  border-color: #2563eb;
  background: #2563eb;
  box-shadow: inset 0 0 0 2px var(--chrome);
}

.search-hit-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 2px;
}

.search-hit-title,
.search-hit-why {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.search-hit-title {
  font-size: 12px;
  font-weight: 600;
}

.search-hit-why {
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  color: var(--ink-muted);
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
  color: var(--ink-faint);
  margin: 0;
  letter-spacing: 0.04em;
}
</style>

<style>
/* Force pen immediately after grabbing a mag (overrides grab on the mag itself). */
html.drawing-pen,
html.drawing-pen * {
  cursor: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24'%3E%3Cpath fill='%23fde68a' stroke='%232c281f' stroke-width='1.2' d='M3.2 21.2 5 17.8 18.6 4.2a1.5 1.5 0 0 1 2.1 2.1L7.1 20l-3.9 1.2z'/%3E%3C/svg%3E") 3 21, crosshair !important;
}
</style>

