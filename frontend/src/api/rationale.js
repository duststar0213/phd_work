/**
 * Rationale-label API — callable on its own (not wired to notes/relations yet).
 * POST /api/rationale-labels → FastAPI → OpenAI.
 * Throws RationaleApiError with a visitor-facing `message` (and `code`) on failure.
 */

const LABEL_KINDS = ['assumption', 'constraint', 'goal', 'tension', 'insight', 'question']
const MIN_RATIONALE_CHARS = 8
const REQUEST_TIMEOUT_MS = 35000
const MAX_LABEL_WORDS = 3

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
  return [
    { text: clipWords(snippet || 'stated intent'), kind: 'goal' },
    { text: clipWords(relation ? 'why these link' : 'unspoken constraint'), kind: 'constraint' },
    { text: clipWords(relation ? 'shared assumption' : 'possible tension'), kind: relation ? 'assumption' : 'tension' },
    { text: clipWords('open question'), kind: 'question' },
  ]
}

/** POST /api/rationale-labels. `target` is generic | note | relation. */
export async function generateRationaleLabels(text, target = 'generic', options = {}) {
  const rationale = String(text ?? '').trim()
  if (rationale.length < MIN_RATIONALE_CHARS) {
    throw new RationaleApiError('Write a bit more rationale so the labels can be specific.', {
      code: 'too_short',
      status: 400,
    })
  }

  const response = await fetchWithTimeout(
    '/api/rationale-labels',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: rationale, target }),
      signal: options.signal,
    },
    REQUEST_TIMEOUT_MS,
  )

  const data = await readJson(response)
  if (!response.ok) {
    throw toApiError(response.status, data)
  }

  const labels = Array.isArray(data.labels) ? data.labels.map(normalizeLabel).filter((item) => item.text) : []
  if (!labels.length) {
    throw new RationaleApiError('The AI returned no labels. Try rephrasing and generate again.', {
      code: 'empty_labels',
      status: response.status,
    })
  }

  return { labels, model: data.model || '' }
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

function normalizeLabel(item) {
  const text = clipWords(String(item?.text ?? '').trim())
  let kind = String(item?.kind ?? 'insight').trim().toLowerCase()
  if (!LABEL_KINDS.includes(kind)) kind = 'insight'
  return { text, kind }
}

/** Visible tag text: at most three words. Kind stays in data only. */
export function clipWords(text, max = MAX_LABEL_WORDS) {
  return String(text || '')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, max)
    .join(' ')
}
