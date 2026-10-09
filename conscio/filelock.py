"""Cross-platform file locking (v4.9: Windows compatibility).

fcntl.flock is POSIX-only; on Windows the equivalent is msvcrt.locking.
One API: ``lock(fd, exclusive=True, nonblocking=False)`` /
``unlock(fd)`` — the three runtime imports (installer/spaces,
ambient/node, liaison/reactor) call these instead of importing fcntl
directly, so ``pip install conscio`` stops breaking on Windows at the
first import (the 4.7.3 state: present but only inside one tool).

stdlib only; msvcrt imports lazily on the platform that needs it.
"""
from __future__ import annotations

import os
import sys

_IS_WINDOWS = sys.platform == "win32"


def lock(fd: int, *, exclusive: bool = True, nonblocking: bool = False) -> None:
    """Lock the whole file. POSIX: fcntl.flock; Windows: msvcrt.locking."""
    if _IS_WINDOWS:
        import msvcrt
        mode = msvcrt.LK_NBLCK if nonblocking else msvcrt.LK_LOCK
        # lock 1 byte at offset 0 — msvcrt has no whole-file primitive;
        # the callers lock sentinel files, so byte 0 is the whole file.
        msvcrt.locking(fd, mode, 1)
        return
    import fcntl
    flags = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    if nonblocking:
        flags |= fcntl.LOCK_NB
    fcntl.flock(fd, flags)


def unlock(fd: int) -> None:
    if _IS_WINDOWS:
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)
