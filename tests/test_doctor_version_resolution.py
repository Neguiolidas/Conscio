"""v4.6.9: the doctor must ask the process itself, not guess from the filesystem.

False positive measured on this machine right after the 4.6.8 ship: a
process running the repo-editable 4.6.8 was reported as 4.6.7 because the
dist-info walk (c) climbs the executable's parents and returns the FIRST
conscio-*.dist-info it finds — and this machine carries several
(~/.local has one, the hermes venv has an ancient 2.7.0, uv-tools has the
current one). Any multi-install machine can hit this.

The fix adds level (b+): resolve the version by importing conscio with the
target process's own interpreter (its /proc/<pid>/exe), which gives the
process's REAL resolution order — sys.path of the venv it actually runs.
The walk (c) stays as the last resort, with dist-infos now SORTED so the
closest one (the venv's own) wins over random parents.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from conscio.liaison.relay_cli import (
    _detect_version_from_interpreter,
    _sort_dist_infos,
)


class TestInterpreterResolution:
    def test_detects_version_of_our_own_interpreter(self):
        """Level (b+): importing with OUR interpreter returns OUR version."""
        ver = _detect_version_from_interpreter(Path(sys.executable))
        assert ver is not None
        # this test runs under the repo-editable install -> must be the package version
        import conscio
        assert ver == conscio.__version__

    def test_dead_interpreter_returns_none(self, tmp_path):
        assert _detect_version_from_interpreter(tmp_path / "no-such-python") is None

    def test_interpreter_without_conscio_returns_none(self, tmp_path):
        fake = tmp_path / "fakepy"
        fake.write_text("#!/bin/sh\nexit 1\n")
        fake.chmod(0o755)
        assert _detect_version_from_interpreter(fake) is None


class TestDistInfoOrder:
    def test_closer_dist_info_wins(self, tmp_path):
        """Sorting: deeper paths (the venv's own site-packages) sort first."""
        far = tmp_path / "lib" / "python3.12" / "site-packages"
        near = tmp_path / "venv" / "lib" / "python3.14" / "site-packages"
        infos = [far / "conscio-1.0.0.dist-info",
                 near / "conscio-9.9.9.dist-info"]
        ordered = _sort_dist_infos(infos)
        assert ordered[0].name == "conscio-9.9.9.dist-info"
