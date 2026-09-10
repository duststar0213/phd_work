<!--
  Rationale-label module: standalone playground (?ai=1) and under sticky notes.
  Enter generates from the sticky-note idea plus the reflection field.
  Suggestions are a temporary tray: edit, drag to top 3, then confirm.
  Confirm deletes unused unmodified AI. Yellow / chosen labels are kept.
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
  askWhyQuestion,
  ideaReadyForWhyQuestion,
  generateRationaleLabels,
  isSimilarLabel,
  clusterSimilarLabels,
  LabelVectorIndex,
  lexicalMeaningHits,
  LIVE_EMBED_MS,
  meaningHitsFromVectors,
  NOT_SURE_LABEL,
  NOT_SURE_RID,
  readyForLiveEmbed,
  rememberDraftVector,
  sameWording,
  persistedRationaleLabels,
} from '../api/rationale'
import LabelPeek from './LabelPeek.vue'

const MAX_PINNED = 3

/** Canvas-scoped rationale identity; labels sharing a rid are the same reasoning. */
function newRid() {
  return `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`
}

const props = defineProps({
  target: { type: String, default: 'generic' }, // generic | note | relation | group
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
  ownerDirectory: { type: Object, default: () => ({}) }, // n12 / c3 -> { key, kind, id, title }
  context: { type: String, default: '' }, // the designer's standing brief; background for the AI only
  placeholder: { type: String, default: 'simply write a reflection on why you have this idea' },
  presetLabels: { type: Array, default: () => [] }, // chips shown on open so the user can skip writing
  actionLabel: { type: String, default: '' }, // optional extra button, e.g. "just abandon it"
  completeOnGenerate: { type: Boolean, default: false }, // emit complete after a successful Enter generate
  active: { type: Boolean, default: true }, // false while the dock is hidden; skip AI guesses
})

const emit = defineEmits(['labels', 'error', 'pin-change', 'rationale-change', 'action', 'complete', 'inspect-pattern'])

const input = ref(props.savedInput || '')
const labels = ref([])
/** Fresh AI chips not yet chosen or edited — discarded if the panel closes unused. */
const suggestions = ref([])
/** True until the person confirms this generate round (leftover AI is then deleted). */
const suggestRound = ref(false)
const loading = ref(false)
const error = ref('')
const errorCode = ref('')
/** Not an error: the addition carried no new reasoning. Offers a re-read of the whole field. */
const noNewNotice = ref('')
const source = ref('') // 'api' | ''
const editingId = ref(null)
const editingZone = ref(null) // which copy of the chip holds the caret
const draft = ref('')
const pinLimitHint = ref(false)
const localPinned = ref([])
const draggingId = ref(null) // label being dragged
const dragOrigin = ref(null) // 'suggest' | 'chosen'
const chosenHot = ref(false) // chosen zone is a live drop target
const pendingMerges = ref([]) // reuse hits awaiting a yes / no from the person
// Last text that actually reached the model. Empty means the next Enter is a first generate.
const lastGenerated = ref('')

const fieldRef = ref(null)
/** AI question about this idea; the person answers it, so it never enters the field. */
const whyQuestion = ref('')
/** Everything already asked here, so a later question cannot repeat an earlier one. */
let askedQuestions = []
let askedIdea = ''
let askedAnswerLength = -1
/** New rationale the person must write before the AI is allowed to ask again. */
const ANSWER_STEP = 24

let abortGenerate = null
let abortQuestion = null
let ideaPromptTimer = 0
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
  const incoming = props.savedLabels.map((item) => fromSaved(item, 'ai'))
  labels.value = persistedRationaleLabels(incoming, props.pinned)
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
  abortQuestion?.abort()
  window.clearTimeout(ideaPromptTimer)
  abortEcho?.abort()
  liveEmbedAbort?.abort()
  window.clearTimeout(liveEmbedTimer)
  shortAbort?.abort()
  window.clearTimeout(shortTimer)
  fieldObserver?.disconnect()
})

const fieldPlaceholder = computed(() => props.placeholder)

function clearWhyQuestion() {
  whyQuestion.value = ''
  askedIdea = ''
  askedAnswerLength = -1
}

/** × drops the bubble; the next rewrite or new rationale earns the next question. */
function dismissWhyQuestion() {
  whyQuestion.value = ''
}

function shouldAskAboutIdea() {
  return props.target === 'note' && !props.completeOnGenerate && props.active
}

