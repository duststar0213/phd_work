<!--
  One sticky note: select, drag, type, resize, recolor, connect.
  Vue 3 SFC. Native APIs: contenteditable, ResizeObserver. ColorWheel uses Canvas 2D.
-->
<script setup>
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue' // Vue 3
import {
  asVector,
  cachedDraftVector,
  clipUserLabel,
  clusterSimilarLabels,
  pairPatternHint,
  embedTexts,
  isSimilarLabel,
  LIVE_EMBED_MS,
  MAX_USER_LABEL_CHARS,
  needsShortLabel,
  SHORTEN_PAUSE_MS,
  suggestShortLabel,
  meaningHitsFromVectors,
  readyForLiveEmbed,
  rememberDraftVector,
} from '../api/rationale'
import ColorWheel from './ColorWheel.vue'
import ConnectionIcon from './ConnectionIcon.vue'
import SuggestRelationIcon from './SuggestRelationIcon.vue'

const MIN_SIZE = 120 // smallest width/height from corner resize; keeps room for the edge tabs
const BASE_FONT_PX = 13
const MAX_FONT_PX = 40
const SIDES = ['top', 'right', 'bottom', 'left']
const PLACEHOLDER = 'define idea here' // grey hint on empty notes; hidden while typing
const MAX_PINNED = 3

const props = defineProps({
  note: { type: Object, required: true },
  scale: { type: Number, default: 1 },
  selected: { type: Boolean, default: false },
  connectMode: { type: Boolean, default: false },
  magOutset: { type: Number, default: 18 }, // px from note edge to mag-point center
  drafting: { type: Boolean, default: false }, // true while dragging a connector from a mag point
  draftFromId: { type: [Number, String], default: null }, // origin note; same-note mag points cannot drop
  ridOwners: { type: Object, default: () => ({}) }, // rid -> how many ideas / relations invoke it
  patternStats: { type: Object, default: () => ({}) },
  canvasLabels: { type: Array, default: () => [] },
})

/** How many ideas share this rationale. 2+ earns a badge on the tab. */
function recurrence(label) {
  return Number(props.ridOwners?.[label?.rid]) || 0
}

function labelPattern(label) {
  return props.patternStats?.byRid?.[label?.rid] || null
}

function groupPatternHint(group) {
  const stats = group.members.map((item) => labelPattern(item)).find((item) => item) || null
  return pairPatternHint(group.members.length, stats)
}

const emit = defineEmits(['move', 'textChange', 'select', 'colorChange', 'resize', 'metrics', 'connectStart', 'connectEnd', 'requestConnect', 'suggestRelations', 'toggleRationale', 'openRationale', 'pin-change', 'labels-change', 'revive'])

const textRef = ref(null)
const noteRef = ref(null)
const colorOpen = ref(false)
const dragging = ref(false)
const resizing = ref(false)
const textFocused = ref(false) // hide placeholder while the caret is in the note
const editing = ref(false) // selected ≠ editing; Delete abandons unless the caret is in the text
let didDrag = false // click vs drag: click selects; second click / empty note edits

const CORNERS = ['nw', 'ne', 'sw', 'se']

let resizeObserver = null
let dragCleanup = null
let resizeCleanup = null

const frozen = computed(() => Boolean(props.note.abandoned || props.note.abandonOpen))

// Abandoned notes are frozen, but still answer to hover + right-click so they can be revived.
const hovered = ref(false)
const menuOpen = ref(false)
const hintPos = ref({ x: 0, y: 0 })
const menuPos = ref({ x: 0, y: 0 })
const showReviveHint = computed(() => props.note.abandoned && hovered.value && !menuOpen.value)

/** Sync initial text + watch the note box so mag points follow wrap/resize. */
onMounted(() => {
  if (textRef.value) {
    textRef.value.innerText = props.note.text
  }
  if (!noteRef.value) return
  resizeObserver = new ResizeObserver(() => {
    if (!noteRef.value || frozen.value) return
    emit('metrics', props.note.id, {
      width: noteRef.value.offsetWidth,
      height: noteRef.value.offsetHeight,
    })
    layoutTabs()
  })
  resizeObserver.observe(noteRef.value)
  relayoutTabs()
  nextTick(fitIdeaText)
})

onUnmounted(() => {
  liveEmbedAbort?.abort()
  window.clearTimeout(liveEmbedTimer)
  shortAbort?.abort()
  window.clearTimeout(shortTimer)
  resizeObserver?.disconnect()
  dragCleanup?.()
  resizeCleanup?.()
  window.removeEventListener('mousedown', onMenuOutside, true)
  window.removeEventListener('keydown', onMenuKeydown)
  window.removeEventListener('wheel', closeReviveMenu)
})

watch(frozen, (isFrozen) => {
  if (!isFrozen) return
  dragCleanup?.()
  resizeCleanup?.()
  dragging.value = false
  resizing.value = false
  colorOpen.value = false
  creating.value = false
  stopTextEdit()
})

