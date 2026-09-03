<!--
  Mid-line rationale control for a connector: same + / tabs as a sticky note,
  sitting on the curve. Click + opens (or, if already open, adds a human tab).
-->
<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { clipWords } from '../api/rationale'

const MAX_PINNED = 3
const GHOST_TAB = 'add your label'

const props = defineProps({
  connection: { type: Object, required: true },
  open: { type: Boolean, default: false },
  selected: { type: Boolean, default: false },
  abandoned: { type: Boolean, default: false },
})

const emit = defineEmits(['toggle-rationale', 'open-rationale', 'pin-change', 'labels-change'])

const trayOpen = ref(false)
const editingId = ref(null)
const draft = ref('')
const addHover = ref(false)
const creating = ref(false)
const createDraft = ref('')
let labelClickTimer = null

onUnmounted(() => {
  clearTimeout(labelClickTimer)
})

const pinnedLabels = computed(() =>
  Array.isArray(props.connection.pinnedLabels) ? props.connection.pinnedLabels.slice(0, MAX_PINNED) : [],
)

const allLabels = computed(() =>
  Array.isArray(props.connection.rationaleLabels) ? props.connection.rationaleLabels : [],
)

const showLabelFold = computed(() => allLabels.value.length > MAX_PINNED)

watch(
  () => allLabels.value.length,
  (count) => {
    if (count <= MAX_PINNED) trayOpen.value = false
  },
)

function pinSnapshot(label) {
  return { id: label.id, text: label.text, kind: label.kind || '', source: label.source }
}

