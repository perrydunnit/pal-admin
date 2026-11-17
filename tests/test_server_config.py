"""Tests for the server settings editor.

Two things here have already gone wrong in other projects and are worth pinning:
the catalogue must not silently lose a key the game ships, and the LAN gate must
not be fooled by a forged X-Forwarded-For header.

Plain unittest so `python -m unittest discover -s tests` needs no dependency;
scripts/verify.sh runs it.
"""

import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import palconfig  # noqa: E402

TMP = tempfile.mkdtemp(prefix="paladmin-test-")
# Set before importing app: it reads its configuration and starts the database at
# import time.
os.environ["PALADMIN_NO_BACKGROUND"] = "1"
os.environ["DB_PATH"] = os.path.join(TMP, "test.db")
os.environ["MUSIC_DIR"] = os.path.join(TMP, "music")
os.environ["PAL_SETTINGS_FILE"] = os.path.join(TMP, "PalWorldSettings.ini")
os.environ["LAN_CIDRS"] = "192.168.0.0/24"

# The catalogue tests need nothing but the standard library. Everything that
# touches the app needs flask and docker, which the gate's interpreter may not
# have; those classes skip instead of failing the whole run.
try:
    import app as paladmin
    APP_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001 - any import failure means "not runnable"
    paladmin = None
    APP_IMPORT_ERROR = str(exc)

needs_app = unittest.skipUnless(
    paladmin is not None, f"app.py is not importable here: {APP_IMPORT_ERROR}")

LAN = {"REMOTE_ADDR": "192.168.0.42"}