/** A rewritten idea always earns a new question; a growing answer earns one every ANSWER_STEP. */
function askIsDue() {
  const text = String(props.idea || '').trim()
  if (!ideaReadyForWhyQuestion(text)) return false
  if (text !== askedIdea) return true
  return Math.abs(String(input.value || '').trim().length - askedAnswerLength) >= ANSWER_STEP
}

async function refreshIdeaQuestion() {
  const text = String(props.idea || '').trim()
  const answer = String(input.value || '').trim()
  if (!shouldAskAboutIdea() || !askIsDue()) return
  abortQuestion?.abort()
  abortQuestion = new AbortController()
  try {
    const question = await askWhyQuestion(text, {
      answer,
      asked: askedQuestions,
      context: props.context,
      signal: abortQuestion.signal,
    })
    if (String(props.idea || '').trim() !== text || !shouldAskAboutIdea()) return
    askedIdea = text
    askedAnswerLength = answer.length
    if (!question || askedQuestions.some((item) => isSimilarLabel(item, question))) return
    whyQuestion.value = question
    askedQuestions = [...askedQuestions, question].slice(-8)
  } catch (err) {
    if (err?.code === 'cancelled' || err?.name === 'AbortError') return
    if (String(props.idea || '').trim() === text) whyQuestion.value = ''
  }
}

// The idea, the answer so far, and the panel opening all decide when the next question is due.
watch(
  () => [props.idea, input.value, props.target, props.completeOnGenerate, props.active],
  () => {
    window.clearTimeout(ideaPromptTimer)
    abortQuestion?.abort()
    if (!ideaReadyForWhyQuestion(props.idea)) {
      clearWhyQuestion()
      return
    }
    if (!shouldAskAboutIdea() || !askIsDue()) return
    ideaPromptTimer = window.setTimeout(refreshIdeaQuestion, 1200)
  },
  { immediate: true },
)

