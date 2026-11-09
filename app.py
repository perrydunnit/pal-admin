"""Palworld admin dashboard.

Collects player presence from the game REST API and container log, persists it to
SQLite, and serves a dashboard of player stats and presence history.
"""



import datetime as dt
import os
import re
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

import docker
import requests
from flask import Flask, jsonify, render_template, request



TZ = ZoneInfo(os.environ.get("TZ", "UTC"))
PAL_API = os.environ.get("PAL_API", "http://palworld-server:8212").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PAL_CONTAINER = os.environ.get("PAL_CONTAINER", "palworld-server")
DB_PATH = os.environ.get("DB_PATH", "/data/paladmin.db")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "30"))

app = Flask(__name__)
_db_lock = threading.Lock()



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

CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,
    name        TEXT,
    platform    TEXT,
    level       INTEGER,
    first_seen  TEXT,
    last_seen   TEXT,
    last_ip     TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    ts          TEXT PRIMARY KEY,
    playernum   INTEGER,
    fps         REAL,
    days        INTEGER,
    basecamps   INTEGER,
    uptime      INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    user_id     TEXT,
    player_id   TEXT,
    PRIMARY KEY (ts, kind, name)
);
"""


def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)


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


def local_to_utc(stamp):
    naive = dt.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    return naive.replace(tzinfo=TZ).astimezone(dt.timezone.utc)


def open_session_for(conn, player_id):
    return conn.execute(
        "SELECT * FROM sessions WHERE player_id = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1",
        (player_id,),
    ).fetchone()


def upsert_player(conn, player_id, name=None, platform=None, level=None,
                  seen=None, ip=None):
    row = conn.execute("SELECT player_id FROM players WHERE player_id = ?", (player_id,)).fetchone()
    seen_iso = iso(seen) if seen else None
    if row is None:
        conn.execute(
            "INSERT INTO players(player_id, name, platform, level, first_seen, last_seen, last_ip)"
            " VALUES(?,?,?,?,?,?,?)",
            (player_id, name, platform, level, seen_iso, seen_iso, ip),
        )
        return
    conn.execute(
        "UPDATE players SET name=COALESCE(?, name), platform=COALESCE(?, platform),"
        " level=COALESCE(?, level), last_seen=COALESCE(?, last_seen),"
        " last_ip=COALESCE(?, last_ip) WHERE player_id=?",
        (name, platform, level, seen_iso, ip, player_id),
    )


def handle_join(conn, ts, name, user_id, player_id):
    open_row = open_session_for(conn, player_id)
    upsert_player(conn, player_id, name=name, platform=platform_of(user_id), seen=ts)
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


def ingest_logs():
    """Replay join/leave events from the container log, each event exactly once."""
    try:
        client = docker.from_env()
        container = client.containers.get(PAL_CONTAINER)
        with db() as conn:
            last = conn.execute("SELECT MAX(ts) AS m FROM events").fetchone()["m"]
        since = 1
        if last:
            since = max(1, int(parse_iso(last).timestamp()) - 120)
        raw = container.logs(since=since, timestamps=False).decode("utf-8", "replace")
    except Exception as exc:
        app.logger.warning("log ingest skipped: %s", exc)
        return

    events = []
    for m in JOIN_RE.finditer(raw):
        events.append((local_to_utc(m.group(1)), "join", m.group(2), m.group(3), m.group(4)))
    for m in LEFT_RE.finditer(raw):
        events.append((local_to_utc(m.group(1)), "left", m.group(2), None, None))
    events.sort(key=lambda e: e[0])

    with _db_lock, db() as conn:
        for ts, kind, name, user_id, player_id in events:
            cur = conn.execute(
                "INSERT OR IGNORE INTO events(ts, kind, name, user_id, player_id)"
                " VALUES(?,?,?,?,?)",
                (iso(ts), kind, name, user_id, player_id),
            )
            if cur.rowcount == 0:
                continue  # already applied on an earlier pass
            if kind == "join":
                handle_join(conn, ts, name, user_id, player_id)
            else:
                handle_left(conn, ts, name)


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
    online = set()
    with _db_lock, db() as conn:
        for p in players:
            pid = p.get("playerId")
            if not pid or pid == "None":
                pid = p.get("userId")
            if not pid:
                continue
            online.add(pid)
            upsert_player(
                conn, pid,
                name=p.get("name"),
                platform=platform_of(p.get("userId")),
                level=p.get("level"),
                seen=now,
                ip=p.get("iP"),
            )
            if open_session_for(conn, pid) is None:
                conn.execute(
                    "INSERT INTO sessions(player_id, user_id, started_at, ended_at, source)"
                    " VALUES(?,?,?,NULL,'poll')",
                    (pid, p.get("userId"), iso(now)),
                )

        for row in conn.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall():
            if row["player_id"] in online:
                continue
            if (now - parse_iso(row["started_at"])).total_seconds() < 90:
                continue
            conn.execute("UPDATE sessions SET ended_at=? WHERE id=?", (iso(now), row["id"]))

        conn.execute(
            "INSERT OR REPLACE INTO metrics(ts, playernum, fps, days, basecamps, uptime)"
            " VALUES(?,?,?,?,?,?)",
            (
                iso(now),
                metrics.get("currentplayernum"),
                metrics.get("serverfpsaverage") or metrics.get("serverfps"),
                metrics.get("days"),
                metrics.get("basecampnum"),
                metrics.get("uptime"),
            ),
        )


def collector():
    time.sleep(2)
    while True:
        try:
            ingest_logs()
            poll_api()
        except Exception as exc:
            app.logger.exception("collector iteration failed: %s", exc)
        time.sleep(POLL_SECONDS)


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


@app.route("/")
def index():
    return render_template("index.html")


# Started at import so it also runs under a WSGI server (single worker only).
init_db()
threading.Thread(target=collector, daemon=True).start()
