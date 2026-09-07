<!--
  Rationale-label module: standalone playground (?ai=1) and under sticky notes.
  Input: designer text. Output: short labels, not a chatbot reply.
  Enter generates (costs an API call). Empty input never generates.
-->
<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  asVector,
  assessReflection,
  cachedDraftVector,
  clipUserLabel,
  clipWords,
  confirmLabelMeaning,
  embedTexts,
  MAX_USER_LABEL_CHARS,
  needsShortLabel,
  SHORTEN_PAUSE_MS,
  suggestShortLabel,
  generateRationaleLabels,
  isSimilarLabel,
  clusterSimilarLabels,
  pairPatternHint,
  LabelVectorIndex,
  lexicalMeaningHits,
  LIVE_EMBED_MS,
  meaningHitsFromVectors,
  NOT_SURE_LABEL,
  NOT_SURE_RID,
  readyForLiveEmbed,
  rememberDraftVector,
  sameWording,
} from '../api/rationale'

const MAX_PINNED = 3

/** Canvas-scoped rationale identity; labels sharing a rid are the same reasoning. */
function newRid() {
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
}

const props = defineProps({
  target: { type: String, default: 'generic' }, // generic | note | relation
  idea: { type: String, default: '' }, // sticky-note / relation text the prompt is about
  compact: { type: Boolean, default: false }, // tighter layout under a note
  idPrefix: { type: String, default: 'label' }, // unique ids when many notes share the canvas
  pinned: { type: Array, default: null }, // labels on the note; null = local (playground)
  savedInput: { type: String, default: '' },
  savedLabels: { type: Array, default: null },
  knownLabels: { type: Array, default: () => [] }, // [{ ref, text }] across the canvas, for reuse detection
  ridOwners: { type: Object, default: () => ({}) }, // rid -> how many ideas / relations invoke it
  patternStats: { type: Object, default: () => ({}) }, // repeating wording across the canvas
  canvasLabels: { type: Array, default: () => [] }, // [{ text, rid, owner }]
  placeholder: { type: String, default: 'Tell me about this idea…' },
  presetLabels: { type: Array, default: () => [] }, // chips shown on open so the user can skip writing
  actionLabel: { type: String, default: '' }, // optional extra button, e.g. "just abandon it"
  completeOnGenerate: { type: Boolean, default: false }, // emit complete after a successful Enter generate
})

const emit = defineEmits(['labels', 'error', 'pin-change', 'rationale-change', 'action', 'complete'])

const input = ref(props.savedInput || '')
const labels = ref([])
const loading = ref(false)
const error = ref('')
const errorCode = ref('')
const source = ref('') // 'api' | ''
const editingId = ref(null)
const editingZone = ref(null) // which copy of the chip holds the caret
const draft = ref('')
const pinLimitHint = ref(false)
const localPinned = ref([])
const draggingId = ref(null) // label being dragged between the two sections
const dragOrigin = ref(null) // 'pool' | 'chosen' — decides re-rank vs pin/unpin
const chosenHot = ref(false) // chosen zone is a live drop target
const pendingMerges = ref([]) // reuse hits awaiting a yes / no from the person
// Last text that actually reached the model. Empty means the next Enter is a first generate.
const lastGenerated = ref('')

const fieldRef = ref(null)

let abortGenerate = null
let abortEcho = new AbortController()
let nextLabelId = 1
let hydrating = true
let fieldObserver = null
let fieldWidth = 0

function bumpNextLabelId(list) {
  const maxId = (Array.isArray(list) ? list : []).reduce((max, item) => Math.max(max, Number(item.id) || 0), 0)
  nextLabelId = Math.max(nextLabelId, maxId + 1)
}

function sameRationale(a, b) {
  if (!a || !b) return false
  if (a.rid && b.rid && a.rid === b.rid) return true
  const at = String(a.text || '').trim()
  const bt = String(b.text || '').trim()
  return Boolean(at && at === bt)
}

function fromSaved(item, fallbackSource) {
  const source = item.source || fallbackSource
  return {
    id: item.id || nextLabelId++,
    text: source === 'user' ? String(item.text || '').trim() : clipWords(item.text),
    kind: item.kind || '',
    source,
    rid: item.rid || newRid(),
    count: Number(item.count) > 0 ? Number(item.count) : 1,
    occurrences: Array.isArray(item.occurrences) ? item.occurrences : [],
    embedding: asVector(item.embedding),
    echo: Boolean(item.echo),
    echoSource: Boolean(item.echoSource),
  }
}

function seedPresets() {
  return (props.presetLabels || [])
    .filter((item) => item && item.text)
    .map((item) => fromSaved(item, 'user'))
}

if (Array.isArray(props.savedLabels) && props.savedLabels.length) {
  labels.value = props.savedLabels.map((item) => fromSaved(item, 'ai'))
  bumpNextLabelId(labels.value)
  if (labels.value.some((item) => item.source === 'ai')) source.value = 'api'
  lastGenerated.value = String(props.savedInput || '').trim()
} else {
  labels.value = seedPresets()
  bumpNextLabelId(labels.value)
}
bumpNextLabelId(props.pinned)

/** No scrollbar: the field grows with its text and follows the note's width. */
function autoGrow() {
  const el = fieldRef.value
  if (!el) return
  el.style.height = 'auto'
  const borders = el.offsetHeight - el.clientHeight // border-box: scrollHeight leaves these out
  el.style.height = `${el.scrollHeight + borders}px`
}