watch(
  () => props.note.abandoned,
  (abandoned) => {
    if (!abandoned) closeReviveMenu()
  },
)

/** Park the hint just above the ghost so it stays legible at any zoom. */
function trackHint() {
  const rect = noteRef.value?.getBoundingClientRect()
  if (!rect) return
  hintPos.value = { x: rect.left + rect.width / 2, y: rect.top }
}

function onNoteEnter() {
  if (!props.note.abandoned) return
  hovered.value = true
  trackHint()
}

function onNoteMove() {
  if (!hovered.value) return
  trackHint()
}

function onNoteLeave() {
  hovered.value = false
}

function onContextMenu(e) {
  if (!props.note.abandoned) return
  e.preventDefault()
  e.stopPropagation()
  menuPos.value = { x: e.clientX, y: e.clientY }
  menuOpen.value = true
}

function closeReviveMenu() {
  menuOpen.value = false
}

function onReviveClick() {
  closeReviveMenu()
  hovered.value = false
  emit('revive', props.note.id)
}

function onMenuOutside(e) {
  if (e.target instanceof HTMLElement && e.target.closest('.revive-menu')) return
  closeReviveMenu()
}

function onMenuKeydown(e) {
  if (e.key === 'Escape') closeReviveMenu()
}

watch(menuOpen, (open) => {
  if (open) {
    window.addEventListener('mousedown', onMenuOutside, true)
    window.addEventListener('keydown', onMenuKeydown)
    window.addEventListener('wheel', closeReviveMenu, { passive: true })
    return
  }
  window.removeEventListener('mousedown', onMenuOutside, true)
  window.removeEventListener('keydown', onMenuKeydown)
  window.removeEventListener('wheel', closeReviveMenu)
})

