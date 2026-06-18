# tests/test_daemon_structure_sync.py
"""v1.7.2 — the daemon brings structure online per consent, and unloads on switch.

This is the switch-safety property at the daemon level: an agent that switches
workspace mid-run must never carry project A's structure into project B unless B
is also consented.
"""
import shutil
from pathlib import Path

from conscio.daemon import Daemon
from conscio.engine import ConsciousnessEngine
from conscio.perception import MockSensor, PerceptionFrame
from conscio.structural_consent import ConsentScope, StructuralConsent
from conscio.workspace import EnvClass, Workspace

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_small.json"


def _frame():
    return PerceptionFrame(source="mock", observations=["obs"], signals={"x": 1.0})


class _StubWorkspace:
    """poll() returns successive workspaces, then stays on the last."""

    def __init__(self, seq):
        self._seq = list(seq)
        self._i = 0

    def poll(self):
        ws = self._seq[min(self._i, len(self._seq) - 1)]
        self._i += 1
        return ws


def _plant(root):
    d = Path(root) / "graphify-out"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, d / "graph.json")


def test_daemon_loads_on_consent_then_unloads_on_switch(tmp_path):
    wa = tmp_path / "a"
    wa.mkdir()
    _plant(wa)
    wb = tmp_path / "b"
    wb.mkdir()                                   # no graph, no consent
    A = Workspace(root=wa, env=EnvClass.SWITCHING, id="ws-a")
    B = Workspace(root=wb, env=EnvClass.SWITCHING, id="ws-b")

    consent = StructuralConsent(tmp_path / "c.json")
    consent.grant("ws-a", ConsentScope.PROJECT)

    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    d = Daemon(eng, sensors=[MockSensor([_frame() for _ in range(6)])],
               workspace=_StubWorkspace([A, B]), consent=consent)
    try:
        d.cycle()                                # workspace A -> consented -> load
        assert eng.structural_signal() is not None
        d.cycle()                                # workspace B -> unconsented -> unload
        assert eng.structural_signal() is None
    finally:
        eng.close()


def test_daemon_no_consent_loads_nothing(tmp_path):
    wa = tmp_path / "a"
    wa.mkdir()
    _plant(wa)                                   # graph present but NOT consented
    A = Workspace(root=wa, env=EnvClass.STABLE, id="ws-a")
    consent = StructuralConsent(tmp_path / "c.json")     # default OFF

    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")
    d = Daemon(eng, sensors=[MockSensor([_frame() for _ in range(3)])],
               workspace=_StubWorkspace([A]), consent=consent)
    try:
        d.cycle()
        assert eng.structural_signal() is None
    finally:
        eng.close()
