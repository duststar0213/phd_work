/**
 * Rationale-label API — callable on its own (not wired to notes/relations yet).
 * POST /api/rationale-labels → FastAPI → OpenAI.
 * Throws RationaleApiError with a visitor-facing `message` (and `code`) on failure.
 */

const LABEL_KINDS = ['assumption', 'constraint', 'goal', 'tension', 'insight', 'question']
const REQUEST_TIMEOUT_MS = 35000
const MAX_LABEL_WORDS = 10
const MAX_KNOWN_LABELS = 40 // matches the backend cap; newest labels are the ones worth matching
const MAX_CONTEXT_CHARS = 1500 // the brief is background, so it must not crowd out the idea itself
const MAX_ASKED_QUESTIONS = 8 // earlier questions the model must not ask again
const MAX_CONTEXT_QUESTIONS = 5 // matches the backend cap on questions back from the brief
const MAX_ASKED_CONTEXT_QUESTIONS = 12 // brief questions already put to the designer
const MAX_CONTEXT_IMAGES = 5 // matches the backend cap on images attached to the brief
const CONTEXT_QUESTION_KINDS = ['goal', 'user', 'constraint', 'scope', 'tension']
const NEW_CLAUSE_WORDS = 4 // unmatched content words that make an addition worth labelling
const MAX_LABELLED_CHARS = 4000 // reflection already turned into labels, sent as background

/** The designer's standing brief, trimmed to what is worth spending prompt on. */
function clipContext(text) {
  return String(text || '').trim().slice(0, MAX_CONTEXT_CHARS)
}

export class RationaleApiError extends Error {
  constructor(message, { code = 'unknown', status = 0 } = {}) {
    super(message)
    this.name = 'RationaleApiError'
    this.code = code
    this.status = status
  }
}

/** Local stand-in labels so the UI can be tested without an API key. */
export function demoRationaleLabels(text, target = 'generic') {
  const words = String(text || '').trim().split(/\s+/).filter(Boolean)
  const snippet = words.slice(0, 4).join(' ') || 'stated intent'
  const relation = target === 'relation'
  const group = target === 'group'
  return [
    { text: clipWords(snippet || 'stated intent'), kind: 'goal' },
    { text: clipWords(group ? 'why they sit together' : relation ? 'why these link' : 'unspoken constraint'), kind: 'constraint' },
    { text: clipWords(group ? 'shared concern' : relation ? 'shared assumption' : 'possible tension'), kind: group || relation ? 'assumption' : 'tension' },
    { text: clipWords('open question'), kind: 'question' },
  ]
}

/**
 * POST /api/rationale-labels. `target` is generic | note | relation | group.
 * `options.known` is [{ ref, text }] of labels the designer already has, so the model can
 * point at one with `same_as` instead of coining a near-duplicate.
 * `options.previous` is the reflection those labels came from; the model reads it as
 * background and labels what the designer has since added. Pass '' to re-read the lot.
 */
export async function generateRationaleLabels(text, target = 'generic', options = {}) {
  const reflection = String(text ?? '').trim()
  const idea = String(options.idea ?? '').trim()
  const fingerprint = [idea, reflection].filter(Boolean).join('\n\n')
  const verdict = assessReflection(fingerprint, options.previous)
  if (!verdict.ok) {
    throw new RationaleApiError(verdict.message, { code: verdict.code, status: 400 })
  }
  if (verdict.preset) {
    return { labels: [{ text: verdict.preset, kind: 'question' }], model: '' }
  }

  const known = (Array.isArray(options.known) ? options.known : [])
    .filter((item) => item?.ref && String(item.text || '').trim())
    .slice(-MAX_KNOWN_LABELS)
    .map((item) => ({ ref: String(item.ref), text: String(item.text).trim().slice(0, 200) }))

  const response = await fetchWithTimeout(
    '/api/rationale-labels',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: reflection,
        idea,
        target,
        known,
        labelled: String(options.previous || '').trim().slice(0, MAX_LABELLED_CHARS),
        context: clipContext(options.context),
      }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )

  const data = await readJson(response)
  if (!response.ok) {
    throw toApiError(response.status, data)
  }

  const labels = Array.isArray(data.labels)
    ? data.labels.map(normalizeLabel).filter((item) => item.text || item.sameAs)
    : []
  // Nothing new to say about an addition is an answer, not a failure; the caller says so gently.
  if (!labels.length && data.reason !== 'nothing_new') {
    throw new RationaleApiError('The AI returned no labels. Try rephrasing and generate again.', {
      code: 'empty_labels',
      status: response.status,
    })
  }

  return { labels, model: data.model || '', reason: String(data.reason || '') }
}