watch(
  () => props.selected,
  (selected) => {
    if (!selected) {
      colorOpen.value = false
      stopTextEdit()
      return
    }
    if (!String(props.note.text || '').trim() && !props.note.abandoned) startTextEdit()
  },
  { immediate: true },
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

const ideaFontPx = ref(BASE_FONT_PX)

/** True when this font still fits inside the resized note box. */
function ideaTextFits(px, targetH) {
  const el = textRef.value
  if (!el) return true
  const style = el.style
  const saved = {
    fontSize: style.fontSize,
    flex: style.flex,
    minHeight: style.minHeight,
    height: style.height,
  }
  style.fontSize = `${px}px`
  style.flex = 'none'
  style.minHeight = '0'
  style.height = 'auto'
  const fits = el.scrollHeight <= targetH + 1
  style.fontSize = saved.fontSize
  style.flex = saved.flex
  style.minHeight = saved.minHeight
  style.height = saved.height
  return fits
}

/** Grow type with the note so a short idea does not leave a tall empty purple block. */
function fitIdeaText() {
  const el = textRef.value
  if (!el) return
  const targetH = Math.max(Number(props.note.height) || 168, MIN_SIZE)
  if (!String(el.innerText || '').trim()) {
    ideaFontPx.value = BASE_FONT_PX
    return
  }
  if (ideaTextFits(MAX_FONT_PX, targetH)) {
    ideaFontPx.value = MAX_FONT_PX
    return
  }
  if (!ideaTextFits(BASE_FONT_PX, targetH)) {
    ideaFontPx.value = BASE_FONT_PX
    return
  }
  let lo = BASE_FONT_PX
  let hi = MAX_FONT_PX
  while (lo < hi) {
    const mid = Math.ceil((lo + hi + 1) / 2)
    if (ideaTextFits(mid, targetH)) lo = mid
    else hi = mid - 1
  }
  ideaFontPx.value = lo
}

watch(
  () => [props.note.width, props.note.height, props.note.text],
  () => nextTick(fitIdeaText),
)

const pinnedLabels = computed(() =>
  Array.isArray(props.note.pinnedLabels) ? props.note.pinnedLabels.slice(0, MAX_PINNED) : [],
)

const allLabels = computed(() =>
  Array.isArray(props.note.rationaleLabels) ? props.note.rationaleLabels : [],
)

const addHover = ref(false)
const creating = ref(false)
const createDraft = ref('')
const editingTabId = ref(null)
const tabDraft = ref('')
const GHOST_TAB = 'add your label'

const liveCreateQuery = computed(() => {
  if (creating.value) return String(createDraft.value || '').trim()
  if (editingTabId.value) return String(tabDraft.value || '').trim()
  return ''
})

const liveEchoIds = computed(() => {
  const query = liveCreateQuery.value
  const ids = new Set()
  if (!query) return ids
  for (const item of allLabels.value) {
    if (!item.text) continue
    if (editingTabId.value && item.id === editingTabId.value) continue
    if (isSimilarLabel(query, item.text)) ids.add(item.id)
  }
  return ids
})

const liveCanvasEcho = computed(() => {
  const query = liveCreateQuery.value
  if (!query) return false
  const owner = `n${props.note.id}`
  return (props.canvasLabels || []).some(
    (item) => item?.text && item.owner !== owner && isSimilarLabel(query, item.text),
  )
})

const liveEmbedHits = ref({})
let liveEmbedTimer = 0
let liveEmbedAbort = null
const shortSuggest = ref('')
const shortFor = ref('')
const shortSkip = new Set()
let shortTimer = 0
let shortAbort = null

function clearShortSuggest() {
  shortSuggest.value = ''
  shortFor.value = ''
}

function acceptShortSuggest() {
  if (!shortSuggest.value) return
  if (creating.value) createDraft.value = shortSuggest.value
  else if (editingTabId.value) tabDraft.value = shortSuggest.value
  shortSkip.add(shortSuggest.value)
  clearShortSuggest()
}

function skipShortSuggest() {
  if (shortFor.value) shortSkip.add(shortFor.value)
  clearShortSuggest()
}

async function runShortSuggest(text) {
  if (text !== liveCreateQuery.value || !needsShortLabel(text) || shortSkip.has(text)) return
  shortAbort?.abort()
  shortAbort = new AbortController()
  try {
    const suggestion = await suggestShortLabel(text, { signal: shortAbort.signal })
    if (text !== liveCreateQuery.value) return
    shortSuggest.value = suggestion
    shortFor.value = suggestion ? text : ''
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
    clearShortSuggest()
  }
}

watch(liveCreateQuery, (text) => {
  window.clearTimeout(shortTimer)
  shortAbort?.abort()
  if (!needsShortLabel(text) || shortSkip.has(text)) {
    clearShortSuggest()
    return
  }
  shortTimer = window.setTimeout(() => {
    runShortSuggest(text).catch(() => {})
  }, SHORTEN_PAUSE_MS)
})

const liveEchoHint = computed(() => {
  if (allLabels.value.some((item) => item.text && (liveEchoIds.value.has(item.id) || liveEmbedHits.value[item.id]))) {
    return 'close to an existing label'
  }
  return liveCanvasEcho.value ? 'this pattern already appears on another idea' : ''
})

function isTabEcho(label) {
  return Boolean(liveEchoIds.value.has(label.id) || liveEmbedHits.value[label.id])
}

// Tabs run down the right edge until the note is too short, then the highest-ranked
// ones wrap onto the top edge so rank order still reads clockwise around the note.
const TAB_GAP = 3 // matches the flex gap in .edge-tabs
const TAB_INSET = 10 // the column starts this far down, and keeps the same clearance at the bottom
const TAB_FALLBACK_H = 18 // one-line tab, used until a tab has been measured on the right edge

const rightTabsRef = ref(null)
const addWrapRef = ref(null)
const topTabCount = ref(0)
const plusOnTop = ref(false)
const tabHeights = new Map() // group id -> height it takes on the right edge
const tabGroups = computed(() => clusterSimilarLabels(pinnedLabels.value))

const topTabs = computed(() => tabGroups.value.slice(0, topTabCount.value))
const rightTabs = computed(() => tabGroups.value.slice(topTabCount.value))

/** Right-edge heights only; a tab on the top edge keeps the last height it had on the right. */
function measureTabs() {
  const column = rightTabsRef.value
  if (!column) return
  for (const el of column.querySelectorAll('[data-tab-id]')) {
    if (el.offsetHeight) tabHeights.set(el.dataset.tabId, el.offsetHeight)
  }
}

function layoutTabs() {
  if (!noteRef.value || frozen.value) return
  measureTabs()
  const list = tabGroups.value
  const plusHeight = addWrapRef.value?.offsetHeight || 16
  const room = noteRef.value.offsetHeight - TAB_INSET * 2
  let count = 0
  while (count < list.length) {
    const stack = list.slice(count)
    const needed =
      stack.reduce((sum, group) => sum + (tabHeights.get(String(group.id)) || TAB_FALLBACK_H) + TAB_GAP, 0) +
      plusHeight
    if (needed <= room) break
    count += 1
  }
  topTabCount.value = count
  plusOnTop.value = count >= list.length && plusHeight > room
}

/** Re-run after the DOM settles: a tab returning to the right edge is only measurable once it is there. */
function relayoutTabs(passes = 2) {
  layoutTabs()
  if (passes > 0) nextTick(() => relayoutTabs(passes - 1))
}

// Pinning, unpinning, folds and edited label text all change how tall the column is.
watch(tabGroups, () => relayoutTabs(), { deep: true })

function applyLiveEmbedHits(vec) {
  const hits = {}
  for (const item of meaningHitsFromVectors(vec, allLabels.value, { excludeId: editingTabId.value })) {
    hits[item.id] = true
  }
  liveEmbedHits.value = hits
}

async function runLiveEmbed(query) {
  if (query !== liveCreateQuery.value || !readyForLiveEmbed(query)) return
  liveEmbedAbort?.abort()
  liveEmbedAbort = new AbortController()
  const { signal } = liveEmbedAbort
  try {
    const missing = allLabels.value.filter((item) => item.text && !asVector(item.embedding))
    if (missing.length) {
      const byText = await embedTexts(missing.map((item) => item.text), { signal })
      const next = allLabels.value.map((item) => {
        const vec = byText.get(item.text)
        return vec && !asVector(item.embedding) ? { ...item, embedding: vec } : item
      })
      if (next.some((item, i) => item !== allLabels.value[i])) setLabels(next)
    }
    let vec = cachedDraftVector(query)
    if (!vec) {
      const byText = await embedTexts([query], { signal })
      vec = byText.get(query)
      if (vec) rememberDraftVector(query, vec)
    }
    if (!vec || query !== liveCreateQuery.value) return
    applyLiveEmbedHits(vec)
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
  }
}

watch(liveCreateQuery, (query) => {
  window.clearTimeout(liveEmbedTimer)
  liveEmbedAbort?.abort()
  if (!readyForLiveEmbed(query)) {
    liveEmbedHits.value = {}
    return
  }
  const cached = cachedDraftVector(query)
  if (cached) {
    applyLiveEmbedHits(cached)
    return
  }
  liveEmbedTimer = window.setTimeout(() => {
    runLiveEmbed(query).catch(() => {})
  }, LIVE_EMBED_MS)
})

/** Caret in: drop the grey hint. Caret out + still empty: hint returns. */
function onTextFocus() {
  editing.value = true
  textFocused.value = true
}

function onTextBlur() {
  editing.value = false
  textFocused.value = false
}

function startTextEdit() {
  if (frozen.value) return
  editing.value = true
  textFocused.value = true
  nextTick(() => textRef.value?.focus())
}

function stopTextEdit() {
  editing.value = false
  textFocused.value = false
  textRef.value?.blur()
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

/** Ask the canvas for AI relation suggestions from this note. */
function requestSuggest(e) {
  e.stopPropagation()
  colorOpen.value = false
  emit('suggestRelations', props.note.id)
}

/** Fold / unfold the rationale panel under this note. */
function toggleRationale(e) {
  e.stopPropagation()
  activate()
  emit('toggleRationale', props.note.id)
}

function pinSnapshot(label) {
  return { id: label.id, text: label.text, kind: label.kind || '', source: label.source, rid: label.rid }
}

function setPinned(next) {
  emit('pin-change', next.slice(0, MAX_PINNED))
}

function setLabels(next) {
  emit('labels-change', next)
}

function nextLabelId() {
  const max = allLabels.value.reduce((n, item) => Math.max(n, Number(item.id) || 0), 0)
  return max + 1
}

/** × on a tab only takes the label off the note; it stays in the reflection pool. */
function unpinLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  setPinned(pinnedLabels.value.filter((item) => item.id !== label.id && item.text !== label.text))
}

/** Tabs are editable in place; an edit makes the label human-authored (yellow). */
function startTabEdit(label) {
  activate()
  emit('openRationale', props.note.id)
  editingTabId.value = label.id
  tabDraft.value = label.text
  nextTick(() => {
    document.getElementById(`tab-edit-${props.note.id}-${label.id}`)?.focus()
  })
}

function onTabDraftInput(e) {
  tabDraft.value = clipUserLabel(e.target.value)
}

function commitTabEdit(label) {
  if (editingTabId.value !== label.id) return
  const text = clipUserLabel(tabDraft.value)
  editingTabId.value = null
  tabDraft.value = ''
  liveEmbedHits.value = {}
  clearShortSuggest()
  if (!text || text === label.text) return
  setPinned(
    pinnedLabels.value.map((item) => (item.id === label.id ? { ...item, text, source: 'user' } : item)),
  )
  setLabels(
    allLabels.value.map((item) => (item.id === label.id ? { ...item, text, source: 'user' } : item)),
  )
}

function onTabEditKeydown(e, label) {
  if (e.key === 'Enter') {
    e.preventDefault()
    e.currentTarget.blur()
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    editingTabId.value = null
    tabDraft.value = ''
  }
}

function beginCreateTab(e) {
  e?.stopPropagation()
  if (creating.value) return
  activate()
  addHover.value = false
  creating.value = true
  createDraft.value = ''
  nextTick(() => {
    document.getElementById(`tab-new-${props.note.id}`)?.focus()
  })
}

function onCreateInput(e) {
  createDraft.value = clipUserLabel(e.target.value)
}

function commitCreate() {
  if (!creating.value) return
  const text = clipUserLabel(createDraft.value)
  creating.value = false
  createDraft.value = ''
  liveEmbedHits.value = {}
  clearShortSuggest()
  if (!text) return
  const chip = { id: nextLabelId(), text, kind: '', source: 'user' }
  setLabels([...allLabels.value, chip])
  // Shortcut straight onto the note, but the 3 chosen slots stay strict.
  if (pinnedLabels.value.length < MAX_PINNED) {
    setPinned([...pinnedLabels.value, pinSnapshot(chip)])
  }
}

function onCreateKeydown(e) {
  if (e.key === 'Enter') {
    e.preventDefault()
    e.currentTarget.blur()
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    creating.value = false
    createDraft.value = ''
  }
}

/** Select + optional drag. Toolbar / resize handles are ignored here. */
function onNoteMouseDown(e) {
  if (e.button !== 0) return
  if (frozen.value) {
    e.stopPropagation()
    return
  }
  if (e.target.closest('.toolbar') || e.target.closest('.resize-handle') || e.target.closest('.mag-point') || e.target.closest('.fold-btn') || e.target.closest('.edge-add-wrap') || e.target.closest('.tab-del')) return
  e.stopPropagation()
  const alreadySelected = props.selected
  activate()
  colorOpen.value = false
  didDrag = false
  const onTab = Boolean(e.target.closest('.edge-tab'))
  // Shift+drag inside the idea text still selects characters; a normal drag always moves the note.
  const textSelect = Boolean(editing.value && e.shiftKey && e.target.closest('.note-text'))
  if (textSelect) return

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
    ev.preventDefault()
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
    dragCleanup = null
    dragging.value = false
    if (!didDrag && onTab) emit('openRationale', props.note.id)
    else if (!didDrag && !onTab && (alreadySelected || !String(props.note.text || '').trim())) startTextEdit()
  }

  dragCleanup = onUp
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
  if (frozen.value) return
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
    resizeCleanup = null
    resizing.value = false
  }

  resizeCleanup = onUp
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

