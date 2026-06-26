"""v2.8.1 — daemon honors daemon_control.json at the top of each cycle.

Off-default invariant: control_path=None -> the control branch is skipped
entirely (byte-identical to pre-v2.8.1). Apply path uses engine.wake()/sleep()
(engine.awake is a read-only property).
"""
from conscio.daemon import Daemon
from conscio.engine import ConsciousnessEngine
from conscio.hub import control
from conscio.perception import MockSensor, PerceptionFrame


def _frame():
    return PerceptionFrame(source="mock", observations=["obs"], signals={"x": 1.0})


def _engine(tmp_path):
    return ConsciousnessEngine("glm-5.1", storage_path=tmp_path / "s")


def test_control_flips_awake_on(tmp_path):
    eng = _engine(tmp_path)                       # default: asleep
    control.write_control(eng.storage, True)
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               control_path=eng.storage / control.CONTROL_FILENAME)
    try:
        d.cycle()
        assert eng.awake is True
    finally:
        eng.close()


def test_control_flips_awake_off(tmp_path):
    eng = _engine(tmp_path)
    eng.wake()
    control.write_control(eng.storage, False)
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               control_path=eng.storage / control.CONTROL_FILENAME)
    try:
        d.cycle()
        assert eng.awake is False
    finally:
        eng.close()


def test_control_path_none_is_inert(tmp_path):
    eng = _engine(tmp_path)                       # asleep
    control.write_control(eng.storage, True)      # file exists but not watched
    d = Daemon(eng, sensors=[MockSensor([_frame()])])   # control_path None
    try:
        d.cycle()
        assert eng.awake is False                 # never read the file
    finally:
        eng.close()


def test_control_corrupt_file_ignored(tmp_path):
    eng = _engine(tmp_path)
    (eng.storage / control.CONTROL_FILENAME).parent.mkdir(parents=True, exist_ok=True)
    (eng.storage / control.CONTROL_FILENAME).write_text("{bad json")
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               control_path=eng.storage / control.CONTROL_FILENAME)
    try:
        d.cycle()                                 # must not raise
        assert eng.awake is False
    finally:
        eng.close()


def test_control_authority_across_restart(tmp_path):
    # operator toggled awake; a fresh daemon (restart) with --watch-control
    # applies the persisted intent on cycle 1, overriding launch state.
    eng = _engine(tmp_path)                       # launched asleep
    control.write_control(eng.storage, True)
    d = Daemon(eng, sensors=[MockSensor([_frame()])],
               control_path=eng.storage / control.CONTROL_FILENAME)
    try:
        d.cycle()
        assert eng.awake is True
    finally:
        eng.close()