function emitRationale() {
  const pinned = Array.isArray(props.pinned) ? props.pinned : localPinned.value
  emit('rationale-change', {
    input: input.value,
    labels: persistedRationaleLabels(
      labels.value.map((item) => ({
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
      pinned,
    ),
    source: source.value,
  })
}

hydrating = false

// Clearing the field never clears labels: chosen and yellow labels stay.
watch(input, () => {
  nextTick(autoGrow) // covers programmatic changes, not just typing
  if (hydrating) return
  emitRationale()
})

watch(
  () => props.savedInput,
  (value) => {
    const next = value || ''
    if (input.value === next) return
    hydrating = true
    input.value = next
    nextTick(() => {
      hydrating = false
      autoGrow()
    })
  },
)

watch(
  () => props.savedLabels,
  (list) => {
    if (!Array.isArray(list)) return
    const incoming = persistedRationaleLabels(
      list.map((item) => fromSaved(item, 'ai')),
      Array.isArray(props.pinned) ? props.pinned : localPinned.value,
    )
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
  return {
    id: label.id,
    text: label.text,
    kind: label.kind || '',
    source: label.source,
    rid: label.rid,
  }
}

function isPinned(label) {
  return currentPinned().some((item) => sameRationale(item, label))
}

function chipTitle(label) {
  if (isEchoSource(label) || isEchoHit(label)) return echoHint.value || 'close to an existing label'
  if (isPinned(label)) return 'already chosen — take it off with ×'
  if (label.source !== 'ai' || isPinned(label)) return 'click to edit'
  if (currentPinned().length >= MAX_PINNED) return '3 already chosen — take one off first'
  return 'click to edit · drag to top 3'
}

const chosenGroups = computed(() => clusterSimilarLabels(currentPinned()))
const suggestionGroups = computed(() => clusterSimilarLabels(suggestions.value.filter((item) => item.text)))

function workingLabels() {
  return [...labels.value, ...suggestions.value]
}

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
  for (const label of workingLabels()) {
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
  for (const label of workingLabels()) {
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
  for (const item of meaningHitsFromVectors(vec, workingLabels(), { excludeId: editingId.value })) {
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
    const pool = workingLabels().filter((item) => item.text)
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
  return workingLabels().some((item) => item.id !== label.id && (liveHits.value[item.id] || liveEmbedHits.value[item.id]))
}

const echoHint = computed(() => {
  if (!editingId.value) return ''
  const hasHit = workingLabels().some(
    (label) =>
      label.id !== editingId.value &&
      label.text &&
      (liveHits.value[label.id] || liveEmbedHits.value[label.id]),
  )
  if (hasHit) return 'close to an existing label'
  const query = liveQueryText()
  if (!query) return ''
  const localRids = new Set(workingLabels().map((item) => item.rid).filter(Boolean))
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
  const pool = workingLabels().filter((item) => item.text)
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
  for (const label of workingLabels()) {
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

/** Hovering another suggestion while dragging re-ranks this round's AI chips. */
function onSuggestChipDragOver(e, target) {
  if (dragOrigin.value !== 'suggest' || draggingId.value == null) return
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  const next = reorder(suggestions.value, draggingId.value, target.id)
  if (next) suggestions.value = next
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
  return workingLabels().find((item) => String(item.id) === String(id))
}

/** Chosen zone takes exactly 3 — a 4th drop is refused rather than swapped. */
function canDropInChosen() {
  const label = workingLabels().find((item) => String(item.id) === String(draggingId.value))
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

function adoptLabel(label) {
  suggestions.value = suggestions.value.filter((item) => item.id !== label.id)
  if (!labels.value.some((item) => item.id === label.id)) {
    labels.value = [...labels.value, label]
  }
}

function chooseIntoTop3(label) {
  if (!label?.text || isPinned(label)) return
  if (currentPinned().length >= MAX_PINNED) {
    pinLimitHint.value = true
    return
  }
  adoptLabel(label)
  setPinned([...currentPinned(), pinSnapshot(label)])
  notify()
}

function confirmSuggestions() {
  if (editingId.value != null) {
    applyEdit(editingId.value, draft.value, editingZone.value === 'suggest')
    editingId.value = null
    editingZone.value = null
    draft.value = ''
  }
  const keepYellow = suggestions.value.filter((item) => item.source === 'user' && String(item.text || '').trim())
  for (const item of keepYellow) {
    if (!labels.value.some((row) => row.id === item.id)) {
      labels.value = [...labels.value, item]
    }
  }
  suggestions.value = []
  pendingMerges.value = []
  suggestRound.value = false
  pinLimitHint.value = false
  notify()
  if (props.completeOnGenerate) emit('complete')
}

function discardSuggestion(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  suggestions.value = suggestions.value.filter((item) => item.id !== label.id)
}

function onChosenDrop(e) {
  e.preventDefault()
  chosenHot.value = false
  const label = draggedLabel(e)
  const origin = dragOrigin.value
  draggingId.value = null
  dragOrigin.value = null
  if (origin === 'chosen') return // order was already applied while dragging
  chooseIntoTop3(label)
}

function unpin(label) {
  if (!isPinned(label)) return
  pinLimitHint.value = false
  setPinned(currentPinned().filter((item) => !sameRationale(item, label)))
  if (label.source === 'user') {
    notify()
    return
  }
  labels.value = labels.value.filter((item) => !sameRationale(item, label))
  if (suggestRound.value && !suggestions.value.some((item) => item.id === label.id)) {
    suggestions.value = [...suggestions.value, { ...label, source: 'ai' }]
  }
  notify()
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
    if (!isPinned(existing) && currentPinned().length < MAX_PINNED) {
      setPinned([...currentPinned(), pinSnapshot(existing)])
    }
    return
  }
  const chip = toChip({ text: NOT_SURE_LABEL, kind: 'question', rid: NOT_SURE_RID }, 'user')
  labels.value = [...labels.value, chip]
  if (currentPinned().length < MAX_PINNED) setPinned([...currentPinned(), pinSnapshot(chip)])
}

/** Enter in the field: generate from the idea plus this reflection. */
function onFieldKeydown(e) {
  if (e.key !== 'Enter' || e.shiftKey) return
  e.preventDefault()
  generate().catch(() => {})
}

function generateFingerprint() {
  const idea = String(props.idea || '').trim()
  const reflection = String(input.value || '').trim()
  return [idea, reflection].filter(Boolean).join('\n\n')
}

/**
 * Ask OpenAI for labels from the sticky-note idea and/or the reflection field.
 * `fresh` re-reads the whole reflection instead of only what was added since last time.
 */
async function generate(options = {}) {
  const fresh = options?.fresh === true
  const reflection = input.value.trim()
  const idea = String(props.idea || '').trim()
  if (loading.value) return
  if (!reflection && !idea) return

  const fingerprint = generateFingerprint()
  const alreadyLabelled = fresh ? '' : lastGenerated.value
  const verdict = assessReflection(fingerprint, alreadyLabelled)
  if (!verdict.ok) {
    // Writing on top of labelled text is normal, so say so plainly instead of erroring.
    if (verdict.code === 'not_distinguishable') {
      error.value = ''
      errorCode.value = ''
      noNewNotice.value = 'no new reasoning in what you added'
      return
    }
    error.value = verdict.message
    errorCode.value = verdict.code || ''
    return
  }
  if (verdict.preset) {
    applyUnsureLabel()
    lastGenerated.value = fingerprint
    error.value = ''
    errorCode.value = ''
    noNewNotice.value = ''
    editingId.value = null
    notify()
    if (props.completeOnGenerate) emit('complete')
    return
  }

  abortGenerate?.abort()
  abortGenerate = new AbortController()
  error.value = ''
  errorCode.value = ''
  noNewNotice.value = ''
  loading.value = true
  try {
    const data = await generateRationaleLabels(reflection, props.target, {
      idea,
      known: props.knownLabels,
      previous: alreadyLabelled,
      context: props.context,
      signal: abortGenerate.signal,
    })

    if (data.reason === 'nothing_new' && !data.labels.length) {
      noNewNotice.value = 'nothing new to label in what you added'
      lastGenerated.value = fingerprint
      return
    }

    // The model points at an existing rationale with same_as instead of coining a near-duplicate.
    // Those are proposals only — nothing merges until the person confirms it.
    pendingMerges.value = data.labels
      .filter((item) => item.sameAs)
      .map((item) => ({
        rid: item.sameAs,
        kind: item.kind || '',
        phrasing: item.phrasing || '',
        existing: knownText(item.sameAs),
        inPool: workingLabels().some((label) => label.rid === item.sameAs),
      }))
      .filter((merge) => merge.existing)

    const known = workingLabels()
    const fresh = data.labels
      .filter((item) => !item.sameAs)
      .map((item) => toChip(item, 'ai'))
      .filter((chip) => chip.text && !known.some((item) => sameWording(item.text, chip.text)))
    suggestions.value = fresh
    suggestRound.value = true
    bumpNextLabelId(fresh)
    source.value = 'api'
    lastGenerated.value = fingerprint
    editingId.value = null
    notify()
    refreshAllUserEchoes()
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
    if (!isPinned(existing) && currentPinned().length < MAX_PINNED) {
      setPinned([...currentPinned(), pinSnapshot(existing)])
    }
  } else {
    const chip = toChip({ text: merge.existing, kind: merge.kind, rid: merge.rid }, 'user')
    chip.occurrences = [occurrence]
    labels.value = [...labels.value, chip]
    if (currentPinned().length < MAX_PINNED) setPinned([...currentPinned(), pinSnapshot(chip)])
  }
  dropMerge(index)
  notify()
}

/** Refused reuse: the AI's wording becomes its own rationale with its own identity. */
function rejectMerge(index) {
  const merge = pendingMerges.value[index]
  if (!merge) return
  const text = merge.phrasing || merge.existing
  if (
    text &&
    !workingLabels().some((item) => sameWording(item.text, text))
  ) {
    const chip = toChip({ text, kind: merge.kind }, 'ai')
    suggestions.value = [...suggestions.value, chip]
    bumpNextLabelId([chip])
  }
  dropMerge(index)
}

function startEdit(label, zone = 'chosen') {
  if (editingId.value != null && editingId.value !== label.id) {
    applyEdit(editingId.value, draft.value, editingZone.value === 'suggest')
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
  const suggestItem = suggestions.value.find((item) => item.id === id)
  const pinnedItem = currentPinned().find((item) => item.id === id)
  if (!text) {
    if (!allowDelete) return
    labels.value = labels.value.filter((item) => item.id !== id)
    suggestions.value = suggestions.value.filter((item) => item.id !== id)
    if (pinnedItem) setPinned(currentPinned().filter((item) => item.id !== id))
    return
  }
  const previous = poolItem?.text ?? suggestItem?.text ?? pinnedItem?.text ?? ''
  const source = text !== previous ? 'user' : poolItem?.source || suggestItem?.source || pinnedItem?.source || 'ai'
  if (poolItem) {
    poolItem.text = text
    poolItem.source = source
    if (text !== previous) poolItem.embedding = null
  }
  if (suggestItem) {
    suggestItem.text = text
    suggestItem.source = source
    if (text !== previous) suggestItem.embedding = null
    if (source === 'user' && !labels.value.some((row) => row.id === suggestItem.id)) {
      labels.value = [...labels.value, suggestItem]
    }
  }
  if (pinnedItem) {
    setPinned(currentPinned().map((item) => (item.id === id ? { ...item, text, source } : item)))
  }
}

function commitEdit(label) {
  if (editingId.value !== label.id) return
  applyEdit(label.id, draft.value, !String(label.text || '').trim() || editingZone.value === 'suggest')
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

/** + in the top 3: a human-authored tag, not an extra API call. */
function startAdd() {
  if (currentPinned().length >= MAX_PINNED) {
    pinLimitHint.value = true
    return
  }
  const chip = toChip({ text: '', kind: '' }, 'user')
  labels.value.push(chip)
  setPinned([...currentPinned(), pinSnapshot(chip)])
  startEdit(chip, 'chosen')
}

function dismissError() {
  error.value = ''
  errorCode.value = ''
}

/** The addition looked spent, but the person disagrees: label the whole reflection again. */
function generateFresh() {
  noNewNotice.value = ''
  generate({ fresh: true })
}

function onAction() {
  emitRationale()
  emit('action')
}

defineExpose({ generate, input, labels })
</script>

<template>
  <section class="module" :class="{ compact }">
    <!-- Sticker, not panel furniture: it hangs off the field and can be flicked away. -->
    <div v-if="whyQuestion" class="why-ask">
      <p class="why-ask-text">{{ whyQuestion }}</p>
      <button
        type="button"
        class="why-ask-close"
        title="Dismiss this question"
        @mousedown.prevent
        @click.stop="dismissWhyQuestion"
      >×</button>
    </div>
    <label class="field">
      <span class="field-label">Rationale</span>
      <textarea
        ref="fieldRef"
        v-model="input"
        rows="1"
        :disabled="loading"
        :placeholder="fieldPlaceholder"
        @keydown="onFieldKeydown"
        @input="autoGrow"
      />
      <span class="hint">press enter to suggest labels · shift+enter for a new line</span>
    </label>

    <p v-if="loading" class="status">generating…</p>
    <div v-else-if="error" class="banner" role="alert">
      <p>{{ error }}</p>
      <p class="banner-hint" v-if="errorCode !== 'not_distinguishable'">press enter to try again</p>
      <button type="button" class="btn tiny ghost" @click="dismissError">dismiss</button>
    </div>
    <div v-else-if="noNewNotice" class="notice">
      <p>{{ noNewNotice }}</p>
      <button type="button" class="btn tiny ghost" @click="generateFresh">label it anyway</button>
      <button type="button" class="btn tiny ghost" @click="noNewNotice = ''">dismiss</button>
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

    <!-- Panel shows only the top 3. Unused AI lives in a temporary suggestion row. -->
    <div class="section">
        <span class="section-label">top 3 rationale labels chosen</span>
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
              ><LabelPeek :text="label.text" prefer="below" /></button>
              <button
                type="button"
                class="chip-del"
                title="Take off the note"
                @mousedown.stop
                @click.stop="unpin(label)"
              >×</button>
            </span>
          </div>
          <button
            v-if="currentPinned().length < MAX_PINNED"
            type="button"
            class="add"
            title="Add your own label"
            @click="startAdd"
          >
            +
          </button>
          <span v-if="!currentPinned().length" class="zone-empty">drag a generated label here, or add your own</span>
        </div>
        <span v-if="pinLimitHint" class="hint warn">3 already chosen — take one off first</span>
        <span v-if="editingId && echoHint" class="hint echo-hint">{{ echoHint }}</span>
        <div v-if="editingId && shortSuggest" class="shorten-ask">
          <span class="shorten-text">{{ shortSuggest }}</span>
          <button type="button" class="btn tiny" @mousedown.prevent="acceptShortSuggest">use</button>
          <button type="button" class="btn tiny ghost" @mousedown.prevent="skipShortSuggest">keep mine</button>
        </div>
      </div>

      <div v-if="suggestRound" class="section">
        <span class="section-label">rationale labels generating this round</span>
        <span class="hint">edit or drag to top 3 · confirm deletes unused AI</span>
        <div class="zone pool">
          <div
            v-for="group in suggestionGroups"
            :key="group.id"
            class="chip-pair"
            :class="{ linked: group.members.length > 1 }"
          >
            <span
              v-for="label in group.members"
              :key="label.id"
              class="chip"
              :class="{
                editing: editingId === label.id && editingZone === 'suggest',
                ai: label.source === 'ai',
                user: label.source === 'user',
                dragging: draggingId === label.id,
                echo: isEchoHit(label),
              }"
              :draggable="!(editingId === label.id && editingZone === 'suggest')"
              :title="chipTitle(label)"
              @dragstart="onDragStart($event, label, 'suggest')"
              @dragover="onSuggestChipDragOver($event, label)"
              @dragend="onDragEnd"
            >
              <input
                v-if="editingId === label.id && editingZone === 'suggest'"
                :id="`${idPrefix}-suggest-edit-${label.id}`"
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
                @click="startEdit(label, 'suggest')"
              ><LabelPeek :text="label.text" prefer="below" /></button>
              <button
                type="button"
                class="chip-del"
                title="Discard this generated label"
                @mousedown.stop
                @click.stop="discardSuggestion(label, $event)"
              >×</button>
            </span>
          </div>
          <span v-if="!suggestionGroups.length" class="zone-empty">chosen labels are in top 3 · confirm to finish</span>
        </div>
        <button
          type="button"
          class="btn action"
          :disabled="loading"
          @click="confirmSuggestions"
        >
          confirm labels
        </button>
      </div>
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
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
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

/* Speech bubble hanging off the right of the field, over the canvas. */
.why-ask {
  position: absolute;
  left: calc(100% + 14px);
  top: 22px;
  z-index: 12;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  width: 200px;
  padding: 8px 8px 8px 10px;
  border: 1px solid #a8caf4;
  border-radius: 12px;
  background: #e8f1fd;
  box-shadow: 0 6px 18px rgba(30, 58, 95, 0.18);
}

/* Two stacked triangles point back at the field: the outer is the border, the inner fills it. */
.why-ask::before,
.why-ask::after {
  content: '';
  position: absolute;
  top: 14px;
  width: 0;
  height: 0;
  border-style: solid;
}

.why-ask::before {
  left: -9px;
  border-width: 8px 9px 8px 0;
  border-color: transparent #a8caf4 transparent transparent;
}

.why-ask::after {
  left: -7px;
  border-width: 7px 8px 7px 0;
  border-color: transparent #e8f1fd transparent transparent;
}

.why-ask-text {
  flex: 1;
  min-width: 0;
  margin: 0;
  color: #1e3a5f;
  font-size: 12px;
  line-height: 1.45;
  overflow-wrap: break-word;
}

.why-ask-close {
  flex: none;
  width: 16px;
  height: 16px;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #1e3a5f;
  opacity: 0.5;
  cursor: pointer;
  font-size: 13px;
  line-height: 16px;
}

.why-ask-close:hover {
  opacity: 1;
  background: rgba(30, 58, 95, 0.12);
}

.hint {
  font-size: 9px;
  line-height: 1.4;
  letter-spacing: 0.03em;
  color: var(--ink-faint);
  min-width: 0;
  max-width: 100%;
  white-space: normal;
  overflow-wrap: anywhere;
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

.notice {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: var(--paper);
}

.notice p {
  margin: 0;
  flex: 1 1 auto;
  font-size: 11px;
  line-height: 1.45;
  color: var(--ink-muted);
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
  min-width: 0;
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
  min-width: 0;
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
  align-items: flex-start;
  max-width: 100%;
  height: auto;
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
  display: block;
  max-width: 160px;
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
  overflow: hidden;
}

.chip-pair {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 6px;
  min-width: 0;
  max-width: 100%;
}

.pattern-block {
  flex-basis: 100%;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  max-width: 100%;
}

.same-hint {
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink-faint);
  font: inherit;
  font-size: 10px;
  line-height: 1.35;
  letter-spacing: 0.02em;
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
  max-width: 100%;
}

.same-hint.live {
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
}

.same-hint.live:hover,
.same-hint.open {
  color: var(--accent);
}

.pattern-places {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
  max-width: 100%;
}

.pattern-place {
  display: block;
  width: 100%;
  margin: 0;
  padding: 3px 0;
  border: 0;
  background: transparent;
  color: var(--ink);
  font: inherit;
  font-size: 10px;
  line-height: 1.35;
  text-align: left;
  cursor: pointer;
  overflow-wrap: anywhere;
}

.pattern-place:hover {
  color: var(--accent);
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
