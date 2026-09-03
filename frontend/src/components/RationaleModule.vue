<!--
  Rationale-label module: standalone playground (?ai=1) and under sticky notes.
  Input: designer text. Output: short labels, not a chatbot reply.
  Enter generates (costs an API call). Empty input never generates.
-->
<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { clipWords, generateRationaleLabels } from '../api/rationale'

const MAX_PINNED = 3

const props = defineProps({
  target: { type: String, default: 'generic' }, // generic | note | relation
  idea: { type: String, default: '' }, // sticky-note / relation text the prompt is about
  compact: { type: Boolean, default: false }, // tighter layout under a note
  idPrefix: { type: String, default: 'label' }, // unique ids when many notes share the canvas
  pinned: { type: Array, default: null }, // labels on the note; null = local (playground)
  savedInput: { type: String, default: '' },
  savedLabels: { type: Array, default: null },
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
const source = ref('') // 'api' | ''
const editingId = ref(null)
const draft = ref('')
const pinLimitHint = ref(false)
const localPinned = ref([])

let abortGenerate = null
let nextLabelId = 1
let clickTimer = null
let hydrating = true

function fromSaved(item, fallbackSource) {
  const source = item.source || fallbackSource
  return {
    id: item.id || nextLabelId++,
    text: source === 'user' ? String(item.text || '').trim() : clipWords(item.text),
    kind: item.kind || '',
    source,
  }
}

function seedPresets() {
  return (props.presetLabels || [])
    .filter((item) => item && item.text)
    .map((item) => fromSaved(item, 'user'))
}

if (Array.isArray(props.savedLabels) && props.savedLabels.length) {
  labels.value = props.savedLabels.map((item) => fromSaved(item, 'ai'))
  const maxId = labels.value.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0)
  nextLabelId = maxId + 1
  if (labels.value.some((item) => item.source === 'ai')) source.value = 'api'
} else {
  labels.value = seedPresets()
  const maxId = labels.value.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0)
  nextLabelId = Math.max(nextLabelId, maxId + 1)
}

onUnmounted(() => {
  abortGenerate?.abort()
  clearTimeout(clickTimer)
})

function emitRationale() {
  emit('rationale-change', {
    input: input.value,
    labels: labels.value.map((item) => ({
      id: item.id,
      text: item.text,
      kind: item.kind || '',
      source: item.source,
    })),
    source: source.value,
  })
}

hydrating = false

watch(input, (value) => {
  if (hydrating) return
  if (!value.trim()) {
    const presets = seedPresets()
    labels.value = presets
    source.value = ''
    error.value = ''
    editingId.value = null
    draft.value = ''
    if (!presets.length) setPinned([])
  }
  emitRationale()
})

watch(
  () => props.savedLabels,
  (list) => {
    if (!Array.isArray(list)) return
    labels.value = list.map((item) => ({
      id: item.id || nextLabelId++,
      text: clipWords(item.text),
      kind: item.kind || '',
      source: item.source || 'ai',
    }))
    const maxId = labels.value.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0)
    nextLabelId = Math.max(nextLabelId, maxId + 1)
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
  return { id: label.id, text: label.text, kind: label.kind || '', source: label.source }
}

function isPinned(label) {
  return currentPinned().some((item) => item.id === label.id || item.text === label.text)
}

/** Click edits. Double-click pins (or unpins) as a tab on the note. */
function onChipClick(label) {
  clearTimeout(clickTimer)
  clickTimer = setTimeout(() => {
    clickTimer = null
    startEdit(label)
  }, 250)
}

function onChipDblClick(label) {
  clearTimeout(clickTimer)
  clickTimer = null
  togglePin(label)
}

/** Double-click a chip to pin it on the note (max 3). Double-click again to swap it off. */
function togglePin(label) {
  if (!label.text) return
  const pinned = currentPinned().slice()
  const idx = pinned.findIndex((item) => item.id === label.id || item.text === label.text)
  if (idx >= 0) {
    pinned.splice(idx, 1)
    setPinned(pinned)
    return
  }
  if (pinned.length >= MAX_PINNED) pinned.pop()
  pinned.push(pinSnapshot(label))
  setPinned(pinned)
}