/** True when the idea is long enough to possibly contain a why. */
export function ideaReadyForReflectionDetect(text) {
  const idea = String(text || '').trim()
  if (idea.length < 36) return false
  const tokens = idea.match(/[\p{Script=Han}]|[a-zA-Z0-9']+/gu) || []
  return tokens.length >= 8
}

/** True when the sticky note has enough content to question. Empty notes never pass. */
export function ideaReadyForWhyQuestion(text) {
  const idea = String(text || '').trim()
  if (idea.length < 12) return false
  const tokens = idea.match(/[\p{Script=Han}]|[a-zA-Z0-9']+/gu) || []
  return tokens.length >= 3
}

/**
 * POST /api/ask-why. One short question about the idea, from the note plus whatever
 * rationale is written so far. `options.asked` are earlier questions it must not repeat.
 * Empty string if the note is too thin or the model answered with a statement.
 */
export async function askWhyQuestion(idea, options = {}) {
  const text = String(idea || '').trim()
  if (!ideaReadyForWhyQuestion(text)) return ''
  const asked = (Array.isArray(options.asked) ? options.asked : [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .slice(-MAX_ASKED_QUESTIONS)
  const response = await fetchWithTimeout(
    '/api/ask-why',
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        idea: text,
        answer: String(options.answer || '').trim().slice(0, 4000),
        asked,
        context: clipContext(options.context),
      }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  const question = clipWords(String(data.question || '').trim(), 16)
  return /[?？]$/.test(question) ? question : ''
}

/**
 * POST /api/detect-reflection. Conservative: true only if the idea already
 * names a why, not just a proposal.
 */
export async function detectEmbeddedReflection(idea, options = {}) {
  const text = String(idea || '').trim()
  if (!ideaReadyForReflectionDetect(text)) {
    return { hasReflection: false, excerpt: '' }
  }
  const response = await fetchWithTimeout(
    '/api/detect-reflection',
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idea: text }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  return {
    hasReflection: Boolean(data.has_reflection),
    excerpt: String(data.excerpt || '').trim(),
    model: data.model || '',
  }
}

/**
 * POST /api/context-questions. The standing brief plus any attached images, read back
 * as questions about this project. Returns [{ text, kind }]; kind "goal" is the AI's
 * reading of the design goal. Images are data URLs, already downscaled by the browser.
 */
export async function askContextQuestions(context, images = [], options = {}) {
  const response = await fetchWithTimeout(
    '/api/context-questions',
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        context: clipContext(context),
        images: (Array.isArray(images) ? images : [])
          .map((url) => String(url || ''))
          .filter((url) => url.startsWith('data:image/'))
          .slice(0, MAX_CONTEXT_IMAGES),
        asked: (Array.isArray(options.asked) ? options.asked : [])
          .map((item) => String(item || '').trim())
          .filter(Boolean)
          .slice(-MAX_ASKED_CONTEXT_QUESTIONS),
      }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  const seen = new Set()
  return {
    questions: (Array.isArray(data.questions) ? data.questions : [])
      .map((item) => ({
        text: String(item?.text || '').trim(),
        kind: CONTEXT_QUESTION_KINDS.includes(item?.kind) ? item.kind : 'scope',
      }))
      .filter((item) => {
        if (!item.text) return false
        const key = item.text.toLowerCase()
        if (seen.has(key)) return false
        seen.add(key)
        return true
      })
      .slice(0, MAX_CONTEXT_QUESTIONS),
    model: data.model || '',
  }
}

/**
 * POST /api/suggest-links. Source + candidates are { id, text, labels[] }.
 * Labels should be the pinned top-3. Returns [{ id, why }].
 */
export async function suggestLinks(source, candidates, options = {}) {
  const response = await fetchWithTimeout(
    '/api/suggest-links',
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, candidates, context: clipContext(options.context) }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  const allowed = new Set((Array.isArray(candidates) ? candidates : []).map((item) => String(item?.id ?? '')))
  const links = (Array.isArray(data.links) ? data.links : [])
    .map((item) => ({
      id: String(item?.id ?? '').trim(),
      why: String(item?.why || '').trim(),
    }))
    .filter((item) => item.id && allowed.has(item.id))
  const seen = new Set()
  return {
    links: links.filter((item) => {
      if (seen.has(item.id)) return false
      seen.add(item.id)
      return true
    }).slice(0, 5),
    model: data.model || '',
  }
}

/**
 * POST /api/group-why. Ideas are { id, text, labels[] }; shared is overlapping rationale labels.
 * Returns one short sentence, or '' if the model had nothing to say.
 */
export async function suggestGroupWhy(payload, options = {}) {
  const ideas = (Array.isArray(payload?.ideas) ? payload.ideas : [])
    .map((item) => ({
      id: String(item?.id ?? '').trim(),
      text: String(item?.text || '').trim().slice(0, 2000),
      labels: (Array.isArray(item?.labels) ? item.labels : [])
        .map((label) => String(label || '').trim())
        .filter(Boolean)
        .slice(0, 3),
    }))
    .filter((item) => item.id)
    .slice(0, 8)
  const shared = (Array.isArray(payload?.shared) ? payload.shared : [])
    .map((item) => String(item || '').trim())
    .filter(Boolean)
    .slice(0, 6)
  if (ideas.length < 2) return ''

  const response = await fetchWithTimeout(
    '/api/group-why',
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ideas, shared, context: clipContext(options.context) }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  return clipWords(String(data.why || '').trim(), 18)
}

/** Fallback copy while the model writes a group why, or if that call fails. */
export function narrativeSharedWhy(labels) {
  const names = [...new Set((Array.isArray(labels) ? labels : []).map((item) => String(item || '').trim()).filter(Boolean))].slice(0, 3)
  if (!names.length) return ''
  if (names.length === 1) return `they belong together because they share this why: ${names[0]}`
  if (names.length === 2) return `they belong together because they share ${names[0]}, and ${names[1]}`
  return `they belong together because they share ${names[0]}, ${names[1]}, and ${names[2]}`
}

export const MEANING_TOP_K = 3
export const COSINE_THRESHOLD = 0.4
export const LIVE_EMBED_MS = 350

export function readyForLiveEmbed(text) {
  const trimmed = String(text || '').trim()
  const words = trimmed.split(/\s+/).filter(Boolean)
  return words.length >= 2 && trimmed.length >= 10
}

const draftVecCache = new Map()

export function cachedDraftVector(text) {
  return asVector(draftVecCache.get(String(text || '').trim()))
}

export function rememberDraftVector(text, vec) {
  const key = String(text || '').trim()
  const vector = asVector(vec)
  if (!key || !vector) return
  if (draftVecCache.size >= 24) draftVecCache.delete(draftVecCache.keys().next().value)
  draftVecCache.set(key, vector)
}

/** Cosine of one draft vector against labels that already have embeddings. */
export function meaningHitsFromVectors(
  queryVec,
  items,
  { excludeId = null, threshold = COSINE_THRESHOLD, k = MEANING_TOP_K } = {},
) {
  const q = asVector(queryVec)
  if (!q) return []
  const skip = excludeId == null ? '' : String(excludeId)
  const hits = []
  for (const item of Array.isArray(items) ? items : []) {
    if (!item?.text || String(item.id) === skip) continue
    const vec = asVector(item.embedding)
    if (!vec) continue
    const score = cosine(q, vec)
    if (score >= threshold) hits.push({ id: item.id, text: item.text, score })
  }
  hits.sort((a, b) => b.score - a.score)
  return hits.slice(0, k)
}

/** Vectors are stored L2-normalised, so cosine is a dot product. */
export function cosine(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length || !a.length) return 0
  let sum = 0
  for (let i = 0; i < a.length; i += 1) sum += Number(a[i]) * Number(b[i])
  return sum
}

export function asVector(raw) {
  if (!Array.isArray(raw) || raw.length < 8) return null
  const vec = raw.map(Number)
  if (vec.some((n) => !Number.isFinite(n))) return null
  return vec
}

/**
 * HNSW over L2-normalised label embeddings (cosine = 1 - dist).
 * Insert and search are O(log n) in the accumulating corpus.
 * API cost stays O(1): one embed on write, one LLM call on the top-k hits.
 *
 * Layered NSW graph (Malkov & Yashunin): greedy search from a sparse top layer
 * down to layer 0, then return the best ef neighbours.
 */
export class LabelVectorIndex {
  constructor({ M = 8, efConstruction = 32, efSearch = 16 } = {}) {
    this.M = M
    this.M0 = M * 2
    this.ml = 1 / Math.log(2)
    this.efConstruction = efConstruction
    this.efSearch = Math.max(efSearch, MEANING_TOP_K)
    this.nodes = new Map()
    this.entryId = null
  }

  clear() {
    this.nodes.clear()
    this.entryId = null
  }

  ids() {
    return [...this.nodes.keys()]
  }

  upsert(id, vec, text) {
    const key = String(id)
    const vector = asVector(vec)
    if (!vector || !text) {
      this.remove(key)
      return
    }
    const existing = this.nodes.get(key)
    if (existing && existing.text === String(text) && sameVec(existing.vec, vector)) return
    this.remove(key)
    this.#insert(key, vector, String(text))
  }

  remove(id) {
    const key = String(id)
    const node = this.nodes.get(key)
    if (!node) return
    for (let lc = 0; lc <= node.level; lc += 1) {
      for (const nb of node.friends[lc]) {
        const other = this.nodes.get(nb)
        if (other?.friends[lc]) other.friends[lc] = other.friends[lc].filter((item) => item !== key)
      }
    }
    this.nodes.delete(key)
    if (this.entryId === key) {
      let best = null
      let bestLevel = -1
      for (const [nid, n] of this.nodes) {
        if (n.level > bestLevel) {
          bestLevel = n.level
          best = nid
        }
      }
      this.entryId = best
    }
  }

  query(queryVec, { k = MEANING_TOP_K, excludeId = null, threshold = COSINE_THRESHOLD } = {}) {
    const q = asVector(queryVec)
    if (!q || !this.entryId) return []
    const skip = excludeId == null ? '' : String(excludeId)
    const want = Math.max(k + (skip ? 1 : 0), this.efSearch)
    const nearest = this.#searchKnn(q, want)
    const hits = []
    for (const id of nearest) {
      if (id === skip) continue
      const node = this.nodes.get(id)
      if (!node) continue
      const score = cosine(q, node.vec)
      if (score >= threshold) hits.push({ ref: id, text: node.text, score })
      if (hits.length >= k) break
    }
    return hits
  }

  #insert(id, vec, text) {
    const level = this.#randomLevel()
    const friends = Array.from({ length: level + 1 }, () => [])
    const node = { vec, text, level, friends }
    if (!this.entryId) {
      this.nodes.set(id, node)
      this.entryId = id
      return
    }
    const entry = this.nodes.get(this.entryId)
    let curr = this.entryId
    for (let lc = entry.level; lc > level; lc -= 1) {
      curr = this.#searchLayer(vec, curr, 1, lc)[0]
    }
    for (let lc = Math.min(level, entry.level); lc >= 0; lc -= 1) {
      const nearest = this.#searchLayer(vec, curr, this.efConstruction, lc)
      if (!nearest.length) continue
      const cap = lc === 0 ? this.M0 : this.M
      const selected = nearest.slice(0, cap)
      node.friends[lc] = selected.slice()
      for (const nb of selected) {
        const other = this.nodes.get(nb)
        if (!other) continue
        if (!other.friends[lc]) other.friends[lc] = []
        other.friends[lc].push(id)
        if (other.friends[lc].length > cap) {
          other.friends[lc] = this.#closest(vecOf(other), other.friends[lc], cap)
        }
      }
      curr = nearest[0]
    }
    this.nodes.set(id, node)
    if (level > entry.level) this.entryId = id
  }

  #searchKnn(q, ef) {
    let curr = this.entryId
    const entry = this.nodes.get(curr)
    for (let lc = entry.level; lc > 0; lc -= 1) {
      curr = this.#searchLayer(q, curr, 1, lc)[0]
    }
    return this.#searchLayer(q, curr, ef, 0)
  }

  #searchLayer(q, entryId, ef, lc) {
    if (!entryId || !this.nodes.get(entryId)) return []
    const visited = new Set([entryId])
    const candidates = [{ id: entryId, dist: dist(q, vecOf(this.nodes.get(entryId))) }]
    const found = [{ id: entryId, dist: candidates[0].dist }]
    while (candidates.length) {
      candidates.sort((a, b) => a.dist - b.dist)
      const current = candidates.shift()
      const worst = found.reduce((max, item) => (item.dist > max.dist ? item : max))
      if (current.dist > worst.dist) break
      const friends = this.nodes.get(current.id)?.friends[lc] || []
      for (const nb of friends) {
        if (visited.has(nb)) continue
        visited.add(nb)
        const d = dist(q, vecOf(this.nodes.get(nb)))
        const far = found.reduce((max, item) => (item.dist > max.dist ? item : max))
        if (d < far.dist || found.length < ef) {
          candidates.push({ id: nb, dist: d })
          found.push({ id: nb, dist: d })
          if (found.length > ef) {
            found.sort((a, b) => a.dist - b.dist)
            found.pop()
          }
        }
      }
    }
    found.sort((a, b) => a.dist - b.dist)
    return found.map((item) => item.id)
  }

  #closest(q, ids, cap) {
    return ids
      .map((id) => ({ id, dist: dist(q, vecOf(this.nodes.get(id))) }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, cap)
      .map((item) => item.id)
  }

  #randomLevel() {
    return Math.floor(-Math.log(Math.random() || 1e-12) * this.ml)
  }
}

function vecOf(node) {
  return node?.vec || []
}

function dist(a, b) {
  return 1 - cosine(a, b)
}

function sameVec(a, b) {
  if (!a || !b || a.length !== b.length) return false
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return false
  return true
}

/** One embeddings request for the whole batch — index writes, not an O(n) prompt. */
export async function embedTexts(texts, options = {}) {
  const unique = [...new Set((Array.isArray(texts) ? texts : []).map((item) => String(item || '').trim()).filter(Boolean))]
  if (!unique.length) return new Map()
  const byText = new Map()
  const chunkSize = 32
  for (let start = 0; start < unique.length; start += chunkSize) {
    const chunk = unique.slice(start, start + chunkSize)
    const response = await fetchWithTimeout(
      '/api/embeddings',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texts: chunk }),
        signal: options.signal,
      },
      REQUEST_TIMEOUT_MS,
    )
    const data = await readJson(response)
    if (!response.ok) throw toApiError(response.status, data)
    const vectors = Array.isArray(data.vectors) ? data.vectors : []
    chunk.forEach((text, i) => {
      const vec = asVector(vectors[i])
      if (vec) byText.set(text, vec)
    })
  }
  return byText
}

