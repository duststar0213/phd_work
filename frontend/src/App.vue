<script setup>
/** Root: invitation code, then login, then canvas. ?ai=1 still opens the standalone rationale module. */
import { onMounted, ref } from 'vue'
import CanvasBoard from './components/CanvasBoard.vue'
import GateScreen from './components/GateScreen.vue'
import LoginScreen from './components/LoginScreen.vue'
import RationalePlayground from './components/RationalePlayground.vue'
import { fetchAuthConfig, fetchMe } from './api/session'

const params = new URLSearchParams(window.location.search)
const showRationale = params.get('ai') === '1' || params.get('ai') === 'rationale'

const username = ref('')
const gateOpen = ref(false)
const checking = ref(!showRationale)

onMounted(async () => {
  if (showRationale) return
  try {
    const data = await fetchMe()
    username.value = data.email || data.username || ''
    gateOpen.value = true
  } catch {
    username.value = ''
    try {
      const cfg = await fetchAuthConfig()
      gateOpen.value = !cfg.access_required || Boolean(cfg.gate_unlocked)
    } catch {
      gateOpen.value = false
    }
  } finally {
    checking.value = false
  }
})

function onUnlocked() {
  gateOpen.value = true
}

function onSignedIn(name) {
  username.value = name
}

function onSignedOut() {
  username.value = ''
}
</script>

<template>
  <RationalePlayground v-if="showRationale" />
  <p v-else-if="checking" class="boot">loading…</p>
  <GateScreen v-else-if="!gateOpen" @unlocked="onUnlocked" />
  <LoginScreen v-else-if="!username" @signed-in="onSignedIn" />
  <CanvasBoard v-else :username="username" @signed-out="onSignedOut" />
</template>

<style scoped>
.boot {
  margin: 0;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--ink-faint);
}
</style>
