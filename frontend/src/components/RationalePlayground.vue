<!--
  Isolated test surface for RationaleModule.
  Open http://localhost:5173/?ai=1  (canvas is the default, without ?ai=1).
-->
<script setup>
import { onMounted, ref } from 'vue'
import { fetchRationaleHealth } from '../api/rationale'
import RationaleModule from './RationaleModule.vue'

const health = ref('checking API…')
const healthOk = ref(null)

onMounted(async () => {
  try {
    const data = await fetchRationaleHealth()
    health.value = data.openai_configured
      ? 'API ready — generate labels from your rationale'
      : 'Label generation is not configured on this server yet'
    healthOk.value = Boolean(data.openai_configured)
  } catch {
    health.value = "Can't reach the label server. Start the backend, or use demo labels."
    healthOk.value = false
  }
})
</script>

<template>
  <div class="playground">
    <div class="panel">
      <header class="head">
        <p class="kicker">repertoire · ai module</p>
        <h1>rationale labels</h1>
        <p class="lede">
          Talk about the idea, then press enter. You get short labels — not a chatbot reply.
          Not attached to sticky notes or connections yet.
        </p>
        <p class="health" :class="{ bad: healthOk === false }">{{ health }}</p>
      </header>

      <RationaleModule target="generic" />

      <p class="back">
        <a href="/">back to canvas</a>
      </p>
    </div>
  </div>
</template>

<style scoped>
.playground {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  box-sizing: border-box;
  background: var(--paper);
  background-image: radial-gradient(var(--dot) 1px, transparent 1px);
  background-size: 28px 28px;
}

.panel {
  width: min(520px, 100%);
  padding: 22px 22px 18px;
  background: var(--chrome);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(44, 40, 31, 0.1);
}

.head {
  margin-bottom: 16px;
}

.kicker {
  margin: 0 0 8px;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: var(--accent);
}

h1 {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 400;
  color: var(--ink);
}

.lede,
.health {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-faint);
}

.health {
  margin-top: 8px;
  color: var(--accent);
}

.health.bad {
  color: #b91c1c;
}

.back {
  margin: 18px 0 0;
  font-size: 12px;
}

.back a {
  color: var(--ink-faint);
  text-decoration: none;
}

.back a:hover {
  color: var(--accent);
}
</style>
