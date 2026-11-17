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
- **Restarts are announced, never forced through Docker.** The panel calls
  `POST /v1/api/shutdown`, the game saves and exits, and `restart: unless-stopped`
  brings the container back. `docker restart` is still forbidden: it can SIGKILL
  mid-save.
- **The LAN gate trusts the last `X-Forwarded-For` hop.** Traefik replaces the
  header with the peer it saw — verified on this host, where a forged
  `X-Forwarded-For: 192.168.0.99` came back as the real peer — so a spoofed
  header cannot move a caller into the LAN. The socket address is used directly
  when it is already local. Anything the host or the router itself sends arrives
  as `192.168.0.1` and therefore counts as local, which is the intended trust
  level. Check `tests/test_server_config.py` before changing the order.
- **Queued restarts are persisted in `app_state`, not memory.** A dashboard
  redeploy must not silently drop the request, and the collector is the only
  thing that fires it, so the queue survives as long as the database does.
- **`docker.sock` mounted read-only.** Needed to read logs. It also means the
  container can read other containers' environment, which is acceptable only
  because the app is behind basic auth on a single-user host. Do not add write
  scopes: restarts go through the game's own REST `shutdown`, not the Docker API.
- **One host directory is mounted read-write:** the game's settings directory, at
  `/palconfig`. It is the only file the dashboard writes outside `data/`, and it
  writes atomically (temp + rename) with a `.bak` of the previous version.
- **The game image rewrites `PalWorldSettings.ini` at every boot** unless
  `DISABLE_GENERATE_SETTINGS=true` is in the game stack's environment. The panel
  detects the missing flag through the Docker socket and shows a banner; if that
  banner ever appears, saved settings are on borrowed time.
- **`OptionSettings` is one line with no escape syntax.** A double quote or a
  newline in a free-text value corrupts every key after it, so validation
  rejects both before anything is written. Commas are fine inside the quotes.
- **Never write a relative `fetch()`/`EventSource` URL.** A page opened from a
  bookmark that embeds credentials (`https://admin:PASSWORD@host/`) keeps them
  in its base URL, and the browser then refuses with *"Request cannot be
  constructed from a URL that includes credentials"*. That is exactly how a
  phone bookmarks a basic-auth site, and it breaks every data panel while the
  HTML still loads — the page looks half-alive. Build requests with `apiUrl()`
  from `location.origin`, which never carries credentials.

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
- **No pal artwork either, only a directory to put it in.** `PAL_ICON_DIR` is
  served by `/api/pal-icon/<species>` and nothing is bundled or hotlinked; a
  missing file is a generated sigil (`emblemGrid` in `world.js`), which is the
  normal state. Keep it that way in the repo: an icon set is the operator's call,
  on their instance.
- **The config panel cannot create settings the game does not read.** Keys the
  image's template omits (`MaxBuildingLimitNumPerPlayer`, `FishingDifficultyRate`,
  `bAllowEnemyCampSpawnNearBaseCamp`, `bEnableFastTravelOnlyBaseCamp`) do appear
  in the game's own default ini and are written by the panel; they only take
  effect if the game build honours them.
- **Settings apply at the next start, and the panel cannot verify that they
  applied.** It compares the file with `/v1/api/settings` to show "pending", and
  clears that when the game reports the new value. A key the game ignores stays
  pending forever — that is a signal, not a bug.
- **No tests for the backend.** Frontend pure helpers are pinned by vitest;
  `split_by_day`, `_clip_session`, `range_payload`, `timeline_payload`,
  `map_point` and `project_world` are still checked by ad-hoc scripts only.
  They take plain data and are the obvious next targets.
-   **`playerId` is `"None"` while a character loads.** The API then reports the
  platform account as `name`, so anything keyed on that pair creates a phantom
  player (two timeline rows for one person). Identity resolution and
  `merge_identity()` handle it; never reintroduce a bare
  `pid = playerId or userId` fallback.

  **Player identity is resolved, not guessed.** `repair_identities()` merges a
  `<platform>_<account>` placeholder only when exactly one real id is known for
  that account. If such a row ever survives a restart, inspect
  `sessions.user_id` before merging by hand — it may be a recreated character.
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
