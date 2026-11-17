import { useCallback, useEffect, useMemo, useState } from 'react'
import { cancelRestart, fetchServerConfig, restartServer, saveServerConfig } from '../api.js'
import { buildChanges, currentValue, isDirty, matchesFilter } from '../config.js'
import { clock } from '../format.js'

function controlFor(row, current, onChange, disabled) {
  if (row.kind === 'bool' || row.kind === 'enum') {
    const choices = row.kind === 'bool' ? ['True', 'False'] : row.choices
    return (
      <select value={current} onChange={(e) => onChange(e.target.value)} disabled={disabled}>
        {!choices.some((c) => c.toLowerCase() === current.toLowerCase()) && (
          <option value={current}>{current}</option>
        )}
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </select>
    )
  }
  if (row.kind === 'int' || row.kind === 'float') {
    return (
      <input
        type="number"
        step={row.kind === 'int' ? '1' : 'any'}
        value={current}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }
  if (row.secret) {
    return (
      <input
        type="password"
        value={current}
        autoComplete="new-password"
        placeholder="unchanged"
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
    )
  }
  return (
    <input
      type="text"
      value={current}
      disabled={disabled}
      placeholder={row.choices.length ? row.choices.join(', ') : ''}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}

export default function ServerConfig() {
  const [data, setData] = useState(null)
  const [draft, setDraft] = useState({})
  const [cleared, setCleared] = useState(() => new Set())
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [filter, setFilter] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      setData(await fetchServerConfig())
      setError(null)
    } catch (err) {
      setError(String(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const rows = useMemo(
    () => (data?.groups ?? []).flatMap((group) => group.settings),
    [data],
  )
  const dirtyKeys = useMemo(
    () => new Set(rows.filter((row) => isDirty(row, draft, cleared)).map((row) => row.key)),
    [rows, draft, cleared],
  )
  const editable = Boolean(data?.editable)

  const edit = (row, value) => {
    setDraft((current) => ({ ...current, [row.key]: value }))
    if (row.secret && value !== '') {
      setCleared((current) => {
        if (!current.has(row.key)) return current
        const next = new Set(current)
        next.delete(row.key)
        return next
      })
    }
  }

  const clearSecret = (row) => {
    setDraft((current) => ({ ...current, [row.key]: '' }))
    setCleared((current) => new Set(current).add(row.key))
  }

  const discard = () => {
    setDraft({})
    setCleared(new Set())
    setNote(null)
  }

  const save = async () => {
    const changes = buildChanges(rows, draft, cleared)
    if (!Object.keys(changes).length) return
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      const payload = await saveServerConfig(changes)
      setData(payload)
      discard()
      setNote(
        `Saved ${payload.saved.length} setting${payload.saved.length === 1 ? '' : 's'} — ` +
          'they apply the next time the server starts.',
      )
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  const restart = async (mode) => {
    const warning =
      mode === 'now'
        ? 'Restart now? Anyone online is disconnected and the world saves first.'
        : 'Queue a restart for the next empty moment?'
    if (!window.confirm(warning)) return
    setBusy(true)
    setNote(null)
    setError(null)
    try {
      setData(await restartServer(mode))
      setNote(
        mode === 'now'
          ? 'Restart requested — the game saves, exits and comes straight back.'
          : 'Restart queued — it fires once the server has been empty for a few minutes.',
      )
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    setError(null)
    try {
      const payload = await cancelRestart()
      setData(payload)
      setNote(payload.cancelled ? 'Queued restart cancelled.' : 'Nothing was queued.')
    } catch (err) {
      setError(String(err))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (name) => {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  if (!data) {
    return (
      <div className="panel x">
        <div className="empty">{error ? `Could not load the config: ${error}` : 'Loading config…'}</div>
      </div>
    )
  }

  const needle = filter.trim().toLowerCase()
  const online = data.players_online
  const restartState = data.restart

  return (
    <div className="panel cfg">
      <div className="cfg-bar">
        <input
          type="search"
          className="cfg-filter"
          placeholder="Filter settings…"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <span className="muted small">
          {dirtyKeys.size ? `${dirtyKeys.size} unsaved` : `${rows.length} settings`}
        </span>
        <button type="button" className="btn ghost" onClick={load} disabled={busy}>
          Refresh
        </button>
        {editable && (
          <>
            <button
              type="button"
              className="btn"
              onClick={save}
              disabled={busy || dirtyKeys.size === 0}
            >
              Save changes
            </button>
            <button
              type="button"
              className="btn ghost"
              onClick={discard}
              disabled={busy || dirtyKeys.size === 0}
            >
              Discard
            </button>
          </>
        )}
      </div>

      {!editable && (
        <div className="cfg-note">
          Read-only: settings can only be changed from the local network ({data.lan}). This
          request came from <code>{data.client_ip || 'an unknown address'}</code>.
        </div>
      )}

      {data.available === false && (
        <div className="cfg-error">{data.warnings[0] || 'The settings file is not mounted.'}</div>
      )}
      {data.warnings.slice(data.available === false ? 1 : 0).map((warning) => (
        <div key={warning} className="cfg-error">
          {warning}
        </div>
      ))}

      <div className="cfg-restart">
        <div className="cfg-restart-main">
          <b>Restart the game server</b>
          <div className="muted small">
            {online === null
              ? 'Player count unavailable.'
              : online === 0
                ? 'Nobody is online right now.'
                : `${online} player${online === 1 ? '' : 's'} online.`}{' '}
            Saved settings only take effect after a restart. A queued restart waits for{' '}
            {Math.round(restartState.idle_seconds / 60)} minutes of an empty server.
          </div>
          {restartState.issued && (
            <div className="cfg-pill warn">
              Restart requested {clock(restartState.issued.at)} by {restartState.issued.by} (
              {restartState.issued.mode}) — waiting for the game to come back.
            </div>
          )}
          {!restartState.issued && restartState.queued && (
            <div className="cfg-pill">
              Queued {clock(restartState.queued.at)} by {restartState.queued.by}
              {restartState.empty_since
                ? ` — empty since ${clock(restartState.empty_since)}, restarting shortly.`
                : ' — waiting for the server to empty.'}
            </div>
          )}
          {restartState.error && <div className="cfg-error">Last attempt failed: {restartState.error}</div>}
        </div>
        <div className="cfg-restart-actions">
          <button
            type="button"
            className="btn"
            onClick={() => restart('queue')}
            disabled={!editable || busy || Boolean(restartState.issued || restartState.queued)}
          >
            Queue for empty
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => restart('now')}
            disabled={!editable || busy || Boolean(restartState.issued)}
          >
            Restart now
          </button>
          <button
            type="button"
            className="btn ghost"
            onClick={cancel}
            disabled={!editable || busy || Boolean(restartState.issued || !restartState.queued)}
          >
            Cancel queued
          </button>
        </div>
      </div>

      {note && <div className="cfg-note ok">{note}</div>}
      {error && <div className="cfg-error">{error}</div>}

      {data.groups.map((group) => {
        const visible = group.settings.filter((row) => matchesFilter(row, needle))
        if (!visible.length) return null
        const edited = visible.filter((row) => dirtyKeys.has(row.key)).length
        const closed = collapsed.has(group.name) && !needle
        return (
          <div className="cfg-group" key={group.name}>
            <button type="button" className="cfg-group-head" onClick={() => toggle(group.name)}>
              <span className="cfg-caret">{closed ? '▸' : '▾'}</span>
              <span>{group.name}</span>
              <span className="muted small">
                {visible.length} setting{visible.length === 1 ? '' : 's'}
                {edited ? ` · ${edited} edited` : ''}
              </span>
            </button>
            {!closed &&
              visible.map((row) => {
                const dirty = dirtyKeys.has(row.key)
                const current = currentValue(row, draft)
                const disabled = !editable || row.locked || busy
                return (
                  <div className={`cfg-row${dirty ? ' dirty' : ''}`} key={row.key}>
                    <div className="cfg-name">
                      <code>{row.key}</code>
                      {row.locked && <span className="pill">locked</span>}
                      {row.secret && row.is_set === false && <span className="pill">unset</span>}
                      <div className="muted small">{row.description}</div>
                      {row.locked && <div className="muted small">{data.locked_reason}</div>}
                      {row.warning && <div className="cfg-warn small">{row.warning}</div>}
                    </div>
                    <div className="cfg-input">
                      {controlFor(row, current, (value) => edit(row, value), disabled)}
                      {row.secret && (
                        <button
                          type="button"
                          className="btn ghost small"
                          onClick={() => clearSecret(row)}
                          disabled={disabled}
                          title="Write an empty value"
                        >
                          Clear
                        </button>
                      )}
                    </div>
                    <div className="cfg-meta small muted">
                      {row.secret ? (
                        cleared.has(row.key) ? (
                          <span className="cfg-warn">will be emptied</span>
                        ) : row.is_set ? (
                          'set — leave blank to keep'
                        ) : (
                          'not set'
                        )
                      ) : row.pending ? (
                        <span className="cfg-warn">live value {row.live} — applies at restart</span>
                      ) : row.is_default ? (
                        `default ${row.default}`
                      ) : (
                        'changed from default'
                      )}
                    </div>
                  </div>
                )
              })}
          </div>
        )
      })}
    </div>
  )
}
