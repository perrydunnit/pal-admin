"""Palworld admin dashboard.

Collects player presence from the game REST API and container log, persists it to
SQLite, and serves a dashboard of player stats and presence history.
"""



import calendar as calmod
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
        "now": iso(now),
    }


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


@app.route("/favicon.ico")
def favicon():
    return ("", 204)


@app.route("/api/summary")
def api_summary():
    return jsonify(summary_payload(request.args.get("month")))


@app.route("/")
def index():
    return render_template("index.html")


# Started at import so it also runs under a WSGI server (single worker only).
init_db()
threading.Thread(target=collector, daemon=True).start()
