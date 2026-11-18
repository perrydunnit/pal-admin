import { useEffect, useRef, useState } from 'react'

// A browser refuses to build a Request from a URL that carries credentials:
// "Request cannot be constructed from a URL that includes credentials". That is
// exactly what happens when the page was opened from a bookmark like
// https://admin:password@pal-admin.example.com/ — the document's base URL keeps
// the credentials, so every *relative* /api/... URL inherits them and every
// fetch() throws. `location.origin` never includes them, so requests are built
// from it explicitly. (Navigations, and therefore the HTML and its assets,
// still work either way, which is why the page loads and only the data does not.)
export function apiUrl(path, origin = typeof location === 'undefined' ? '' : location.origin) {
  return `${origin}${path}`
}

// Cleans the address bar once, so anything left relative in the document and
// anything the user copies from it is credential-free too.
export function forgetUrlCredentials() {
  if (typeof window === 'undefined' || !window.history?.replaceState) return
  const { origin, pathname, search, hash } = window.location
  const clean = `${origin}${pathname}${search}${hash}`
  if (window.location.href !== clean) window.history.replaceState(null, '', clean)
}

// One EventSource for the whole app. Every frame is a full /api/summary, so the
// components re-render from the same shape they get on first load. If the
// stream cannot be established the old 30 s fetch keeps the page current.
export function useLiveSummary() {
  const [summary, setSummary] = useState(null)
  const [live, setLive] = useState(false)
  const [tick, setTick] = useState(0)
  const [error, setError] = useState(null)
  const liveRef = useRef(false)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const res = await fetch(apiUrl('/api/summary'), { cache: 'no-store' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const data = await res.json()
        if (!cancelled) {
          setSummary(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(String(err))
      }
    }

    load()

    let source
    try {
      source = new EventSource(apiUrl('/api/events'))
      source.addEventListener('update', (event) => {
        liveRef.current = true
        setLive(true)
        try {
          setSummary(JSON.parse(event.data))
          setTick((n) => n + 1)
        } catch (err) {
          console.error('live update failed', err)
        }
      })
      source.onerror = () => {
        liveRef.current = false
        setLive(false)
      }
    } catch {
      liveRef.current = false
    }

    const timer = setInterval(() => {
      if (!liveRef.current) load()
    }, 30000)

    return () => {
      cancelled = true
      clearInterval(timer)
      source?.close()
    }
  }, [])

  return { summary, live, tick, error }
}

// A network-level failure (the request never got an answer) is worth one retry:
// mobile browsers drop the first request of a freshly woken connection often
// enough that a single failure says nothing about the server.
export async function fetchJSON(url, attempt = 0) {
  let res
  try {
    res = await fetch(apiUrl(url), { cache: 'no-store' })
  } catch (err) {
    if (attempt === 0) {
      await new Promise((resolve) => setTimeout(resolve, 400))
      return fetchJSON(url, 1)
    }
    throw new Error(`network error: ${err?.message || err}`)
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function sendJSON(url, method, body) {
  const res = await fetch(apiUrl(url), {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

export async function fetchServerConfig() {
  return fetchJSON('/api/server-config')
}

// Only reachable from the LAN; the server answers 403 with the reason otherwise.
export async function saveServerConfig(changes) {
  return sendJSON('/api/server-config', 'POST', { changes })
}

export async function restartServer(mode) {
  return sendJSON('/api/server-restart', 'POST', { mode })
}

export async function cancelRestart() {
  return sendJSON('/api/server-restart', 'DELETE')
}
