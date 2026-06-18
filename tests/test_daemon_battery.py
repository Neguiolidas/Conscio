# tests/test_daemon_battery.py
"""v1.9 deep battery — daemon cross-cutting robustness.

B-012: _write_heartbeat used non-atomic Path.write_text. The v1.6 #5/#9 contract
       is "a host can tail daemon_heartbeat.json", but a host json.loads-ing the
       file WHILE the daemon rewrites it reads a torn/partial file (truncate-then-
       write on the same inode). Atomic write (tmp + os.replace) makes every read
       see either the old or the new COMPLETE file, never a partial one.
"""
import json
import threading

from conscio.daemon import Daemon


class _Eng:
    """Minimal engine stub — Daemon only needs storage/awake/advisory here."""
    awake = False

    def __init__(self, storage, blob_kb):
        self.storage = storage
        self._blob = "x" * (blob_kb * 1024)

    def advisory(self):
        return {"awake": False, "goals": [], "status": {},
                "recommendations": [], "blob": self._blob}


def test_try_break_heartbeat_atomic_under_concurrent_read(tmp_path):
    # ~700KB advisory widens the write window so a torn read is reliably caught
    # pre-fix; the file path is the daemon's own heartbeat_path.
    eng = _Eng(tmp_path, blob_kb=700)
    d = Daemon(eng, sensors=[])
    d._write_heartbeat()                         # seed a first complete file

    errors: list[str] = []
    ok: list[int] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                txt = d.heartbeat_path.read_text()
            except OSError:
                continue
            if not txt:
                continue
            try:
                json.loads(txt)
                ok.append(1)
            except json.JSONDecodeError:
                errors.append("torn")

    t = threading.Thread(target=reader)
    t.start()
    try:
        for _ in range(120):
            d._write_heartbeat()
    finally:
        stop.set()
        t.join()

    assert ok, "reader never managed a clean read (test setup broken)"
    assert not errors, (
        f"host tailing daemon_heartbeat.json read a TORN file {len(errors)}x "
        "— heartbeat write is not atomic (B-012)")
