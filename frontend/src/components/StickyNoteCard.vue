<script setup>
import { onMounted, ref } from 'vue'

const props = defineProps({
  note: { type: Object, required: true },
  scale: { type: Number, default: 1 },
})

const emit = defineEmits(['move', 'textChange', 'delete'])

const textRef = ref(null)
const drag = { startX: 0, startY: 0 }

onMounted(() => {
  if (textRef.value) {
    textRef.value.innerText = props.note.text
  }
})

function onHeaderMouseDown(e) {
  e.stopPropagation()
  drag.startX = e.clientX
  drag.startY = e.clientY

  const move = (ev) => {
    const dx = (ev.clientX - drag.startX) / props.scale
    const dy = (ev.clientY - drag.startY) / props.scale
    drag.startX = ev.clientX
    drag.startY = ev.clientY
    emit('move', props.note.id, dx, dy)
  }

  const up = () => {
    window.removeEventListener('mousemove', move)
    window.removeEventListener('mouseup', up)
  }

  window.addEventListener('mousemove', move)
  window.addEventListener('mouseup', up)
}

function onTextInput(e) {
  emit('textChange', props.note.id, e.currentTarget.innerText)
}

function onDelete(e) {
  e.stopPropagation()
  emit('delete', props.note.id)
}
</script>

<template>
  <div
    class="note"
    :style="{ left: `${note.x}px`, top: `${note.y}px`, background: note.color }"
    @mousedown.stop
    @click.stop
  >
    <div class="note-handle" @mousedown="onHeaderMouseDown">
      <button class="note-delete" type="button" title="Delete" @mousedown.stop @click="onDelete">
        ×
      </button>
    </div>
    <div
      ref="textRef"
      class="note-text"
      contenteditable="true"
      spellcheck="false"
      @input="onTextInput"
      @mousedown.stop
    />
  </div>
</template>

<style scoped>
.note {
  position: absolute;
  min-width: 180px;
  max-width: 340px;
  border-radius: 4px;
  box-shadow: 0 4px 18px rgba(0, 0, 0, 0.35), 0 1px 4px rgba(0, 0, 0, 0.2);
  display: flex;
  flex-direction: column;
  user-select: none;
  transform-origin: top left;
}

.note-handle {
  height: 28px;
  cursor: grab;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  padding-right: 8px;
  border-radius: 4px 4px 0 0;
  background: rgba(0, 0, 0, 0.07);
  flex-shrink: 0;
}

.note-handle:active {
  cursor: grabbing;
}

.note-delete {
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(0, 0, 0, 0.35);
  font-size: 16px;
  line-height: 1;
  padding: 0 2px;
  border-radius: 3px;
}

.note-delete:hover {
  color: rgba(0, 0, 0, 0.65);
}

.note-text {
  padding: 10px 12px 14px;
  min-height: 60px;
  outline: none;
  font-family: 'DM Mono', ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.55;
  color: rgba(0, 0, 0, 0.75);
  white-space: pre-wrap;
  word-break: break-word;
  cursor: text;
}
</style>
