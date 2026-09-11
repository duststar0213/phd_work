/**
 * Images attached to the design context.
 *
 * The board is kept in localStorage (see api/localStore.js), so an imported photo has to be
 * shrunk before it is ever stored: a phone picture would fill the whole quota on its own.
 * Every import is redrawn at most MAX_EDGE across and re-encoded as JPEG.
 */

export const MAX_CONTEXT_IMAGES = 5
const MAX_EDGE = 1024
const QUALITY = 0.72
const MAX_SOURCE_BYTES = 25 * 1024 * 1024
const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp'

export const IMAGE_ACCEPT = ACCEPT

export class ContextImageError extends Error {
  constructor(message, { code = 'unknown' } = {}) {
    super(message)
    this.name = 'ContextImageError'
    this.code = code
  }
}

function loadBitmap(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      URL.revokeObjectURL(url)
      resolve(img)
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      reject(new ContextImageError("That file couldn't be read as an image.", { code: 'decode_failed' }))
    }
    img.src = url
  })
}

/** Fit inside a MAX_EDGE box, never scaling a small image up. */
function fitted(width, height) {
  const longest = Math.max(width, height)
  if (!longest) return { w: 0, h: 0 }
  const ratio = Math.min(1, MAX_EDGE / longest)
  return { w: Math.round(width * ratio), h: Math.round(height * ratio) }
}

/**
 * File → { id, name, dataUrl, w, h, bytes }.
 * The returned dataUrl is what gets stored and what the AI reads.
 */
export async function importContextImage(file) {
  if (!file || !String(file.type || '').startsWith('image/')) {
    throw new ContextImageError('Pick an image file (PNG, JPEG, GIF or WebP).', { code: 'not_image' })
  }
  if (file.size > MAX_SOURCE_BYTES) {
    throw new ContextImageError('That image is too large to import.', { code: 'too_large' })
  }

  const img = await loadBitmap(file)
  const { w, h } = fitted(img.naturalWidth, img.naturalHeight)
  if (!w || !h) {
    throw new ContextImageError("That file couldn't be read as an image.", { code: 'decode_failed' })
  }

  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')
  // JPEG has no alpha, so a transparent PNG would otherwise flatten onto black.
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, w, h)
  ctx.drawImage(img, 0, 0, w, h)

  const dataUrl = canvas.toDataURL('image/jpeg', QUALITY)
  return {
    id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: String(file.name || 'image').slice(0, 80),
    dataUrl,
    w,
    h,
    bytes: dataUrl.length,
  }
}

/** Images a paste or drop carried, in the order the browser reported them. */
export function imageFilesFrom(dataTransfer) {
  if (!dataTransfer) return []
  const items = Array.from(dataTransfer.items || [])
  const fromItems = items
    .filter((item) => item.kind === 'file' && String(item.type || '').startsWith('image/'))
    .map((item) => item.getAsFile())
    .filter(Boolean)
  if (fromItems.length) return fromItems
  return Array.from(dataTransfer.files || []).filter((file) => String(file.type || '').startsWith('image/'))
}