onMounted(() => {
  autoGrow()
  refreshAllUserEchoes()
  if (!fieldRef.value) return
  fieldWidth = fieldRef.value.offsetWidth
  fieldObserver = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width
    if (width == null || width === fieldWidth) return // height changes are ours, ignore them
    fieldWidth = width
    autoGrow()
  })
  fieldObserver.observe(fieldRef.value)
})

onUnmounted(() => {
  abortGenerate?.abort()
  abortEcho?.abort()
  liveEmbedAbort?.abort()
  window.clearTimeout(liveEmbedTimer)
  shortAbort?.abort()
  window.clearTimeout(shortTimer)
  fieldObserver?.disconnect()
})

function emitRationale() {
  emit('rationale-change', {
    input: input.value,
    labels: labels.value.map((item) => ({
      id: item.id,
      text: item.text,
      kind: item.kind || '',
      source: item.source,
      rid: item.rid,
      count: item.count || 1,
      occurrences: item.occurrences || [],
      embedding: asVector(item.embedding),
      echo: Boolean(item.echo),
      echoSource: Boolean(item.echoSource),
    })),
    source: source.value,
  })
}

hydrating = false

// Clearing the field never clears labels: the pool is the record of everything generated.
watch(input, () => {
  nextTick(autoGrow) // covers programmatic changes, not just typing
  if (hydrating) return
  emitRationale()
})

watch(
  () => props.savedLabels,
  (list) => {
    if (!Array.isArray(list)) return
    const incoming = list.map((item) => fromSaved(item, 'ai'))
    bumpNextLabelId(incoming)
    const unchanged =
      incoming.length === labels.value.length &&
      incoming.every((item) =>
        labels.value.some((cur) => cur.id === item.id && cur.text === item.text && cur.source === item.source),
      )
    if (unchanged) {
      for (const item of incoming) {
        const cur = labels.value.find((row) => row.id === item.id)
        if (cur && !asVector(cur.embedding) && asVector(item.embedding)) cur.embedding = item.embedding
      }
      return
    }
    labels.value = incoming
    if (!hydrating) refreshAllUserEchoes()
  },
  { deep: true },
)

function notify() {
  emit('labels', labels.value)
  emitRationale()
}

function currentPinned() {
  return Array.isArray(props.pinned) ? props.pinned : localPinned.value
}

function setPinned(next) {
  const clipped = next.slice(0, MAX_PINNED)
  pinLimitHint.value = false
  if (Array.isArray(props.pinned)) emit('pin-change', clipped)
  else localPinned.value = clipped
}

function pinSnapshot(label) {
  return { id: label.id, text: label.text, kind: label.kind || '', source: label.source, rid: label.rid }
}

function isPinned(label) {
  return currentPinned().some((item) => sameRationale(item, label))
}

function chipTitle(label) {
  if (isEchoSource(label) || isEchoHit(label)) return echoHint.value || 'close to an existing label'
  if (isPinned(label)) return 'already chosen — drag it back down to take it off'
  if (currentPinned().length >= MAX_PINNED) return '3 already chosen — drag one out first'
  return 'click to edit · drag to rank or to choose'
}

const chosenGroups = computed(() => clusterSimilarLabels(currentPinned()))
const poolGroups = computed(() => clusterSimilarLabels(visibleLabels()))

const echoHit = ref({})
const echoUser = ref({})
const echoByQuery = new Map()
const vectorIndex = new LabelVectorIndex()

function paintEchoes() {
  const hits = {}
  const sources = {}
  for (const [uid, ids] of echoByQuery) {
    if (!ids.length) continue
    sources[uid] = true
    for (const id of ids) hits[id] = true
  }
  echoHit.value = hits
  echoUser.value = sources
  for (const label of labels.value) {
    label.echo = Boolean(hits[label.id] || sources[label.id])
    label.echoSource = Boolean(sources[label.id])
  }
}

/** Only the label being created / edited — never the reflection field. */
function liveQueryText() {
  if (!editingId.value) return ''
  return String(draft.value || '').trim()
}

const liveHits = computed(() => {
  const query = liveQueryText()
  const hits = {}
  if (!query) return hits
  for (const label of labels.value) {
    if (!label.text) continue
    if (editingId.value && label.id === editingId.value) continue
    if (isSimilarLabel(query, label.text)) hits[label.id] = true
  }
  return hits
})

const liveEmbedHits = ref({})
let liveEmbedTimer = 0
let liveEmbedAbort = null
const shortSuggest = ref('')
const shortFor = ref('')
const shortSkip = new Set()
let shortTimer = 0
let shortAbort = null

function applyLiveEmbedHits(query, vec) {
  const hits = {}
  for (const item of meaningHitsFromVectors(vec, labels.value, { excludeId: editingId.value })) {
    hits[item.id] = true
  }
  liveEmbedHits.value = hits
}

