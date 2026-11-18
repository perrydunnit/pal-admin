"""Shared environment for the tests that import app.py.

Two test modules import the app, and the first import wins: a module that set its
own DB_PATH afterwards would silently exercise the wrong files. Everything both
modules need is therefore fixed here, before either one imports anything.
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="paladmin-test-")

os.environ.update({
    "PALADMIN_NO_BACKGROUND": "1",
    "DB_PATH": os.path.join(TMP, "test.db"),
    "PAL_SETTINGS_FILE": os.path.join(TMP, "PalWorldSettings.ini"),
    "PAL_ICON_DIR": os.path.join(TMP, "palicons"),
    "LAN_CIDRS": "192.168.0.0/24",
    "PAL_API": "http://127.0.0.1:9",
})


def import_app():
    """The app module, or (None, reason) when flask/docker are not installed."""
    try:
        import app
        return app, None
    except Exception as exc:  # noqa: BLE001 - any import failure means "not runnable"
        return None, str(exc)


def cleanup():
    # The sqlite connections in app.py are committed but not closed, and Windows
    # keeps the database file locked until they are collected.
    import gc
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(cleanup)
