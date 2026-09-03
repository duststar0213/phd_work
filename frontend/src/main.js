/**
 * App entry.
 *
 * npm libs:
 *   vue                  — UI framework (Composition API / <script setup>)
 *   vite                 — dev server + bundler (devDependency)
 *   @vitejs/plugin-vue   — compiles .vue SFCs (devDependency)
 *
 * Not npm — browser / platform APIs used in this app:
 *   Canvas 2D            — ColorWheel.vue
 *   SVG                  — grid, connector paths, icons
 *   contenteditable      — sticky-note text
 *   ResizeObserver       — live note size for mag points + rationale width
 *   Google Fonts (DM Mono)
 *
 * Unused right now: ChatPanel.vue + api/chat.js (chatbot — not the rationale module).
 * Rationale labels: api/rationale.js + RationaleModule.vue
 *   Canvas: under each sticky note. Isolated test: ?ai=1
 */
import { createApp } from 'vue'
import './style.css'
import App from './App.vue'

createApp(App).mount('#app')