async function runLiveEmbed(query) {
  if (query !== liveQueryText() || !readyForLiveEmbed(query)) return
  liveEmbedAbort?.abort()
  liveEmbedAbort = new AbortController()
  const { signal } = liveEmbedAbort
  try {
    const pool = labels.value.filter((item) => item.text)
    await ensureIndexed(pool, { signal })
    let vec = cachedDraftVector(query)
    if (!vec) {
      const byText = await embedTexts([query], { signal })
      vec = byText.get(query)
      if (vec) rememberDraftVector(query, vec)
    }
    if (!vec || query !== liveQueryText()) return
    applyLiveEmbedHits(query, vec)
    notify()
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
  }
}

watch(
  () => liveQueryText(),
  (query) => {
    window.clearTimeout(liveEmbedTimer)
    liveEmbedAbort?.abort()
    if (!readyForLiveEmbed(query)) {
      liveEmbedHits.value = {}
      return
    }
    const cached = cachedDraftVector(query)
    if (cached) {
      applyLiveEmbedHits(query, cached)
      return
    }
    liveEmbedTimer = window.setTimeout(() => {
      runLiveEmbed(query).catch(() => {})
    }, LIVE_EMBED_MS)
  },
)

function isEchoHit(label) {
  if (!editingId.value) return false
  return Boolean(liveHits.value[label.id] || liveEmbedHits.value[label.id])
}

function isEchoSource(label) {
  if (echoUser.value[label.id]) return true
  if (editingId.value !== label.id) return false
  return labels.value.some((item) => item.id !== label.id && (liveHits.value[item.id] || liveEmbedHits.value[item.id]))
}

const echoHint = computed(() => {
  if (!editingId.value) return ''
  const hasHit = labels.value.some(
    (label) =>
      label.id !== editingId.value &&
      label.text &&
      (liveHits.value[label.id] || liveEmbedHits.value[label.id]),
  )
  if (hasHit) return 'close to an existing label'
  const query = liveQueryText()
  if (!query) return ''
  const localRids = new Set(labels.value.map((item) => item.rid).filter(Boolean))
  const elsewhere = (props.canvasLabels || []).some(
    (item) => item?.text && !localRids.has(item.rid) && isSimilarLabel(query, item.text),
  )
  return elsewhere ? 'this pattern already appears on another idea' : ''
})

/** Write-time: embed rows that lack a vector, then upsert into HNSW (no full rebuild). */
async function ensureIndexed(items, signal) {
  const missing = items.filter((item) => item?.text && !asVector(item.embedding))
  if (missing.length) {
    const byText = await embedTexts(missing.map((item) => item.text), { signal })
    for (const item of missing) {
      const vec = byText.get(item.text)
      if (vec) item.embedding = vec
    }
  }
  const live = new Set()
  for (const item of items) {
    if (!item?.text) continue
    live.add(String(item.id))
    vectorIndex.upsert(item.id, item.embedding, item.text)
  }
  for (const id of vectorIndex.ids()) {
    if (!live.has(id)) vectorIndex.remove(id)
  }
}

function echoId(ref) {
  const asNum = Number(ref)
  if (Number.isFinite(asNum) && String(asNum) === String(ref)) return asNum
  return String(ref)
}

function mergeMeaningHits(...lists) {
  const seen = new Set()
  const out = []
  for (const list of lists) {
    for (const item of list || []) {
      const ref = String(item?.ref || '')
      if (!ref || seen.has(ref)) continue
      seen.add(ref)
      out.push(item)
      if (out.length >= 3) return out
    }
  }
  return out
}

/**
 * Paint wording matches immediately. Embeddings / the model may add more;
 * they must not wipe a match that wording or cosine already found.
 */
async function refreshMeaningEchoes(queryLabel) {
  if (!queryLabel?.text) {
    echoByQuery.delete(queryLabel?.id)
    paintEchoes()
    return
  }
  const { signal } = abortEcho || {}
  const pool = labels.value.filter((item) => item.text)
  const lexical = lexicalMeaningHits(queryLabel.text, pool, queryLabel.id)
  if (lexical.length) {
    echoByQuery.set(queryLabel.id, lexical.map((item) => echoId(item.ref)))
  } else {
    echoByQuery.delete(queryLabel.id)
  }
  paintEchoes()
  notify()

  try {
    await ensureIndexed(pool, { signal })
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
  }
  const queryVec = asVector(queryLabel.embedding)
  const neighbours = queryVec
    ? vectorIndex.query(queryVec, { excludeId: queryLabel.id })
    : []
  const fallback = mergeMeaningHits(lexical, neighbours)
  if (!fallback.length) {
    echoByQuery.delete(queryLabel.id)
    paintEchoes()
    notify()
    return
  }
  if (fallback.length) echoByQuery.set(queryLabel.id, fallback.map((item) => echoId(item.ref)))
  paintEchoes()
  notify()
  if (!fallback.length) return
  try {
    const matches = await confirmLabelMeaning(queryLabel.text, fallback, { signal })
    const allowed = new Set(fallback.map((item) => String(item.ref)))
    const confirmed = matches
      .map((ref) => String(ref))
      .filter((ref) => allowed.has(ref))
      .map(echoId)
    if (confirmed.length) echoByQuery.set(queryLabel.id, confirmed)
    paintEchoes()
    notify()
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
    paintEchoes()
  }
}

function refreshPoolEchoes() {
  for (const label of labels.value) {
    if (label.text) refreshMeaningEchoes(label).catch(() => {})
  }
}

function refreshAllUserEchoes() {
  refreshPoolEchoes()
}