/** LLM sees at most MEANING_TOP_K cosine neighbours. */
export async function confirmLabelMeaning(query, candidates, options = {}) {
  const list = (Array.isArray(candidates) ? candidates : [])
    .filter((item) => item?.ref && String(item.text || '').trim())
    .slice(0, MEANING_TOP_K)
    .map((item) => ({ ref: String(item.ref), text: String(item.text).trim().slice(0, 200) }))
  if (!String(query || '').trim() || !list.length) return []
  const response = await fetchWithTimeout(
    '/api/label-meaning',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: String(query).trim(), candidates: list }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  return Array.isArray(data.matches) ? data.matches.map(String) : []
}

export const MAX_USER_LABEL_CHARS = 400
export const SHORT_LABEL_WORDS = 10
export const SHORTEN_PAUSE_MS = 550

export function clipUserLabel(text) {
  return String(text || '').replace(/\s+/g, ' ').trim().slice(0, MAX_USER_LABEL_CHARS)
}

export function labelWordCount(text) {
  return String(text || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length
}

export function needsShortLabel(text) {
  return labelWordCount(text) > SHORT_LABEL_WORDS
}

function labelTokenList(text) {
  return String(text || '')
    .toLowerCase()
    .trim()
    .split(/\s+/)
    .filter(Boolean)
}

/** Suggestion must be a shorter run of the writer's own words, in the same order. */
export function isKeptWording(source, suggestion) {
  const src = labelTokenList(source)
  const sug = labelTokenList(suggestion)
  if (!sug.length || sug.length >= src.length) return false
  let i = 0
  for (const token of sug) {
    while (i < src.length && src[i] !== token) i += 1
    if (i >= src.length) return false
    i += 1
  }
  return true
}

/** One cheap shorten call after the writer pauses. Empty string means do not offer. */
export async function suggestShortLabel(text, options = {}) {
  const source = clipUserLabel(text)
  if (!needsShortLabel(source)) return ''
  const response = await fetchWithTimeout(
    '/api/shorten-label',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: source }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )
  const data = await readJson(response)
  if (!response.ok) throw toApiError(response.status, data)
  const suggestion = clipWords(String(data.text || '').trim(), SHORT_LABEL_WORDS)
  return isKeptWording(source, suggestion) ? suggestion : ''
}

