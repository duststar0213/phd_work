/**
 * Participant session + canvas persistence.
 * Cookies are httpOnly; fetch must send credentials.
 */

export class SessionError extends Error {
  constructor(message, { code = 'unknown', status = 0 } = {}) {
    super(message)
    this.name = 'SessionError'
    this.code = code
    this.status = status
  }
}

async function api(path, init = {}) {
  const response = await fetch(path, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(init.headers || {}) },
    ...init,
  })
  let data = {}
  try {
    data = await response.json()
  } catch {
    data = {}
  }
  if (!response.ok) {
    const detail = data?.detail
    const payload = typeof detail === 'object' && detail ? detail : data
    const message =
      (typeof payload?.message === 'string' && payload.message) ||
      (typeof detail === 'string' && detail) ||
      "Couldn't reach the study server."
    throw new SessionError(message, { code: payload?.code || 'unknown', status: response.status })
  }
  return data
}

export function fetchAuthConfig() {
  return api('/api/auth/config')
}

export function unlockGate(access) {
  return api('/api/auth/gate', {
    method: 'POST',
    body: JSON.stringify({ access }),
  })
}

export function startSession(email) {
  return api('/api/auth/start', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function login(email, password) {
  return api('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
}

export function resetPassword(email) {
  return api('/api/auth/reset', {
    method: 'POST',
    body: JSON.stringify({ email }),
  })
}

export function logout() {
  return api('/api/auth/logout', { method: 'POST', body: '{}' })
}

export function fetchMe() {
  return api('/api/auth/me')
}

export function fetchCanvas() {
  return api('/api/canvas')
}

export function saveCanvas(payload) {
  return api('/api/canvas', { method: 'PUT', body: JSON.stringify(payload) })
}

export function logEvent(type, payload = {}) {
  return api('/api/events', {
    method: 'POST',
    body: JSON.stringify({ type, payload }),
  }).catch(() => {})
}