/** Drag-and-drop is the only way a label gets onto the note. */
function onDragStart(e, label, origin) {
  if (!label.text) {
    e.preventDefault()
    return
  }
  draggingId.value = label.id
  dragOrigin.value = origin
  pinLimitHint.value = false
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', String(label.id))
}

function onDragEnd() {
  draggingId.value = null
  dragOrigin.value = null
  chosenHot.value = false
}

/** Shared reorder: the dragged chip takes the slot it is hovering. */
function reorder(list, dragId, targetId) {
  const from = list.findIndex((item) => String(item.id) === String(dragId))
  const to = list.findIndex((item) => String(item.id) === String(targetId))
  if (from < 0 || to < 0 || from === to) return null
  const next = list.slice()
  const [moved] = next.splice(from, 1)
  next.splice(to, 0, moved)
  return next
}

/** Hovering another pool chip while dragging within the pool re-ranks the list. */
function onPoolChipDragOver(e, target) {
  if (dragOrigin.value !== 'pool' || draggingId.value == null) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  const next = reorder(labels.value, draggingId.value, target.id)
  if (next) labels.value = next
}

function onChosenChipDragOver(e, target) {
  if (dragOrigin.value !== 'chosen' || draggingId.value == null) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  const next = reorder(currentPinned(), draggingId.value, target.id)
  if (next) setPinned(next)
}

function draggedLabel(e) {
  const raw = e.dataTransfer?.getData('text/plain')
  const id = raw ? raw : draggingId.value
  return visibleLabels().find((item) => String(item.id) === String(id))
}

/** Chosen zone takes exactly 3 — a 4th drop is refused rather than swapped. */
function canDropInChosen() {
  const label = visibleLabels().find((item) => String(item.id) === String(draggingId.value))
  if (!label || !label.text) return false
  if (isPinned(label)) return false
  return currentPinned().length < MAX_PINNED
}

function onChosenDragOver(e) {
  if (dragOrigin.value === 'chosen') return // re-ranking inside the zone, not a new pin
  if (!canDropInChosen()) {
    if (draggingId.value != null && currentPinned().length >= MAX_PINNED) pinLimitHint.value = true
    return
  }
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  chosenHot.value = true
}

function onChosenDragLeave() {
  chosenHot.value = false
}

function onChosenDrop(e) {
  e.preventDefault()
  chosenHot.value = false
  const label = draggedLabel(e)
  const origin = dragOrigin.value
  draggingId.value = null
  dragOrigin.value = null
  if (origin === 'chosen') return // order was already applied while dragging
  if (!label || !label.text || isPinned(label)) return
  if (currentPinned().length >= MAX_PINNED) {
    pinLimitHint.value = true
    return
  }
  setPinned([...currentPinned(), pinSnapshot(label)])
}

/** Pool takes both a re-rank from inside and a chosen label dropped back down. */
function onPoolDragOver(e) {
  if (dragOrigin.value === 'pool') {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    return
  }
  const label = visibleLabels().find((item) => String(item.id) === String(draggingId.value))
  if (!label || !isPinned(label)) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
}

function onPoolDrop(e) {
  e.preventDefault()
  const label = draggedLabel(e)
  const origin = dragOrigin.value
  draggingId.value = null
  dragOrigin.value = null
  if (origin === 'pool') {
    emitRationale() // persist the new ranking
    return
  }
  if (!label) return
  unpin(label)
}

function unpin(label) {
  if (!isPinned(label)) return
  pinLimitHint.value = false
  setPinned(currentPinned().filter((item) => !sameRationale(item, label)))
}

/** Generated chips plus any pins that are no longer in the current set (so they can be swapped). */
function visibleLabels() {
  const pool = labels.value
  const extras = currentPinned().filter(
    (item) => !pool.some((label) => sameRationale(label, item)),
  )
  return extras.length ? [...pool, ...extras] : pool
}

function toChip(item, origin) {
  return {
    id: nextLabelId++,
    text: clipWords(item.text),
    kind: item.kind || '',
    source: origin,
    rid: item.rid || newRid(),
    count: 1,
    occurrences: [],
    embedding: asVector(item.embedding),
  }
}

/** idk / don't know / hmm: one yellow chip, no API call. */
function applyUnsureLabel() {
  const existing = labels.value.find((item) => item.rid === NOT_SURE_RID || sameWording(item.text, NOT_SURE_LABEL))
  if (existing) {
    existing.count = (existing.count || 1) + 1
    existing.rid = existing.rid || NOT_SURE_RID
    return
  }
  labels.value = [...labels.value, toChip({ text: NOT_SURE_LABEL, kind: 'question', rid: NOT_SURE_RID }, 'user')]
}

