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
          Type a why, then press enter. Get short labels — not a chatbot reply.
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
  background: #16161d;
  background-image: radial-gradient(rgba(255, 255, 255, 0.08) 1px, transparent 1px);
  background-size: 28px 28px;
}

.panel {
  width: min(520px, 100%);
  padding: 22px 22px 18px;
  background: #2c2c2c;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  box-shadow: 0 10px 32px rgba(0, 0, 0, 0.45);
}

.head {
  margin-bottom: 16px;
}

.kicker {
  margin: 0 0 8px;
  font-size: 11px;
  letter-spacing: 0.1em;
  color: rgba(253, 230, 138, 0.7);
}

h1 {
  margin: 0 0 8px;
  font-size: 18px;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.92);
}

.lede,
.health {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.42);
}

.health {
  margin-top: 8px;
  color: rgba(253, 230, 138, 0.5);
}

.health.bad {
  color: #fca5a5;
}

.back {
  margin: 18px 0 0;
  font-size: 12px;
}

.back a {
  color: rgba(255, 255, 255, 0.45);
  text-decoration: none;
}

.back a:hover {
  color: #fde68a;
}
</style>
