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
          <template v-if="inviteOnly">Use the email from your invitation.</template>
          <template v-else>Enter your email. A new address gets a new space and a password to save.</template>
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
        <p class="lede">Save this password now. You will need it the next time you log in. We cannot email it to you.</p>
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
  background: #16161d;
  background-image: radial-gradient(rgba(255, 255, 255, 0.08) 1px, transparent 1px);
  background-size: 28px 28px;
}

.card {
  width: min(360px, calc(100% - 32px));
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 22px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.45);
}

.kicker {
  margin: 0;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: rgba(253, 230, 138, 0.7);
}

h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.92);
}

.lede,
label span {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.42);
}

.issued {
  margin: 0;
  text-align: center;
  font-size: 22px;
  letter-spacing: 0.12em;
  color: #fde68a;
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
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: #1c1c24;
  color: rgba(255, 255, 255, 0.88);
  font-family: inherit;
  font-size: 13px;
  outline: none;
}

input:focus {
  border-color: rgba(253, 230, 138, 0.45);
}

.error {
  margin: 0;
  font-size: 12px;
  color: #fca5a5;
}

button {
  height: 36px;
  border: 1px solid rgba(253, 230, 138, 0.35);
  border-radius: 8px;
  background: rgba(253, 230, 138, 0.12);
  color: #fde68a;
  cursor: pointer;
}

button:hover:not(:disabled) {
  background: rgba(253, 230, 138, 0.2);
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
  color: rgba(255, 255, 255, 0.4);
  font-size: 11px;
  text-align: left;
}

.text-btn:hover:not(:disabled) {
  background: transparent;
  color: #fde68a;
}
</style>
