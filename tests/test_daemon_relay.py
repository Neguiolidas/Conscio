from conscio.daemon import _arg_parser, _build_sensors
from conscio.perception.relay_sensor import RelaySensor


def test_build_relay_sensor_with_identity(tmp_path):
    db = tmp_path / "liaison.db"
    sensors = _build_sensors("relay", agent_source=None,
                             liaison_db=db, self_id="me-1", relay_peers=("p",))
    assert len(sensors) == 1
    assert isinstance(sensors[0], RelaySensor)
    assert sensors[0].self_id == "me-1"
    assert sensors[0].peers == frozenset({"p"})


def test_build_relay_skipped_without_identity(tmp_path):
    sensors = _build_sensors("relay", agent_source=None,
                             liaison_db=tmp_path / "x.db", self_id="",
                             relay_peers=())
    assert sensors == []                     # no identity -> skip, don't crash


def test_build_host_and_relay(tmp_path):
    from conscio.perception import HostSensor
    sensors = _build_sensors("host,relay", agent_source=None,
                             liaison_db=tmp_path / "x.db", self_id="me",
                             relay_peers=())
    kinds = [type(s) for s in sensors]
    assert HostSensor in kinds and RelaySensor in kinds


def test_arg_parser_has_relay_flags():
    args = _arg_parser().parse_args(
        ["--sensors", "relay", "--relay-peer", "p1", "--relay-peer", "p2",
         "--liaison-db", "/tmp/x.db"])
    assert args.relay_peer == ["p1", "p2"]
    assert args.liaison_db == "/tmp/x.db"
