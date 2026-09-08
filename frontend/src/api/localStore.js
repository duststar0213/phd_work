/**
 * Browser-only accounts and canvases. Same email + password on this computer
 * restores the last board. Nothing here is sent to the study server.
 */

const PREFIX = 'repertoire.local'
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PASSWORD_CHARS = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'

export class LocalAuthError extends Error {
  constructor(message, { code = 'unknown' } = {}) {
    super(message)
    this.name = 'LocalAuthError'
    this.code = code
  }
}

function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

function writeJson(key, value) {
  localStorage.setItem(key, JSON.stringify(value))
}

function accountsKey() {
  return `${PREFIX}.accounts`
}

function sessionKey() {
  return `${PREFIX}.session`
}

function canvasKey(email) {
  return `${PREFIX}.canvas.${email}`
}

export function normalizeEmail(raw) {
  const email = String(raw || '').trim().toLowerCase()
  if (!EMAIL_RE.test(email)) {
    throw new LocalAuthError('Please enter a valid email address.', { code: 'invalid_email' })
  }
  return email
}

function readAccounts() {
  const data = readJson(accountsKey(), {})
  return data && typeof data === 'object' ? data : {}
}

function writeAccounts(accounts) {
  writeJson(accountsKey(), accounts)
}

function randomPassword(length = 8) {
  const bytes = crypto.getRandomValues(new Uint8Array(length))
  return [...bytes].map((n) => PASSWORD_CHARS[n % PASSWORD_CHARS.length]).join('')
}

async function hashPassword(password) {
  const saltBytes = crypto.getRandomValues(new Uint8Array(16))
  const salt = [...saltBytes].map((n) => n.toString(16).padStart(2, '0')).join('')
  const encoded = new TextEncoder().encode(`${salt}:${password}`)
  const digest = await crypto.subtle.digest('SHA-256', encoded)
  const hex = [...new Uint8Array(digest)].map((n) => n.toString(16).padStart(2, '0')).join('')
  return `${salt}$${hex}`
}

async function verifyPassword(password, stored) {
  const [salt, digest] = String(stored || '').split('$')
  if (!salt || !digest) return false
  const encoded = new TextEncoder().encode(`${salt}:${password}`)
  const check = await crypto.subtle.digest('SHA-256', encoded)
  const hex = [...new Uint8Array(check)].map((n) => n.toString(16).padStart(2, '0')).join('')
  return hex === digest
}

export function getSessionEmail() {
  const data = readJson(sessionKey(), null)
  const email = String(data?.email || '').trim().toLowerCase()
  return email || ''
}

export function setSessionEmail(email) {
  writeJson(sessionKey(), { email })
}

export function clearSession() {
  localStorage.removeItem(sessionKey())
}

export function emptyCanvas() {
  return { notes: [], connections: [], pan: { x: 0, y: 0 }, nextId: 1, nextConnId: 1, scale: 1 }
}

export function loadLocalCanvas(email) {
  const data = readJson(canvasKey(email), null)
  if (!data || typeof data !== 'object') return null
  return data
}

export function saveLocalCanvas(email, payload) {
  writeJson(canvasKey(email), payload)
}

export function hasLocalAccount(email) {
  return Boolean(readAccounts()[email]?.passwordHash)
}

export async function registerLocalAccount(rawEmail) {
  const email = normalizeEmail(rawEmail)
  if (hasLocalAccount(email)) {
    return { email, isNew: false }
  }
  const password = randomPassword()
  const accounts = readAccounts()
  accounts[email] = {
    passwordHash: await hashPassword(password),
    createdAt: new Date().toISOString(),
  }
  writeAccounts(accounts)
  setSessionEmail(email)
  return { email, isNew: true, password }
}

export async function loginLocalAccount(rawEmail, password) {
  const email = normalizeEmail(rawEmail)
  const account = readAccounts()[email]
  if (!account?.passwordHash) {
    throw new LocalAuthError('No study space for this email yet. Start from the email step.', {
      code: 'unknown_email',
    })
  }
  if (!(await verifyPassword(String(password || '').trim(), account.passwordHash))) {
    throw new LocalAuthError('That password is not right.', { code: 'bad_credentials' })
  }
  setSessionEmail(email)
  return { email }
}

export async function resetLocalPassword(rawEmail) {
  const email = normalizeEmail(rawEmail)
  const password = randomPassword()
  const accounts = readAccounts()
  if (!accounts[email] && !loadLocalCanvas(email)) {
    throw new LocalAuthError('No study space for this email yet. Start from the email step.', {
      code: 'unknown_email',
    })
  }
  accounts[email] = {
    passwordHash: await hashPassword(password),
    createdAt: accounts[email]?.createdAt || new Date().toISOString(),
  }
  writeAccounts(accounts)
  setSessionEmail(email)
  return { email, password }
}
