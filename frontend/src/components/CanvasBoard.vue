<!--
  Repertoire prototype — infinite canvas for sticky-note brainstorming.
  Vue 3 SFC (script setup). No extra UI libraries: pan/place/select live here.
-->
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue' // Vue 3 reactivity + lifecycle
import { fetchCanvas, logEvent, logout, saveCanvas } from '../api/session'
import { patternStatsFromLabels, persistedRationaleLabels, embedTexts, cosine, asVector, suggestLinks, clusterSimilarLabels } from '../api/rationale'
import StickyNoteCard from './StickyNoteCard.vue'
import StickyNoteIcon from './StickyNoteIcon.vue'
import GroupIcon from './GroupIcon.vue'
import SearchIcon from './SearchIcon.vue'
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
const activeTool = ref(null) // null | 'sticky' | 'connect' | 'group'
const groupStroke = ref([]) // canvas-space points while the lasso is being drawn
const groupHint = ref('')
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
let searchTestPackCursor = 0
let suggestAbort = null

let nextId = 1
let nextConnId = 1
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

function liveNotePos(note) {
  if (!note) return { x: 0, y: 0 }
  const visitor = patternGather.value?.visitors.find((item) => item.id === note.id)
  if (visitor) return { x: visitor.to.x, y: visitor.to.y }
  return { x: note.x, y: note.y }
}