/** Push wrapped text up to the canvas note model. */
function onTextInput(e) {
  if (frozen.value) return
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
    :class="{ selected: selected && !frozen, dragging, resizing, abandoned: note.abandoned, frozen, editing }"
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
    @mouseenter="onNoteEnter"
    @mousemove="onNoteMove"
    @mouseleave="onNoteLeave"
    @contextmenu="onContextMenu"
  >
    <!-- Hidden while a mag-point drag is in progress so it does not cover the path. -->
    <div v-if="selected && !drafting && !frozen" class="toolbar" @pointerdown.stop @mousedown.stop @click.stop>
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
      <div class="toolbar-item">
        <button
          class="color-btn suggest-btn"
          type="button"
          title="Suggest relations (AI)"
          @click="requestSuggest"
        >
          <SuggestRelationIcon :size="18" />
        </button>
      </div>

      <div v-if="colorOpen" class="color-popover">
        <ColorWheel :model-value="note.color" @update:model-value="onColorChange" />
      </div>
    </div>

    <template v-if="selected && !frozen">
      <div
        v-for="corner in CORNERS"
        :key="corner"
        class="resize-handle"
        :class="corner"
        :title="`Resize ${corner}`"
        @mousedown="onResizeMouseDown($event, corner)"
      />
    </template>

    <template v-if="connectMode && !frozen">
      <button
        v-for="side in SIDES"
        :key="side"
        class="mag-point"
        :class="[side, { blocked: drafting && draftFromId === note.id }]"
        type="button"
        :title="`Connect ${side}`"
        @mousedown="onMagMouseDown($event, side)"
        @mouseup="onMagMouseUp($event, side)"
      />
    </template>

    <!-- Overflow row: highest-ranked tabs the right edge cannot hold, laid out along the top edge. -->
    <div v-if="!frozen && topTabs.length" class="edge-tabs-top">
      <div
        v-for="group in topTabs"
        :key="group.id"
        class="tab-pair"
        :class="{ linked: group.members.length > 1 }"
      >
        <span
          v-for="label in group.members"
          :key="label.id"
          class="edge-tab"
          :class="[label.source === 'user' ? 'user' : 'ai', { echo: isTabEcho(label) }]"
          :title="isTabEcho(label) ? 'close to an existing label' : undefined"
        >
          <input
            v-if="editingTabId === label.id"
            :id="`tab-edit-${note.id}-${label.id}`"
            class="tab-input"
            :value="tabDraft"
            :maxlength="MAX_USER_LABEL_CHARS"
            @input="onTabDraftInput"
            @keydown="onTabEditKeydown($event, label)"
            @blur="commitTabEdit(label)"
            @pointerdown.stop
            @mousedown.stop
            @click.stop
          />
          <span
            v-else
            class="tab-label"
            :title="isTabEcho(label) ? 'close to an existing label' : 'Click to edit'"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="startTabEdit(label)"
          >{{ label.text }}</span>
          <span
            v-if="recurrence(label) > 1"
            class="tab-badge"
            :title="`this rationale is behind ${recurrence(label)} ideas`"
          >×{{ recurrence(label) }}</span>
          <button
            type="button"
            class="tab-del"
            title="Take off the note"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="unpinLabel(label, $event)"
          >×</button>
        </span>
        <span v-if="groupPatternHint(group)" class="tab-same-hint">{{ groupPatternHint(group) }}</span>
      </div>
    </div>

    <!-- File-folder tabs on the right edge. + always offers a human tab (hover = feedforward). -->
    <div v-if="!frozen" ref="rightTabsRef" class="edge-tabs" :class="{ 'plus-top': plusOnTop }">
      <div
        v-for="group in rightTabs"
        :key="group.id"
        :data-tab-id="group.id"
        class="tab-pair"
        :class="{ linked: group.members.length > 1 }"
      >
        <span
          v-for="label in group.members"
          :key="label.id"
          class="edge-tab"
          :class="[label.source === 'user' ? 'user' : 'ai', { echo: isTabEcho(label) }]"
          :title="isTabEcho(label) ? 'close to an existing label' : undefined"
        >
          <input
            v-if="editingTabId === label.id"
            :id="`tab-edit-${note.id}-${label.id}`"
            class="tab-input"
            :value="tabDraft"
            :maxlength="MAX_USER_LABEL_CHARS"
            @input="onTabDraftInput"
            @keydown="onTabEditKeydown($event, label)"
            @blur="commitTabEdit(label)"
            @pointerdown.stop
            @mousedown.stop
            @click.stop
          />
          <span
            v-else
            class="tab-label"
            :title="isTabEcho(label) ? 'close to an existing label' : 'Click to edit'"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="startTabEdit(label)"
          >{{ label.text }}</span>
          <span
            v-if="recurrence(label) > 1"
            class="tab-badge"
            :title="`this rationale is behind ${recurrence(label)} ideas`"
          >×{{ recurrence(label) }}</span>
          <button
            type="button"
            class="tab-del"
            title="Take off the note"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="unpinLabel(label, $event)"
          >×</button>
        </span>
        <span v-if="groupPatternHint(group)" class="tab-same-hint">{{ groupPatternHint(group) }}</span>
      </div>
      <div
        ref="addWrapRef"
        class="edge-add-wrap"
        @pointerdown.stop
        @mousedown.stop
        @mouseenter="addHover = true"
        @mouseleave="addHover = false"
      >
        <span v-if="creating" class="edge-tab user draft">
          <input
            :id="`tab-new-${note.id}`"
            class="tab-input"
            :value="createDraft"
            :maxlength="MAX_USER_LABEL_CHARS"
            placeholder="rationale label of this idea"
            @input="onCreateInput"
            @keydown="onCreateKeydown"
            @blur="commitCreate"
            @click.stop
          />
        </span>
        <span v-if="liveEchoHint" class="tab-echo-hint">{{ liveEchoHint }}</span>
        <div v-if="shortSuggest" class="tab-shorten">
          <span>{{ shortSuggest }}</span>
          <button type="button" @mousedown.prevent="acceptShortSuggest">use</button>
          <button type="button" @mousedown.prevent="skipShortSuggest">keep mine</button>
        </div>
        <span v-else-if="addHover" class="edge-tab ghost">{{ GHOST_TAB }}</span>
        <button
          type="button"
          class="edge-add"
          title="Add a rationale label"
          @click.stop="beginCreateTab"
        >+</button>
      </div>
    </div>

    <!-- Native contenteditable; wraps and grows the note (no inner scroll) -->
    <span
      v-if="showPlaceholder"
      class="note-placeholder"
      :style="{ color: placeholderColor, fontSize: `${ideaFontPx}px` }"
    >{{ PLACEHOLDER }}</span>
    <div
      ref="textRef"
      class="note-text"
      :style="{ color: textColor, fontSize: `${ideaFontPx}px` }"
      :contenteditable="Boolean(editing && !frozen)"
      spellcheck="false"
      @input="onTextInput"
      @focus="onTextFocus"
      @dblclick="startTextEdit"
      @blur="onTextBlur"
    />

    <button
      v-if="!frozen"
      class="fold-btn"
      type="button"
      :class="{ open: note.rationaleOpen }"
      :title="note.rationaleOpen ? 'Hide reflection' : 'Show reflection'"
      :style="{ color: textColor }"
      @pointerdown.stop
      @mousedown.stop
      @click.stop="toggleRationale"
    >
      <svg viewBox="0 0 12 8" aria-hidden="true">
        <path d="M2 2.5 L6 6 L10 2.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <!-- Hint + menu live outside the note so the ghost opacity does not swallow them. -->
    <Teleport to="body">
      <div
        v-if="showReviveHint"
        class="revive-hint"
        :style="{ left: `${hintPos.x}px`, top: `${hintPos.y}px` }"
      >to revive this idea, right click</div>
      <div
        v-if="menuOpen"
        class="revive-menu"
        :style="{ left: `${menuPos.x}px`, top: `${menuPos.y}px` }"
        @contextmenu.prevent
      >
        <button type="button" class="revive-item" @click="onReviveClick">revive idea</button>
      </div>
    </Teleport>
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
  touch-action: none;
  overflow: visible;
  pointer-events: auto;
}