/** GET /api/health — whether FastAPI + OpenAI key are ready. */
export async function fetchRationaleHealth(options = {}) {
  const response = await fetchWithTimeout('/api/health', { signal: options.signal }, 8000)
  if (!response.ok) {
    throw new RationaleApiError("Can't reach the label server. Refresh, or try again in a moment.", {
      code: 'offline',
      status: response.status,
    })
  }
  return response.json()
}

async function fetchWithTimeout(url, init, timeoutMs) {
  const outer = new AbortController()
  const timer = setTimeout(() => outer.abort(), timeoutMs)
  const onAbort = () => outer.abort()
  if (init.signal) {
    if (init.signal.aborted) outer.abort()
    else init.signal.addEventListener('abort', onAbort, { once: true })
  }

  try {
    return await fetch(url, { ...init, signal: outer.signal })
  } catch (err) {
    if (err?.name === 'AbortError') {
      if (init.signal?.aborted) {
        throw new RationaleApiError('Label generation was cancelled.', { code: 'cancelled', status: 0 })
      }
      throw new RationaleApiError('The AI took too long. Check your connection and try again.', {
        code: 'timeout',
        status: 0,
      })
    }
    throw new RationaleApiError("Can't reach the server. Refresh, or try again in a moment.", {
      code: 'offline',
      status: 0,
    })
  } finally {
    clearTimeout(timer)
    init.signal?.removeEventListener('abort', onAbort)
  }
}

async function readJson(response) {
  try {
    return await response.json()
  } catch {
    return {}
  }
}

function toApiError(status, data) {
  const detail = data?.detail
  const payload = typeof detail === 'object' && detail ? detail : data
  const code = payload?.code || statusCodeToCode(status)
  const message =
    (typeof payload?.message === 'string' && payload.message) ||
    (typeof detail === 'string' && detail) ||
    fallbackMessage(status)
  return new RationaleApiError(message, { code, status })
}

function statusCodeToCode(status) {
  if (status === 400) return 'invalid_input'
  if (status === 429) return 'rate_limit'
  if (status === 503) return 'offline'
  if (status === 504) return 'timeout'
  return 'upstream_error'
}