/** Enter in the field: generate only when the person typed a rationale. */
function onFieldKeydown(e) {
  if (e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  generate().catch(() => {})
}

/** Ask OpenAI for labels. No call if the rationale field is empty. */
async function generate() {
  const text = input.value.trim()
  if (loading.value) return
  if (!text) return

  // Local gate: junk or a near-copy never reaches OpenAI. Can't-articulate
  // stamps a fixed "not sure" chip instead of spending a prompt.
  const verdict = assessReflection(text, lastGenerated.value)
  if (!verdict.ok) {
    error.value = verdict.message
    errorCode.value = verdict.code || ''
    return
  }
  if (verdict.preset) {
    applyUnsureLabel()
    lastGenerated.value = text
    error.value = ''
    errorCode.value = ''
    editingId.value = null
    notify()
    if (props.completeOnGenerate) emit('complete')
    return
  }

  abortGenerate?.abort()
  abortGenerate = new AbortController()
  error.value = ''
  errorCode.value = ''
  loading.value = true
  try {
    const data = await generateRationaleLabels(text, props.target, {
      known: props.knownLabels,
      previous: lastGenerated.value,
      signal: abortGenerate.signal,
    })

    // The model points at an existing rationale with same_as instead of coining a near-duplicate.
    // Those are proposals only — nothing merges until the person confirms it.
    pendingMerges.value = data.labels
      .filter((item) => item.sameAs)
      .map((item) => ({
        rid: item.sameAs,
        kind: item.kind || '',
        phrasing: item.phrasing || '',
        existing: knownText(item.sameAs),
        inPool: labels.value.some((label) => label.rid === item.sameAs),
      }))
      .filter((merge) => merge.existing)

    // Each round appends to the pool; earlier labels and the current top 3 stay put.
    const fresh = data.labels
      .filter((item) => !item.sameAs)
      .map((item) => toChip(item, 'ai'))
      .filter((chip) => chip.text && !labels.value.some((item) => sameWording(item.text, chip.text)))
    labels.value = [...labels.value, ...fresh]
    source.value = 'api'
    lastGenerated.value = text
    editingId.value = null
    notify()
    refreshAllUserEchoes()
    if (props.completeOnGenerate) emit('complete')
  } catch (err) {
    if (err?.code === 'cancelled') return
    error.value = err.message || "Couldn't generate labels. Try enter again."
    errorCode.value = err?.code || ''
    emit('error', error.value)
    throw err
  } finally {
    loading.value = false
  }
}

function knownText(rid) {
  return props.knownLabels.find((item) => item.ref === rid)?.text || ''
}

/** How many ideas / relations invoke this rationale. 2+ is what the chip badge reports. */
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

function dropMerge(index) {
  pendingMerges.value = pendingMerges.value.filter((_, i) => i !== index)
}

/**
 * Confirmed reuse. The existing wording always survives; the AI's phrasing is filed as an
 * occurrence so the merge stays auditable. Already in this pool = a repeat on the same idea,
 * so it only bumps the count. Otherwise this idea joins an existing rationale.
 */
function acceptMerge(index) {
  const merge = pendingMerges.value[index]
  if (!merge) return
  const occurrence = { at: new Date().toISOString(), phrasing: merge.phrasing }
  const existing = labels.value.find((item) => item.rid === merge.rid)
  if (existing) {
    existing.count = (existing.count || 1) + 1
    existing.occurrences = [...(existing.occurrences || []), occurrence]
  } else {
    const chip = toChip({ text: merge.existing, kind: merge.kind, rid: merge.rid }, 'ai')
    chip.occurrences = [occurrence]
    labels.value = [...labels.value, chip]
  }
  dropMerge(index)
  notify()
}

/** Refused reuse: the AI's wording becomes its own rationale with its own identity. */
function rejectMerge(index) {
  const merge = pendingMerges.value[index]
  if (!merge) return
  const text = merge.phrasing || merge.existing
  if (text && !labels.value.some((item) => sameWording(item.text, text))) {
    labels.value = [...labels.value, toChip({ text, kind: merge.kind }, 'ai')]
  }
  dropMerge(index)
  notify()
}

function startEdit(label, zone = 'pool') {
  if (editingId.value != null && editingId.value !== label.id) {
    applyEdit(editingId.value, draft.value, editingZone.value === 'pool')
  }
  editingId.value = label.id
  editingZone.value = zone
  draft.value = label.text
  nextTick(() => {
    document.getElementById(`${props.idPrefix}-${zone}-edit-${label.id}`)?.focus()
  })
}

function onDraftInput(e) {
  draft.value = clipUserLabel(e.target.value)
}

function clearShortSuggest() {
  shortSuggest.value = ''
  shortFor.value = ''
}

function acceptShortSuggest() {
  if (!shortSuggest.value || !editingId.value) return
  draft.value = shortSuggest.value
  shortSkip.add(shortSuggest.value)
  clearShortSuggest()
}

function skipShortSuggest() {
  if (shortFor.value) shortSkip.add(shortFor.value)
  clearShortSuggest()
}

async function runShortSuggest(text) {
  if (!editingId.value || text !== clipUserLabel(draft.value) || !needsShortLabel(text)) return
  if (shortSkip.has(text)) return
  shortAbort?.abort()
  shortAbort = new AbortController()
  try {
    const suggestion = await suggestShortLabel(text, { signal: shortAbort.signal })
    if (text !== clipUserLabel(draft.value) || !editingId.value) return
    shortSuggest.value = suggestion
    shortFor.value = suggestion ? text : ''
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
    clearShortSuggest()
  }
}

watch(
  () => (editingId.value ? clipUserLabel(draft.value) : ''),
  (text) => {
    window.clearTimeout(shortTimer)
    shortAbort?.abort()
    if (!needsShortLabel(text) || shortSkip.has(text)) {
      clearShortSuggest()
      return
    }
    shortTimer = window.setTimeout(() => {
      runShortSuggest(text).catch(() => {})
    }, SHORTEN_PAUSE_MS)
  },
)

/** Any text change makes the label human-authored, which turns it yellow. */
function applyEdit(id, rawText, allowDelete) {
  const text = clipUserLabel(rawText)
  const poolItem = labels.value.find((item) => item.id === id)
  const pinnedItem = currentPinned().find((item) => item.id === id)
  if (!text) {
    if (!allowDelete) return
    labels.value = labels.value.filter((item) => item.id !== id)
    if (pinnedItem) setPinned(currentPinned().filter((item) => item.id !== id))
    return
  }
  const previous = poolItem?.text ?? pinnedItem?.text ?? ''
  const source = text !== previous ? 'user' : poolItem?.source || pinnedItem?.source || 'ai'
  if (poolItem) {
    poolItem.text = text
    poolItem.source = source
    if (text !== previous) poolItem.embedding = null
  }
  if (pinnedItem) {
    setPinned(currentPinned().map((item) => (item.id === id ? { ...item, text, source } : item)))
  }
}

function commitEdit(label) {
  if (editingId.value !== label.id) return
  applyEdit(label.id, draft.value, editingZone.value === 'pool')
  editingId.value = null
  editingZone.value = null
  draft.value = ''
  shortSuggest.value = ''
  liveEmbedHits.value = {}
  notify()
  const poolItem = labels.value.find((item) => item.id === label.id)
  if (poolItem?.text) ensureIndexed([poolItem]).catch(() => {})
}

function removeLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  if (editingId.value === label.id) {
    editingId.value = null
    draft.value = ''
  }
  labels.value = labels.value.filter((item) => item.id !== label.id)
  echoByQuery.delete(label.id)
  vectorIndex.remove(label.id)
  paintEchoes()
  if (currentPinned().some((item) => sameRationale(item, label))) {
    setPinned(currentPinned().filter((item) => !sameRationale(item, label)))
  }
  notify()
}

