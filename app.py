"""Palworld admin dashboard.

Collects player presence from two sources and persists it to SQLite:
  * the game REST API (/v1/api/players) for live state and levels
  * the container log for exact join/leave timestamps

Serves a single-page dashboard with player stats and a presence calendar.
"""

import calendar as calmod
import datetime as dt
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

import docker
import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

import palconfig

TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
PAL_API = os.environ.get("PAL_API", "http://palworld-server:8212").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PAL_CONTAINER = os.environ.get("PAL_CONTAINER", "palworld-server")
DB_PATH = os.environ.get("DB_PATH", "/data/paladmin.db")
STATIC_DIR = os.environ.get(
    "STATIC_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))
# Optional pal portraits, supplied by whoever runs this instance. Nothing is
# shipped with the repo: the game's art is Pocketpair's, so a missing file falls
# back to a coloured monogram. Named after the species, e.g. Anubis.png.
PAL_ICON_DIR = os.environ.get(
    "PAL_ICON_DIR", os.path.join(os.path.dirname(DB_PATH) or ".", "palicons"))
PAL_ICON_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))
MIN_POLL_GAP = float(os.environ.get("MIN_POLL_GAP", "2"))
INFO_REFRESH_SECONDS = int(os.environ.get("INFO_REFRESH_SECONDS", "1800"))
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "4"))
GAME_DATA_SECONDS = int(os.environ.get("GAME_DATA_SECONDS", "20"))
GAME_DATA_RETRY_SECONDS = int(os.environ.get("GAME_DATA_RETRY_SECONDS", "300"))

# The game's own settings file, mounted read-write from the game stack. Editing
# it is the only way to change a setting without recreating the container: the
# image writes it from .env once, at boot, so a plain restart keeps our values.
PAL_SETTINGS_FILE = os.environ.get("PAL_SETTINGS_FILE", "/palconfig/PalWorldSettings.ini")
# Only these addresses may change settings or restart the server. Everyone who
# can reach the page still sees the values.
LAN_CIDRS = os.environ.get("LAN_CIDRS", "192.168.0.0/24")
# A queued restart waits for this many seconds of continuous emptiness, so a
# player who drops for a moment cannot be caught mid-session.
RESTART_IDLE_SECONDS = int(os.environ.get("RESTART_IDLE_SECONDS", "300"))
# Countdown the game announces for a restart, in seconds.
RESTART_COUNTDOWN = int(os.environ.get("RESTART_COUNTDOWN", "30"))
DEFAULT_RESTART_MESSAGE = "Server restarting to apply new settings."

# Palpagos map calibration, from the community map pipeline (world units are
# Unreal centimetres). bounds = (maxX, maxY, minX, minY); the map axis is
# swapped: screen X follows world Y, screen Y follows world X.  The game-map
# read-out is a separate scale/offset.
MAP_BOUNDS = (349400.0, 724400.0, -1099400.0, -724400.0)
MAP_GAME_X_OFFSET = 158000.0
MAP_GAME_Y_OFFSET = 123888.0
MAP_GAME_SCALE = 459.0

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
_db_lock = threading.Lock()

# Bumped whenever stored state changes; SSE subscribers wait on it instead of
# re-reading the database on a timer.
_rev = 0
_rev_cond = threading.Condition()
# Set by the log follower so a join/leave is reflected by an API poll at once.
wake = threading.Event()
_streams = 0
_stream_lock = threading.Lock()
# Latest game-data projection; replaced wholesale by poll_world().
world = {"available": False, "reason": "unknown", "detail": "not probed yet",
         "checked": None, "ts": None, "counts": None,
         "players": [], "bases": [], "guilds": []}
_world_lock = threading.Lock()