function fallbackMessage(status) {
  if (status === 429) return 'Too many requests. Wait a few seconds and try again.'
  if (status === 503 || status === 504) return "Can't reach the AI service right now. Try again in a moment."
  return "Couldn't generate labels. Try again."
}

/** Cue leftovers that flip the claim. Synonyms (because/since, cheap/inexpensive) are not here. */
const NEGATION_TOKENS = new Set([
  'not', 'no', 'never', 'none', 'nor', 'cannot', 'cant', "can't", 'dont', "don't", 'wont', "won't",
  'without',
])

const OPPOSITE_OF = oppositeMap([
  ['cheap', 'expensive'],
  ['cheap', 'costly'],
  ['cheaper', 'expensive'],
  ['cheaper', 'costly'],
  ['more', 'less'],
  ['better', 'worse'],
  ['wrong', 'right'],
  ['break', 'merge'],
  ['break', 'combine'],
  ['breaking', 'merging'],
])

function oppositeMap(pairs) {
  const map = new Map()
  for (const [a, b] of pairs) {
    if (!map.has(a)) map.set(a, new Set())
    if (!map.has(b)) map.set(b, new Set())
    map.get(a).add(b)
    map.get(b).add(a)
  }
  return map
}

const STOP_TOKENS = new Set([
  'the', 'a', 'an', 'and', 'or', 'to', 'of', 'in', 'on', 'for', 'it', 'is', 'this', 'that',
  'i', 'we', 'my', 'our', 'with', 'as', 'at', 'be', 'was', 'are', 'so', 'just', 'very',
  'really', 'quite', 'also', 'too', 'then', 'than', 'from', 'by', 'about', 'into', 'over',
  'but', 'if',
])

const SMASH_RE = /asdf|qwer|zxcv|hjkl|qazwsx|1234|abcd|yyy|xxx|zzz/

export const NOT_SURE_LABEL = 'not sure'
export const NOT_SURE_RID = 'r:not-sure'

/**
 * Local gate so Enter never spends a token on junk or on a near-copy of the last generate.
 * No embedding, no second model: lexical structure + a small cue-word list.
 *
 * Returns { ok: true } to call the model, { ok: true, preset } to stamp a fixed
 * label with no API call, or { ok: false, code, message }.
 */
export function assessReflection(text, previous = '') {
  const rationale = String(text || '').trim()
  if (!rationale) {
    return { ok: false, code: 'empty', message: '' }
  }
  // Can't-articulate: one fixed chip, no prompt.
  if (isHesitation(rationale)) {
    return { ok: true, preset: NOT_SURE_LABEL }
  }
  if (isGibberish(rationale)) {
    return {
      ok: false,
      code: 'not_meaningful',
      message: "That doesn't read as a reflection. Write what you can, even if it's unsure.",
    }
  }
  if (isTrivialRevision(previous, rationale)) {
    return {
      ok: false,
      code: 'not_distinguishable',
      message: 'no new labels due to no new input reflection',
    }
  }
  return { ok: true }
}

