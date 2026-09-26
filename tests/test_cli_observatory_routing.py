"""A47-A1: `conscio observatory` must route to _cmd_observatory.

The space slice (ac219f0) replaced the observatory dispatch block with its
`space` block instead of adding to it, so `conscio observatory` fell
through to the top-level help and exited rc=2 while _cmd_observatory sat
unrouted (vulture flagged it as unused for that reason). This test is the
tooth that keeps the dispatch from being lost again: it calls main() with
the observatory command, mocks _cmd_observatory, and asserts it was called
with the parsed arguments.
"""
from conscio import cli


def test_observatory_dispatch_reaches_cmd_observatory(monkeypatch):
    seen = {}

    def fake_observatory(*, host, port, root, token, storage,
                         noosphere="", liaison_db=""):
        seen.update(host=host, port=port, root=root, token=token,
                    storage=storage, noosphere=noosphere,
                    liaison_db=liaison_db)
        return 0

    monkeypatch.setattr(cli, "_cmd_observatory", fake_observatory)
    rc = cli.main(["observatory", "--port", "9999"])
    assert rc == 0
    assert "port" in seen, "observatory dispatch block is missing"
    assert seen["port"] == 9999
    assert seen["host"] == "127.0.0.1"
    assert seen["storage"] == ""