function onEditKeydown(e, label) {
  if (e.key === 'Enter') {
    e.preventDefault()
    e.currentTarget.blur()
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    draft.value = label.text
    editingId.value = null
    editingZone.value = null
  }
}

/** + after generated labels: a human-authored tag, not an extra API call. */
function startAdd() {
  const chip = toChip({ text: '', kind: '' }, 'user')
  labels.value.push(chip)
  startEdit(chip)
}

function dismissError() {
  error.value = ''
  errorCode.value = ''
}

function onAction() {
  emitRationale()
  emit('action')
}

defineExpose({ generate, input, labels })
</script>

<template>
  <section class="module" :class="{ compact }">
    <label class="field">
      <span class="field-label">reflection</span>
      <textarea
        ref="fieldRef"
        v-model="input"
        rows="1"
        :disabled="loading"
        :placeholder="placeholder"
        @keydown="onFieldKeydown"
        @input="autoGrow"
      />
      <span class="hint">press enter when you're ready · shift+enter for a new line</span>
    </label>

    <p v-if="loading" class="status">generating…</p>
    <div v-else-if="error" class="banner" role="alert">
      <p>{{ error }}</p>
      <p class="banner-hint" v-if="errorCode !== 'not_distinguishable'">press enter to try again</p>
      <button type="button" class="btn tiny ghost" @click="dismissError">dismiss</button>
    </div>

    <!-- Reuse proposals: the AI thinks it just restated a rationale you already have. -->
    <div v-for="(merge, i) in pendingMerges" :key="merge.rid" class="merge-ask">
      <p class="merge-line">
        <span class="merge-new">{{ merge.phrasing || 'this reason' }}</span>
        looks like a repeat of
        <span class="merge-old">{{ merge.existing }}</span>
      </p>
      <div class="merge-actions">
        <button type="button" class="btn tiny" @click="acceptMerge(i)">
          {{ merge.inPool ? 'same one' : 'use that label' }}
        </button>
        <button type="button" class="btn tiny ghost" @click="rejectMerge(i)">keep separate</button>
      </div>
    </div>

    <!-- Both sections stay put across rounds; a new generate appends to the pool. -->
    <template v-if="visibleLabels().length">
      <div class="section">
        <span class="section-label">top 3 chosen</span>
        <div
          class="zone chosen"
          :class="{ hot: chosenHot, full: currentPinned().length >= MAX_PINNED }"
          @dragover="onChosenDragOver"
          @dragleave="onChosenDragLeave"
          @drop="onChosenDrop"
        >
          <div
            v-for="group in chosenGroups"
            :key="group.id"
            class="chip-pair"
            :class="{ linked: group.members.length > 1 }"
          >
            <span
              v-for="label in group.members"
              :key="label.id"
              class="chip selected"
              :class="{
                ai: label.source === 'ai',
                user: label.source === 'user',
                editing: editingId === label.id && editingZone === 'chosen',
                dragging: draggingId === label.id,
                echo: isEchoHit(label),
              }"
              :draggable="!(editingId === label.id && editingZone === 'chosen')"
              :title="chipTitle(label)"
              @dragstart="onDragStart($event, label, 'chosen')"
              @dragover="onChosenChipDragOver($event, label)"
              @dragend="onDragEnd"
            >
              <input
                v-if="editingId === label.id && editingZone === 'chosen'"
                :id="`${idPrefix}-chosen-edit-${label.id}`"
                class="chip-input"
                :value="draft"
                :maxlength="MAX_USER_LABEL_CHARS"
                @input="onDraftInput"
                @keydown="onEditKeydown($event, label)"
                @blur="commitEdit(label)"
              />
              <button
                v-else
                type="button"
                class="chip-text"
                @click="startEdit(label, 'chosen')"
              >{{ label.text }}</button>
              <span
                v-if="recurrence(label) > 1"
                class="chip-badge"
                :title="`this rationale is behind ${recurrence(label)} ideas`"
              >×{{ recurrence(label) }}</span>
              <button
                type="button"
                class="chip-del"
                title="Take off the note"
                @mousedown.stop
                @click.stop="unpin(label)"
              >×</button>
            </span>
            <span v-if="groupPatternHint(group)" class="same-hint">{{ groupPatternHint(group) }}</span>
          </div>
          <span v-if="!currentPinned().length" class="zone-empty">drag your top 3 here</span>
        </div>
        <span v-if="pinLimitHint" class="hint warn">3 already chosen — drag one out first</span>
      </div>

      <div class="section">
        <span class="section-label">all rationale labels</span>
        <span class="hint">drag to rank them, then drag your top 3 up · blue is generated by ai</span>
        <div class="zone pool" @dragover="onPoolDragOver" @drop="onPoolDrop">
          <div
            v-for="group in poolGroups"
            :key="group.id"
            class="chip-pair"
            :class="{ linked: group.members.length > 1 }"
          >
            <span
              v-for="label in group.members"
              :key="label.id"
              class="chip"
              :class="{
                editing: editingId === label.id && editingZone === 'pool',
                ai: label.source === 'ai',
                user: label.source === 'user',
                chosen: isPinned(label),
                dragging: draggingId === label.id,
                echo: isEchoHit(label),
              }"
              :draggable="!(editingId === label.id && editingZone === 'pool')"
              :title="chipTitle(label)"
              @dragstart="onDragStart($event, label, 'pool')"
              @dragover="onPoolChipDragOver($event, label)"
              @dragend="onDragEnd"
            >
              <input
                v-if="editingId === label.id && editingZone === 'pool'"
                :id="`${idPrefix}-pool-edit-${label.id}`"
                class="chip-input"
                :value="draft"
                :maxlength="MAX_USER_LABEL_CHARS"
                @input="onDraftInput"
                @keydown="onEditKeydown($event, label)"
                @blur="commitEdit(label)"
              />
              <button
                v-else
                type="button"
                class="chip-text"
                :title="chipTitle(label)"
                @click="startEdit(label)"
              >
                {{ label.text || '…' }}
              </button>
              <span
                v-if="recurrence(label) > 1"
                class="chip-badge"
                :title="`this rationale is behind ${recurrence(label)} ideas`"
              >×{{ recurrence(label) }}</span>
              <button
                type="button"
                class="chip-del"
                title="Delete label"
                @mousedown.stop
                @click.stop="removeLabel(label, $event)"
              >×</button>
            </span>
            <span v-if="groupPatternHint(group)" class="same-hint">{{ groupPatternHint(group) }}</span>
          </div>
          <button
            type="button"
            class="add"
            title="Add your own label"
            @click="startAdd"
          >
            +
          </button>
        </div>
        <span v-if="editingId && echoHint" class="hint echo-hint">{{ echoHint }}</span>
        <div v-if="editingId && shortSuggest" class="shorten-ask">
          <span class="shorten-text">{{ shortSuggest }}</span>
          <button type="button" class="btn tiny" @mousedown.prevent="acceptShortSuggest">use</button>
          <button type="button" class="btn tiny ghost" @mousedown.prevent="skipShortSuggest">keep mine</button>
        </div>
      </div>
    </template>
    <button
      v-if="actionLabel"
      type="button"
      class="btn action"
      :disabled="loading"
      @click="onAction"
    >
      {{ actionLabel }}
    </button>
  </section>