.note.selected {
  z-index: 20;
  box-shadow:
    0 0 0 1.5px #2563eb,
    0 8px 24px rgba(44, 40, 31, 0.16);
}

.note.frozen {
  pointer-events: none;
  cursor: default;
}

.note.frozen .note-text {
  cursor: default;
  caret-color: transparent;
}

.note.frozen .tab-del,
.note.frozen .edge-add-wrap {
  display: none;
}

/* Ghost stays inert, but hover + right-click still reach it so it can be revived. */
.note.abandoned {
  z-index: 0;
  opacity: 0.28;
  box-shadow: none;
  pointer-events: auto;
  cursor: context-menu;
  transition: opacity 0.15s ease;
}

.note.abandoned > * {
  pointer-events: none;
}

.note.abandoned:hover {
  opacity: 0.26;
}

.note.abandoned .note-text {
  cursor: default;
  caret-color: transparent;
}

.note.abandoned .tab-del,
.note.abandoned .edge-add-wrap {
  display: none;
}

.note.dragging,
.note.dragging .note-text {
  cursor: grabbing;
}

.revive-hint {
  position: fixed;
  transform: translate(-50%, calc(-100% - 8px));
  padding: 3px 8px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 7px;
  color: var(--ink-muted);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1.35;
  white-space: nowrap;
  pointer-events: none;
  z-index: 4000;
}

