<!--
  Standalone rationale-label module (not attached to notes/relations yet).
  Input: designer text. Output: short labels, not a chatbot reply.
  Enter generates (costs an API call). Empty input never generates.
-->
<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { clipWords, generateRationaleLabels } from '../api/rationale'

const props = defineProps({
  target: { type: String, default: 'generic' }, // generic | note | relation
})

const emit = defineEmits(['labels', 'error'])

const input = ref('')
const labels = ref([])
const loading = ref(false)
const error = ref('')
const source = ref('') // 'api' | ''
const editingId = ref(null)
const draft = ref('')

let abortGenerate = null
let nextLabelId = 1

onUnmounted(() => {
  abortGenerate?.abort()
})

watch(input, (value) => {
  if (value.trim()) return
  labels.value = []
  source.value = ''
  error.value = ''
  editingId.value = null
  draft.value = ''
})

function notify() {
  emit('labels', labels.value)
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
    labels.value = []
    source.value = ''
    error.value = ''
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
    notify()
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
    document.getElementById(`label-edit-${label.id}`)?.focus()
  })
}

function onDraftInput(e) {
  draft.value = clipWords(e.target.value)
}

function finishEdit(label) {
  const text = clipWords(draft.value)
  if (!text) {
    labels.value = labels.value.filter((item) => item.id !== label.id)
    return
  }
  if (text !== label.text) label.source = 'user'
  label.text = text
}

function commitEdit(label) {
  if (editingId.value !== label.id) return
  finishEdit(label)
  editingId.value = null
  draft.value = ''
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

defineExpose({ generate, input, labels })
</script>

<template>
  <section class="module">
    <label class="field">
      <span class="field-label">rationale</span>
      <textarea
        v-model="input"
        rows="5"
        :disabled="loading"
        placeholder="Why this idea exists — constraints, assumptions, what you are trying…"
        @keydown="onFieldKeydown"
      />
      <span class="hint">enter to generate · shift+enter for a new line</span>
    </label>

    <p v-if="loading" class="status">generating…</p>
    <div v-else-if="error" class="banner" role="alert">
      <p>{{ error }}</p>
      <p class="banner-hint">press enter to try again</p>
      <button type="button" class="btn tiny ghost" @click="dismissError">dismiss</button>
    </div>
    <p v-else-if="source === 'api'" class="status">from chatgpt</p>

    <div class="labels" aria-live="polite">
      <span v-if="!labels.length && !loading" class="empty-labels">no labels yet</span>
      <template v-else>
        <span
          v-for="label in labels"
          :key="label.id"
          class="chip"
          :class="{ editing: editingId === label.id, ai: label.source === 'ai', user: label.source === 'user' }"
        >
          <input
            v-if="editingId === label.id"
            :id="`label-edit-${label.id}`"
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
            :title="label.source === 'ai' ? 'click to edit' : 'click to edit'"
            @click="startEdit(label)"
          >
            {{ label.text || '…' }}
          </button>
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
</style>
