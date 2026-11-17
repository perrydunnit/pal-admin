import { useCallback, useEffect, useRef, useState } from 'react'
import { ACCEPT, formatBytes, kindLabel, sortTracks, usage, validateFile } from '../music.js'
import { apiUrl } from '../api.js'

// Uploads go to /api/music and land in the container's /data/music volume, next
// to the database, so they survive redeploys and are covered by the same backup.
export default function MusicPanel({ summary }) {
  const [tracks, setTracks] = useState([])
  const [limit, setLimit] = useState(64 * 1024 * 1024)
  const [error, setError] = useState(null)
  const [note, setNote] = useState(null)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/music'))
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setTracks(sortTracks(data.tracks))
      setLimit(Number(data.limit_bytes) || 64 * 1024 * 1024)
      setError(null)
    } catch (err) {
      setError(String(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  // The live summary carries the library size, so a second browser (or another
  // admin) sees uploads without reloading the page.
  const signature = `${summary?.music?.count ?? 0}:${summary?.music?.bytes ?? 0}`
  useEffect(() => {
    load()
  }, [signature, load])

  const upload = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || [])
      if (!files.length) return

      setBusy(true)
      setError(null)
      setNote(null)
      const added = []
      const skipped = []

      for (const file of files) {
        const problem = validateFile(file, limit)
        if (problem) {
          skipped.push(problem)
          continue
        }
        const body = new FormData()
        body.append('file', file)
        try {
          const res = await fetch(apiUrl('/api/music'), { method: 'POST', body })
          const data = await res.json().catch(() => ({}))
          if (!res.ok) {
            skipped.push(`${file.name}: ${data.error || `HTTP ${res.status}`}`)
            continue
          }
          if (data.duplicate) skipped.push(`${file.name}: already in the library`)
          else added.push(file.name)
        } catch (err) {
          skipped.push(`${file.name}: ${err}`)
        }
      }

      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
      if (added.length) setNote(`Added ${added.length} track${added.length === 1 ? '' : 's'}.`)
      if (skipped.length) setError(skipped.join(' · '))
      load()
    },
    [limit, load],
  )

  const remove = useCallback(
    async (track) => {
      if (!window.confirm(`Delete "${track.name}"? Clients using it will lose it.`)) return
      setError(null)
      try {
        const res = await fetch(apiUrl(`/api/music/${track.id}`), { method: 'DELETE' })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        setNote(`Deleted ${track.name}.`)
        load()
      } catch (err) {
        setError(String(err))
      }
    },
    [load],
  )

  const totals = usage(tracks, limit)

  return (
    <div className="panel x">
      <div className="music-bar">
        <label className={`btn${busy ? ' busy' : ''}`}>
          {busy ? 'Uploading…' : 'Upload audio'}
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPT}
            disabled={busy}
            onChange={(event) => upload(event.target.files)}
          />
        </label>
        <span className="muted">
          {totals.count} track{totals.count === 1 ? '' : 's'} · {totals.label}
        </span>
        <button type="button" className="btn ghost" onClick={load} disabled={busy}>
          Refresh
        </button>
      </div>

      {note && <div className="music-note">{note}</div>}
      {error && <div className="music-error">{error}</div>}

      {tracks.length === 0 ? (
        <div className="empty">
          No audio yet. Upload .mid, .mp3, .flac or .wav — the mod lists them at{' '}
          <code>/api/music?format=tsv</code>.
        </div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th className="num">Size</th>
              <th className="num">Added</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tracks.map((track) => (
              <tr key={track.id}>
                <td>
                  <a href={apiUrl(track.url)} download>
                    {track.name}
                  </a>
                  {track.source && track.source !== `${track.name}${track.ext}` && (
                    <div className="muted small">from {track.source}</div>
                  )}
                </td>
                <td className="muted">{kindLabel(track.kind)}</td>
                <td className="num muted">{formatBytes(track.bytes)}</td>
                <td className="num muted">{(track.uploaded || '').slice(0, 10)}</td>
                <td className="num">
                  <button type="button" className="btn danger" onClick={() => remove(track)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