.revive-menu {
  position: fixed;
  min-width: 118px;
  padding: 4px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(44, 40, 31, 0.14);
  z-index: 4001;
}

.revive-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  background: none;
  border: 0;
  border-radius: 5px;
  color: var(--ink);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}

.revive-item:hover {
  background: var(--accent-soft);
  color: var(--accent);
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
  gap: 2px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(44, 40, 31, 0.14);
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
  color: var(--ink-muted);
  cursor: pointer;
}

.color-btn:hover,
.color-btn.open {
  background: var(--accent-soft);
  color: var(--ink);
}

.suggest-btn:hover {
  color: var(--accent);
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
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 7px;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 10px;
  line-height: 1.3;
  color: var(--ink-muted);
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
  border: 1px solid var(--line);
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
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 10px 28px rgba(44, 40, 31, 0.14);
}

.resize-handle {
  position: absolute;
  width: 9px;
  height: 9px;
  background: #fff;
  border: 1.5px solid #2563eb;
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

.edge-tabs {
  position: absolute;
  left: calc(100% - 4px);
  top: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  z-index: 2;
}

/* Only reachable below the minimum note size: the + has no room left on the right edge. */
.edge-tabs.plus-top {
  left: auto;
  right: 4px;
  top: auto;
  bottom: calc(100% + 2px);
}

/* Anchored at its bottom so extra rows grow upward, never into the note. */
.edge-tabs-top {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: calc(100% - 4px);
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 3px;
  z-index: 2;
}

.edge-tabs-top .tab-pair {
  align-items: flex-end;
}

/* Shrink to share the note's width, and only wrap to a second row when that is not enough. */
.edge-tabs-top .edge-tab {
  flex: 0 1 auto;
  min-width: 60px;
  padding: 2px 5px;
  border-radius: 3px 3px 0 0;
  border-left-width: 1px; /* border colour still comes from the .ai / .user shorthand */
  border-left-style: solid;
  border-bottom-width: 0;
  box-shadow: 1px -1px 3px rgba(0, 0, 0, 0.18);
}

.edge-tab {
  box-sizing: border-box;
  display: inline-flex;
  align-items: flex-start;
  gap: 2px;
  width: max-content;
  max-width: 88px;
  padding: 2px 4px 2px 6px;
  border-left: 0;
  border-radius: 0 3px 3px 0;
  box-shadow: 1px 1px 3px rgba(0, 0, 0, 0.18);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.25;
  letter-spacing: 0.01em;
  overflow-wrap: break-word;
  word-break: normal;
  cursor: pointer;
}

.edge-tab.ai {
  background: rgba(147, 197, 253, 0.82);
  border: 1px solid rgba(147, 197, 253, 0.9);
  border-left: 0;
  color: #1e3a5f;
}

.edge-tab.user {
  background: rgba(253, 230, 138, 0.92);
  border: 1px solid rgba(253, 230, 138, 0.95);
  border-left: 0;
  color: #5b4a12;
}

.edge-tab.echo {
  animation: echo-breathe 2.2s ease-in-out infinite;
}

.edge-tab.ai.echo {
  animation-name: echo-breathe-ai;
}

.edge-tab.user.echo {
  animation-name: echo-breathe-user;
}

@keyframes echo-breathe-ai {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.4), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(147, 197, 253, 0.95), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
}