function isPinned(label) {
  return pinnedLabels.value.some((item) => item.id === label.id || item.text === label.text)
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

function toggleTray(e) {
  e.stopPropagation()
  trayOpen.value = !trayOpen.value
}

function togglePin(label) {
  if (!label.text) return
  const pinned = pinnedLabels.value.slice()
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

function onTrayChipClick(label) {
  clearTimeout(labelClickTimer)
  labelClickTimer = setTimeout(() => {
    labelClickTimer = null
    startEdit(label)
  }, 250)
}

function onTrayChipDblClick(label) {
  clearTimeout(labelClickTimer)
  labelClickTimer = null
  togglePin(label)
}

function removeLabel(label, e) {
  e?.preventDefault()
  e?.stopPropagation()
  clearTimeout(labelClickTimer)
  if (editingId.value === label.id) {
    editingId.value = null
    draft.value = ''
  }
  setLabels(allLabels.value.filter((item) => item.id !== label.id))
  if (isPinned(label)) setPinned(pinnedLabels.value.filter((item) => item.id !== label.id))
}

function startEdit(label) {
  editingId.value = label.id
  draft.value = label.text
  nextTick(() => {
    document.getElementById(`rel-tab-edit-${props.connection.id}-${label.id}`)?.focus()
  })
}

function onDraftInput(e) {
  draft.value = clipWords(e.target.value)
}

function commitEdit(label) {
  if (editingId.value !== label.id) return
  const text = clipWords(draft.value)
  editingId.value = null
  draft.value = ''
  if (!text) {
    setLabels(allLabels.value.filter((item) => item.id !== label.id))
    if (isPinned(label)) setPinned(pinnedLabels.value.filter((item) => item.id !== label.id))
    return
  }
  const source = text !== label.text ? 'user' : label.source
  setLabels(
    allLabels.value.map((item) => (item.id === label.id ? { ...item, text, source } : item)),
  )
  if (isPinned(label)) {
    setPinned(
      pinnedLabels.value.map((item) =>
        item.id === label.id ? { ...item, text, source } : item,
      ),
    )
  } else if (pinnedLabels.value.length < MAX_PINNED) {
    setPinned([...pinnedLabels.value, { id: label.id, text, kind: label.kind || '', source }])
  }
}

function onEditKeydown(e, label) {
  if (e.key === 'Enter') {
    e.preventDefault()
    e.currentTarget.blur()
    return
  }
  if (e.key === 'Escape') {
    e.preventDefault()
    creating.value = false
    draft.value = label.text
    editingId.value = null
  }
}

function addLabel() {
  trayOpen.value = true
  const chip = { id: nextLabelId(), text: '', kind: '', source: 'user' }
  setLabels([...allLabels.value, chip])
  nextTick(() => startEdit(chip))
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
  createDraft.value = clipWords(e.target.value)
}

function commitCreate() {
  if (!creating.value) return
  const text = clipWords(createDraft.value)
  creating.value = false
  createDraft.value = ''
  if (!text) return
  const chip = { id: nextLabelId(), text, kind: '', source: 'user' }
  setLabels([...allLabels.value, chip])
  const pinned = pinnedLabels.value.slice()
  if (pinned.length >= MAX_PINNED) pinned.pop()
  pinned.push(pinSnapshot(chip))
  setPinned(pinned)
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

function onTabClick() {
  emit('open-rationale')
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
    <div v-if="pinnedLabels.length || showLabelFold" class="tab-stack">
      <span
        v-for="label in pinnedLabels"
        :key="label.id"
        class="edge-tab"
        :class="label.source === 'user' ? 'user' : 'ai'"
        @click="onTabClick"
      >
        <span class="tab-label">{{ label.text }}</span>
        <button
          v-if="!abandoned"
          type="button"
          class="tab-del"
          title="Delete label"
          @pointerdown.stop
          @mousedown.stop
          @click.stop="removeLabel(label, $event)"
        >×</button>
      </span>
      <button
        v-if="showLabelFold && !abandoned"
        type="button"
        class="edge-fold"
        :class="{ open: trayOpen }"
        title="More labels"
        @click.stop="toggleTray"
      >
        <svg viewBox="0 0 12 8" aria-hidden="true">
          <path d="M2 2.5 L6 6 L10 2.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
      </button>
      <div v-if="trayOpen" class="edge-tray" @pointerdown.stop @mousedown.stop @click.stop>
        <p class="tray-hint">click to edit · double-click to put on or take off the line · × to delete</p>
        <div class="tray-chips">
          <span
            v-for="label in allLabels"
            :key="label.id"
            class="chip"
            :class="{
              editing: editingId === label.id,
              ai: label.source === 'ai',
              user: label.source === 'user',
              selected: isPinned(label),
            }"
          >
            <input
              v-if="editingId === label.id"
              :id="`rel-tab-edit-${connection.id}-${label.id}`"
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
              @click="onTrayChipClick(label)"
              @dblclick.prevent="onTrayChipDblClick(label)"
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
          <button type="button" class="tray-plus" title="Add a label" @click="addLabel">+</button>
        </div>
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
          maxlength="48"
          placeholder="rationale label of this link"
          @input="onCreateInput"
          @keydown="onCreateKeydown"
          @blur="commitCreate"
          @click.stop
        />
      </span>
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
  color: #5b4a12;
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

.edge-tray {
  position: absolute;
  left: calc(100% + 4px);
  top: 0;
  width: 148px;
  padding: 8px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.4);
  z-index: 6;
}

.tray-hint {
  margin: 0 0 6px;
  font-size: 9px;
  letter-spacing: 0.03em;
  color: rgba(255, 255, 255, 0.32);
}

.tray-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}

.chip {
  display: inline-flex;
  align-items: center;
  max-width: 100%;
  min-height: 22px;
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

.chip.selected.ai {
  background: #93c5fd;
  border-color: #7eb6f8;
}

.chip.selected.user {
  background: #fde68a;
  border-color: #f3d56a;
}

.chip-text,
.chip-input {
  margin: 0;
  padding: 3px 8px;
  border: 0;
  background: transparent;
  color: rgba(255, 255, 255, 0.88);
  font-family: inherit;
  font-size: 10px;
  line-height: 1.3;
}

.chip.selected.ai .chip-text,
.chip.selected.ai .chip-input,
.chip.selected.ai .chip-del {
  color: #1e3a5f;
}

.chip.selected.user .chip-text,
.chip.selected.user .chip-input,
.chip.selected.user .chip-del {
  color: #5b4a12;
}

.chip-text {
  cursor: pointer;
}

.chip-del {
  flex-shrink: 0;
  width: 14px;
  height: 14px;
  margin: 0 3px 0 0;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: inherit;
  opacity: 0.45;
  cursor: pointer;
  font-size: 12px;
  line-height: 14px;
}

.chip-del:hover {
  opacity: 1;
  background: rgba(0, 0, 0, 0.12);
}

.tray-plus {
  width: 22px;
  height: 22px;
  padding: 0;
  border-radius: 999px;
  border: 1px dashed rgba(255, 255, 255, 0.28);
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
}
</style>
