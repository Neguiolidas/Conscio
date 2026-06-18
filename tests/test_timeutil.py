# tests/test_timeutil.py
"""v1.9 I-A3 / B-003b — epoch→naive-UTC must not drift to local time."""
import time
from datetime import datetime, timezone

from conscio.timeutil import naive_utc_from_epoch


def test_try_break_epoch_conversion_survives_nonutc_tz(monkeypatch):
    # Under a non-UTC TZ, the OLD code (datetime.fromtimestamp(ts), naive LOCAL)
    # would be hours off. The helper must always return the UTC wall clock.
    monkeypatch.setenv("TZ", "Asia/Tokyo")          # UTC+9
    time.tzset()
    try:
        epoch = 1_700_000_000.0                       # 2023-11-14T22:13:20Z
        got = naive_utc_from_epoch(epoch)
        local_buggy = datetime.fromtimestamp(epoch)   # the bug's value (naive local)
        assert got != local_buggy                     # bug would make these equal
        assert got == datetime.fromtimestamp(
            epoch, timezone.utc).replace(tzinfo=None)
        assert got.tzinfo is None
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_try_keep_epoch_conversion_matches_utc():
    epoch = 1_700_000_000.0
    assert naive_utc_from_epoch(epoch) == datetime.fromtimestamp(
        epoch, timezone.utc).replace(tzinfo=None)
