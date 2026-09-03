<!--
  Rationale-label module: standalone playground (?ai=1) and under sticky notes.
  Input: designer text. Output: short labels, not a chatbot reply.
  Enter generates (costs an API call). Empty input never generates.
-->
<script setup>
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
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
const editingZone = ref(null) // which copy of the chip holds the caret
const draft = ref('')
const pinLimitHint = ref(false)
const localPinned = ref([])
const draggingId = ref(null) // label being dragged between the two sections
const dragOrigin = ref(null) // 'pool' | 'chosen' — decides re-rank vs pin/unpin
const chosenHot = ref(false) // chosen zone is a live drop target

const fieldRef = ref(null)

let abortGenerate = null
let nextLabelId = 1
let hydrating = true
let fieldObserver = null
let fieldWidth = 0

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
    })),
    source: source.value,
  })
}

hydrating = false

watch(input, (value) => {
  nextTick(autoGrow) // covers programmatic changes, not just typing
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
    labels.value = list.map((item) => fromSaved(item, 'ai'))
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

function chipTitle(label) {
  if (isPinned(label)) return 'already chosen — drag it back down to take it off'
  if (currentPinned().length >= MAX_PINNED) return '3 already chosen — drag one out first'
  return 'click to edit · drag to rank or to choose'
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
  setPinned(currentPinned().filter((item) => item.id !== label.id && item.text !== label.text))
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
    setPinned([]) // nothing reaches the note until the person drags it up
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
  draft.value = clipWords(e.target.value)
}

/** Any text change makes the label human-authored, which turns it yellow. */
function applyEdit(id, rawText, allowDelete) {
  const text = clipWords(rawText)
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
  notify()
}

function removeLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
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
      <p class="banner-hint">press enter to try again</p>
      <button type="button" class="btn tiny ghost" @click="dismissError">dismiss</button>
    </div>

    <!-- Both label sections only exist once there is a reflection to label. -->
    <template v-if="visibleLabels().length && !loading">
      <div class="section">
        <span class="section-label">top 3 chosen</span>
        <div
          class="zone chosen"
          :class="{ hot: chosenHot, full: currentPinned().length >= MAX_PINNED }"
          @dragover="onChosenDragOver"
          @dragleave="onChosenDragLeave"
          @drop="onChosenDrop"
        >
          <span
            v-for="label in currentPinned()"
            :key="label.id"
            class="chip selected"
            :class="{
              ai: label.source === 'ai',
              user: label.source === 'user',
              editing: editingId === label.id && editingZone === 'chosen',
              dragging: draggingId === label.id,
            }"
            :draggable="!(editingId === label.id && editingZone === 'chosen')"
            title="click to edit · drag to re-rank · drag back down to take it off"
            @dragstart="onDragStart($event, label, 'chosen')"
            @dragover="onChosenChipDragOver($event, label)"
            @dragend="onDragEnd"
          >
            <input
              v-if="editingId === label.id && editingZone === 'chosen'"
              :id="`${idPrefix}-chosen-edit-${label.id}`"
              class="chip-input"
              :value="draft"
              maxlength="96"
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
            <button
              type="button"
              class="chip-del"
              title="Take off the note"
              @mousedown.stop
              @click.stop="unpin(label)"
            >×</button>
          </span>
          <span v-if="!currentPinned().length" class="zone-empty">drag your top 3 here</span>
        </div>
        <span v-if="pinLimitHint" class="hint warn">3 already chosen — drag one out first</span>
      </div>

      <div class="section">
        <span class="section-label">all rationale labels</span>
        <span class="hint">drag to rank them, then drag your top 3 up · blue is generated by ai</span>
        <div class="zone pool" @dragover="onPoolDragOver" @drop="onPoolDrop">
          <span
            v-for="label in visibleLabels()"
            :key="label.id"
            class="chip"
            :class="{
              editing: editingId === label.id && editingZone === 'pool',
              ai: label.source === 'ai',
              user: label.source === 'user',
              chosen: isPinned(label),
              dragging: draggingId === label.id,
            }"
            :draggable="!(editingId === label.id && editingZone === 'pool')"
            @dragstart="onDragStart($event, label, 'pool')"
            @dragover="onPoolChipDragOver($event, label)"
            @dragend="onDragEnd"
          >
            <input
              v-if="editingId === label.id && editingZone === 'pool'"
              :id="`${idPrefix}-pool-edit-${label.id}`"
              class="chip-input"
              :value="draft"
              maxlength="96"
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
            <button
              type="button"
              class="chip-del"
              title="Delete label"
              @mousedown.stop
              @click.stop="removeLabel(label, $event)"
            >×</button>
          </span>
          <button
            type="button"
            class="add"
            title="Add your own label"
            @click="startAdd"
          >
            +
          </button>
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
  color: rgba(253, 230, 138, 0.65);
}

textarea {
  width: 100%;
  box-sizing: border-box;
  resize: none;
  overflow: hidden;
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
  font-size: 9px;
  line-height: 1.4;
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
  color: rgba(253, 230, 138, 0.65);
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
  border: 1px dashed rgba(255, 255, 255, 0.16);
  background: rgba(255, 255, 255, 0.02);
}

.zone.chosen.hot {
  border-color: rgba(253, 230, 138, 0.6);
  background: rgba(253, 230, 138, 0.08);
}

.zone.pool {
  padding: 0;
  min-height: 28px;
}

.zone-empty {
  align-self: center;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.22);
}

.hint.warn {
  color: rgba(252, 165, 165, 0.8);
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
  cursor: grab;
}

.chip.dragging {
  opacity: 0.4;
}

/* Already on the note: dimmed in the pool so the remaining choices stand out. */
.chip.chosen {
  opacity: 0.4;
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