/** Latin words stay whole; CJK ideographs are tokenized one character at a time. */
function reflectionTokens(text) {
  return String(text || '')
    .toLowerCase()
    .match(/[\p{Script=Han}]|[a-z0-9']+/gu) || []
}

function isGibberishToken(token) {
  if (!token || token.length < 4) return false
  if (/^(.)\1{3,}$/.test(token)) return true
  if (SMASH_RE.test(token)) return true
  if (!/^[a-z']+$/.test(token)) return false
  const vowels = (token.match(/[aeiou]/g) || []).length
  return vowels / token.length < 0.12
}

function isGibberish(text) {
  const letters = String(text).match(/[\p{L}\p{N}]/gu) || []
  const tokens = reflectionTokens(text)
  if (!letters.length && !tokens.length) return true

  const compact = letters.join('').toLowerCase()
  const nonSpace = String(text).replace(/\s/g, '')
  if (compact.length >= 8 && compact.length / Math.max(nonSpace.length, 1) < 0.55) return true

  if (compact.length) {
    const freq = new Map()
    for (const ch of compact) freq.set(ch, (freq.get(ch) || 0) + 1)
    const top = Math.max(...freq.values())
    if (compact.length >= 8 && top / compact.length > 0.55) return true
    if (compact.length >= 12 && freq.size <= 3) return true
  }

  const latin = compact.match(/[a-z]/g) || []
  const vowels = compact.match(/[aeiou]/g) || []
  const han = compact.match(/\p{Script=Han}/gu) || []
  if (latin.length >= 10 && han.length === 0 && vowels.length / latin.length < 0.12) return true
  if (SMASH_RE.test(compact)) return true

  if (!tokens.length) return false
  const real = tokens.filter((token) => !isGibberishToken(token))
  if (tokens.length && !real.length) return true
  if (tokens.length >= 6 && new Set(tokens).size / tokens.length < 0.25) return true
  return false
}

/**
 * True only when the reflection has no substance left after hedges are removed.
 * "idk" in a longer note is a tic, not the whole rationale — leftover content still generates.
 */
function isHesitation(text) {
  const folded = String(text || '')
    .toLowerCase()
    .replace(/[.!?,…~\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!folded) return /[.?!…]+/.test(text)
  const leftover = stripHedges(folded)
  if (!leftover) return true
  const tokens = leftover.match(/[\p{Script=Han}]|[a-z0-9']+/gu) || []
  const content = tokens.filter((token) => !STOP_TOKENS.has(token) && !HEDGE_WORDS.has(token) && !isGibberishToken(token))
  return content.length === 0
}

const HEDGE_PHRASES = [
  /\b(i|im|i'm|i am)\s+(dont|don't|do not)\s+know\b/g,
  /\b(dont|don't|do not)\s+know\b/g,
  /\bidk\b/g,
  /\bdunno\b/g,
  /\bnot sure\b/g,
  /\bno idea\b/g,
  /\bhard to say\b/g,
  /\b(cant|can't|cannot)\s+(say|explain|tell)\b/g,
  /\bi guess\b/g,
  /\byou know\b/g,
  /\bi mean\b/g,
  /\bhmm+\b/g,
  /\bhuh+\b/g,
  /\bmeh+\b/g,
  /\bugh+\b/g,
]

const HEDGE_WORDS = new Set(['unsure', 'confused', 'lost', 'maybe', 'perhaps', 'whatever', 'anyway'])

function stripHedges(folded) {
  let next = folded
  for (const re of HEDGE_PHRASES) {
    re.lastIndex = 0
    next = next.replace(re, ' ')
  }
  return next.replace(/\s+/g, ' ').trim()
}

function contentTokens(tokens) {
  return tokens.filter((token) => !STOP_TOKENS.has(token) && !isGibberishToken(token))
}

/** Suffix strip only — enough to treat prototype/prototyping and cheap/cheaper as the same word. */
function stem(token) {
  if (token.length <= 4 || !/^[a-z']+$/.test(token)) return token
  let next = token.replace(/'s$/, '')
  next = next.replace(/ing$/, '').replace(/ed$/, '').replace(/ly$/, '')
  next = next.replace(/ers$/, 'er').replace(/est$/, '').replace(/ies$/, 'y').replace(/es$/, '').replace(/s$/, '')
  next = next.replace(/er$/, '')
  return next.length >= 3 ? next : token
}

/** in/un/non + X is a different word from X (inexpensive ≠ expensive). */
function isNegationPair(a, b) {
  const [long, short] = a.length >= b.length ? [a, b] : [b, a]
  if (long.length - short.length < 2) return false
  return /^(un|in|im|ir|non)/.test(long) && long.endsWith(short)
}

function damerau(a, b) {
  if (a === b) return 0
  const n = a.length
  const m = b.length
  if (!n) return m
  if (!m) return n
  const row = Array.from({ length: m + 1 }, (_, i) => i)
  let prevPrev
  let prevRow = row.slice()
  for (let i = 1; i <= n; i += 1) {
    const current = [i]
    for (let j = 1; j <= m; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1
      current[j] = Math.min(prevRow[j] + 1, current[j - 1] + 1, prevRow[j - 1] + cost)
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        current[j] = Math.min(current[j], (prevPrev?.[j - 2] ?? i) + 1)
      }
    }
    prevPrev = prevRow
    prevRow = current
  }
  return prevRow[m]
}

/**
 * Same word after a stiff tweak: case is already gone; this catches typos,
 * one or two added/removed letters, transpositions, and light stemming.
 */
function tokensClose(a, b) {
  if (a === b) return true
  if (isNegationPair(a, b)) return false
  const sa = stem(a)
  const sb = stem(b)
  if (sa === sb) return true
  const n = Math.min(a.length, b.length)
  const dist = damerau(a, b)
  if (n >= 3 && dist <= 1) return true
  if (n >= 6 && dist <= 2) return true
  return sa.length >= 3 && sb.length >= 3 && damerau(sa, sb) <= 1
}

function isNegation(token) {
  return NEGATION_TOKENS.has(token) || NEGATION_TOKENS.has(stem(token))
}

function oppositesOf(token) {
  return new Set([...(OPPOSITE_OF.get(token) || []), ...(OPPOSITE_OF.get(stem(token)) || [])])
}

/** Added/removed negation, or swapped a known opposite. Synonym leftovers do not count. */
function isClaimFlip(leftoverPrev, leftoverNext) {
  const prevNeg = leftoverPrev.filter(isNegation)
  const nextNeg = leftoverNext.filter(isNegation)
  if (prevNeg.length !== nextNeg.length) return true
  return leftoverPrev.some((token) => {
    const opps = oppositesOf(token)
    return leftoverNext.some((other) => opps.has(other) || opps.has(stem(other)))
  })
}

/** Greedy 1:1 match of next tokens onto prev tokens using tokensClose. */
function unmatchedContent(prev, next) {
  const used = new Set()
  const leftoverNext = []
  for (const token of next) {
    const hit = prev.findIndex((candidate, i) => !used.has(i) && tokensClose(token, candidate))
    if (hit >= 0) used.add(hit)
    else leftoverNext.push(token)
  }
  const leftoverPrev = prev.filter((_, i) => !used.has(i))
  return { leftoverPrev, leftoverNext, matched: used.size }
}

/**
 * True when `next` is the previous reflection with a stiff tweak or junk appended.
 *
 * Counts as the same (no generate): case, punctuation, word order, typos, adding/removing
 * a letter or two inside a word, and swapping one word for a synonym.
 * Counts as new: a cue-word flip (not / cheap→expensive) or a whole new clause.
 *
 * Newness is counted in words, never as a share of the text: a clause added to a long
 * reflection is just as new as the same clause added to a short one.
 */
function isTrivialRevision(previous, next) {
  const prev = String(previous || '').trim()
  if (!prev) return false
  const prevTokens = reflectionTokens(prev)
  const nextTokens = reflectionTokens(next)
  if (!prevTokens.length) return false

  const nextReal = nextTokens.filter((token) => !isGibberishToken(token))
  if (!nextReal.length) return true
  if (sameWording(prevRealText(prevTokens), nextReal.join(' '))) return true

  const prevContent = contentTokens(prevTokens)
  const nextContent = contentTokens(nextTokens)
  if (!prevContent.length && !nextContent.length) return true

  const { leftoverPrev, leftoverNext, matched } = unmatchedContent(prevContent, nextContent)
  if (isClaimFlip(leftoverPrev, leftoverNext)) return false

  const leftover = leftoverPrev.length + leftoverNext.length
  if (leftover <= 2) return true
  if (leftoverNext.length >= NEW_CLAUSE_WORDS) return false
  const total = Math.max(prevContent.length, nextContent.length)
  if (total && matched / total >= 0.85) return true
  return false
}

function prevRealText(tokens) {
  return tokens.filter((token) => !isGibberishToken(token)).join(' ')
}

/**
 * Same idea in different words: vague ↔ non-clear, inputs ↔ input.
 * Not a generate-gate — only for highlighting a user chip against AI chips.
 */
const LABEL_SYNONYMS = synonymIndex([
  ['vague', 'unclear', 'fuzzy', 'ambiguous', 'imprecise', 'nonclear', 'indistinct'],
  ['input', 'inputs'],
  ['allow', 'allows', 'allowed', 'accept', 'accepts', 'permit', 'permits'],
  ['comfort', 'comfortable', 'convenience'],
  ['reason', 'rationale'],
  ['user', 'users', 'people'],
  ['merge', 'merging', 'merges', 'merged', 'merg', 'combine', 'combining', 'remix', 'remixing'],
  ['artifact', 'artifacts', 'part', 'parts', 'piece', 'pieces', 'thing', 'things', 'component', 'components'],
  ['create', 'creating', 'creation', 'creativity', 'creative', 'invent'],
  ['enhance', 'enhances', 'enhancing', 'boost', 'boosts'],
  ['break', 'breaking', 'breaks', 'broke', 'broken', 'decompose', 'deconstruct', 'dismantle'],
  ['current', 'existing', 'exist', 'present'],
  ['idea', 'ideas', 'innovation', 'innovate', 'innovative'],
  ['foster', 'foester', 'fosters', 'fostering', 'encourage', 'encourages'],
])

function synonymIndex(groups) {
  const map = new Map()
  for (const group of groups) {
    const canon = group[0]
    for (const word of group) map.set(word, canon)
  }
  return map
}

function mergeNegationPrefixes(tokens) {
  const out = []
  for (let i = 0; i < tokens.length; i += 1) {
    if (tokens[i] === 'non' && tokens[i + 1]) {
      out.push(`un${tokens[i + 1]}`, `non${tokens[i + 1]}`)
      i += 1
      continue
    }
    out.push(tokens[i])
  }
  return out
}

function canonToken(token) {
  const stemmed = stem(token)
  const keys = [token, stemmed]
  if (token.endsWith('ing') && token.length > 5 && !stemmed.endsWith('e')) keys.push(`${stemmed}e`)
  for (const key of keys) {
    if (LABEL_SYNONYMS.has(key)) return LABEL_SYNONYMS.get(key)
  }
  return stemmed
}

function labelCanonSet(text) {
  const tokens = mergeNegationPrefixes(reflectionTokens(text)).filter(
    (token) => !STOP_TOKENS.has(token) && !isGibberishToken(token),
  )
  return new Set(tokens.map(canonToken))
}

/**
 * Two short labels name the same reason. Catches paraphrase, not only typos.
 * Opposites (cheap vs expensive) are not similar. Used to highlight, not to rewrite.
 */
function labelCanonList(text) {
  return mergeNegationPrefixes(reflectionTokens(text))
    .filter((token) => !STOP_TOKENS.has(token) && !isGibberishToken(token))
    .map(canonToken)
}

export function isSimilarLabel(a, b) {
  const left = String(a || '').trim()
  const right = String(b || '').trim()
  if (!left || !right) return false
  if (sameWording(left, right)) return true

  const la = labelCanonList(left)
  const lb = labelCanonList(right)
  if (!la.length || !lb.length) return false
  const aligned = unmatchedContent(la, lb)
  if (isClaimFlip(aligned.leftoverPrev, aligned.leftoverNext)) return false
  const leftover = aligned.leftoverPrev.length + aligned.leftoverNext.length
  const total = Math.max(la.length, lb.length)
  if (aligned.matched >= 2 && aligned.matched / total >= 0.4) return true
  if (aligned.matched >= 2 && leftover <= 4 && aligned.matched / total >= 0.33) return true

  const ta = contentTokens(reflectionTokens(left))
  const tb = contentTokens(reflectionTokens(right))
  const { leftoverPrev, leftoverNext, matched } = unmatchedContent(ta, tb)
  if (isClaimFlip(leftoverPrev, leftoverNext)) return false
  const rawLeftover = leftoverPrev.length + leftoverNext.length
  const rawTotal = Math.max(ta.length, tb.length)
  if (matched && rawLeftover <= 2 && matched / rawTotal >= 0.5) return true
  if (matched / rawTotal >= 0.85) return true

  const sa = new Set(la)
  const sb = new Set(lb)
  let shared = 0
  for (const token of sa) if (sb.has(token)) shared += 1
  if (shared < 2) return false
  const union = sa.size + sb.size - shared
  return shared / union >= 0.3
}

/** Group labels that share meaning. Original texts stay intact; UI can pair them. */
export function clusterSimilarLabels(items) {
  const list = (Array.isArray(items) ? items : []).filter(Boolean)
  const parent = list.map((_, i) => i)
  const find = (i) => {
    if (parent[i] !== i) parent[i] = find(parent[i])
    return parent[i]
  }
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      if (!isSimilarLabel(list[i].text, list[j].text)) continue
      parent[find(i)] = find(j)
    }
  }
  const buckets = new Map()
  list.forEach((item, i) => {
    const root = find(i)
    if (!buckets.has(root)) buckets.set(root, [])
    buckets.get(root).push(item)
  })
  return [...buckets.values()].map((members) => {
    const preview = members.reduce((best, item) =>
      String(item.text || '').length < String(best.text || '').length ? item : best,
    members[0])
    return {
      id: members[0].id,
      preview: preview.text,
      previewId: preview.id,
      source: members.some((item) => item.source === 'user') ? 'user' : 'ai',
      members,
    }
  })
}

/** Short cue after a side-by-side pair. Does not rewrite either label. */
export function sameMeaningHint(count) {
  if (count < 2) return ''
  return count === 2 ? 'these two are the same' : 'these mean the same'
}

/**
 * How often a wording pattern shows up across the canvas.
 * `owners` = distinct notes/relations; `phrases` = label copies in the cluster.
 */
export function patternStatsFromLabels(rows) {
  const groups = clusterSimilarLabels(rows)
  const byRid = {}
  for (const group of groups) {
    const owners = new Set(group.members.map((item) => item.owner).filter(Boolean))
    const stats = { owners: owners.size, phrases: group.members.length, ownerKeys: [...owners] }
    if (stats.owners < 2 && stats.phrases < 2) continue
    for (const item of group.members) {
      if (!item?.rid) continue
      const prev = byRid[item.rid]
      if (!prev || stats.owners > prev.owners || stats.phrases > prev.phrases) {
        byRid[item.rid] = stats
      }
    }
  }
  return { byRid }
}

/** Repeating pattern across ideas — not just two chips on the same note. */
export function patternAppearsHint(stats) {
  const n = Number(stats?.owners) || 0
  if (n < 2) return ''
  return n === 2 ? 'this pattern appears on 2 ideas' : `this pattern appears on ${n} ideas`
}

/** Local pair cue plus canvas-wide recurrence, when both apply. */
export function pairPatternHint(memberCount, stats) {
  const local = sameMeaningHint(memberCount)
  const across = patternAppearsHint(stats)
  if (local && across) return `${local} · ${across}`
  return local || across
}

/** Shorter copy for the thin strip beside a note tab. */
export function tabPatternHint(memberCount, stats) {
  const n = Number(stats?.owners) || 0
  if (n >= 2) return n === 2 ? 'on 2 ideas' : `on ${n} ideas`
  return sameMeaningHint(memberCount)
}

/** Lexical neighbours for the highlight path when cosine is empty or too strict. */
export function lexicalMeaningHits(queryText, items, excludeId, limit = MEANING_TOP_K) {
  return (Array.isArray(items) ? items : [])
    .filter(
      (item) =>
        item?.text &&
        String(item.id) !== String(excludeId) &&
        isSimilarLabel(queryText, item.text),
    )
    .slice(0, limit)
    .map((item) => ({ ref: String(item.id), text: item.text, score: 1 }))
}

function normalizeLabel(item) {
  const text = clipWords(String(item?.text ?? '').trim())
  let kind = String(item?.kind ?? 'insight').trim().toLowerCase()
  if (!LABEL_KINDS.includes(kind)) kind = 'insight'
  const sameAs = String(item?.same_as ?? item?.sameAs ?? '').trim()
  if (sameAs) return { text: '', kind, sameAs, phrasing: clipWords(String(item?.phrasing ?? '').trim()), embedding: null }
  return {
    text,
    kind,
    sameAs: '',
    phrasing: '',
    embedding: asVector(item?.embedding),
  }
}

/** Same reasoning identity: shared rid, else identical wording. */
export function sameRationale(a, b) {
  if (!a || !b) return false
  if (a.rid && b.rid && a.rid === b.rid) return true
  const at = String(a.text || '').trim()
  const bt = String(b.text || '').trim()
  return Boolean(at && at === bt)
}

/**
 * What we actually keep: human-created or human-edited (yellow) labels,
 * plus whatever is currently in the top 3 — including unmodified AI that was chosen.
 * Pure AI that was never pinned and never edited is dropped.
 */
export function persistedRationaleLabels(labels, pinned = []) {
  const pool = Array.isArray(labels) ? labels : []
  const pins = Array.isArray(pinned) ? pinned : []
  const kept = []
  const seen = new Set()
  const keyOf = (item) => {
    if (item?.rid) return `rid:${item.rid}`
    if (item?.id != null) return `id:${item.id}`
    return `t:${String(item?.text || '').trim().toLowerCase()}`
  }
  const add = (item) => {
    const text = String(item?.text || '').trim()
    if (!text) return
    const human = item?.source === 'user'
    const chosen = pins.some((pin) => sameRationale(pin, item))
    if (!human && !chosen) return
    const key = keyOf(item)
    if (seen.has(key)) return
    seen.add(key)
    kept.push(item)
  }
  for (const item of pool) add(item)
  for (const pin of pins) {
    if (!kept.some((item) => sameRationale(item, pin))) add(pin)
  }
  return kept
}

/**
 * Cheap backstop for the paths the model never sees (demo labels, offline).
 * Only catches wording that is effectively identical once punctuation and word order go.
 */
export function sameWording(a, b) {
  const key = (text) =>
    String(text || '')
      .toLowerCase()
      .replace(/[^\p{L}\p{N}\s]/gu, ' ')
      .split(/\s+/)
      .filter(Boolean)
      .sort()
      .join(' ')
  const left = key(a)
  return Boolean(left) && left === key(b)
}

/**
 * Idle tab/chip text: keep a short head and the distinctive tail.
 * The stored label is unchanged; hover can show `full`.
 */
const PREVIEW_MAX_CHARS = 34

export function compactLabelDisplay(text) {
  const full = String(text || '').replace(/\s+/g, ' ').trim()
  if (!full) return { preview: '', full: '', compact: false }
  const words = full.split(/\s+/).filter(Boolean)
  if (words.length >= 2) {
    if (words.length <= 6 && full.length <= PREVIEW_MAX_CHARS) {
      return { preview: full, full, compact: false }
    }
    const attempts = [[3, 2], [2, 2], [2, 1]]
    for (const [headN, tailN] of attempts) {
      if (words.length <= headN + tailN) continue
      const preview = `${words.slice(0, headN).join(' ')} … ${words.slice(-tailN).join(' ')}`
      if (preview.length <= PREVIEW_MAX_CHARS) return { preview, full, compact: true }
    }
    return {
      preview: `${full.slice(0, 12).trim()} … ${full.slice(-10).trim()}`,
      full,
      compact: true,
    }
  }
  if (full.length <= 22) return { preview: full, full, compact: false }
  return {
    preview: `${full.slice(0, 10).trim()} … ${full.slice(-8).trim()}`,
    full,
    compact: true,
  }
}

/** Visible tag text: at most ten words. Kind stays in data only. */
export function clipWords(text, max = MAX_LABEL_WORDS) {
  return String(text || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, max)
    .join(' ')
}