@keyframes echo-breathe-user {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.4), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(253, 230, 138, 0.95), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
}

@media (prefers-reduced-motion: reduce) {
  .edge-tab.ai.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.9), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
  .edge-tab.user.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.9), 1px 1px 3px rgba(0, 0, 0, 0.18);
  }
}

.edge-tab.ghost {
  position: absolute;
  left: calc(100% + 2px);
  top: 50%;
  transform: translateY(-50%);
  z-index: 3;
  max-width: 76px;
  padding: 2px 6px;
  border-radius: 3px;
  border: 1px solid rgba(253, 230, 138, 0.4);
  background: rgba(253, 230, 138, 0.28);
  color: rgba(91, 74, 18, 0.62);
  box-shadow: none;
  font-size: 8px;
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  pointer-events: none;
}

.edge-tab.draft {
  position: absolute;
  left: calc(100% + 2px);
  top: 50%;
  transform: translateY(-50%);
  z-index: 3;
  max-width: 220px;
  border-radius: 3px;
}

.edge-tab .tab-input {
  display: block;
  width: 72px;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  font-family: inherit;
  font-size: inherit;
  line-height: inherit;
  outline: none;
}

.edge-tab .tab-input::placeholder {
  color: rgba(91, 74, 18, 0.45);
}

.tab-label {
  min-width: 0;
  flex: 1;
  cursor: text;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.tab-pair {
  display: flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: flex-start;
  gap: 2px;
  width: max-content;
}

.tab-pair.linked {
  align-items: center;
}

.tab-same-hint {
  align-self: center;
  max-width: 92px;
  padding: 0 4px;
  color: var(--ink-faint);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 8px;
  line-height: 1.2;
  letter-spacing: 0.01em;
}

/* Recurrence, not authorship: the tab colour already says who wrote it. */
.tab-badge {
  flex-shrink: 0;
  align-self: center;
  padding: 0 3px;
  border-radius: 6px;
  background: rgba(0, 0, 0, 0.22);
  font-size: 8px;
  font-weight: 700;
  line-height: 12px;
}

.tab-del {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  margin-top: 1px;
  padding: 0;
  border: 0;
  border-radius: 2px;
  background: transparent;
  color: inherit;
  opacity: 0.5;
  cursor: pointer;
  font-size: 11px;
  line-height: 12px;
}

.tab-del:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.12);
}

