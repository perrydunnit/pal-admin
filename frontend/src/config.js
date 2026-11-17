// Pure helpers for the server config panel, kept out of the component so the
// rules for "what changed" can be tested without a DOM.

export const originalOf = (row) => (row.secret ? '' : row.value ?? row.default)

export function isDirty(row, draft, cleared) {
  if (row.secret) {
    // A secret is never sent to the browser, so blank means "leave it alone"
    // and emptying it has to be an explicit choice.
    if (cleared.has(row.key)) return true
    return (draft[row.key] ?? '') !== ''
  }
  return (draft[row.key] ?? originalOf(row)) !== originalOf(row)
}

// The payload the server expects: only rows the user actually touched.
export function buildChanges(rows, draft, cleared) {
  const changes = {}
  for (const row of rows) {
    if (!isDirty(row, draft, cleared)) continue
    changes[row.key] = row.secret && cleared.has(row.key) ? '' : draft[row.key]
  }
  return changes
}

export const currentValue = (row, draft) =>
  row.secret ? draft[row.key] ?? '' : draft[row.key] ?? originalOf(row)

export function matchesFilter(row, needle) {
  if (!needle) return true
  const text = needle.trim().toLowerCase()
  if (!text) return true
  return row.key.toLowerCase().includes(text) || row.description.toLowerCase().includes(text)
}
