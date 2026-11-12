import { useEffect, useRef, useState } from 'react'

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
        const res = await fetch('/api/summary')
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
      source = new EventSource('/api/events')
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

export async function fetchJSON(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function listMusic() {
  return fetchJSON('/api/music')
}

export async function uploadMusic(file) {
  const body = new FormData()
  body.append('file', file)
  const res = await fetch('/api/music', { method: 'POST', body })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`)
  return data
}

export async function deleteMusic(id) {
  const res = await fetch(`/api/music/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}
