"""Palworld admin dashboard.

Collects player presence from two sources and persists it to SQLite:
  * the game REST API (/v1/api/players) for live state and levels
  * the container log for exact join/leave timestamps

Serves a single-page dashboard with player stats and a presence calendar.
"""

import calendar as calmod
import datetime as dt
import json
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
MIN_POLL_GAP = float(os.environ.get("MIN_POLL_GAP", "2"))
INFO_REFRESH_SECONDS = int(os.environ.get("INFO_REFRESH_SECONDS", "1800"))
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "4"))

app = Flask(__name__)
_db_lock = threading.Lock()

# Bumped whenever stored state changes; SSE subscribers wait on it instead of
# re-reading the database on a timer.
_rev = 0
_rev_cond = threading.Condition()
# Set by the log follower so a join/leave is reflected by an API poll at once.
wake = threading.Event()
_streams = 0
_stream_lock = threading.Lock()


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
    last_y      REAL
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
"""

# Added after the first release; created above for fresh databases, patched in
# for existing ones.
ADDED_COLUMNS = {
    "players": [("account_name", "TEXT"), ("ping", "REAL"), ("last_x", "REAL"),
                ("last_y", "REAL")],
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
    with db() as conn:
        conn.executescript(SCHEMA)
        _add_missing_columns(conn)


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


def upsert_player(conn, player_id, name=None, platform=None, level=None,
                  seen=None, ip=None, account_name=None, ping=None, x=None, y=None):
    row = conn.execute("SELECT player_id FROM players WHERE player_id = ?", (player_id,)).fetchone()
    seen_iso = iso(seen) if seen else None
    if row is None:
        conn.execute(
            "INSERT INTO players(player_id, name, platform, level, first_seen, last_seen,"
            " last_ip, account_name, ping, last_x, last_y)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (player_id, name, platform, level, seen_iso, seen_iso, ip,
             account_name, ping, x, y),
        )
        return
    conn.execute(
        "UPDATE players SET name=COALESCE(?, name), platform=COALESCE(?, platform),"
        " level=COALESCE(?, level), last_seen=COALESCE(?, last_seen),"
        " last_ip=COALESCE(?, last_ip), account_name=COALESCE(?, account_name),"
        " ping=COALESCE(?, ping), last_x=COALESCE(?, last_x), last_y=COALESCE(?, last_y)"
        " WHERE player_id=?",
        (name, platform, level, seen_iso, ip, account_name, ping, x, y, player_id),
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
                account_name=p.get("accountName"),
                ping=p.get("ping"),
                x=p.get("location_x"),
                y=p.get("location_y"),
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


def summary_payload(month=None):
    now = now_utc()
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

    player_rows = []
    for pid, meta in players.items():
        agg = totals.get(pid, {"seconds": 0.0, "sessions": 0})
        player_rows.append({
            "player_id": pid,
            "name": meta["name"] or pid,
            "platform": meta["platform"] or "unknown",
            "level": meta["level"],
            "last_seen": meta["last_seen"],
            "ip": meta["last_ip"],
            "account_name": meta["account_name"],
            "ping": meta["ping"],
            "x": meta["last_x"],
            "y": meta["last_y"],
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


@app.route("/")
def index():
    return render_template("index.html")


# Started at import so it also runs under a WSGI server (single worker only).
init_db()
threading.Thread(target=follow_logs, daemon=True).start()
threading.Thread(target=collector, daemon=True).start()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
