// Pure helpers for the music library panel, kept out of the component so they
// can be tested without a DOM.

export const ACCEPT = [
  '.mid', '.midi', '.wav', '.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.aac', '.wma',
].join(',')

export const MAX_BYTES = 64 * 1024 * 1024

export function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export function kindLabel(kind) {
  return kind === 'midi' ? 'MIDI' : 'Audio'
}

export function extensionOf(filename) {
  const match = /(\.[^.]+)$/.exec(String(filename || '').toLowerCase())
  return match ? match[1] : ''
}

export function isAccepted(filename) {
  return ACCEPT.split(',').includes(extensionOf(filename))
}

// Rejects before the upload starts, so the user gets an instant reason instead
// of a 400 from the server.
export function validateFile(file, limit = MAX_BYTES) {
  if (!file) return 'no file selected'
  if (!isAccepted(file.name)) return `${file.name}: unsupported type`
  if (!file.size) return `${file.name}: the file is empty`
  if (file.size > limit) return `${file.name}: larger than ${formatBytes(limit)}`
  return null
}

// Sorted for display; unknown shapes are skipped rather than crashing the panel.
export function sortTracks(tracks) {
  return (Array.isArray(tracks) ? tracks : [])
    .filter((t) => t && typeof t === 'object' && t.id && t.name)
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }))
}

// "3 of 64 MB" style summary, plus how full the share is.
export function usage(tracks, limit = MAX_BYTES) {
  const list = Array.isArray(tracks) ? tracks : []
  const bytes = list.reduce((sum, t) => sum + (Number(t?.bytes) || 0), 0)
  return {
    count: list.length,
    bytes,
    label: `${formatBytes(bytes)} of ${formatBytes(limit)}`,
    share: limit > 0 ? Math.min(1, bytes / limit) : 0,
  }
}