def publish():
    """Tell connected browsers something changed."""
    global _rev
    with _rev_cond:
        _rev += 1
        _rev_cond.notify_all()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   TEXT NOT NULL,
    user_id     TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    source      TEXT NOT NULL DEFAULT 'poll'
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_open_session
    ON sessions(player_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_sessions_player ON sessions(player_id, started_at);

-- Advancement history. `players.level` only holds the last value seen while
-- online, so the curve of a player's progress has to be captured as it happens.
CREATE TABLE IF NOT EXISTS level_samples (
    player_id   TEXT NOT NULL,
    ts          TEXT NOT NULL,
    level       INTEGER NOT NULL,
    PRIMARY KEY (player_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_level_samples_player ON level_samples(player_id, ts);

CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,
    name        TEXT,
    platform    TEXT,
    level       INTEGER,
    first_seen  TEXT,
    last_seen   TEXT,
    last_ip     TEXT,
    account_name TEXT,
    ping        REAL,
    last_x      REAL,
    last_y      REAL,
    user_id     TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    ts          TEXT PRIMARY KEY,
    playernum   INTEGER,
    fps         REAL,
    days        INTEGER,
    basecamps   INTEGER,
    uptime      INTEGER,
    fps_avg     REAL,
    frametime   REAL,
    maxplayernum INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    user_id     TEXT,
    player_id   TEXT,
    PRIMARY KEY (ts, kind, name)
);

CREATE TABLE IF NOT EXISTS chat (
    ts          TEXT NOT NULL,
    name        TEXT NOT NULL,
    message     TEXT NOT NULL,
    PRIMARY KEY (ts, name, message)
);

CREATE TABLE IF NOT EXISTS server (
    key         TEXT PRIMARY KEY,
    value       TEXT
);

-- Admin actions that must survive a container restart, such as a queued
-- server restart. Values are JSON.
CREATE TABLE IF NOT EXISTS app_state (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
"""

# Added after the first release; created above for fresh databases, patched in
# for existing ones.
ADDED_COLUMNS = {
    "players": [("account_name", "TEXT"), ("ping", "REAL"), ("last_x", "REAL"),
                ("last_y", "REAL"), ("user_id", "TEXT")],
    "metrics": [("fps_avg", "REAL"), ("frametime", "REAL"), ("maxplayernum", "INTEGER")],
}


def _add_missing_columns(conn):
    for table, columns in ADDED_COLUMNS.items():
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    os.makedirs(PAL_ICON_DIR, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)
        # After the migration, so it works on a database created before user_id.
        conn.execute("CREATE INDEX IF NOT EXISTS ix_players_user ON players(user_id)")
        repair_identities(conn)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(ts):
    return ts.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_iso(s):
    return dt.datetime.fromisoformat(s)


def platform_of(user_id):
    if not user_id:
        return "unknown"
    return user_id.split("_", 1)[0]


# ---------------------------------------------------------------- collection

JOIN_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[LOG\] (.+?) joined the server\. "
    r"\(User id: ([^,]+), Player id: ([A-Za-z0-9]+)\)"
)
LEFT_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[LOG\] (.+?) left the server\."
)
CHAT_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\] \[CHAT\] <(.+?)> (.*)"
)


def local_to_utc(stamp):
    naive = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=TZ).astimezone(dt.timezone.utc)


def open_session_for(conn, player_id):
    return conn.execute(
        "SELECT * FROM sessions WHERE player_id = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (player_id,),
    ).fetchone()


PLATFORM_PREFIXES = ("steam_", "ps5_", "xbox_", "mac_")


def resolve_player_id(conn, player_id, user_id):
    """Prefer the real Palworld id, falling back to the platform account.

    `/v1/api/players` reports `playerId: "None"` for a few seconds while a
    player is still loading, and the `name` it reports then is the platform
    account rather than the character. Keying on the account creates a
    throwaway second player, so look for a row already known for that account
    before inventing one.
    """
    if player_id and player_id != "None":
        return player_id
    if not user_id:
        return None
    row = conn.execute(
        "SELECT player_id FROM players WHERE user_id = ? AND player_id != ?"
        " ORDER BY last_seen DESC LIMIT 1",
        (user_id, user_id),
    ).fetchone()
    return row["player_id"] if row else user_id


def merge_identity(conn, player_id, user_id):
    """Fold a `<platform>_<account>` placeholder row into the real player row."""
    if not player_id or not user_id or player_id == user_id:
        return False
    alias = conn.execute("SELECT * FROM players WHERE player_id = ?", (user_id,)).fetchone()
    alias_open = open_session_for(conn, user_id)
    if alias is None and alias_open is None:
        return False

    real_open = open_session_for(conn, player_id)
    if alias_open is not None:
        if real_open is None:
            conn.execute("UPDATE sessions SET player_id=? WHERE id=?", (player_id, alias_open["id"]))
        else:
            # The same connection was recorded twice; keep the earlier start.
            if parse_iso(alias_open["started_at"]) < parse_iso(real_open["started_at"]):
                conn.execute("UPDATE sessions SET started_at=? WHERE id=?",
                             (alias_open["started_at"], real_open["id"]))
            conn.execute("DELETE FROM sessions WHERE id=?", (alias_open["id"],))
    conn.execute("UPDATE sessions SET player_id=? WHERE player_id=?", (player_id, user_id))

    if alias is not None:
        current = conn.execute("SELECT first_seen FROM players WHERE player_id = ?",
                               (player_id,)).fetchone()
        if current is not None and alias["first_seen"] and (
                not current["first_seen"] or alias["first_seen"] < current["first_seen"]):
            conn.execute("UPDATE players SET first_seen=? WHERE player_id=?",
                         (alias["first_seen"], player_id))
        conn.execute("DELETE FROM players WHERE player_id=?", (user_id,))
    return True


def repair_identities(conn):
    """Merge placeholder rows left behind by earlier polling (runs at startup)."""
    aliases = [
        row["player_id"] for row in conn.execute("SELECT player_id FROM players")
        if row["player_id"].startswith(PLATFORM_PREFIXES)
    ]
    for alias in aliases:
        candidates = [row["player_id"] for row in conn.execute(
            "SELECT DISTINCT player_id FROM sessions WHERE user_id = ? AND player_id != ?",
            (alias, alias))]
        if len(candidates) != 1:
            continue  # unknown, or a genuine character recreation
        conn.execute("UPDATE players SET user_id=? WHERE player_id=?", (alias, candidates[0]))
        merge_identity(conn, candidates[0], alias)


def upsert_player(conn, player_id, name=None, platform=None, level=None,
                  seen=None, ip=None, account_name=None, ping=None, x=None, y=None,
                  user_id=None):
    row = conn.execute("SELECT player_id FROM players WHERE player_id = ?", (player_id,)).fetchone()
    seen_iso = iso(seen) if seen else None
    if row is None:
        conn.execute(
            "INSERT INTO players(player_id, name, platform, level, first_seen, last_seen,"
            " last_ip, account_name, ping, last_x, last_y, user_id)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (player_id, name, platform, level, seen_iso, seen_iso, ip,
             account_name, ping, x, y, user_id),
        )
        return
    conn.execute(
        "UPDATE players SET name=COALESCE(?, name), platform=COALESCE(?, platform),"
        " level=COALESCE(?, level), last_seen=COALESCE(?, last_seen),"
        " last_ip=COALESCE(?, last_ip), account_name=COALESCE(?, account_name),"
        " ping=COALESCE(?, ping), last_x=COALESCE(?, last_x), last_y=COALESCE(?, last_y),"
        " user_id=COALESCE(?, user_id)"
        " WHERE player_id=?",
        (name, platform, level, seen_iso, ip, account_name, ping, x, y, user_id, player_id),
    )


def record_level(conn, player_id, level, now, session_start=None):
    """Store a level observation, but only when it adds information.

    Rows are written when the level changed, or when the current session has no
    observation yet (which brackets the session start). Polling every 30 s would
    otherwise add ~2,880 identical rows per player per day.
    """
    if level is None:
        return False
    try:
        level = int(level)
    except (TypeError, ValueError):
        return False
    if isinstance(session_start, str):
        session_start = parse_iso(session_start)
    last = conn.execute(
        "SELECT ts, level FROM level_samples WHERE player_id=? ORDER BY ts DESC LIMIT 1",
        (player_id,),
    ).fetchone()
    if last is not None and int(last["level"]) == level:
        if session_start is None or parse_iso(last["ts"]) >= session_start:
            return False
    cur = conn.execute(
        "INSERT OR IGNORE INTO level_samples(player_id, ts, level) VALUES(?,?,?)",
        (player_id, iso(now), level),
    )
    return cur.rowcount > 0


def handle_join(conn, ts, name, user_id, player_id):
    # A poll may already have recorded this connection under the account id.
    merge_identity(conn, player_id, user_id)
    open_row = open_session_for(conn, player_id)
    upsert_player(conn, player_id, name=name, platform=platform_of(user_id), seen=ts,
                  user_id=user_id)
    if open_row is not None:
        delta = abs((parse_iso(open_row["started_at"]) - ts).total_seconds())
        if delta <= 180:
            conn.execute(
                "UPDATE sessions SET started_at=?, source='log', user_id=? WHERE id=?",
                (iso(ts), user_id, open_row["id"]),
            )
            return
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (iso(ts), open_row["id"]))
    conn.execute(
        "INSERT INTO sessions(player_id, user_id, started_at, ended_at, source)"
        " VALUES(?,?,?,NULL,'log')",
        (player_id, user_id, iso(ts)),
    )


def handle_left(conn, ts, name):
    row = conn.execute(
        "SELECT player_id FROM players WHERE name = ? ORDER BY last_seen DESC LIMIT 1",
        (name,),
    ).fetchone()
    if row is None:
        return
    open_row = open_session_for(conn, row["player_id"])
    if open_row is not None:
        conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (iso(ts), open_row["id"]))


def apply_log_line(conn, line):
    """Apply one log line. Returns "presence", "chat" or None."""
    chat = CHAT_RE.search(line)
    if chat:
        ts = local_to_utc(chat.group(1))
        cur = conn.execute(
            "INSERT OR IGNORE INTO chat(ts, name, message) VALUES(?,?,?)",
            (iso(ts), chat.group(2), chat.group(3)),
        )
        return "chat" if cur.rowcount else None

    m = JOIN_RE.search(line)
    if m:
        ts = local_to_utc(m.group(1))
        cur = conn.execute(
            "INSERT OR IGNORE INTO events(ts, kind, name, user_id, player_id)"
            " VALUES(?,?,?,?,?)",
            (iso(ts), "join", m.group(2), m.group(3), m.group(4)),
        )
        if cur.rowcount == 0:
            return None  # already applied on an earlier pass
        handle_join(conn, ts, m.group(2), m.group(3), m.group(4))
        return "presence"

    m = LEFT_RE.search(line)
    if m:
        ts = local_to_utc(m.group(1))
        cur = conn.execute(
            "INSERT OR IGNORE INTO events(ts, kind, name, user_id, player_id)"
            " VALUES(?,?,?,?,?)",
            (iso(ts), "left", m.group(2), None, None),
        )
        if cur.rowcount == 0:
            return None
        handle_left(conn, ts, m.group(2))
        return "presence"
    return None


def apply_lines(raw_lines):
    """Ingest a batch of log lines, then nudge the poller and the browsers."""
    presence = dirty = False
    with _db_lock, db() as conn:
        for raw in raw_lines:
            line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
            kind = apply_log_line(conn, line)
            if kind == "presence":
                presence = dirty = True
            elif kind == "chat":
                dirty = True
    if presence:
        wake.set()  # a join/leave changes the roster; reconcile now, not on the tick
    if dirty:
        publish()


def follow_logs():
    """Stream the game log so joins, leaves and chat arrive as they happen.

    `since` replays whatever happened while this container was down before
    switching to live lines, and the event table's primary key makes the replay
    idempotent. A stopped or recreated game container ends the stream; the loop
    reconnects and picks up from the newest stored event.
    """
    backoff = 5
    while True:
        try:
            container = docker.from_env().containers.get(PAL_CONTAINER)
            with db() as conn:
                last = conn.execute("SELECT MAX(ts) AS m FROM events").fetchone()["m"]
            since = 1
            if last:
                since = max(1, int(parse_iso(last).timestamp()) - 120)
            buf = b""
            for chunk in container.logs(stream=True, follow=True, since=since,
                                        timestamps=False):
                buf += chunk
                parts = buf.split(b"\n")
                buf = parts.pop()
                if parts:
                    apply_lines(parts)
            if buf:
                apply_lines([buf])
            backoff = 5
        except Exception as exc:
            app.logger.warning("log stream interrupted: %s", exc)
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)  # a stopped game must not flood the log


def fetch_server_info(now):
    """Static server identity, refreshed rarely — it only changes on an update."""
    with db() as conn:
        row = conn.execute("SELECT value FROM server WHERE key='fetched_at'").fetchone()
    if row and now.timestamp() - float(row["value"]) < INFO_REFRESH_SECONDS:
        return None
    try:
        info = requests.get(
            f"{PAL_API}/v1/api/info",
            auth=(ADMIN_USER, ADMIN_PASSWORD),
            timeout=10,
        ).json()
    except Exception as exc:
        app.logger.warning("server info fetch failed: %s", exc)
        return None
    return [(key, str(info.get(key) or "")) for key in
            ("version", "servername", "description", "worldguid")] + [
        ("fetched_at", str(now.timestamp()))
    ]


def poll_api():
    """Reconcile presence with the live REST API and record metrics."""
    try:
        players = requests.get(
            f"{PAL_API}/v1/api/players",
            auth=(ADMIN_USER, ADMIN_PASSWORD),
            timeout=10,
        ).json().get("players", [])
        metrics = requests.get(
            f"{PAL_API}/v1/api/metrics",
            auth=(ADMIN_USER, ADMIN_PASSWORD),
            timeout=10,
        ).json()
    except Exception as exc:
        app.logger.warning("api poll failed: %s", exc)
        return

    now = now_utc()
    info_rows = fetch_server_info(now)
    online = set()
    with _db_lock, db() as conn:
        if info_rows:
            conn.executemany("INSERT OR REPLACE INTO server(key, value) VALUES(?,?)", info_rows)
        for p in players:
            raw_id = p.get("playerId")
            user_id = p.get("userId")
            pid = resolve_player_id(conn, raw_id, user_id)
            if not pid:
                continue
            if raw_id and raw_id != "None":
                merge_identity(conn, pid, user_id)
            online.add(pid)
            upsert_player(
                conn, pid,
                name=p.get("name"),
                platform=platform_of(user_id),
                level=p.get("level"),
                seen=now,
                ip=p.get("iP"),
                account_name=p.get("accountName"),
                ping=p.get("ping"),
                x=p.get("location_x"),
                y=p.get("location_y"),
                user_id=user_id,
            )
            open_row = open_session_for(conn, pid)
            if open_row is None:
                session_start = now
                conn.execute(
                    "INSERT INTO sessions(player_id, user_id, started_at, ended_at, source)"
                    " VALUES(?,?,?,NULL,'poll')",
                    (pid, user_id, iso(now)),
                )
            else:
                session_start = parse_iso(open_row["started_at"])
            record_level(conn, pid, p.get("level"), now, session_start)

        for row in conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall():
            if row["player_id"] in online:
                continue
            if (now - parse_iso(row["started_at"])).total_seconds() < 90:
                continue
            conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (iso(now), row["id"]))

        conn.execute(
            "INSERT OR REPLACE INTO metrics(ts, playernum, fps, fps_avg, frametime, days,"
            " basecamps, maxplayernum, uptime) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                iso(now),
                metrics.get("currentplayernum"),
                metrics.get("serverfpsaverage") or metrics.get("serverfps"),
                metrics.get("serverfpsaverage"),
                metrics.get("serverframetime"),
                metrics.get("days"),
                metrics.get("basecampnum"),
                metrics.get("maxplayernum"),
                metrics.get("uptime"),
            ),
        )
    publish()
    try:
        check_restart_queue(metrics.get("currentplayernum"), metrics.get("uptime"))
    except Exception as exc:
        app.logger.exception("restart watch failed: %s", exc)


def collector():
    time.sleep(2)
    while True:
        started = time.monotonic()
        try:
            poll_api()
        except Exception as exc:
            app.logger.exception("collector iteration failed: %s", exc)
        # The heartbeat keeps trends and world counters moving; a log event
        # shortens the wait so a join shows up in about a second.
        if wake.wait(POLL_SECONDS):
            wake.clear()
            time.sleep(max(0.0, MIN_POLL_GAP - (time.monotonic() - started)))


# ------------------------------------------------------------------ game data

def map_point(x, y):
    """Unreal world units → normalised map position and the in-game map read-out."""
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return None
    max_x, max_y, min_x, min_y = MAP_BOUNDS
    if not (min_x <= x <= max_x and min_y <= y <= max_y):
        return None
    nx = (y - min_y) / (max_y - min_y)
    ny = (max_x - x) / (max_x - min_x)
    # The player-facing map read-out swaps the axes, offsets the origin in world
    # units and scales down to the values the game shows.
    return {
        "px": round(nx, 4),
        "py": round(ny, 4),
        "map_x": round((y - MAP_GAME_X_OFFSET) / MAP_GAME_SCALE),
        "map_y": round((x + MAP_GAME_Y_OFFSET) / MAP_GAME_SCALE),
    }


def _field(actor, *names, default=None):
    for name in names:
        if name in actor and actor[name] not in (None, ""):
            return actor[name]
    return default


def _split_id(value):
    """Game-data ids are '<playerId> : <runtimeActorId>' — keep the stable half."""
    return str(value or "").split(" : ")[0].strip()


# Boss and variant blueprints share the base species' artwork.
_PAL_VARIANT_RE = re.compile(r"_(MiddleBoss|Boss|BOSS|MAX|Max|Tower|RaidBoss|Raid)$")


def pal_species(class_name, display_name=""):
    """`BP_Anubis_MiddleBoss_C` and `Anubis` name the same pal.

    Used as the icon key, so a single `Anubis.png` covers the boss, the alpha
    and every tamed one.
    """
    raw = str(class_name or "").strip().rsplit("/", 1)[-1]
    if raw:
        raw = re.sub(r"_(C|02)$", "", raw)
        raw = re.sub(r"^(BP|SK)_", "", raw)
        raw = _PAL_VARIANT_RE.sub("", raw)
        if raw:
            return raw
    return re.sub(r"[^A-Za-z0-9]", "", str(display_name or ""))


_icon_cache = {"at": 0.0, "icons": {}}

# The game ships one portrait per pal as /Game/Pal/Texture/PalIcon/Normal/
# T_<Species>_icon_normal, with alternates under SKin/. An export of that folder
# is usable as-is.
_ICON_STEM_RE = re.compile(r"^T_(?P<species>.+?)(?:_skin\d+)?_icon(?:_normal)?$", re.I)


def icon_species(stem):
    """`T_Anubis_icon_normal`, `T_Anubis_Skin001_icon_normal` and `Anubis` all mean Anubis."""
    match = _ICON_STEM_RE.match(stem or "")
    if match:
        return match.group("species")
    return str(stem or "")


def pal_icons():
    """{lowercased species: filename} for the portraits this instance has."""
    with _config_lock:
        if time.monotonic() - _icon_cache["at"] < 30:
            return _icon_cache["icons"]
    found = {}
    try:
        for name in sorted(os.listdir(PAL_ICON_DIR)):
            stem, ext = os.path.splitext(name)
            if not stem or ext.lower() not in PAL_ICON_TYPES:
                continue
            key = icon_species(stem).lower()
            if not key:
                continue
            # The plain portrait wins over a skin/alternate of the same pal.
            current = found.get(key)
            if current is None or ("_skin" in current.lower() and "_skin" not in name.lower()):
                found[key] = name
    except OSError:
        pass
    with _config_lock:
        _icon_cache.update(at=time.monotonic(), icons=found)
    return found


def _base_name(raw):
    """Bases are reported with the game's untranslated template placeholder."""
    name = str(raw or "").strip()
    if not name:
        return "Unnamed base"
    match = re.search(r"テンプレート名(\d+)", name)
    if match:
        return f"Unnamed base {int(match.group(1)) + 1}"
    return name


def _nearest_base(pal, bases):
    """Base workers carry no trainer/base id, so use position within the guild."""
    if not bases:
        return None
    if len(bases) == 1:
        return bases[0]
    if pal.get("px") is None or pal.get("py") is None:
        return None
    best, best_distance = None, None
    for base in bases:
        if base.get("px") is None or base.get("py") is None:
            continue
        distance = (base["px"] - pal["px"]) ** 2 + (base["py"] - pal["py"]) ** 2
        if best_distance is None or distance < best_distance:
            best, best_distance = base, distance
    return best


def _number(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def _unit(actor):
    """Name/level/health/position for one actor, whatever casing the API uses."""
    position = map_point(_field(actor, "LocationX", "location_x", "X", "x"),
                         _field(actor, "LocationY", "location_y", "Y", "y"))
    name = str(_field(actor, "NickName", "nickname", "Nickname", "Name", default=""))
    item = {
        "id": _split_id(_field(actor, "InstanceID", "instance_id", "instanceId", default=""))
              or str(_field(actor, "userid", "userId", default="")),
        "runtime_id": str(_field(actor, "InstanceID", "instance_id", "instanceId", default="")),
        "name": name,
        "species": pal_species(_field(actor, "Class", "class", default=""), name),
        "level": _number(_field(actor, "level", "Level")),
        "hp": _number(_field(actor, "HP", "hp")),
        "max_hp": _number(_field(actor, "MaxHP", "max_hp", "MaxHp")),
        "active": str(_field(actor, "IsActive", "is_active", default="")).lower() == "true",
        "guild_id": str(_field(actor, "GuildID", "guild_id", default="")),
        "guild": str(_field(actor, "GuildName", "guild_name", default="")),
        "trainer_id": str(_field(actor, "TrainerInstanceID", "trainer_instance_id", default="")),
        "trainer": str(_field(actor, "TrainerNickName", "trainer_nickname", default="")),
        "class": str(_field(actor, "Class", "class", default="")),
        "action": str(_field(actor, "Action", "action", default="")),
    }
    if position:
        item.update(position)
    return item


def project_world(actors, now):
    """Reduce an actor snapshot to the dashboard's world payload."""
    by_id = {}
    palboxes, party, workers, loose_workers = [], {}, {}, []
    counts = {"players": 0, "party_pals": 0, "base_pals": 0, "wild_pals": 0,
              "npcs": 0, "bases": 0, "other": 0}

    for actor in actors:
        if not isinstance(actor, dict):
            continue
        item = _unit(actor)
        unit = str(_field(actor, "UnitType", "unit_type", "unitType", default="")).lower()
        kind = str(_field(actor, "Type", "type", default="")).lower()
        if kind == "palbox":
            counts["bases"] += 1
            raw = item["name"]
            item["raw_name"] = raw
            item["name"] = _base_name(raw)
            item["id"] = f"{item['guild_id']}:{raw}"
            palboxes.append(item)
            continue
        if unit == "player":
            counts["players"] += 1
            by_id.setdefault(item["id"], item)
            continue
        if unit == "otomopal":
            counts["party_pals"] += 1
            party.setdefault(_split_id(item["trainer_id"]), []).append(item)
        elif unit == "basecamppal":
            counts["base_pals"] += 1
            trainer = _split_id(item["trainer_id"])
            if trainer:
                workers.setdefault(trainer, []).append(item)
            else:
                loose_workers.append(item)
        elif unit == "wildpal":
            counts["wild_pals"] += 1
        elif unit == "npc":
            counts["npcs"] += 1
        else:
            counts["other"] += 1

    players = sorted(by_id.values(), key=lambda p: (p["name"] or p["id"]).lower())
    for player in players:
        player["party"] = sorted(party.pop(player["id"], []), key=lambda p: p["name"].lower())

    bases = sorted(palboxes, key=lambda b: (b["name"], b["id"]))
    for base in bases:
        base["workers"] = sorted(workers.pop(base["id"], []), key=lambda p: p["name"].lower())

    unattributed = sum(len(v) for v in party.values()) + sum(len(v) for v in workers.values())
    for pal in loose_workers:
        candidates = [b for b in bases if not pal["guild_id"] or b["guild_id"] == pal["guild_id"]]
        base = _nearest_base(pal, candidates or bases)
        if base is None:
            unattributed += 1
        else:
            base["workers"].append(pal)
    for base in bases:
        base["workers"].sort(key=lambda p: p["name"].lower())

    guilds = {}
    for actor in players + bases:
        key = actor["guild_id"] or actor["guild"] or "unknown"
        entry = guilds.setdefault(key, {
            "id": actor["guild_id"], "name": actor["guild"] or "Unknown guild",
            "players": 0, "bases": 0, "pals": 0,
        })
        if actor.get("party") is not None:
            entry["players"] += 1
            entry["pals"] += len(actor["party"])
        else:
            entry["bases"] += 1
            entry["pals"] += len(actor["workers"])

    return {
        "available": True,
        "reason": None,
        "detail": None,
        "checked": iso(now),
        "ts": iso(now),
        "counts": counts,
        "players": players,
        "bases": bases,
        "guilds": sorted(guilds.values(), key=lambda g: (-g["players"], g["name"].lower())),
        "unattributed_pals": unattributed,
    }


def _world_unavailable(reason, detail, now):
    return {
        "available": False,
        "reason": reason,
        "detail": detail,
        "checked": iso(now),
        "ts": None,
        "counts": None,
        "players": [],
        "bases": [],
        "guilds": [],
        "unattributed_pals": 0,
    }


def poll_world():
    """Fetch /v1/api/game-data. Returns True when the snapshot was usable."""
    global world
    now = now_utc()
    try:
        resp = requests.get(
            f"{PAL_API}/v1/api/game-data",
            auth=(ADMIN_USER, ADMIN_PASSWORD),
            timeout=20,
        )
    except Exception as exc:
        snapshot = _world_unavailable("error", str(exc)[:200], now)
    else:
        if resp.status_code == 404:
            snapshot = _world_unavailable(
                "disabled", (resp.text or "game-data endpoint not available").strip()[:200], now)
        elif resp.status_code != 200:
            snapshot = _world_unavailable("error", f"HTTP {resp.status_code}", now)
        else:
            try:
                payload = resp.json()
                actors = payload if isinstance(payload, list) else next(
                    (v for v in payload.values() if isinstance(v, list)), [])
                snapshot = project_world(actors, now)
                if isinstance(payload, dict):
                    snapshot["time"] = _field(payload, "InGameTime", "Time", "time")
                    snapshot["days"] = _number(_field(payload, "InGameDays", "days"))
                    snapshot["fps"] = _number(_field(payload, "FPS", "fps"))
                    snapshot["fps_avg"] = _number(_field(payload, "AverageFPS", "average_fps"))
            except Exception as exc:
                snapshot = _world_unavailable("error", f"unparsable snapshot: {exc}"[:200], now)

    with _world_lock:
        changed = json.dumps(snapshot, sort_keys=True) != json.dumps(world, sort_keys=True)
        world = snapshot
    if changed:
        publish()
    return snapshot["available"]


def world_loop():
    time.sleep(3)
    while True:
        try:
            ok = poll_world()
        except Exception as exc:
            app.logger.exception("world poll failed: %s", exc)
            ok = False
        time.sleep(GAME_DATA_SECONDS if ok else GAME_DATA_RETRY_SECONDS)


# -------------------------------------------------------------- server config

_config_lock = threading.Lock()
_generation_cache = {"at": 0.0, "value": None}


def _lan_networks():
    networks = []
    for cidr in re.split(r"[,\s]+", LAN_CIDRS.strip()):
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            app.logger.warning("ignoring unusable LAN_CIDRS entry: %s", cidr)
    return networks


def _valid_ip(text):
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _ip_inside(text, networks):
    if not _valid_ip(text):
        return False
    address = ipaddress.ip_address(text)
    return any(address in network for network in networks)


def resolve_client_ip(remote, forwarded, networks):
    """The address a request really came from.

    Behind Traefik `remote_addr` is the proxy on the Docker network, so the
    client is in `X-Forwarded-For`. The *last* hop is the one our own proxy
    appended, so a header a caller forged can never put them inside the LAN.
    """
    remote = (remote or "").strip()
    if _ip_inside(remote, networks):
        return remote
    hops = [hop.strip() for hop in (forwarded or "").split(",") if hop.strip()]
    for hop in reversed(hops):
        if _valid_ip(hop):
            return hop
    return remote


def client_context():
    """(client ip, may this client change the server)."""
    networks = _lan_networks()
    ip = resolve_client_ip(
        request.remote_addr, request.headers.get("X-Forwarded-For", ""), networks)
    return ip, _ip_inside(ip, networks)


def _read_settings_text():
    try:
        with open(PAL_SETTINGS_FILE, encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _write_settings(changes):
    """Apply validated tokens to the ini, keeping a copy of the previous file."""
    text = _read_settings_text()
    values = palconfig.parse(text) if text is not None else dict(palconfig.DEFAULTS)
    values.update(changes)
    body = palconfig.render(values)
    directory = os.path.dirname(PAL_SETTINGS_FILE) or "."
    # Never create it: a missing directory means the game stack is not mounted,
    # and writing into the container's own filesystem would look like success.
    if not os.path.isdir(directory):
        raise OSError(f"{directory} is not mounted")
    temporary = PAL_SETTINGS_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    os.chmod(temporary, 0o644)
    if text is not None:
        shutil.copy2(PAL_SETTINGS_FILE, PAL_SETTINGS_FILE + ".bak")
    os.replace(temporary, PAL_SETTINGS_FILE)
    return values


def _live_settings():
    """Settings the running game is using, or None when it cannot be reached."""
    try:
        data = requests.get(
            f"{PAL_API}/v1/api/settings", auth=(ADMIN_USER, ADMIN_PASSWORD), timeout=5).json()
    except Exception as exc:
        app.logger.info("live settings unavailable: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    # The API reports this one with a lower-case first letter.
    if "autoSaveSpan" in data and "AutoSaveSpan" not in data:
        data["AutoSaveSpan"] = data["autoSaveSpan"]
    return data


def _live_token(entry, value):
    """A live JSON value in the same token form the ini stores."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return f"{float(value):.6f}" if entry["kind"] == "float" else str(value)
    if isinstance(value, (list, tuple)):
        items = ",".join(str(item) for item in value)
        return f"({items})" if entry.get("tuple") else f'"{items}"'
    if entry["kind"] in ("enum", "raw"):
        return str(value)
    return f'"{value}"'


def _regenerated_from_env():
    """True when the image would overwrite our edits at the next boot."""
    with _config_lock:
        if time.monotonic() - _generation_cache["at"] < 60:
            return _generation_cache["value"]
    value = None
    try:
        container = docker.from_env().containers.get(PAL_CONTAINER)
        env = container.attrs.get("Config", {}).get("Env") or []
        flag = next((item.split("=", 1)[1] for item in env
                     if item.upper().startswith("DISABLE_GENERATE_SETTINGS=")), "")
        value = flag.strip().lower() != "true"
    except Exception as exc:
        app.logger.info("cannot read the game container environment: %s", exc)
    with _config_lock:
        _generation_cache.update(at=time.monotonic(), value=value)
    return value


def _state(conn, key):
    row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    if not row or row["value"] is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return None


def _set_state(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT OR REPLACE INTO app_state(key, value) VALUES(?, ?)", (key, json.dumps(value)))


def restart_payload():
    with db() as conn:
        queued = _state(conn, "restart_queue")
        issued = _state(conn, "restart_issued")
        empty_since = _state(conn, "restart_empty_since")
        error = _state(conn, "restart_error")
    return {
        "queued": queued,
        "issued": issued,
        "empty_since": empty_since,
        "error": error,
        "idle_seconds": RESTART_IDLE_SECONDS,
        "countdown": RESTART_COUNTDOWN,
    }


def config_payload():
    """Everything the settings panel needs, including why it may be read-only."""
    ip, lan = client_context()
    text = _read_settings_text()
    available = text is not None
    values = palconfig.parse(text) if available else {}
    known = palconfig.by_key()
    catalog = palconfig.catalog([key for key in values if key not in known])
    live = _live_settings() if available else None
    directory = os.path.dirname(PAL_SETTINGS_FILE) or "."
    writable = available and (
        os.access(PAL_SETTINGS_FILE, os.W_OK) or os.access(directory, os.W_OK))
    regenerated = _regenerated_from_env()

    warnings = []
    if not available:
        warnings.append(
            f"{PAL_SETTINGS_FILE} is not mounted, so only the game's defaults are shown.")
    elif not writable:
        warnings.append("The settings file is not writable from this container.")
    if regenerated:
        warnings.append(
            "The game image rewrites PalWorldSettings.ini from the stack's .env at every "
            "boot (DISABLE_GENERATE_SETTINGS is not true), so saved values would be lost "
            "when the container is recreated.")

    rows = []
    for entry in catalog:
        token = values.get(entry["key"])
        secret = bool(entry.get("secret"))
        live_token = _live_token(entry, live.get(entry["key"])) if live else None
        rows.append({
            "key": entry["key"],
            "group": entry["group"],
            "kind": entry["kind"],
            "description": entry["description"],
            "choices": list(entry.get("choices") or []),
            "tuple": bool(entry.get("tuple")),
            "locked": bool(entry.get("locked")),
            "warning": entry.get("warning"),
            "secret": secret,
            "default": palconfig.friendly(entry, entry.get("default")),
            "value": None if secret else (
                palconfig.friendly(entry, token) if token is not None else None),
            "is_set": bool(palconfig.friendly(entry, token)) if secret else None,
            "is_default": token is None or palconfig.same_value(
                entry, token, entry.get("default")),
            "live": None if secret or live_token is None else palconfig.friendly(
                entry, live_token),
            "pending": bool(
                token is not None and live_token is not None
                and not palconfig.same_value(entry, token, live_token)),
        })

    with db() as conn:
        metrics_row = conn.execute(
            "SELECT playernum, ts FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
    restart = restart_payload()

    return {
        "available": available,
        "writable": writable,
        "editable": available and writable and lan,
        "client_ip": ip,
        "lan": LAN_CIDRS,
        "file": PAL_SETTINGS_FILE,
        "warnings": warnings,
        "groups": [
            {"name": name, "settings": [row for row in rows if row["group"] == name]}
            for name in palconfig.GROUPS
            if any(row["group"] == name for row in rows)
        ],
        "restart": restart,
        "players_online": metrics_row["playernum"] if metrics_row else None,
        "players_at": metrics_row["ts"] if metrics_row else None,
        "locked_reason": palconfig.LOCKED_REASON,
    }


def perform_restart(message, countdown, by, mode, uptime=None):
    """Ask the game to shut down cleanly; Docker's restart policy brings it back."""
    auth = (ADMIN_USER, ADMIN_PASSWORD)
    if uptime is None:
        with db() as conn:
            row = conn.execute("SELECT uptime FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
            uptime = row["uptime"] if row else None
    try:
        requests.post(f"{PAL_API}/v1/api/announce", auth=auth,
                      json={"message": message}, timeout=10)
    except Exception as exc:
        app.logger.warning("restart announcement failed: %s", exc)
    response = requests.post(
        f"{PAL_API}/v1/api/shutdown", auth=auth,
        json={"waittime": max(1, int(countdown)), "message": message}, timeout=15)
    response.raise_for_status()
    with _db_lock, db() as conn:
        _set_state(conn, "restart_issued", {
            "at": iso(now_utc()), "by": by, "mode": mode,
            "message": message, "uptime": uptime,
        })
        _set_state(conn, "restart_queue", None)
        _set_state(conn, "restart_empty_since", None)
        _set_state(conn, "restart_error", None)
    publish()
    app.logger.info("restart requested by %s (%s): %s", by, mode, message)


def check_restart_queue(playernum, uptime):
    """Fire a queued restart once the server has been empty long enough."""
    now = now_utc()
    act = None
    with _db_lock, db() as conn:
        issued = _state(conn, "restart_issued")
        if issued:
            # A restarted game reports less uptime than the time that has passed
            # since the request, whatever the box was up to beforehand.
            elapsed = (now - parse_iso(issued["at"])).total_seconds()
            started_again = uptime is not None and uptime + 30 < elapsed
            if started_again or elapsed > 900:
                _set_state(conn, "restart_issued", None)
                publish()
            return
        queued = _state(conn, "restart_queue")
        if not queued:
            if _state(conn, "restart_empty_since"):
                _set_state(conn, "restart_empty_since", None)
                publish()
            return
        if playernum is None:
            return
        if playernum > 0:
            if _state(conn, "restart_empty_since"):
                _set_state(conn, "restart_empty_since", None)
                publish()
            return
        empty_since = _state(conn, "restart_empty_since")
        if not empty_since:
            _set_state(conn, "restart_empty_since", iso(now))
            publish()
            return
        if (now - parse_iso(empty_since)).total_seconds() < RESTART_IDLE_SECONDS:
            return
        retry_after = queued.get("retry_after")
        if retry_after and now < parse_iso(retry_after):
            return
        act = queued
    if act is None:
        return
    try:
        perform_restart(act.get("message") or DEFAULT_RESTART_MESSAGE,
                        RESTART_COUNTDOWN, act.get("by"), "queue", uptime)
    except Exception as exc:
        app.logger.warning("queued restart failed: %s", exc)
        with _db_lock, db() as conn:
            act["retry_after"] = iso(now_utc() + dt.timedelta(minutes=5))
            _set_state(conn, "restart_queue", act)
            _set_state(conn, "restart_error", str(exc)[:200])
        publish()


# ------------------------------------------------------------------- queries

def session_rows(conn, start=None, end=None):
    sql = "SELECT * FROM sessions"
    args = []
    if start and end:
        sql += " WHERE COALESCE(ended_at, ?) >= ? AND started_at < ?"
        args = [iso(end), iso(start), iso(end)]
    return conn.execute(sql, args).fetchall()


def split_by_day(row, lo, hi):
    """Yield (date, seconds) for a session clipped to [lo, hi)."""
    start = max(parse_iso(row["started_at"]), lo)
    end = min(parse_iso(row["ended_at"]) if row["ended_at"] else now_utc(), hi)
    if end <= start:
        return
    cursor = start
    while cursor < end:
        next_midnight = dt.datetime.combine(
            (cursor.astimezone(TZ) + dt.timedelta(days=1)).date(),
            dt.time.min,
            tzinfo=TZ,
        ).astimezone(dt.timezone.utc)
        chunk_end = min(next_midnight, end)
        yield cursor.astimezone(TZ).date(), (chunk_end - cursor).total_seconds()
        cursor = chunk_end


# Player EXP required to leave each level, 1 through 79, from
# palworld.wiki.gg/wiki/Level (1.0, cap 80). The API never reports EXP, so this
# table is how a level change turns into an amount of progress.
LEVEL_COST = (
    50, 150, 200, 340, 520, 700, 900, 1150, 1500, 1900,
    2400, 3000, 3800, 4677, 5353, 5860, 6367, 8564, 9409, 11944,
    12789, 15831, 17690, 21070, 22760, 26140, 27830, 29520, 31210, 34546,
    38229, 42295, 46783, 51738, 57207, 63245, 69910, 77267, 85389, 94355,
    104254, 115182, 127245, 140561, 155260, 171487, 189400, 209174, 230983, 255059,
    281635, 310972, 343356, 378104, 417573, 460143, 506239, 556335, 610983, 670817,
    736563, 809074, 889276, 977950, 1075818, 1187703, 1311213, 1447560, 1598080, 1764244,
    1947679, 2150182, 2373734, 2620524, 2892960, 3193698, 3525662, 3892088, 4296550,
)
# LEVEL_TOTAL[L] is the lifetime EXP of a character that has just reached L.
LEVEL_TOTAL = [0, 0]
for _cost in LEVEL_COST:
    LEVEL_TOTAL.append(LEVEL_TOTAL[-1] + _cost)
del _cost


def level_exp(level):
    """Lifetime EXP at `level`. Past the table's cap, extrapolate its last step.

    The wiki stops at the game's cap of 80. A modded server can raise it, and
    clamping would silently report a stalled player, so keep growing the cost
    by the final ratio instead.
    """
    level = max(1, int(level))
    top = len(LEVEL_TOTAL) - 1
    if level <= top:
        return LEVEL_TOTAL[level]
    cost, total = LEVEL_COST[-1], LEVEL_TOTAL[-1]
    ratio = LEVEL_COST[-1] / LEVEL_COST[-2]
    for _ in range(level - top):
        cost *= ratio
        total += cost
    return int(round(total))


def active_hours_before(sessions, cut, now):
    """Hours of recorded playtime that had elapsed by `cut`."""
    total = 0.0
    for row in sessions:
        start = parse_iso(row["started_at"])
        end = parse_iso(row["ended_at"]) if row["ended_at"] else now
        end = min(end, cut)
        if end > start:
            total += (end - start).total_seconds()
    return total / 3600.0


def advancement(samples, sessions, now):
    """EXP per active hour, inferred from level changes.

    Levels per hour falls as the curve steepens, so it cannot compare a level-20
    player with a level-60 one. EXP per hour does: the cost of a level grows
    roughly geometrically while the EXP a player earns per hour does not, so
    this number stays flat as they advance. ExpRate multiplies it, as it does
    every other EXP figure in the game.

    Only level-up observations are used, and only between the first and last of
    them, because their timestamps are the moment the level changed; a sample
    taken at session start sits an unknown distance into its level and would
    bias the total by up to one level. Levels are reported only while a player
    is online, so this needs `level_samples`; a level that drops is a recreated
    character, so only the run after the last drop counts.
    """
    ordered = sorted(samples, key=lambda r: r["ts"])
    run = []
    for sample in ordered:
        if run and int(sample["level"]) < int(run[-1]["level"]):
            run = []
        run.append(sample)

    changes = [cur for prev, cur in zip(run, run[1:])
               if int(cur["level"]) > int(prev["level"])]
    if len(changes) < 2:
        return None

    first, last = changes[0], changes[-1]
    hours = (active_hours_before(sessions, parse_iso(last["ts"]), now)
             - active_hours_before(sessions, parse_iso(first["ts"]), now))
    exp = level_exp(last["level"]) - level_exp(first["level"])
    if hours <= 0 or exp <= 0:
        return None
    return {
        "level": int(run[-1]["level"]),
        "observed_since": first["ts"],
        "samples": len(run),
        "level_ups": len(changes),
        "hours": round(hours, 1),
        "exp": exp,
        "exp_per_hour": round(exp / hours),
    }


def summary_payload(month=None):
    now = now_utc()
    with _world_lock:
        world_snapshot = dict(world)
    if month:
        year, mon = (int(x) for x in month.split("-"))
    else:
        local = now.astimezone(TZ)
        year, mon = local.year, local.month

    month_start = dt.datetime(year, mon, 1, tzinfo=TZ).astimezone(dt.timezone.utc)
    days_in_month = calmod.monthrange(year, mon)[1]
    month_end = (
        dt.datetime(year, mon, 1, tzinfo=TZ) + dt.timedelta(days=days_in_month)
    ).astimezone(dt.timezone.utc)

    with db() as conn:
        players = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
        all_sessions = session_rows(conn)
        month_sessions = session_rows(conn, month_start, month_end)
        level_history = conn.execute(
            "SELECT player_id, ts, level FROM level_samples ORDER BY player_id, ts"
        ).fetchall()
        open_by_player = {
            r["player_id"]: r
            for r in conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL")
        }
        metrics_row = conn.execute("SELECT * FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
        trend = conn.execute(
            "SELECT ts, playernum, fps FROM metrics ORDER BY ts DESC LIMIT 240"
        ).fetchall()
        server = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM server")}
        chat = conn.execute(
            "SELECT ts, name, message FROM chat ORDER BY ts DESC LIMIT 60"
        ).fetchall()

    totals = {}
    for row in all_sessions:
        start = parse_iso(row["started_at"])
        end = parse_iso(row["ended_at"]) if row["ended_at"] else now
        agg = totals.setdefault(row["player_id"], {"seconds": 0.0, "sessions": 0})
        agg["seconds"] += max(0.0, (end - start).total_seconds())
        agg["sessions"] += 1

    sessions_by_player = {}
    for row in all_sessions:
        sessions_by_player.setdefault(row["player_id"], []).append(row)
    levels_by_player = {}
    for row in level_history:
        levels_by_player.setdefault(row["player_id"], []).append(row)

    player_rows = []
    for pid, meta in players.items():
        agg = totals.get(pid, {"seconds": 0.0, "sessions": 0})
        player_rows.append({
            "player_id": pid,
            "name": meta["name"] or pid,
            "platform": meta["platform"] or "unknown",
            "level": meta["level"],
            "advancement": advancement(
                levels_by_player.get(pid, []), sessions_by_player.get(pid, []), now),
            "last_seen": meta["last_seen"],
            "ip": meta["last_ip"],
            "account_name": meta["account_name"],
            "ping": meta["ping"],
            "x": meta["last_x"],
            "y": meta["last_y"],
            "map": map_point(meta["last_x"], meta["last_y"]),
            "online": pid in open_by_player,
            "session_started": open_by_player[pid]["started_at"] if pid in open_by_player else None,
            "total_seconds": round(agg["seconds"]),
            "sessions": agg["sessions"],
            "avg_seconds": round(agg["seconds"] / agg["sessions"]) if agg["sessions"] else 0,
        })
    player_rows.sort(key=lambda r: (not r["online"], -r["total_seconds"]))

    day_map = {}
    for row in month_sessions:
        for day, seconds in split_by_day(row, month_start, month_end):
            bucket = day_map.setdefault(day.isoformat(), {})
            bucket[row["player_id"]] = bucket.get(row["player_id"], 0) + seconds

    days = []
    for daynum in range(1, days_in_month + 1):
        key = dt.date(year, mon, daynum).isoformat()
        per_player = day_map.get(key, {})
        days.append({
            "date": key,
            "total": round(sum(per_player.values())),
            "players": [
                {
                    "player_id": pid,
                    "name": (players.get(pid) or {}).get("name") or pid,
                    "seconds": round(secs),
                }
                for pid, secs in sorted(per_player.items(), key=lambda kv: -kv[1])
            ],
        })

    return {
        "month": f"{year:04d}-{mon:02d}",
        "month_label": dt.date(year, mon, 1).strftime("%B %Y"),
        "today": now.astimezone(TZ).date().isoformat(),
        "days": days,
        "players": player_rows,
        "metrics": dict(metrics_row) if metrics_row else None,
        "trend": [dict(r) for r in reversed(trend)],
        "server": server or None,
        "chat": [dict(r) for r in reversed(chat)],
        "world": world_snapshot,
        # Lowercased species keys this instance has portraits for, so the UI can
        # ask for the ones that exist instead of probing for every pal.
        "pal_icons": sorted(pal_icons()),
        "now": iso(now),
    }


def _clip_session(row, lo, hi, span, players, now):
    """One session clipped to [lo, hi), or None if it does not overlap."""
    raw_start = parse_iso(row["started_at"])
    ongoing = row["ended_at"] is None
    raw_end = parse_iso(row["ended_at"]) if row["ended_at"] else now
    start, end = max(raw_start, lo), min(raw_end, hi)
    if end <= start:
        return None
    meta = players.get(row["player_id"], {})
    return {
        "id": row["id"],
        "player_id": row["player_id"],
        "name": meta.get("name") or row["player_id"],
        "platform": meta.get("platform") or "",
        "start": iso(raw_start),
        "end": None if ongoing else iso(raw_end),
        "ongoing": ongoing,
        "start_frac": round((start - lo).total_seconds() / span, 6),
        "end_frac": round((end - lo).total_seconds() / span, 6),
        "continues_before": raw_start < lo,
        "continues_after": raw_end > hi,
        "day_seconds": round((end - start).total_seconds()),
        "duration_seconds": round((raw_end - raw_start).total_seconds()),
    }


def range_payload(start_str, end_str):
    """Per-day session lists for an inclusive date range (local days)."""
    first = dt.date.fromisoformat(start_str)
    last = dt.date.fromisoformat(end_str)
    if last < first:
        raise ValueError("end must not precede start")
    if (last - first).days > 92:
        raise ValueError("range too large")

    lo = dt.datetime.combine(first, dt.time.min, tzinfo=TZ).astimezone(dt.timezone.utc)
    hi = (
        dt.datetime.combine(last, dt.time.min, tzinfo=TZ) + dt.timedelta(days=1)
    ).astimezone(dt.timezone.utc)
    now = now_utc()

    with db() as conn:
        players = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
        rows = session_rows(conn, lo, hi)

    days = []
    cursor = first
    while cursor <= last:
        d_lo = dt.datetime.combine(cursor, dt.time.min, tzinfo=TZ).astimezone(dt.timezone.utc)
        d_hi = (
            dt.datetime.combine(cursor, dt.time.min, tzinfo=TZ) + dt.timedelta(days=1)
        ).astimezone(dt.timezone.utc)
        span = (d_hi - d_lo).total_seconds()
        sessions = [
            s for s in (_clip_session(r, d_lo, d_hi, span, players, now) for r in rows) if s
        ]
        sessions.sort(key=lambda s: (s["name"].lower(), s["start"]))
        days.append({
            "date": cursor.isoformat(),
            "sessions": sessions,
            "total_seconds": round(sum(s["day_seconds"] for s in sessions)),
            "player_count": len({s["player_id"] for s in sessions}),
            "now": iso(now),
        })
        cursor += dt.timedelta(days=1)

    return {"start": start_str, "end": end_str, "days": days, "now": iso(now)}


def day_payload(date_str):
    return range_payload(date_str, date_str)["days"][0]


def timeline_payload(month):
    """One continuous timeline for a month: sessions positioned on a single axis."""
    year, mon = (int(x) for x in month.split("-"))
    first = dt.date(year, mon, 1)
    last = dt.date(year, mon, calmod.monthrange(year, mon)[1])
    lo = dt.datetime.combine(first, dt.time.min, tzinfo=TZ).astimezone(dt.timezone.utc)
    hi = (
        dt.datetime.combine(last, dt.time.min, tzinfo=TZ) + dt.timedelta(days=1)
    ).astimezone(dt.timezone.utc)
    span = (hi - lo).total_seconds()
    now = now_utc()

    with db() as conn:
        players = {r["player_id"]: dict(r) for r in conn.execute("SELECT * FROM players")}
        rows = session_rows(conn, lo, hi)

    sessions = [s for s in (_clip_session(r, lo, hi, span, players, now) for r in rows) if s]
    sessions.sort(key=lambda s: (s["name"].lower(), s["start"]))

    days = []
    cursor = first
    while cursor <= last:
        d_lo = dt.datetime.combine(cursor, dt.time.min, tzinfo=TZ).astimezone(dt.timezone.utc)
        d_hi = (
            dt.datetime.combine(cursor, dt.time.min, tzinfo=TZ) + dt.timedelta(days=1)
        ).astimezone(dt.timezone.utc)
        days.append({
            "date": cursor.isoformat(),
            "start_frac": round((d_lo - lo).total_seconds() / span, 6),
            "mid_frac": round(((d_lo + (d_hi - d_lo) / 2) - lo).total_seconds() / span, 6),
        })
        cursor += dt.timedelta(days=1)

    now_frac = None
    if lo <= now <= hi:
        now_frac = round((now - lo).total_seconds() / span, 6)

    return {
        "month": f"{year:04d}-{mon:02d}",
        "month_label": first.strftime("%B %Y"),
        "days": days,
        "sessions": sessions,
        "now_frac": now_frac,
        "total_seconds": round(sum(s["day_seconds"] for s in sessions)),
    }


@app.route("/api/timeline")
def api_timeline():
    month = request.args.get("month")
    if not month:
        return jsonify(error="month required"), 400
    try:
        return jsonify(timeline_payload(month))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.route("/api/events")
def api_events():
    """Server-sent events: one push per state change instead of a 30 s poll."""
    global _streams
    with _stream_lock:
        if _streams >= MAX_STREAMS:
            return jsonify(error="too many live streams"), 503
        _streams += 1

    def stream():
        global _streams
        seen = None
        try:
            while True:
                with _rev_cond:
                    if seen == _rev:
                        _rev_cond.wait(10)
                    current = _rev
                if current == seen:
                    yield ": keepalive\n\n"
                    continue
                seen = current
                try:
                    payload = summary_payload(None)
                except Exception as exc:
                    app.logger.exception("live payload failed: %s", exc)
                    continue
                yield f"event: update\ndata: {json.dumps(payload)}\n\n"
        finally:
            with _stream_lock:
                _streams -= 1

    resp = app.response_class(stream(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/summary")
def api_summary():
    return jsonify(summary_payload(request.args.get("month")))


@app.route("/api/day")
def api_day():
    date_str = request.args.get("date")
    if not date_str:
        return jsonify(error="date required"), 400
    return jsonify(day_payload(date_str))


@app.route("/api/range")
def api_range():
    start, end = request.args.get("start"), request.args.get("end")
    if not start or not end:
        return jsonify(error="start and end required"), 400
    try:
        return jsonify(range_payload(start, end))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.route("/api/pal-icon/<species>")
def api_pal_icon(species):
    """Serve an operator-supplied portrait, or 404 and let the UI draw a monogram."""
    filename = pal_icons().get(species.strip().lower())
    if not filename:
        return jsonify(error="no portrait for that pal"), 404
    path = os.path.join(PAL_ICON_DIR, filename)
    if not os.path.isfile(path):
        return jsonify(error="portrait vanished"), 404
    response = send_file(
        path, mimetype=PAL_ICON_TYPES[os.path.splitext(filename)[1].lower()],
        conditional=True)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/api/server-config")
def api_server_config():
    return jsonify(config_payload())


@app.route("/api/server-config", methods=["POST"])
def api_server_config_save():
    ip, lan = client_context()
    if not lan:
        return jsonify(error=(
            f"settings can only be changed from the local network ({LAN_CIDRS}); "
            f"this request came from {ip}")), 403

    # A JSON body is required, which also means a cross-site form post cannot
    # reach this route: no CORS headers are served, and a simple request cannot
    # carry this content type.
    body = request.get_json(silent=True) or {}
    changes = body.get("changes")
    if not isinstance(changes, dict) or not changes:
        return jsonify(error="no changes supplied"), 400

    text = _read_settings_text()
    values = palconfig.parse(text) if text is not None else dict(palconfig.DEFAULTS)
    catalog = palconfig.by_key()
    applied = {}
    for key, raw in changes.items():
        entry = catalog.get(key)
        if entry is None:
            return jsonify(error=f"unknown setting: {key}"), 400
        if entry.get("locked"):
            return jsonify(error=f"{key} is {palconfig.LOCKED_REASON.lower()}"), 400
        try:
            token = palconfig.coerce(entry, raw)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        if not palconfig.same_value(entry, token, values.get(key)):
            applied[key] = token

    if applied:
        try:
            with _config_lock:
                _write_settings(applied)
        except OSError as exc:
            return jsonify(error=f"could not write {PAL_SETTINGS_FILE}: {exc}"), 500
        app.logger.info("%s changed %s", ip, ", ".join(sorted(applied)))

    payload = config_payload()
    payload["saved"] = sorted(applied)
    return jsonify(payload)


@app.route("/api/server-restart", methods=["POST"])
def api_server_restart():
    ip, lan = client_context()
    if not lan:
        return jsonify(error=(
            f"only the local network ({LAN_CIDRS}) may restart the server; "
            f"this request came from {ip}")), 403

    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode") or "").strip()
    message = str(body.get("message") or "").strip() or DEFAULT_RESTART_MESSAGE
    if mode == "now":
        try:
            perform_restart(message, RESTART_COUNTDOWN, ip, "now")
        except Exception as exc:
            return jsonify(error=f"the game did not accept the restart: {exc}"), 502
    elif mode == "queue":
        with _db_lock, db() as conn:
            _set_state(conn, "restart_queue", {
                "at": iso(now_utc()), "by": ip, "message": message})
            _set_state(conn, "restart_empty_since", None)
            _set_state(conn, "restart_error", None)
        publish()
    else:
        return jsonify(error="mode must be 'queue' or 'now'"), 400
    return jsonify(config_payload())


@app.route("/api/server-restart", methods=["DELETE"])
def api_server_restart_cancel():
    ip, lan = client_context()
    if not lan:
        return jsonify(error=(
            f"only the local network ({LAN_CIDRS}) may change the restart queue; "
            f"this request came from {ip}")), 403
    with _db_lock, db() as conn:
        cancelled = _state(conn, "restart_queue") is not None
        _set_state(conn, "restart_queue", None)
        _set_state(conn, "restart_empty_since", None)
        _set_state(conn, "restart_error", None)
    publish()
    payload = config_payload()
    payload["cancelled"] = cancelled
    return jsonify(payload)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.after_request
def cache_headers(response):
    if request.path.startswith("/static/assets/"):
        # Vite fingerprints these file names, so they are safe to pin.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif request.path.startswith("/api/pal-icon/"):
        # Portraits are replaced by hand, so they are not pinned like assets.
        response.headers["Cache-Control"] = "public, max-age=3600"
    elif request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Started at import so it also runs under a WSGI server (single worker only).
# PALADMIN_NO_BACKGROUND is a test seam: the unit tests import this module and
# must not leave a collector polling a server that is not there.
init_db()
if os.environ.get("PALADMIN_NO_BACKGROUND") != "1":
    threading.Thread(target=follow_logs, daemon=True).start()
    threading.Thread(target=collector, daemon=True).start()
    threading.Thread(target=world_loop, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
