# Enhancement notes

Read before changing behaviour. Each item is a constraint discovered while
building this, not a suggestion.

## Things that will bite

**Single gunicorn worker.** `init_db()` and the collector + log-follower threads
start at import. Raising `--workers` above 1 gives every worker its own
collector: duplicate polls, duplicate `metrics` rows, and racy session opens. If
you need concurrency, scale with `--threads` only (each live SSE stream occupies
one).

**The collector assumes one writer.** `_db_lock` is per-process. Any second
process writing to `paladmin.db` must not rely on it. A separate ingestion script
is fine (SQLite serialises), but it must use the same `INSERT OR IGNORE INTO
events` guard, or history will double-count.

**Log timestamps are game-local.** `local_to_utc()` attaches this stack's `TZ`.
If the game stack's `TZ` changes and this one does not, every new session lands
hours off — silently, because the values still look plausible.

**"Left" events have no player id.** They are matched by display name against the
most recently seen player row. Nickname changes within a session can therefore
close the wrong session (the API reconciliation usually fixes it within 90 s).
Storing a name→id map keyed by time would be a real improvement.

**Recreating the game container resets its log.** Anything not yet ingested is
unrecoverable. Ingestion is now streaming, so the window is small, but a recreate
still drops lines the follower had not read.

**The log stream can lag a few seconds.** The game flushes stdout on its own
schedule; measured arrivals ranged from 18 ms to 4.1 s after the event. Do not
promise sub-second presence in the UI copy.

**Every SSE connection holds a gunicorn thread.** `--threads 8` in the Dockerfile
is the ceiling; `MAX_STREAMS` (4) keeps two threads free for ordinary requests.
Raising the thread count is safe, raising `--workers` is not.

**`--force-recreate`, not `restart`,** is required for `.env` changes; Compose does
not always diff env-file values on `up -d`.

**Line endings.** `.gitattributes` pins `eol=lf`, and this repo sets
`core.autocrlf=false`. Without that, Windows checkouts rewrite every line and
blob/disk comparisons diverge.

## Deliberate choices

- **React, built at image-build time.** The UI lives in `frontend/` and is
  compiled in a `node:22-alpine` stage; nothing from node ships at runtime. Keep
  it plain — no CSS framework, no router, no state library. `scripts/verify.sh`
  runs the unit tests and the production build, and `npm run dev` proxies `/api`
  to Flask for UI work.
- **`frontend/dist/` is generated.** It is gitignored and produced by Docker.
  Never edit or commit it. The Flask app serves it from `STATIC_DIR`
  (`/app/static` in the image).
- **Diffed refreshes.** Panes and calendar cells compare their JSON before
  rebuilding. Re-rendering wholesale causes visible flicker and loses scroll
  position — the original bug report. This is what keeps the push path safe:
  a frame that changes nothing touches no DOM.
- **Fractions, not pixels.** The API returns 0–1 positions; the client applies
  day width. Do not move pixel maths into the backend, or the month layout
  becomes fixed for all screens.
- **Sticky via CSS, not JS.** The left column is `position: sticky` inside a
  `width: max-content` inner wrapper. The wrapper width is essential — without it
  the sticky element has no travel room and scrolls away.
- **No per-day clipping in `/api/timeline`.** That is what makes a midnight
  crossing one bar; `/api/day` keeps the clipped view for other consumers.
- **`docker.sock` mounted read-only.** Needed to read logs. It also means the
  container can read other containers' environment, which is acceptable only
  because the app is behind basic auth on a single-user host. Do not add write
  scopes.

## Known gaps

- **No pruning.** `metrics` grows ~2,880 rows/day; `sessions` and `events` grow
  slowly. Add a retention job before this matters at multi-year scale.
- **No timezone in the DB.** Stored UTC, rendered in the browser's locale, so two
  admins in different zones see different "days". Add a preferred zone if that
  ever confuses anyone.
- **`trend` is fetched but unused** by the current UI — cheap, but dead until a
  sparkline exists.
- **`game-data` is unavailable** on this server until the game is restarted with
  `-EnableGameDataAPI`. That is the only source of guilds, base camps and pals.
- **The world panel has only been tested against fixtures.** The actor field
  names come from the documented schema plus tolerant aliases; the first real
  snapshot after enabling the flag is worth eyeballing.
- **No map artwork.** The Palpagos texture is Pocketpair's; the map is a
  schematic grid with correctly projected dots and an external link, by choice.
- **No tests for the backend.** Frontend pure helpers are pinned by vitest;
  `split_by_day`, `_clip_session`, `range_payload`, `timeline_payload`,
  `map_point` and `project_world` are still checked by ad-hoc scripts only.
  They take plain data and are the obvious next targets.
- **Player rename** creates a second `players` row if the REST API reports a new
  `playerId`; a rename in name only updates in place.
- **No auth inside the app.** All gating is Traefik basic auth. Running the
  container on a network without that proxy exposes it.
- **Session repair is best-effort.** A crash leaves a session open; the next poll
  closes it at poll time, not at the real moment the player left.

## Useful commands

```bash
# compile check (no runtime needed)
python -m py_compile app.py

# frontend: unit tests, production build, live-reload dev server
cd frontend && npm test && npm run build
cd frontend && npm run dev        # proxies /api to http://127.0.0.1:8000

# exercise the API against a copy of the DB
DB_PATH=/tmp/t.db python app.py

# everything the gate runs
scripts/verify.sh
```
