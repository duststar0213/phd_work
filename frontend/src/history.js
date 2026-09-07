/**
 * Snapshot undo/redo for the canvas. A burst of related mutations
 * (one drag, one typing pause) collapses into a single step.
 */
const LIMIT = 80

function clone(state) {
  return JSON.parse(JSON.stringify(state))
}

export function createCanvasHistory(options = {}) {
  const undoStack = []
  const redoStack = []
  let baseline = null
  let pending = null
  let coalescing = false
  let applying = false
  let ready = false
  let timer = 0
  let pointerGesture = false

  function notify() {
    options.onChange?.(undoStack.length > 0, redoStack.length > 0)
  }

  function flush() {
    window.clearTimeout(timer)
    timer = 0
    if (pending) baseline = pending
    pending = null
    coalescing = false
  }

  function noteChange(state) {
    if (applying || !ready || !baseline) return
    if (!coalescing) {
      undoStack.push(clone(baseline))
      if (undoStack.length > LIMIT) undoStack.shift()
      redoStack.length = 0
      coalescing = true
      notify()
    }
    pending = state
    window.clearTimeout(timer)
    if (!pointerGesture) {
      timer = window.setTimeout(flush, 400)
    }
  }

  return {
    setReady(value) {
      ready = Boolean(value)
    },
    seed(state) {
      window.clearTimeout(timer)
      baseline = clone(state)
      pending = null
      coalescing = false
      undoStack.length = 0
      redoStack.length = 0
      applying = false
      notify()
    },
    noteChange,
    pointerDown() {
      pointerGesture = true
    },
    pointerUp() {
      pointerGesture = false
      flush()
      notify()
    },
    undo(getState, apply) {
      if (!undoStack.length || applying) return false
      window.clearTimeout(timer)
      timer = 0
      coalescing = false
      pending = null
      redoStack.push(clone(getState()))
      const snap = undoStack.pop()
      applying = true
      apply(clone(snap))
      baseline = clone(snap)
      applying = false
      notify()
      return true
    },
    redo(getState, apply) {
      if (!redoStack.length || applying) return false
      window.clearTimeout(timer)
      timer = 0
      coalescing = false
      pending = null
      undoStack.push(clone(getState()))
      const snap = redoStack.pop()
      applying = true
      apply(clone(snap))
      baseline = clone(snap)
      applying = false
      notify()
      return true
    },
    isApplying() {
      return applying
    },
  }
}