.edge-add-wrap {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  align-self: flex-start;
  width: max-content;
  margin-top: 3px;
}

.tab-shorten {
  position: absolute;
  left: calc(100% + 8px);
  top: calc(50% + 34px);
  z-index: 4;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  width: max-content;
  max-width: 200px;
  color: #78716c;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.35;
}

.tab-shorten button {
  padding: 0 4px;
  border: 0;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.55);
  color: #44403c;
  cursor: pointer;
  font: inherit;
}

.tab-echo-hint {
  position: absolute;
  left: calc(100% + 8px);
  top: calc(50% + 18px);
  z-index: 4;
  width: max-content;
  max-width: 180px;
  color: #ca8a04;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.35;
  pointer-events: none;
}

.edge-add {
  box-sizing: border-box;
  width: 16px;
  height: 12px;
  padding: 0;
  margin-top: 3px;
  border: 0;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.28);
  font-size: 10px;
  line-height: 12px;
  color: rgba(22, 22, 29, 0.78);
  cursor: pointer;
}

.edge-add:hover {
  background: rgba(255, 255, 255, 0.45);
  color: rgba(22, 22, 29, 0.92);
}

.note-text {
  padding: 14px 14px 26px;
  overflow: visible;
  outline: none;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.55;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  word-break: break-word;
  cursor: inherit;
  flex: 1;
  min-height: 100%;
}

.note.editing .note-text {
  cursor: text;
  user-select: text;
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

.mag-point.blocked,
.mag-point.blocked:hover {
  cursor: not-allowed;
  transform: translate(-50%, -50%);
  background: #fff;
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

.fold-btn {
  position: absolute;
  left: 50%;
  bottom: 3px;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: rgba(0, 0, 0, 0.18);
  color: rgba(0, 0, 0, 0.45);
  cursor: pointer;
  z-index: 5;
}

.fold-btn:hover {
  background: rgba(0, 0, 0, 0.28);
  color: rgba(0, 0, 0, 0.7);
}

.fold-btn svg {
  width: 12px;
  height: 8px;
  display: block;
  transition: transform 0.14s ease;
}

.fold-btn.open svg {
  transform: rotate(180deg);
}
</style>
