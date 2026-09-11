/**
 * Participant session + canvas persistence.
 * Identity and the board live in this browser. The study server is not sent
 * emails, passwords, canvases, or interaction events.
 * Cookies are still used only for the invitation-code gate.
 */

import {
  clearSession,
  emptyCanvas,
  getSessionEmail,
  loadLocalCanvas,
  loginLocalAccount,
  LocalAuthError,
  normalizeEmail,
  registerLocalAccount,
  resetLocalPassword,
  saveLocalCanvas,
  setSessionEmail,
} from './localStore'

export class SessionError extends Error {
  constructor(message, { code = 'unknown', status = 0 } = {}) {
    super(message)
    this.name = 'SessionError'
    this.code = code
    this.status = status
  }
}

function asSessionError(err) {
  if (err instanceof SessionError) return err
  if (err instanceof LocalAuthError) {
    return new SessionError(err.message, { code: err.code })
  }
  return new SessionError(err?.message || "Couldn't reach the study server.")
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

export async function startSession(email) {
  try {
    const data = await registerLocalAccount(email)
    if (data.isNew) return { email: data.email, is_new: true, password: data.password }
    return { email: data.email, is_new: false }
  } catch (err) {
    throw asSessionError(err)
  }
}

export async function login(email, password) {
  try {
    const data = await loginLocalAccount(email, password)
    return { email: data.email }
  } catch (err) {
    throw asSessionError(err)
  }
}

export async function resetPassword(email) {
  try {
    const data = await resetLocalPassword(email)
    return { email: data.email, password: data.password }
  } catch (err) {
    throw asSessionError(err)
  }
}

export function logout() {
  clearSession()
  return Promise.resolve({ ok: true })
}

async function currentEmail() {
  const local = getSessionEmail()
  if (local) return local
  const data = await api('/api/auth/me')
  const email = normalizeEmail(data.email || data.username || '')
  setSessionEmail(email)
  return email
}

export async function fetchMe() {
  try {
    const email = await currentEmail()
    return { email }
  } catch {
    throw new SessionError('Please sign in with your email.', { code: 'auth_required', status: 401 })
  }
}

async function importServerCanvas() {
  try {
    return await api('/api/canvas')
  } catch {
    return null
  }
}

export async function fetchCanvas() {
  const email = await currentEmail().catch(() => '')
  if (!email) throw new SessionError('Please sign in with your email.', { code: 'auth_required', status: 401 })
  const local = loadLocalCanvas(email)
  if (local) return local
  const imported = await importServerCanvas()
  if (imported && typeof imported === 'object') {
    saveLocalCanvas(email, imported)
    return imported
  }
  return emptyCanvas()
}

export function saveCanvas(payload) {
  const email = getSessionEmail()
  if (!email) return Promise.reject(new SessionError('Please sign in with your email.', { code: 'auth_required', status: 401 }))
  try {
    saveLocalCanvas(email, payload)
  } catch (err) {
    // Browser storage is finite and context images are the one thing that can fill it.
    return Promise.reject(
      new SessionError('The board is too big to save in this browser. Remove a context image.', {
        code: 'storage_full',
      }),
    )
  }
  return Promise.resolve({ ok: true })
}

export function logEvent() {
  return Promise.resolve({ ok: true })
}
