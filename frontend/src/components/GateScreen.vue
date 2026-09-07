<!-- Door 1: invitation code. Login is not shown until this succeeds. -->
<script setup>
import { ref } from 'vue'
import { unlockGate } from '../api/session'

const emit = defineEmits(['unlocked'])

const access = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  const code = access.value.trim()
  if (!code || loading.value) return
  error.value = ''
  loading.value = true
  try {
    await unlockGate(code)
    emit('unlocked')
  } catch (err) {
    error.value = err.message || 'That invitation code is not right.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login">
    <form class="card" @submit.prevent="submit">
      <p class="kicker">repertoire</p>
      <h1>enter code</h1>
      <p class="lede">This study is invite-only. Enter the code from your invitation.</p>
      <label>
        <span>invitation code</span>
        <input v-model="access" type="text" autocomplete="off" autofocus />
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="loading">
        {{ loading ? 'checking…' : 'continue' }}
      </button>
    </form>
  </div>
</template>

<style scoped>
.login {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--paper);
  background-image: radial-gradient(var(--dot) 1px, transparent 1px);
  background-size: 28px 28px;
}

.card {
  width: min(360px, calc(100% - 32px));
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 22px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.1);
}

.kicker {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--accent);
}

h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 400;
  color: var(--ink);
}

.lede,
label span {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-faint);
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

input {
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  color: var(--ink);
  font-family: inherit;
  font-size: 13px;
  outline: none;
}

input:focus {
  border-color: rgba(180, 83, 9, 0.45);
}

.error {
  margin: 0;
  font-size: 12px;
  color: #b91c1c;
}

button {
  height: 36px;
  border: 1px solid rgba(180, 83, 9, 0.35);
  border-radius: 8px;
  background: var(--accent-soft);
  color: var(--accent);
  cursor: pointer;
}

button:hover:not(:disabled) {
  background: rgba(180, 83, 9, 0.2);
}

button:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>