</template>

<style scoped>
.module {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
}

textarea {
  width: 100%;
  box-sizing: border-box;
  resize: none;
  overflow: hidden;
  min-height: 108px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.5;
  outline: none;
}

textarea:focus {
  border-color: rgba(180, 83, 9, 0.45);
}

textarea::placeholder {
  color: var(--ink-faint);
}

.hint {
  font-size: 9px;
  line-height: 1.4;
  letter-spacing: 0.03em;
  color: var(--ink-faint);
}

.btn {
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.03em;
}

.btn:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--ink);
}

.btn.ghost {
  border-color: transparent;
}

.btn.tiny {
  height: 26px;
  padding: 0 8px;
  font-size: 11px;
}

.btn.action {
  align-self: stretch;
  height: 28px;
  margin-top: 2px;
  border-color: var(--line);
  color: var(--ink-muted);
  font-family: inherit;
}

.banner {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid rgba(252, 165, 165, 0.35);
  background: rgba(252, 165, 165, 0.1);
}

.banner p {
  margin: 0;
  flex: 1 1 100%;
  font-size: 12px;
  line-height: 1.45;
  color: #b91c1c;
}

.banner-hint {
  flex: 1;
  font-size: 11px;
  color: var(--ink-faint);
}

.status {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: var(--ink-faint);
}

.merge-ask {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--paper);
}

