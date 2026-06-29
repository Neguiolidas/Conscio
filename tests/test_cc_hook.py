import pathlib
import subprocess
import sys
import conscio.integrations.claude_code as cc

HOOK = (pathlib.Path(cc.__file__).parent / "assets" / "hooks"
        / "conscio_awareness.py")


def test_hook_exits_zero_and_prints_awareness():
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True,
                       text=True, timeout=5)
    assert r.returncode == 0
    assert "Conscio" in r.stdout


def test_hook_survives_broken_env():
    # even with a bogus HOME the hook must still exit 0 (defensive)
    env = {"HOME": "/nonexistent-xyz", "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, str(HOOK)], capture_output=True,
                       text=True, timeout=5, env=env)
    assert r.returncode == 0