function chipTitle(label) {
  if (isPinned(label)) return 'double-click to take off the note'
  if (currentPinned().length >= MAX_PINNED) return 'already 3 on the note — double-click one to swap'
  return 'click to edit · double-click to pin'
}

/** Generated chips plus any pins that are no longer in the current set (so they can be swapped). */
function visibleLabels() {
  const pool = labels.value
  const extras = currentPinned().filter(
    (item) => !pool.some((label) => label.id === item.id || label.text === item.text),
  )
  return extras.length ? [...pool, ...extras] : pool
}

function toChip(item, origin) {
  return {
    id: nextLabelId++,
    text: clipWords(item.text),
    kind: item.kind || '',
    source: origin,
  }
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
  if (!text) {
    if (!seedPresets().length) {
      labels.value = []
      source.value = ''
      error.value = ''
    }
    return
  }

  abortGenerate?.abort()
  abortGenerate = new AbortController()
  error.value = ''
  loading.value = true
  try {
    const data = await generateRationaleLabels(text, props.target, { signal: abortGenerate.signal })
    labels.value = data.labels.map((item) => toChip(item, 'ai'))
    source.value = 'api'
    editingId.value = null
    setPinned(labels.value.slice(0, MAX_PINNED).map(pinSnapshot))
    notify()
    if (props.completeOnGenerate) emit('complete')
  } catch (err) {
    if (err?.code === 'cancelled') return
    error.value = err.message || "Couldn't generate labels. Try enter again."
    emit('error', error.value)
    throw err
  } finally {
    loading.value = false
  }
}

function startEdit(label) {
  if (editingId.value != null && editingId.value !== label.id) {
    const current = labels.value.find((item) => item.id === editingId.value)
    if (current) finishEdit(current)
  }
  editingId.value = label.id
  draft.value = label.text
  nextTick(() => {
    document.getElementById(`${props.idPrefix}-edit-${label.id}`)?.focus()
  })
}

function onDraftInput(e) {
  draft.value = clipWords(e.target.value)
}

function finishEdit(label) {
  const text = clipWords(draft.value)
  if (!text) {
    labels.value = labels.value.filter((item) => item.id !== label.id)
    if (currentPinned().some((item) => item.id === label.id)) {
      setPinned(currentPinned().filter((item) => item.id !== label.id))
    }
    return
  }
  if (text !== label.text) label.source = 'user'
  label.text = text
  if (currentPinned().some((item) => item.id === label.id)) {
    setPinned(
      currentPinned().map((item) =>
        item.id === label.id ? { ...item, text, source: label.source } : item,
      ),
    )
  }
}

function commitEdit(label) {
  if (editingId.value !== label.id) return
  finishEdit(label)
  editingId.value = null
  draft.value = ''
  notify()
}

function removeLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  clearTimeout(clickTimer)
  if (editingId.value === label.id) {
    editingId.value = null
    draft.value = ''
  }
  labels.value = labels.value.filter((item) => item.id !== label.id)
  if (currentPinned().some((item) => item.id === label.id || item.text === label.text)) {
    setPinned(currentPinned().filter((item) => item.id !== label.id && item.text !== label.text))
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
        v-model="input"
        :rows="compact ? 3 : 5"
        :disabled="loading"
        :placeholder="placeholder"
        @keydown="onFieldKeydown"
      />
      <span class="hint">press enter when you're ready · shift+enter for a new line</span>
    </label>

    <p v-if="loading" class="status">generating…</p>
    <div v-else-if="error" class="banner" role="alert">
      <p>{{ error }}</p>
      <p class="banner-hint">press enter to try again</p>
      <button type="button" class="btn tiny ghost" @click="dismissError">dismiss</button>
    </div>
    <p v-else-if="source === 'api'" class="status">Rationale Label based on reflection</p>
    <p v-if="pinLimitHint && !compact" class="status">3 on the note — click one to swap</p>
    <p v-else-if="currentPinned().length && !compact" class="status">
      {{ currentPinned().length }} / {{ MAX_PINNED }} on the note
    </p>

    <div v-if="!compact || (visibleLabels().length && !loading)" class="labels" aria-live="polite">
      <span v-if="!compact && !visibleLabels().length && !loading" class="empty-labels">no labels yet</span>
      <template v-else>
        <span
          v-for="label in visibleLabels()"
          :key="label.id"
          class="chip"
          :class="{
            editing: editingId === label.id,
            ai: label.source === 'ai',
            user: label.source === 'user',
            selected: isPinned(label),
            full: !isPinned(label) && currentPinned().length >= MAX_PINNED,
          }"
        >
          <input
            v-if="editingId === label.id"
            :id="`${idPrefix}-edit-${label.id}`"
            class="chip-input"
            :value="draft"
            maxlength="48"
            @input="onDraftInput"
            @keydown="onEditKeydown($event, label)"
            @blur="commitEdit(label)"
          />
          <button
            v-else
            type="button"
            class="chip-text"
            :title="chipTitle(label)"
            @click="onChipClick(label)"
            @dblclick.prevent="onChipDblClick(label)"
          >
            {{ label.text || '…' }}
          </button>
          <button
            type="button"
            class="chip-del"
            title="Delete label"
            @mousedown.stop
            @click.stop="removeLabel(label, $event)"
          >×</button>
        </span>
        <button
          v-if="labels.length"
          type="button"
          class="add"
          title="Add a label"
          @click="startAdd"
        >
          +
        </button>
      </template>
    </div>
    <span v-if="!compact && visibleLabels().length && !loading" class="hint">click to edit · double-click to pin · × to delete · max 3</span>
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
  letter-spacing: 0.08em;
  text-transform: lowercase;
  color: rgba(253, 230, 138, 0.65);
}

textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  min-height: 108px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: #1c1c24;
  color: rgba(255, 255, 255, 0.88);
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.5;
  outline: none;
}

textarea:focus {
  border-color: rgba(253, 230, 138, 0.45);
}

textarea::placeholder {
  color: rgba(255, 255, 255, 0.28);
}

.hint {
  font-size: 11px;
  letter-spacing: 0.03em;
  color: rgba(255, 255, 255, 0.28);
}

.btn {
  height: 32px;
  padding: 0 12px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: transparent;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  font-size: 12px;
  letter-spacing: 0.03em;
}

.btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
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
  border-color: rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.55);
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
  color: #fca5a5;
}

.banner-hint {
  flex: 1;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.35);
}

.status {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.04em;
  color: rgba(255, 255, 255, 0.35);
}

.labels {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  min-height: 28px;
}

.empty-labels {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.22);
}

.chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-height: 26px;
  padding: 0;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.07);
}

.chip.ai {
  background: rgba(147, 197, 253, 0.16);
  border-color: rgba(147, 197, 253, 0.42);
}

.chip.user {
  background: rgba(253, 230, 138, 0.16);
  border-color: rgba(253, 230, 138, 0.48);
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
  color: rgba(255, 255, 255, 0.88);
  font-family: inherit;
  font-size: 12px;
  line-height: 1.4;
}

.chip-text {
  cursor: text;
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
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.35);
}

.chip.selected.ai {
  background: rgba(147, 197, 253, 0.32);
  border-color: rgba(147, 197, 253, 0.85);
}

.chip.selected.user {
  background: rgba(253, 230, 138, 0.32);
  border-color: rgba(253, 230, 138, 0.9);
}

.chip.full {
  opacity: 0.45;
}

.chip.ai .chip-input {
  color: #bfdbfe;
}

.chip.user .chip-input {
  color: #fde68a;
}

.add {
  width: 26px;
  height: 26px;
  padding: 0;
  border-radius: 50%;
  border: 1px dashed rgba(255, 255, 255, 0.28);
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
}

.add:hover {
  border-color: rgba(253, 230, 138, 0.5);
  color: #fde68a;
}

.module.compact {
  gap: 8px;
}

.module.compact textarea {
  min-height: 72px;
  padding: 8px;
  font-size: 12px;
  resize: vertical;
}

.module.compact .hint,
.module.compact .status,
.module.compact .empty-labels,
.module.compact .banner p {
  white-space: normal;
  overflow-wrap: break-word;
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
