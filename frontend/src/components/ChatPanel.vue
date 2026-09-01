<script setup>
/**
 * Early chat UI (Vue + FastAPI + OpenAI). Not mounted; App.vue uses CanvasBoard.
 * fetch() is the only HTTP client — see api/chat.js.
 */
import { nextTick, ref } from 'vue'
import { sendChat } from '../api/chat'

const input = ref('')
const loading = ref(false)
const error = ref('')
const messages = ref([
  {
    role: 'assistant',
    content: 'Hello. Ask a question and I will reply through the Python backend and OpenAI API.',
  },
])
const listEl = ref(null)

/** After Vue paints, scroll the message list to the latest bubble. */
async function scrollToBottom() {
  await nextTick() // wait until Vue has rendered the new message
  if (listEl.value) {
    listEl.value.scrollTop = listEl.value.scrollHeight
  }
}

/** Send the typed prompt through FastAPI → OpenAI (api/chat.js). */
async function onSubmit() {
  const text = input.value.trim()
  if (!text || loading.value) return

  error.value = ''
  messages.value.push({ role: 'user', content: text })
  input.value = ''
  loading.value = true
  await scrollToBottom()

  try {
    const payload = messages.value.filter((m) => m.role === 'user' || m.role === 'assistant')
    const data = await sendChat(payload)
    messages.value.push({ role: 'assistant', content: data.content })
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}
</script>

<template>
  <div class="chat">
    <header class="chat-header">
      <h1>Interactive prototype</h1>
      <p>Vue.js frontend · Python FastAPI · OpenAI</p>
    </header>

    <div ref="listEl" class="chat-messages" aria-live="polite">
      <article
        v-for="(message, index) in messages"
        :key="index"
        class="bubble"
        :class="message.role"
      >
        <span class="role">{{ message.role }}</span>
        <p>{{ message.content }}</p>
      </article>
      <p v-if="loading" class="status">Thinking…</p>
      <p v-if="error" class="error">{{ error }}</p>
    </div>

    <form class="chat-form" @submit.prevent="onSubmit">
      <label class="sr-only" for="prompt">Message</label>
      <textarea
        id="prompt"
        v-model="input"
        rows="2"
        placeholder="Type a message…"
        :disabled="loading"
        @keydown.enter.exact.prevent="onSubmit"
      />
      <button type="submit" :disabled="loading || !input.trim()">
        Send
      </button>
    </form>
  </div>
</template>