.merge-line {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--ink-muted);
}

.merge-new,
.merge-old {
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}

.merge-new {
  background: rgba(147, 197, 253, 0.18);
  color: #1e3a5f;
}

.merge-old {
  background: rgba(147, 197, 253, 0.32);
  color: #1e3a5f;
}

.merge-actions {
  display: flex;
  gap: 6px;
}

/* Recurrence, not authorship: colour already says who wrote the label. */
.chip-badge {
  flex-shrink: 0;
  align-self: center;
  margin-left: 2px;
  padding: 0 4px;
  border-radius: 7px;
  background: rgba(0, 0, 0, 0.22);
  color: inherit;
  font-size: 9px;
  font-weight: 700;
  line-height: 14px;
  letter-spacing: 0.02em;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.section-label {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
}

.zone {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px;
  min-height: 34px;
  padding: 6px;
  border-radius: 8px;
}

.zone.chosen {
  border: 1px dashed var(--line);
  background: var(--paper);
}

.zone.chosen.hot {
  border-color: rgba(180, 83, 9, 0.45);
  background: var(--accent-soft);
}

.zone.pool {
  padding: 0;
  min-height: 28px;
}

.zone-empty {
  align-self: center;
  font-size: 11px;
  color: var(--ink-faint);
}

.hint.warn {
  color: rgba(252, 165, 165, 0.8);
}

.hint.echo-hint {
  font-size: 10px;
  line-height: 1.35;
  letter-spacing: 0.02em;
  color: #fbbf24;
}

.shorten-ask {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}

.shorten-text {
  max-width: 100%;
  color: var(--ink-muted);
  font-size: 10px;
  line-height: 1.35;
}

.chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-height: 26px;
  padding: 0;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--chrome);
  cursor: grab;
}

.chip.dragging {
  opacity: 0.4;
}

/* Already on the note: dimmed in the pool so the remaining choices stand out. */
.chip.chosen {
  opacity: 0.4;
}

.chip.chosen.echo {
  opacity: 1;
}

.chip.editing {
  cursor: text;
}

.chip.ai {
  background: rgba(147, 197, 253, 0.16);
  border-color: rgba(147, 197, 253, 0.42);
}

.chip.user {
  background: rgba(253, 230, 138, 0.16);
  border-color: rgba(253, 230, 138, 0.48);
}

/* Existing match only: keep chip colour, pulse a ring in that same hue. */
.chip.echo {
  animation: echo-breathe 2.2s ease-in-out infinite;
}

.chip.ai.echo {
  animation-name: echo-breathe-ai;
}

.chip.user.echo {
  animation-name: echo-breathe-user;
}

@keyframes echo-breathe-ai {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.35);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(147, 197, 253, 0.95);
  }
}

@keyframes echo-breathe-user {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.35);
  }
  50% {
    box-shadow: 0 0 0 5px rgba(253, 230, 138, 0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .chip.ai.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.85);
  }
  .chip.user.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.85);
  }
}

.chip.editing.ai {
  border-color: rgba(147, 197, 253, 0.75);
}

.chip.editing.user {
  border-color: rgba(253, 230, 138, 0.8);
}

.chip-text,
.chip-input {
  margin: 0;
  padding: 4px 10px;
  border: 0;
  background: transparent;
  color: var(--ink);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.4;
}

.chip-text {
  cursor: text;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-align: left;
  max-width: 180px;
}

.chip-pair {
  display: contents;
}

.chip-pair.linked {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  max-width: 100%;
}

.same-hint {
  flex-shrink: 0;
  color: var(--ink-faint);
  font-size: 10px;
  line-height: 1.3;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.chip-del {
  flex-shrink: 0;
  width: 16px;
  height: 16px;
  margin: 0 4px 0 0;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  opacity: 0.45;
  cursor: pointer;
  font-size: 13px;
  line-height: 16px;
}

.chip-del:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.12);
}

.chip.selected {
  box-shadow: inset 0 0 0 1px var(--line);
}

.chip.selected.ai {
  background: rgba(147, 197, 253, 0.32);
  border-color: rgba(147, 197, 253, 0.85);
}

.chip.selected.user {
  background: rgba(253, 230, 138, 0.32);
  border-color: rgba(253, 230, 138, 0.9);
}

.chip.ai .chip-input {
  color: #1e3a5f;
}

.chip.user .chip-input {
  color: #5b4a12;
}

.add {
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 50%;
  border: 1px dashed var(--line);
  background: transparent;
  color: var(--ink-muted);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.add:hover {
  border-color: rgba(180, 83, 9, 0.5);
  color: var(--accent);
}

.module.compact {
  gap: 8px;
}

.module.compact textarea {
  min-height: 72px;
  padding: 8px;
  font-size: 12px;
}

.module.compact .hint,
.module.compact .status,
.module.compact .zone-empty,
.module.compact .banner p {
  white-space: normal;
  overflow-wrap: break-word;
}

.module.compact .section-label {
  font-size: 10px;
}

.module.compact .zone {
  gap: 6px;
  min-height: 30px;
}

.module.compact .chip-text,
.module.compact .chip-input {
  padding: 3px 8px;
  font-size: 11px;
}

.module.compact .chip-del {
  width: 14px;
  height: 14px;
  font-size: 12px;
  line-height: 14px;
}
</style>
