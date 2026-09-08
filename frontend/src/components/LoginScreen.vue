<!-- Study login: new email gets a password to save; returning email + password. -->
<script setup>
import { onMounted, ref } from 'vue'
import { fetchAuthConfig, login, resetPassword, startSession } from '../api/session'

const emit = defineEmits(['signed-in'])

const step = ref('email') // email | password | save
const email = ref('')
const password = ref('')
const issued = ref('')
const savedOk = ref(false)
const copied = ref(false)
const inviteOnly = ref(false)
const error = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    const data = await fetchAuthConfig()
    inviteOnly.value = Boolean(data.invite_only)
  } catch {
    inviteOnly.value = false
  }
})

function showIssued(value) {
  issued.value = value
  savedOk.value = false
  copied.value = false
  step.value = 'save'
}

async function onEmail() {
  const address = email.value.trim()
  if (!address || loading.value) return
  error.value = ''
  loading.value = true
  try {
    const data = await startSession(address)
    email.value = data.email || address
    if (data.is_new && data.password) showIssued(data.password)
    else {
      password.value = ''
      step.value = 'password'
    }
  } catch (err) {
    error.value = err.message || "Couldn't start."
  } finally {
    loading.value = false
  }
}

async function onLogin() {
  if (!password.value.trim() || loading.value) return
  error.value = ''
  loading.value = true
  try {
    const data = await login(email.value, password.value.trim())
    emit('signed-in', data.email)
  } catch (err) {
    error.value = err.message || "Couldn't log in."
  } finally {
    loading.value = false
  }
}

async function onForgot() {
  if (loading.value) return
  error.value = ''
  loading.value = true
  try {
    const data = await resetPassword(email.value)
    showIssued(data.password)
  } catch (err) {
    error.value = err.message || "Couldn't make a new password."
  } finally {
    loading.value = false
  }
}

async function copyIssued() {
  try {
    await navigator.clipboard.writeText(issued.value)
    copied.value = true
  } catch {
    copied.value = false
  }
}

function continueAfterSave() {
  if (!savedOk.value) return
  emit('signed-in', email.value)
}

function submit() {
  if (step.value === 'email') return onEmail()
  if (step.value === 'password') return onLogin()
  return continueAfterSave()
}

function backToEmail() {
  step.value = 'email'
  password.value = ''
  issued.value = ''
  savedOk.value = false
  error.value = ''
}
</script>

<template>
  <div class="login">
    <form class="card" @submit.prevent="submit">
      <p class="kicker">repertoire</p>
      <h1>study login</h1>

      <template v-if="step === 'email'">
        <p class="lede">
          <template v-if="inviteOnly">Use the email from your invitation. This browser keeps your canvas for the next visit.</template>
          <template v-else>Enter your email. A new address gets a password to save. This browser keeps your canvas for the next visit.</template>
        </p>
        <label>
          <span>email</span>
          <input v-model="email" type="email" autocomplete="email" autofocus />
        </label>
      </template>

      <template v-else-if="step === 'password'">
        <p class="lede">Welcome back. Enter the password you saved for {{ email }}.</p>
        <label>
          <span>password</span>
          <input v-model="password" type="password" autocomplete="current-password" autofocus />
        </label>
        <button type="button" class="text-btn" :disabled="loading" @click="onForgot">
          forgot password — make a new one
        </button>
        <button type="button" class="text-btn" @click="backToEmail">use a different email</button>
      </template>

      <template v-else>
        <p class="lede">Save this password now. You will need it the next time you log in on this computer. We cannot email it to you.</p>
        <p class="issued">{{ issued }}</p>
        <button type="button" class="text-btn" @click="copyIssued">
          {{ copied ? 'copied' : 'copy password' }}
        </button>
        <label class="check">
          <input v-model="savedOk" type="checkbox" />
          <span>I saved this password</span>
        </label>
      </template>

      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="loading || (step === 'save' && !savedOk)">
        <template v-if="step === 'email'">{{ loading ? 'checking…' : 'continue' }}</template>
        <template v-else-if="step === 'password'">{{ loading ? 'signing in…' : 'enter' }}</template>
        <template v-else>enter the study</template>
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

.issued {
  margin: 0;
  text-align: center;
  font-size: 22px;
  letter-spacing: 0.12em;
  color: var(--accent);
  user-select: all;
}

label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.check {
  flex-direction: row;
  align-items: center;
  gap: 8px;
}

.check input {
  width: 16px;
  height: 16px;
  padding: 0;
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

.text-btn {
  height: auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--ink-faint);
  font-size: 11px;
  text-align: left;
}

.text-btn:hover:not(:disabled) {
  background: transparent;
  color: var(--accent);
}
</style>