/** Rationale panel sits under the note and matches its current width. */
function rationaleStyle(note) {
  const { width, height } = noteSize(note)
  const pos = liveNotePos(note)
  return {
    left: `${pos.x}px`,
    top: `${pos.y + height + RATIONALE_GAP}px`,
    width: `${width}px`,
    zIndex: selectedId.value === note.id ? 19 : 1,
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
  if (activeTool.value === 'group' && next !== 'group') clearGroupStroke()
  if (next === 'group') {
    if (patternGather.value) endPatternGather(false)
    groupHint.value = ''
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
  const from = noteSize(fromNote)
  const to = noteSize(toNote)
  const dx = toNote.x + to.width / 2 - (fromNote.x + from.width / 2)
  const dy = toNote.y + to.height / 2 - (fromNote.y + from.height / 2)
  if (Math.abs(dx) >= Math.abs(dy)) {
    return dx >= 0 ? ['right', 'left'] : ['left', 'right']
  }
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
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
      { signal },
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

function toggleSuggestedLink(key) {
  suggestedLinks.value = suggestedLinks.value.map((link) =>
    link.key === key ? { ...link, picked: !link.picked } : link,
  )
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
  if (!connectActive.value || isNoteFrozen(note) || isAbandoned(note)) return false
  if (selectedId.value === note.id) return true
  return Boolean(draft.value) && magHoverId.value === note.id
}

/** Drop window listeners used while rubber-banding a connector. */
function clearConnectDrag() {
  if (connectMove) window.removeEventListener('mousemove', connectMove)
  if (connectUp) window.removeEventListener('mouseup', connectUp)
  connectMove = null
  connectUp = null
  magHoverId.value = null
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
  if (activeTool.value === 'group') {
    clearGroupStroke()
    groupHint.value = ''
    activeTool.value = null
  }
  selectedId.value = null
  selectedConnId.value = null
  if (patternGather.value) endPatternGather(false)
  clearSuggestions()
  const el = document.activeElement
  if (el instanceof HTMLElement && (el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'INPUT')) {
    el.blur()
  }
}

/** Empty-canvas press: return to idle, then pan if the pointer moves. Sticky / group tools still handle the click. */
function onCanvasMouseDown(e) {
  if (stickyActive.value || groupActive.value) return
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
  if (groupActive.value) return
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
  if (gather && patternDragId.value === id) {
    const member = gather.visitors.some((item) => item.id === id)
    if (member && isOutsidePatternGroup(id)) ejectPatternVisitor(id)
    else if (!member && isInsidePatternGroup(id)) admitPatternVisitor(id)
  }
  patternDragId.value = null
  patternAdmitFrom = null
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
  return dir
})

/** Preview: ideas linked by overlapping patterns compact into one suggested group. */
const patternGather = ref(null) // { sourceId, memberKey, visitors: [{ id, title, from, to, picked }] }
const patternAnimatingIds = ref(new Set())
const patternDragId = ref(null)
let patternAdmitFrom = null
let patternAnimTimer = 0
const patternHitIds = computed(() => new Set((patternGather.value?.visitors || []).map((item) => item.id)))
const patternPickedIds = computed(
  () => new Set((patternGather.value?.visitors || []).filter((item) => item.picked).map((item) => item.id)),
)
const patternDimActive = computed(() => Boolean(patternGather.value))
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
  return noteOnGroupStage(line.fromId) && noteOnGroupStage(line.toId)
}

function memberSetKey(ids) {
  return [...ids].map(Number).sort((a, b) => a - b).join(',')
}

/** Pairwise pattern links only — so A–B and A–C become one group without swallowing every 5-idea cluster. */
function pairwisePatternAdjacency() {
  const edges = new Map()
  const add = (a, b) => {
    if (a === b) return
    if (!edges.has(a)) edges.set(a, new Set())
    edges.get(a).add(b)
  }
  for (const stats of Object.values(patternStats.value?.byRid || {})) {
    const ids = [...new Set(
      (stats.ownerKeys || [])
        .filter((key) => String(key).startsWith('n'))
        .map((key) => Number(String(key).slice(1)))
        .filter((id) => Number.isFinite(id)),
    )]
    if (ids.length !== 2) continue
    add(ids[0], ids[1])
    add(ids[1], ids[0])
  }
  return edges
}

function expandPatternNeighborhood(seedIds) {
  const seeds = [...new Set(seedIds.filter((id) => id != null))]
  const adj = pairwisePatternAdjacency()
  const seen = new Set(seeds)
  const queue = [...seeds]
  while (queue.length && seen.size < 6) {
    const id = queue.shift()
    for (const next of adj.get(id) || []) {
      if (seen.has(next)) continue
      seen.add(next)
      queue.push(next)
      if (seen.size >= 6) break
    }
  }
  return [...seen]
}

/** Right-edge tabs (and the count badge) sit outside the note box. */
const TAB_MAX_W = 160
const TAB_BADGE_PAD = 16
const TAB_PLUS_W = 28
const TAB_LINE_H = 31
const CLUSTER_GAP = 28

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
function placeCluster(memberNotes) {
  const gap = CLUSTER_GAP
  const n = memberNotes.length
  const cols = n <= 3 ? n : Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const sizes = memberNotes.map((note) => noteSize(note))
  const padX = memberNotes.map((note) => tabClearX(note))
  const padY = memberNotes.map((note) => tabClearY(note))
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
  const rowH = rowBody.map((h, r) => h + rowTop[r])
  const gridW = colW.reduce((sum, w) => sum + w, 0) + gap * Math.max(0, cols - 1)
  const gridH = rowH.reduce((sum, h) => sum + h, 0) + gap * Math.max(0, rows - 1)
  let cx = 0
  let cy = 0
  for (const note of memberNotes) {
    const size = noteSize(note)
    cx += note.x + size.width / 2
    cy += note.y + size.height / 2
  }
  cx /= n
  cy /= n
  const slots = []
  let y = cy - gridH / 2
  for (let r = 0; r < rows; r++) {
    let x = cx - gridW / 2
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

function panToSlots(slots) {
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
  return noteVisualBox(note, liveNotePos(note))
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
  if (!patternGather.value?.visitors.some((item) => item.id === id)) return null
  return isOutsidePatternGroup(id) ? id : null
})

const patternJoiningId = computed(() => {
  const id = patternDragId.value
  if (id == null || !patternGather.value) return null
  if (patternGather.value.visitors.some((item) => item.id === id)) return null
  return isInsidePatternGroup(id) ? id : null
})

const patternFrameStyle = computed(() => {
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

/** Shared rationale patterns among the gathered notes — why they sit in this frame. */
const patternGroupWhy = computed(() => {
  const gather = patternGather.value
  if (!gather?.visitors.length || gather.origin === 'lasso') return ''
  const members = new Set(gather.visitors.map((item) => `n${item.id}`))
  const rows = canvasLabels.value.filter((row) => members.has(row.owner))
  const shared = clusterSimilarLabels(rows)
    .map((group) => {
      const owners = new Set(group.members.map((item) => item.owner).filter(Boolean))
      return { text: String(group.preview || '').trim(), owners: owners.size }
    })
    .filter((item) => item.text && item.owners >= 2)
    .sort((a, b) => b.owners - a.owners || a.text.localeCompare(b.text))
  return shared.slice(0, 3).map((item) => item.text).join(' · ')
})

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

function beginGather(memberNotes, { sourceId = null, origin = 'pattern' } = {}) {
  const sorted = [...memberNotes].sort((a, b) => a.id - b.id)
  if (sorted.length < 2) return false
  endPatternGather(false)
  const slots = clusterSlots(sorted)
  const sid = sourceId ?? sorted[0].id
  patternGather.value = {
    sourceId: sid,
    memberKey: memberSetKey(sorted.map((note) => note.id)),
    origin,
    visitors: sorted.map((note, i) => ({
      id: note.id,
      title: String(note.text || '').trim() || 'untitled idea',
      from: { x: note.x, y: note.y },
      to: { x: slots[i].x, y: slots[i].y },
      picked: false,
    })),
  }
  selectedId.value = sid
  selectedConnId.value = null
  panToSlots(slots)
  return true
}

function inspectPattern(places, sourceId) {
  const incoming = Array.isArray(places) ? places : []
  const seedIds = incoming.filter((item) => item.kind === 'note').map((item) => item.id)
  if (sourceId != null) seedIds.push(sourceId)
  if (!seedIds.length) {
    endPatternGather(false)
    return
  }
  const memberIds = expandPatternNeighborhood(seedIds)
  const memberNotes = memberIds
    .map((id) => notes.value.find((note) => note.id === id))
    .filter((note) => note && !isNoteFrozen(note) && !isAbandoned(note))
    .sort((a, b) => a.id - b.id)
  if (memberNotes.length < 2) {
    endPatternGather(false)
    return
  }
  const key = memberSetKey(memberNotes.map((note) => note.id))
  if (patternGather.value?.memberKey === key) {
    if (patternGather.value.sourceId === sourceId) {
      endPatternGather(false)
      return
    }
    patternGather.value.sourceId = sourceId
    selectedId.value = sourceId
    return
  }
  beginGather(memberNotes, { sourceId, origin: 'pattern' })
  logEvent('pattern_gather', { sourceId, visitors: memberNotes.map((item) => item.id) }).catch(() => {})
}

function keepPatternGroup() {
  const gather = patternGather.value
  if (!gather) return
  if (!gather.visitors.some((item) => item.picked)) {
    for (const visitor of gather.visitors) visitor.picked = true
  }
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
    if (searchOpen.value) {
      closeSearch()
      return
    }
    if (groupActive.value || groupStroke.value.length) {
      clearGroupStroke()
      groupHint.value = ''
      activeTool.value = null
      return
    }
    if (patternGather.value) {
      endPatternGather(false)
      return
    }
    if (suggestedLinks.value.length || suggestLoading.value || suggestError.value || connectActive.value) {
      clearSuggestions()
      activeTool.value = null
      draft.value = null
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
  } catch {
    notes.value = []
  }
  loaded = true
  history.seed(historyState())
  history.setReady(true)
  await nextTick()
  window.addEventListener('resize', onWindowResize)
  watch(
    [notes, connections],
    () => {
      scheduleSave()
      history.noteChange(historyState())
    },
    { deep: true },
  )
  watch(pan, scheduleSave, { deep: true })
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
  clearTimeout(saveTimer)
  window.clearTimeout(patternAnimTimer)
  suggestAbort?.abort()
})
</script>

<template>
  <div class="board">
    <div
      ref="canvasRef"
      class="canvas"
      :class="{ placing: stickyActive, connecting: connectActive, grouping: groupActive, 'group-focus': patternDimActive }"
      :style="connectActive || groupActive ? { cursor: PEN_CURSOR } : undefined"
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
              :stroke="line.faded ? 'rgba(44, 40, 31, 0.12)' : selectedConnId === line.id ? '#2563eb' : 'rgba(44, 40, 31, 0.45)'"
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
              @mousedown.stop.prevent="toggleSuggestedLink(line.key)"
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
            <span class="pattern-frame-kicker">{{ patternJoiningId ? 'drop to add' : patternGather?.origin === 'lasso' ? 'your group' : 'suggested group' }}</span>
            <span v-if="patternGroupWhy && !patternJoiningId" class="pattern-frame-why">{{ patternGroupWhy }}</span>
          </div>
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
            @inspect-pattern="(places) => inspectPattern(places, line.fromId)"
          />
        </div>
        <div
          v-for="line in renderedSuggestions"
          v-show="!patternDimActive"
          :key="`sug-chip-${line.key}`"
          class="suggest-wrap"
          :style="{
            left: `${line.mid.x}px`,
            top: `${line.mid.y}px`,
            zIndex: 24,
          }"
          @pointerdown.stop
          @mousedown.stop
        >
          <button
            type="button"
            class="suggest-pick"
            :class="{ on: line.picked }"
            :title="line.picked ? 'Unselect this suggested link' : 'Select this suggested link'"
            @click.stop="toggleSuggestedLink(line.key)"
          >
            <span class="suggest-check" :class="{ on: line.picked }" />
            <span class="suggest-why">{{ line.why }}</span>
          </button>
        </div>
        <template v-for="note in notes" :key="note.id">
          <StickyNoteCard
            :note="note"
            :scale="scale"
            :selected="selectedId === note.id"
            :connect-mode="noteShowsMag(note)"
            :mag-outset="MAG_OUTSET"
            :drafting="!!draft"
            :draft-from-id="draft?.fromId ?? null"
            :rid-owners="ridOwners"
            :pattern-stats="patternStats"
            :canvas-labels="canvasLabels"
            :owner-directory="ownerDirectory"
            :display-x="liveNotePos(note).x"
            :display-y="liveNotePos(note).y"
            :gathering="patternHitIds.has(note.id) || patternAnimatingIds.has(note.id)"
            :leaving-group="patternLeavingId === note.id"
            :joining-group="patternJoiningId === note.id"
            :search-hit="searchHitIds.has(note.id) || patternHitIds.has(note.id) || patternJoiningId === note.id"
            :search-picked="searchPickedIds.has(note.id) || patternPickedIds.has(note.id)"
            :search-dim="searchDimActive"
            :stage-hidden="patternDimActive && !noteOnGroupStage(note.id)"
            :suggesting="suggestLoading && suggestSourceId === note.id"
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
            @inspect-pattern="(places) => inspectPattern(places, note.id)"
            @revive="reviveNote"
          />
          <div
            v-if="note.abandonOpen && !note.abandoned && noteOnGroupStage(note.id)"
            class="rationale-dock"
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
              :preset-labels="[presetAbandonLabel(note.id)]"
              placeholder="Tell me why abandon this idea…"
              action-label="just abandon it"
              complete-on-generate
              @pin-change="(pinned) => setAbandonPinned(note.id, pinned)"
              @rationale-change="(payload) => updateAbandonRationale(note.id, payload)"
              @labels="() => logEvent('abandon_labels_generated', { noteId: note.id })"
              @inspect-pattern="(places) => inspectPattern(places, note.id)"
              @action="confirmAbandon(note.id)"
              @complete="confirmAbandon(note.id)"
            />
          </div>
          <div
            v-show="note.rationaleOpen && !note.abandoned && !note.abandonOpen && noteOnGroupStage(note.id)"
            class="rationale-dock"
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
              @pin-change="(pinned) => setPinnedLabels(note.id, pinned)"
              @rationale-change="(payload) => updateRationale(note.id, payload)"
              @labels="() => logEvent('labels_generated', { noteId: note.id })"
              @inspect-pattern="(places) => inspectPattern(places, note.id)"
            />
          </div>
        </template>
        <template v-for="line in renderedConnections" :key="`rel-abandon-${line.id}`">
          <div
            v-if="line.abandonOpen && !line.abandoned && connectionOnGroupStage(line)"
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
            :preset-labels="[presetAbandonLabel(`rel-${line.id}`)]"
            placeholder="Tell me why abandon this relation…"
            action-label="just abandon it"
            complete-on-generate
            @pin-change="(pinned) => setRelationAbandonPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationAbandonRationale(line.id, payload)"
            @labels="() => logEvent('relation_abandon_labels_generated', { connectionId: line.id })"
            @inspect-pattern="(places) => inspectPattern(places, line.fromId)"
            @action="confirmAbandonRelation(line.id)"
            @complete="confirmAbandonRelation(line.id)"
          />
          </div>
        </template>
        <div
          v-for="line in renderedConnections"
          v-show="line.rationaleOpen && !line.abandoned && !line.abandonOpen && connectionOnGroupStage(line)"
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
            placeholder="Tell me about how they relates…"
            @pin-change="(pinned) => setRelationPinned(line.id, pinned)"
            @rationale-change="(payload) => updateRelationRationale(line.id, payload)"
            @labels="() => logEvent('labels_generated', { connectionId: line.id })"
            @inspect-pattern="(places) => inspectPattern(places, line.fromId)"
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
        @pointerdown="onGroupPointerDown"
        @pointermove="onGroupPointerMove"
        @pointerup="onGroupPointerUp"
        @pointercancel="onGroupPointerUp"
      />

      <!-- FigJam-style tool well: sticky note only -->
      <div class="bottom-bar" @mousedown.stop @click.stop>
        <div v-if="stickyActive" class="hint">click anywhere to place a note · esc to cancel</div>
        <div v-else-if="groupActive && groupHint" class="hint">{{ groupHint }}</div>
        <div v-else-if="groupActive" class="hint">draw around ideas to group them · esc to cancel</div>
        <div v-else-if="suggestError" class="hint">{{ suggestError }} · or drag a mag point to draw your own</div>
        <div v-else-if="suggestLoading" class="hint">looking for related ideas… · you can still drag a mag point</div>
        <div v-else-if="suggestedLinks.length" class="hint">blue dashed = AI · yellow dashed = you drawing · click a blue line to keep it</div>
        <div v-else-if="connectActive" class="hint">drag a mag point on this note · other notes show mag points when the pen is close</div>
        <div v-else-if="patternGather" class="hint">{{ patternGather.origin === 'lasso' ? 'your group · drag an idea out or drop one in · keep group · esc cancels' : 'dashed frame = suggested group · labels on the frame are why they sit together · drag an idea out or drop one in · keep group · esc cancels' }}</div>
        <div v-else-if="searchOpen && !searchHits.length && !searchError" class="hint">press enter to look up · esc to close</div>
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
          :title="patternPickedCount ? 'Keep the ticked ideas in the group' : 'Keep the suggested group together'"
          @click="keepPatternGroup"
        >
          <span class="sample-plus">✓</span>
          <span class="btn-label">{{ patternPickedCount ? `keep ${patternPickedCount}` : 'keep group' }}</span>
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
  cursor: grab;
  touch-action: none;
  overscroll-behavior: none;
}

.canvas.placing,
.canvas.grouping {
  cursor: crosshair;
}

.group-lasso-layer {
  position: absolute;
  inset: 0;
  z-index: 25;
  touch-action: none;
  cursor: inherit;
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

.suggest-wrap {
  position: absolute;
  width: max-content;
  max-width: 260px;
  transform: translate(-50%, -50%);
  pointer-events: auto;
  z-index: 24;
}

.suggest-pick {
  display: inline-flex;
  align-items: flex-start;
  gap: 6px;
  box-sizing: border-box;
  width: max-content;
  max-width: 260px;
  margin: 0;
  padding: 6px 9px;
  border: 1px dashed #93c5fd;
  border-radius: 10px;
  background: var(--chrome);
  color: #1e3a5f;
  box-shadow: 0 8px 20px rgba(44, 40, 31, 0.12);
  cursor: pointer;
}

.suggest-pick.on {
  border-style: solid;
  border-color: #60a5fa;
  background: rgba(147, 197, 253, 0.28);
}

.suggest-check {
  flex: 0 0 auto;
  width: 13px;
  height: 13px;
  margin-top: 1px;
  border: 1.5px solid var(--line);
  border-radius: 4px;
  background: var(--paper);
}

.suggest-check.on {
  border-color: #60a5fa;
  background: #93c5fd;
  box-shadow: inset 0 0 0 2px var(--chrome);
}

.suggest-why {
  flex: 0 1 auto;
  min-width: 9em;
  max-width: 220px;
  text-align: left;
  white-space: normal;
  overflow-wrap: break-word;
  word-break: normal;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  line-height: 1.35;
  color: var(--ink-muted);
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
  overflow: hidden;
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

