<!--
  Repertoire prototype — infinite canvas for sticky-note brainstorming.
  Vue 3 SFC (script setup). No extra UI libraries: pan/place/select live here.
-->
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue' // Vue 3 reactivity + lifecycle
import { fetchCanvas, logEvent, logout, saveCanvas } from '../api/session'
import { patternStatsFromLabels, embedTexts, cosine, asVector } from '../api/rationale'
import StickyNoteCard from './StickyNoteCard.vue'
import StickyNoteIcon from './StickyNoteIcon.vue'
import SearchIcon from './SearchIcon.vue'
import RationaleModule from './RationaleModule.vue'
import RelationMarker from './RelationMarker.vue'
import { SEARCH_TEST_PACKS } from '../data/searchTestPacks'

const props = defineProps({
  username: { type: String, default: '' },
})
const emit = defineEmits(['signed-out'])

/** Pen nib hotspot — used while the connector tool is on. */
const PEN_CURSOR = `url("data:image/svg+xml,${encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#fde68a" stroke="#2c281f" stroke-width="1.2" d="M3.2 21.2 5 17.8 18.6 4.2a1.5 1.5 0 0 1 2.1 2.1L7.1 20l-3.9 1.2z"/></svg>',
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
const metrics = ref({}) // noteId -> { width, height } from ResizeObserver
const selectedId = ref(null)
const selectedConnId = ref(null)
const activeTool = ref(null) // null | 'sticky' | 'connect'
const draft = ref(null) // in-progress path: { fromId, fromSide, x, y }
const pan = ref({ x: 0, y: 0 })
const scale = ref(1)
const canvasRef = ref(null)
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
let searchTestPackCursor = 0

let nextId = 1
let nextConnId = 1
const panRef = { x: 0, y: 0 }
let connectMove = null
let connectUp = null

const GRID = 28
const BOARD_PAD = 28
const SEARCH_THRESHOLD = 0.28
const SEARCH_MAX = 16

const stickyActive = computed(() => activeTool.value === 'sticky') // place-note tool
const connectActive = computed(() => activeTool.value === 'connect') // mag-point connector tool

/** Moves the notes layer with the canvas pan. Scale stays at 100%. */
const notesLayerStyle = computed(() => ({
  transform: `translate(${pan.value.x}px, ${pan.value.y}px) scale(${scale.value})`,
}))

/** Distance from note edge to mag-point center (must match StickyNoteCard --mag-outset). */
const MAG_OUTSET = 18
/** Gap under the note so the rationale box sits below the bottom mag point. */
const RATIONALE_GAP = 28

/** Live note box (ResizeObserver), falling back to the stored size. */
function noteSize(note) {
  const size = metrics.value[note.id]
  return {
    width: size?.width ?? note.width ?? 168,
    height: size?.height ?? note.height ?? 168,
  }
}

/** Rationale panel sits under the note and matches its current width. */
function rationaleStyle(note) {
  const { width, height } = noteSize(note)
  return {
    left: `${note.x}px`,
    top: `${note.y + height + RATIONALE_GAP}px`,
    width: `${width}px`,
    zIndex: selectedId.value === note.id ? 19 : 1,
  }
}
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

const searchHitIds = computed(() => new Set(searchHits.value.map((item) => item.id)))
const searchPickedIds = computed(() => new Set(searchPicked.value))
const searchDimActive = computed(() => searchHits.value.length > 0)

/** Arm / disarm a tool. Sticky and connect are mutually exclusive. */
function toggleTool(name) {
  if (name === 'sticky' && searchOpen.value) closeSearch()
  activeTool.value = activeTool.value === name ? null : name
  if (activeTool.value !== 'connect') draft.value = null
}

function noteSearchBlob(note) {
  const pins = (note.pinnedLabels || [])
    .map((item) => String(item?.text || '').trim())
    .filter(Boolean)
    .slice(0, 3)
  return [note.text, note.rationaleText, ...pins]
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

function clearBoard() {
  clearArmed.value = false
  closeSearch()
  clearConnectDrag()
  notes.value = []
  connections.value = []
  metrics.value = {}
  selectedId.value = null
  selectedConnId.value = null
  activeTool.value = null
  draft.value = null
  searchVecCache.clear()
  searchTestHint.value = ''
  nextId = 1
  nextConnId = 1
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

/** DEV only: append the next sample pack. Search still embeds whatever is live. */
function addSearchTestPack() {
  if (!isDev || !SEARCH_TEST_PACKS.length) return
  const pack = SEARCH_TEST_PACKS[searchTestPackCursor % SEARCH_TEST_PACKS.length]
  searchTestPackCursor += 1
  const s = scale.value
  const originX = (BOARD_PAD - panRef.x) / s
  const originY = (BOARD_PAD - panRef.y) / s
  const cols = 4
  const cellW = 220
  const cellH = 200
  const offset = notes.value.length
  for (const [i, idea] of pack.ideas.entries()) {
    const id = nextId++
    const col = (offset + i) % cols
    const row = Math.floor((offset + i) / cols)
    notes.value.push(
      hydrateRids({
        id,
        x: originX + col * cellW + ((offset + i) % 3) * 10,
        y: originY + row * cellH + ((offset + i) % 2) * 12,
        text: idea.text,
        color: NOTE_COLORS[(id - 1) % NOTE_COLORS.length],
        width: 200,
        height: 168,
        ...emptyNoteMeta(),
        rationaleOpen: false,
        rationaleText: idea.rationale || '',
        rationaleLabels: samplePoolLabels(idea.pins),
        pinnedLabels: [],
      }),
    )
  }
  searchTestHint.value = `added “${pack.id}” (${pack.ideas.length}) · try “${pack.tryQuery}”, then add another pack and search again`
  logEvent('search_test_pack', { pack: pack.id, added: pack.ideas.length, total: notes.value.length }).catch(() => {})
}

function toggleSearch() {
  if (searchOpen.value) {
    closeSearch()
    return
  }
  activeTool.value = null
  draft.value = null
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
  const note = notes.value.find((n) => n.id === noteId)
  if (isNoteFrozen(note)) return
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

/** Mag-point mouseup: commit a path. Same sticky (any mag point) is ignored. */
function onConnectEnd(noteId, side) {
  if (!draft.value) return
  const fromNote = notes.value.find((n) => n.id === draft.value.fromId)
  const toNote = notes.value.find((n) => n.id === noteId)
  const sameNote = draft.value.fromId === noteId
  if (!sameNote && !isNoteFrozen(fromNote) && !isNoteFrozen(toNote)) {
    connections.value.push({
      id: nextConnId++,
      fromId: draft.value.fromId,
      fromSide: draft.value.fromSide,
      toId: noteId,
      toSide: side,
      ...emptyRelation(),
    })
  }
  clearConnectDrag()
  draft.value = null
}

/** Idle canvas: no selection, no connector tool, no in-progress path. Sticky place-tool stays on. */
function resetToIdle() {
  clearArmed.value = false
  clearConnectDrag()
  draft.value = null
  if (activeTool.value === 'connect') activeTool.value = null
  selectedId.value = null
  selectedConnId.value = null
  const el = document.activeElement
  if (el instanceof HTMLElement && (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
    el.blur()
  }
}

/** Empty-canvas press: return to idle, then pan if the pointer moves. Sticky tool still places on click. */
function onCanvasMouseDown(e) {
  if (stickyActive.value) return
  if (e.target.closest('.note') || e.target.closest('.relation-wrap') || e.target.closest('.rationale-dock')) return
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
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.x += dx
  note.y += dy
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
  return owner
}

/** One entry per rationale identity — the list the model checks before coining a new label. */
const labelInventory = computed(() => {
  const byRid = new Map()
  const add = (label) => {
    const text = String(label?.text || '').trim()
    if (!label?.rid || !text || byRid.has(label.rid)) return
    byRid.set(label.rid, { ref: label.rid, text })
  }
  for (const note of notes.value) (note.rationaleLabels || []).forEach(add)
  for (const conn of connections.value) (conn.rationaleLabels || []).forEach(add)
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
  for (const note of notes.value) track(`n${note.id}`, note.rationaleLabels)
  for (const conn of connections.value) track(`c${conn.id}`, conn.rationaleLabels)
  const counts = {}
  for (const [rid, keys] of owners) counts[rid] = keys.size
  return counts
})

/** Every rationale phrasing on the canvas — same note and other notes — for repeating patterns. */
const canvasLabels = computed(() => {
  const rows = []
  for (const note of notes.value) {
    for (const label of note.rationaleLabels || []) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: `n${note.id}` })
    }
  }
  for (const conn of connections.value) {
    for (const label of conn.rationaleLabels || []) {
      if (!String(label?.text || '').trim()) continue
      rows.push({ id: label.id, rid: label.rid, text: label.text, owner: `c${conn.id}` })
    }
  }
  return rows
})

const patternStats = computed(() => patternStatsFromLabels(canvasLabels.value))

/** Keep up to 3 labels attached to the side of a note. */
function setPinnedLabels(id, pinned) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.pinnedLabels = Array.isArray(pinned) ? pinned.slice(0, 3) : []
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
  note.rationaleLabels = Array.isArray(labels) ? labels : []
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
}

function updateAbandonRationale(id, payload) {
  const note = notes.value.find((n) => n.id === id)
  if (!note) return
  note.abandonText = payload.input || ''
  note.abandonLabels = Array.isArray(payload.labels) ? payload.labels : []
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
}

function updateRelationAbandonRationale(id, payload) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.abandonText = payload.input || ''
  conn.abandonLabels = Array.isArray(payload.labels) ? payload.labels : []
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
  logEvent('pin_change', { connectionId: id, pinned: conn.pinnedLabels }).catch(() => {})
}

function updateRelationLabels(id, labels) {
  const conn = connections.value.find((c) => c.id === id)
  if (!conn) return
  conn.rationaleLabels = Array.isArray(labels) ? labels : []
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
  conn.rationaleLabels = Array.isArray(payload.labels) ? payload.labels : []
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

/** Esc: drop draft, then cancel tool / deselect. N: toggle sticky tool. */
function onKeydown(e) {
  const typing = isTypingTarget(e.target)
  if (e.key === 'Escape') {
    if (clearArmed.value) {
      clearArmed.value = false
      return
    }
    if (searchOpen.value) {
      closeSearch()
      return
    }
    if (draft.value) {
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
  if ((e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey && !e.altKey) {
    e.preventDefault()
    toggleTool('sticky')
  }
}

function updateRationale(id, payload) {
  const note = notes.value.find((n) => n.id === id)
  if (!note || isNoteFrozen(note)) return
  note.rationaleText = payload.input || ''
  note.rationaleLabels = Array.isArray(payload.labels) ? payload.labels : []
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
      rationaleLabels: note.rationaleLabels || [],
      abandoned: Boolean(note.abandoned),
      abandonOpen: Boolean(note.abandonOpen),
      abandonText: note.abandonText || '',
      abandonLabels: note.abandonLabels || [],
      abandonPinned: note.abandonPinned || [],
      frozenX: note.frozenX,
      frozenY: note.frozenY,
      frozenWidth: note.frozenWidth,
      frozenHeight: note.frozenHeight,
    })),
    connections: connections.value,
    pan: pan.value,
    scale: scale.value,
    nextId,
    nextConnId,
  }
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

/** Register / drop keyboard shortcuts (N, Esc). Load this participant's canvas. */
onMounted(async () => {
  window.addEventListener('keydown', onKeydown)
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
  } catch {
    notes.value = []
  }
  loaded = true
  await nextTick()
  window.addEventListener('resize', onWindowResize)
  watch(notes, scheduleSave, { deep: true })
  watch(connections, scheduleSave, { deep: true })
  watch(pan, scheduleSave, { deep: true })
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  window.removeEventListener('resize', onWindowResize)
  wheelTarget?.removeEventListener('wheel', onWheel)
  wheelTarget = null
  clearConnectDrag()
  clearTimeout(saveTimer)
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
        <svg class="connectors" overflow="visible" aria-hidden="true">
          <g v-for="line in renderedConnections" :key="line.id">
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
              :stroke="line.faded ? 'rgba(44, 40, 31, 0.12)' : selectedConnId === line.id ? '#2563eb' : 'rgba(44, 40, 31, 0.45)'"
              :stroke-width="selectedConnId === line.id && !line.abandoned ? 3 : 2.5"
              stroke-linecap="round"
            />
          </g>
          <path
            v-if="draftPath"
            :d="draftPath"
            fill="none"
            stroke="#b45309"
            stroke-width="2.5"
            stroke-linecap="round"
            stroke-dasharray="6 5"
          />
        </svg>
        <div
          v-for="line in renderedConnections"
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
            :selected="selectedConnId === line.id && !line.abandoned"
            :abandoned="Boolean(line.abandoned)"
            @open-rationale="openRelationRationale(line.id)"
            @toggle-rationale="toggleRelationRationale(line.id)"
            @pin-change="(pinned) => setRelationPinned(line.id, pinned)"
            @labels-change="(labels) => updateRelationLabels(line.id, labels)"
          />
        </div>
        <template v-for="note in notes" :key="note.id">
          <StickyNoteCard
            :note="note"
            :scale="scale"
            :selected="selectedId === note.id"
            :connect-mode="connectActive"
            :mag-outset="MAG_OUTSET"
            :drafting="!!draft"
            :draft-from-id="draft?.fromId ?? null"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :search-hit="searchHitIds.has(note.id)"
            :search-picked="searchPickedIds.has(note.id)"
            :search-dim="searchDimActive"
            @move="moveNote"
            @text-change="changeText"
            @select="selectNote"
            @color-change="changeColor"
            @resize="resizeNote"
            @metrics="setMetrics"
            @connect-start="onConnectStart"
            @connect-end="onConnectEnd"
            @request-connect="toggleTool('connect')"
            @suggest-relations="(id) => logEvent('suggest_relations', { noteId: id })"
            @request-delete="requestDeleteNote"
            @toggle-rationale="toggleRationale"
            @open-rationale="openRationale"
            @pin-change="(pinned) => setPinnedLabels(note.id, pinned)"
            @labels-change="(labels) => updateLabels(note.id, labels)"
            @revive="reviveNote"
          />
          <div
            v-if="note.abandonOpen && !note.abandoned"
            class="rationale-dock"
            :style="rationaleStyle(note)"
            @pointerdown.stop="onRationalePointer(note.id)"
            @mousedown.stop="onRationalePointer(note.id)"
            @click.stop
          >
            <RationaleModule
              :key="`abandon-${note.id}`"
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
              :preset-labels="[presetAbandonLabel(note.id)]"
              placeholder="Tell me why abandon this idea…"
              action-label="just abandon it"
              complete-on-generate
              @pin-change="(pinned) => setAbandonPinned(note.id, pinned)"
              @rationale-change="(payload) => updateAbandonRationale(note.id, payload)"
              @labels="() => logEvent('abandon_labels_generated', { noteId: note.id })"
              @action="confirmAbandon(note.id)"
              @complete="confirmAbandon(note.id)"
            />
          </div>
          <div
            v-show="note.rationaleOpen && !note.abandoned && !note.abandonOpen"
            class="rationale-dock"
            :style="rationaleStyle(note)"
            @pointerdown.stop="onRationalePointer(note.id)"
            @mousedown.stop="onRationalePointer(note.id)"
            @click.stop
          >
            <RationaleModule
              :key="note.id"
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
              @pin-change="(pinned) => setPinnedLabels(note.id, pinned)"
              @rationale-change="(payload) => updateRationale(note.id, payload)"
              @labels="() => logEvent('labels_generated', { noteId: note.id })"
            />
          </div>
        </template>
        <template v-for="line in renderedConnections" :key="`rel-abandon-${line.id}`">
          <div
            v-if="line.abandonOpen && !line.abandoned"
            class="rationale-dock"
            :style="relationRationaleStyle(line)"
            @pointerdown.stop="onRelationPointer(line.id)"
            @mousedown.stop="onRelationPointer(line.id)"
            @click.stop
          >
          <RationaleModule
            :key="`rel-abandon-${line.id}`"
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
            :preset-labels="[presetAbandonLabel(`rel-${line.id}`)]"
            placeholder="Tell me why abandon this relation…"
            action-label="just abandon it"
            complete-on-generate
            @pin-change="(pinned) => setRelationAbandonPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationAbandonRationale(line.id, payload)"
            @labels="() => logEvent('relation_abandon_labels_generated', { connectionId: line.id })"
            @action="confirmAbandonRelation(line.id)"
            @complete="confirmAbandonRelation(line.id)"
          />
          </div>
        </template>
        <div
          v-for="line in renderedConnections"
          v-show="line.rationaleOpen && !line.abandoned && !line.abandonOpen"
          :key="`rel-dock-${line.id}`"
          class="rationale-dock"
          :style="relationRationaleStyle(line)"
          @pointerdown.stop="onRelationPointer(line.id)"
          @mousedown.stop="onRelationPointer(line.id)"
          @click.stop
        >
          <RationaleModule
            :key="`rel-${line.id}`"
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
            placeholder="Tell me about how they relates…"
            @pin-change="(pinned) => setRelationPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationRationale(line.id, payload)"
            @labels="() => logEvent('labels_generated', { connectionId: line.id })"
          />
        </div>
      </div>

      <!-- Account: floating clear + logout -->
      <div class="account-bar" @mousedown.stop @click.stop>
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

      <!-- FigJam-style tool well: sticky note only -->
      <div class="bottom-bar" @mousedown.stop @click.stop>
        <div v-if="stickyActive" class="hint">click anywhere to place a note · esc to cancel</div>
        <div v-else-if="searchTestHint" class="hint">{{ searchTestHint }}</div>
        <div v-else-if="searchOpen && !searchHits.length && !searchError" class="hint">press enter to look up · esc to close</div>
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
        <button
          v-if="isDev"
          type="button"
          class="tool-btn"
          title="DEV: append a sample pack. Look-up embeds whatever is on this board, not this file."
          @click="addSearchTestPack"
        >
          <span class="sample-plus">+</span>
          <span class="btn-label">add samples</span>
        </button>
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
  cursor: grab;
  touch-action: none;
  overscroll-behavior: none;
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

.connector-hit {
  pointer-events: stroke;
  cursor: pointer;
}

.connector-hit.abandoned {
  pointer-events: none;
  cursor: default;
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
}

.notes-layer > :deep(.note),
.relation-wrap,
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
  padding: 10px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(44, 40, 31, 0.12);
  cursor: default;
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

.sample-plus {
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