class CatalogueTests(unittest.TestCase):
    def test_every_game_default_has_an_entry_and_a_description(self):
        keys = {entry["key"] for entry in palconfig.catalog()}
        self.assertEqual(sorted(set(palconfig.DEFAULTS) - keys), [])
        for entry in palconfig.catalog():
            self.assertTrue(entry["description"], entry["key"])

    def test_no_duplicates(self):
        keys = [entry["key"] for entry in palconfig.catalog()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_round_trip_keeps_every_value(self):
        values = dict(palconfig.DEFAULTS)
        values["ServerName"] = '"A, quoted, name"'
        parsed = palconfig.parse(palconfig.render(values))
        self.assertEqual(parsed, values)

    def test_unknown_keys_are_kept_and_listed(self):
        text = palconfig.render({"SomeFutureKey": "7"})
        self.assertEqual(palconfig.parse(text), {"SomeFutureKey": "7"})
        listed = {entry["key"]: entry for entry in palconfig.catalog(["SomeFutureKey"])}
        self.assertEqual(listed["SomeFutureKey"]["group"], "Other")

    def test_coerce_normalises_and_rejects(self):
        catalog = palconfig.by_key()
        self.assertEqual(palconfig.coerce(catalog["ExpRate"], "1.5"), "1.500000")
        self.assertEqual(palconfig.coerce(catalog["bIsPvP"], "yes"), "True")
        self.assertEqual(palconfig.coerce(catalog["Difficulty"], "hard"), "Hard")
        self.assertEqual(
            palconfig.coerce(catalog["CrossplayPlatforms"], "steam, ps5"), "(Steam,PS5)")
        for key, raw in (("ExpRate", "fast"), ("bIsPvP", "maybe"),
                         ("Difficulty", "Nightmare"), ("ServerName", 'a"b'),
                         ("ServerName", "two\nlines")):
            with self.subTest(key=key, raw=raw):
                with self.assertRaises(ValueError):
                    palconfig.coerce(catalog[key], raw)

    def test_same_value_is_semantic(self):
        catalog = palconfig.by_key()
        self.assertTrue(palconfig.same_value(catalog["bAllowClientMod"], "true", "True"))
        self.assertTrue(palconfig.same_value(catalog["AutoSaveSpan"], "30.000000", "30"))
        self.assertFalse(palconfig.same_value(catalog["ExpRate"], "1.000000", "1.500000"))


@needs_app
class ClientIpTests(unittest.TestCase):
    def setUp(self):
        import ipaddress
        self.nets = [ipaddress.ip_network("192.168.0.0/24")]

    def test_direct_lan_client(self):
        self.assertEqual(
            paladmin.resolve_client_ip("192.168.0.9", "", self.nets), "192.168.0.9")

    def test_proxy_replacing_the_header(self):
        self.assertEqual(
            paladmin.resolve_client_ip("172.18.0.7", "192.168.0.9", self.nets), "192.168.0.9")

    def test_forged_header_is_ignored(self):
        # Traefik appends the real peer, so the spoofed entry is never the last one.
        self.assertEqual(
            paladmin.resolve_client_ip("172.18.0.7", "192.168.0.9, 203.0.113.5", self.nets),
            "203.0.113.5")

    def test_garbage_falls_back_to_the_socket_address(self):
        self.assertEqual(
            paladmin.resolve_client_ip("172.18.0.7", "not-an-ip", self.nets), "172.18.0.7")

    def test_wan_address_is_not_lan(self):
        self.assertFalse(paladmin._ip_inside("203.0.113.5", self.nets))


@needs_app
class ConfigApiTests(unittest.TestCase):
    def setUp(self):
        self.client = paladmin.app.test_client()
        self.settings = os.environ["PAL_SETTINGS_FILE"]
        with open(self.settings, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(palconfig.render({"ServerName": '"Palworld Admin"',
                                           "ExpRate": "1.000000"}))
        self.install(lambda: None, "generation")
        self.install(lambda: None, "live")

    def tearDown(self):
        for path in (self.settings, self.settings + ".bak"):
            if os.path.exists(path):
                os.remove(path)

    def install(self, value, which):
        """Replace the docker and REST API probes so the tests stay offline."""
        name = "_regenerated_from_env" if which == "generation" else "_live_settings"
        original = getattr(paladmin, name)
        setattr(paladmin, name, value)
        self.addCleanup(setattr, paladmin, name, original)

    def values(self):
        with open(self.settings, encoding="utf-8") as handle:
            return palconfig.parse(handle.read())

    def test_read_from_lan_is_editable(self):
        payload = self.client.get("/api/server-config", environ_base=LAN).get_json()
        self.assertTrue(payload["available"])
        self.assertTrue(payload["editable"])
        self.assertEqual(payload["client_ip"], "192.168.0.42")
        keys = {row["key"] for group in payload["groups"] for row in group["settings"]}
        self.assertIn("ExpRate", keys)
        self.assertIn("MaxBuildingLimitNumPerPlayer", keys)

    def test_read_from_wan_is_visible_but_not_editable(self):
        payload = self.client.get(
            "/api/server-config", environ_base={"REMOTE_ADDR": "203.0.113.5"}).get_json()
        self.assertFalse(payload["editable"])
        self.assertTrue(payload["groups"])

    def test_secrets_are_never_sent(self):
        payload = self.client.get("/api/server-config", environ_base=LAN).get_json()
        rows = {row["key"]: row for group in payload["groups"] for row in group["settings"]}
        self.assertIsNone(rows["AdminPassword"]["value"])
        self.assertIsNone(rows["ServerPassword"]["value"])

    def test_write_from_wan_is_refused(self):
        response = self.client.post(
            "/api/server-config", json={"changes": {"ExpRate": "2"}},
            environ_base={"REMOTE_ADDR": "203.0.113.5"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.values()["ExpRate"], "1.000000")

    def test_write_from_lan_applies_and_keeps_the_rest(self):
        response = self.client.post(
            "/api/server-config", json={"changes": {"ExpRate": "2.5"}}, environ_base=LAN)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["saved"], ["ExpRate"])
        values = self.values()
        self.assertEqual(values["ExpRate"], "2.500000")
        self.assertEqual(values["ServerName"], '"Palworld Admin"')
        self.assertTrue(os.path.exists(self.settings + ".bak"))

    def test_write_rejects_a_bad_value_without_touching_the_file(self):
        response = self.client.post(
            "/api/server-config", json={"changes": {"ExpRate": "as fast as possible"}},
            environ_base=LAN)
        self.assertEqual(response.status_code, 400)
        self.assertIn("ExpRate", response.get_json()["error"])
        self.assertEqual(self.values()["ExpRate"], "1.000000")

    def test_write_rejects_locked_keys_and_unknown_keys(self):
        for key in ("RESTAPIEnabled", "NotARealSetting"):
            with self.subTest(key=key):
                response = self.client.post(
                    "/api/server-config", json={"changes": {key: "True"}}, environ_base=LAN)
                self.assertEqual(response.status_code, 400)


@needs_app
class RestartQueueTests(unittest.TestCase):
    def setUp(self):
        self.client = paladmin.app.test_client()
        original = paladmin.perform_restart
        self.calls = []

        def fake(message, countdown, by, mode, uptime=None):
            self.calls.append({"message": message, "mode": mode, "by": by})

        paladmin.perform_restart = fake
        self.addCleanup(setattr, paladmin, "perform_restart", original)
        paladmin._regenerated_from_env = lambda: None   # noqa: E731 - offline probe
        paladmin._live_settings = lambda: None          # noqa: E731 - offline probe
        with paladmin._db_lock, paladmin.db() as conn:
            paladmin._set_state(conn, "restart_queue", None)
            paladmin._set_state(conn, "restart_issued", None)
            paladmin._set_state(conn, "restart_empty_since", None)

    def test_queue_then_cancel(self):
        response = self.client.post(
            "/api/server-restart", json={"mode": "queue"}, environ_base=LAN)
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.get_json()["restart"]["queued"])

        response = self.client.delete("/api/server-restart", environ_base=LAN)
        self.assertTrue(response.get_json()["cancelled"])
        self.assertIsNone(response.get_json()["restart"]["queued"])

    def test_restart_is_refused_from_wan(self):
        response = self.client.post(
            "/api/server-restart", json={"mode": "now"},
            environ_base={"REMOTE_ADDR": "203.0.113.5"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])

    def test_queued_restart_waits_for_the_server_to_be_empty(self):
        self.client.post("/api/server-restart", json={"mode": "queue"}, environ_base=LAN)
        paladmin.check_restart_queue(3, 1000)        # someone is playing
        self.assertEqual(self.calls, [])
        paladmin.check_restart_queue(0, 1000)        # empty, clock starts now
        self.assertEqual(self.calls, [])
        with paladmin._db_lock, paladmin.db() as conn:
            long_ago = paladmin.iso(
                paladmin.now_utc() - dt.timedelta(seconds=paladmin.RESTART_IDLE_SECONDS + 5))
            paladmin._set_state(conn, "restart_empty_since", long_ago)
        paladmin.check_restart_queue(0, 1000)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["mode"], "queue")

    def test_a_returning_player_resets_the_idle_clock(self):
        self.client.post("/api/server-restart", json={"mode": "queue"}, environ_base=LAN)
        paladmin.check_restart_queue(0, 1000)
        with paladmin.db() as conn:
            self.assertIsNotNone(paladmin._state(conn, "restart_empty_since"))
        paladmin.check_restart_queue(1, 1000)
        with paladmin.db() as conn:
            self.assertIsNone(paladmin._state(conn, "restart_empty_since"))
        self.assertEqual(self.calls, [])

    def test_issued_flag_clears_once_the_game_reports_fresh_uptime(self):
        def issued(seconds_ago):
            with paladmin._db_lock, paladmin.db() as conn:
                paladmin._set_state(conn, "restart_issued", {
                    "at": paladmin.iso(paladmin.now_utc() - dt.timedelta(seconds=seconds_ago)),
                    "by": "192.168.0.42", "mode": "queue", "message": "m", "uptime": 14})

        issued(200)
        paladmin.check_restart_queue(0, 60)          # back up: 60 s of uptime, 200 elapsed
        with paladmin.db() as conn:
            self.assertIsNone(paladmin._state(conn, "restart_issued"))

        issued(200)
        paladmin.check_restart_queue(0, 214)         # never went down: uptime tracks elapsed
        with paladmin.db() as conn:
            self.assertIsNotNone(paladmin._state(conn, "restart_issued"))


def tearDownModule():
    # The sqlite connections in app.py are committed but not closed, and Windows
    # keeps the database file locked until they are collected.
    import gc
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
