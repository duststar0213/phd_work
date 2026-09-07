<!--
  Mid-line rationale control for a connector: same + / tabs as a sticky note,
  sitting on the curve. Click + opens (or, if already open, adds a human tab).
-->
<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
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

const MAX_PINNED = 3
const GHOST_TAB = 'add your label'

const props = defineProps({
  connection: { type: Object, required: true },
  open: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
  abandoned: { type: Boolean, default: false },
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

const emit = defineEmits(['toggle-rationale', 'open-rationale', 'pin-change', 'labels-change'])

const addHover = ref(false)
const creating = ref(false)
const createDraft = ref('')
const editingTabId = ref(null)
const tabDraft = ref('')

const pinnedLabels = computed(() =>
  Array.isArray(props.connection.pinnedLabels) ? props.connection.pinnedLabels.slice(0, MAX_PINNED) : [],
)

const allLabels = computed(() =>
  Array.isArray(props.connection.rationaleLabels) ? props.connection.rationaleLabels : [],
)

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
  const owner = `c${props.connection.id}`
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

const tabGroups = computed(() => clusterSimilarLabels(pinnedLabels.value))

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

onUnmounted(() => {
  liveEmbedAbort?.abort()
  window.clearTimeout(liveEmbedTimer)
  shortAbort?.abort()
  window.clearTimeout(shortTimer)
})

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

/** × on a tab only takes the label off the line; it stays in the reflection pool. */
function unpinLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  setPinned(pinnedLabels.value.filter((item) => item.id !== label.id && item.text !== label.text))
}

/** Tabs are editable in place; an edit makes the label human-authored (yellow). */
function startTabEdit(label) {
  emit('open-rationale')
  editingTabId.value = label.id
  tabDraft.value = label.text
  nextTick(() => {
    document.getElementById(`rel-tab-edit-${props.connection.id}-${label.id}`)?.focus()
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

/** Closed: open reflection. Already open: same as the note + (draft a human tab). */
function onAddClick(e) {
  e.stopPropagation()
  if (!props.open) {
    emit('open-rationale')
    return
  }
  if (creating.value) return
  beginCreateTab()
}

function beginCreateTab() {
  addHover.value = false
  creating.value = true
  createDraft.value = ''
  nextTick(() => {
    document.getElementById(`rel-tab-new-${props.connection.id}`)?.focus()
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
  // Shortcut straight onto the line, but the 3 chosen slots stay strict.
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

</script>

<template>
  <div
    class="relation-marker"
    :class="{ selected, abandoned }"
    @pointerdown.stop
    @mousedown.stop
    @click.stop
  >
    <div v-if="tabGroups.length" class="tab-stack">
      <div
        v-for="group in tabGroups"
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
            :id="`rel-tab-edit-${connection.id}-${label.id}`"
            class="tab-input"
            :value="tabDraft"
            :maxlength="MAX_USER_LABEL_CHARS"
            @input="onTabDraftInput"
            @keydown="onTabEditKeydown($event, label)"
            @blur="commitTabEdit(label)"
            @click.stop
          />
          <span
            v-else
            class="tab-label"
            title="Click to edit"
            @click.stop="startTabEdit(label)"
          >{{ label.text }}</span>
          <span
            v-if="recurrence(label) > 1"
            class="tab-badge"
            :title="`this rationale is behind ${recurrence(label)} ideas`"
          >×{{ recurrence(label) }}</span>
          <button
            v-if="!abandoned"
            type="button"
            class="tab-del"
            title="Take off the line"
            @pointerdown.stop
            @mousedown.stop
            @click.stop="unpinLabel(label, $event)"
          >×</button>
        </span>
        <span v-if="groupPatternHint(group)" class="tab-same-hint">{{ groupPatternHint(group) }}</span>
      </div>
    </div>
    <button
      v-if="open && !abandoned"
      type="button"
      class="edge-fold open"
      title="Hide reflection"
      @click.stop="emit('toggle-rationale')"
    >
      <svg viewBox="0 0 12 8" aria-hidden="true">
        <path d="M2 2.5 L6 6 L10 2.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>
    <div
      v-if="!abandoned"
      class="edge-add-wrap"
      @mouseenter="addHover = true"
      @mouseleave="addHover = false"
    >
      <span v-if="creating" class="edge-tab user draft">
        <input
          :id="`rel-tab-new-${connection.id}`"
          class="tab-input"
          :value="createDraft"
          :maxlength="MAX_USER_LABEL_CHARS"
          placeholder="rationale label of this link"
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
        @click="onAddClick"
      >+</button>
    </div>
  </div>
</template>

<style scoped>
.relation-marker.selected {
  box-shadow: 0 0 0 1.5px #8ec8ff;
}

.relation-marker.abandoned {
  pointer-events: none;
  box-shadow: none;
}

.tab-stack {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 2px);
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  padding: 2px;
  background: #16161d;
  border-radius: 3px;
  z-index: 4;
}

.edge-tab {
  box-sizing: border-box;
  display: inline-flex;
  align-items: flex-start;
  gap: 2px;
  width: max-content;
  max-width: 88px;
  padding: 2px 4px 2px 6px;
  border-radius: 3px;
  box-shadow: 1px 1px 3px rgba(0, 0, 0, 0.18);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.25;
  letter-spacing: 0.01em;
  text-align: left;
  overflow-wrap: break-word;
  word-break: normal;
  cursor: pointer;
}

.edge-tab.ai {
  background: rgba(147, 197, 253, 0.82);
  border: 1px solid rgba(147, 197, 253, 0.9);
  color: #1e3a5f;
}

.edge-tab.user {
  background: rgba(253, 230, 138, 0.92);
  border: 1px solid rgba(253, 230, 138, 0.95);
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
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.4);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(147, 197, 253, 0.95);
  }
}

@keyframes echo-breathe-user {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.4);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(253, 230, 138, 0.95);
  }
}

@media (prefers-reduced-motion: reduce) {
  .edge-tab.ai.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(147, 197, 253, 0.9);
  }
  .edge-tab.user.echo {
    animation: none;
    box-shadow: 0 0 0 2px rgba(253, 230, 138, 0.9);
  }
}

.edge-tab.ghost {
  position: absolute;
  left: calc(100% + 6px);
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
  left: calc(100% + 6px);
  top: 50%;
  transform: translateY(-50%);
  z-index: 3;
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
  font-size: 9px;
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
  align-items: center;
  gap: 2px;
  width: max-content;
}

.tab-same-hint {
  max-width: 92px;
  padding: 0 4px;
  color: rgba(255, 255, 255, 0.5);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 8px;
  line-height: 1.2;
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
  align-items: center;
}

.tab-shorten {
  position: absolute;
  left: calc(100% + 8px);
  top: calc(50% + 16px);
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
  top: 50%;
  z-index: 4;
  width: max-content;
  max-width: 180px;
  transform: translateY(-50%);
  color: #ca8a04;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 9px;
  line-height: 1.35;
  pointer-events: none;
}

.edge-fold,
.edge-add {
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.28);
  color: rgba(22, 22, 29, 0.78);
  cursor: pointer;
}

.edge-fold {
  width: 16px;
  height: 12px;
}

.edge-add {
  width: 16px;
  height: 12px;
  font-size: 10px;
  line-height: 12px;
}

.edge-fold:hover,
.edge-add:hover {
  background: rgba(255, 255, 255, 0.45);
  color: rgba(22, 22, 29, 0.92);
}

.edge-fold svg {
  width: 9px;
  height: 6px;
  display: block;
  transition: transform 0.14s ease;
}

.edge-fold.open svg {
  transform: rotate(180deg);
}

</style>
