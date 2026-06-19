# tests/test_mcp_fuzz.py
"""v2.0 seeded stdlib fuzz — transport reader/parse + event validation never
hang, OOM, or crash; only structured errors or clean rejects (no hypothesis)."""
import io
import json
import random
import string

from conscio.engine import ConsciousnessEngine
from conscio.mcp import jsonrpc as j
from conscio.mcp.schemas import validate_event
from conscio.mcp.seen import SeenStore
from conscio.mcp.server import Bindings, serve


def _rand_line(rng):
    kind = rng.choice(["json", "garbage", "partial", "huge"])
    if kind == "json":
        return json.dumps({"jsonrpc": rng.choice(["2.0", "1.0", 2]),
                           "id": rng.randint(0, 9),
                           "method": rng.choice(["ping", "tools/list", "bogus",
                                                 "initialize"]),
                           "params": {}})
    if kind == "garbage":
        return "".join(rng.choice(string.printable)
                       for _ in range(rng.randint(1, 80)))
    if kind == "partial":
        return '{"jsonrpc":"2.0","id":1,"method":'
    return '{"x":"' + "a" * rng.randint(2000, 6000) + '"}'


def test_fuzz_read_frames_and_parse_never_crash():
    rng = random.Random(1337)
    for _ in range(2000):
        stream = io.StringIO(_rand_line(rng) + "\n")
        for frame in j.read_frames(stream, max_bytes=4096):
            if frame is j.OVERSIZE:
                continue
            try:
                json.loads(frame)
            except json.JSONDecodeError:
                pass


def test_fuzz_serve_always_answers_or_skips(tmp_path):
    rng = random.Random(7)
    eng = ConsciousnessEngine("glm-5.1", storage_path=tmp_path)
    seen = SeenStore(tmp_path / "mcp_seen.db")
    b = Bindings(eng, seen, workspace_id="ws")
    try:
        lines = "".join(_rand_line(rng) + "\n" for _ in range(500))
        out = io.StringIO()
        serve(b, io.StringIO(lines), out, max_bytes=4096)   # must return
        for raw in out.getvalue().splitlines():
            if raw:
                msg = json.loads(raw)
                assert "result" in msg or "error" in msg
    finally:
        seen.close(); eng.close()


def test_fuzz_validate_event_never_crashes():
    rng = random.Random(99)
    for _ in range(2000):
        ev = {rng.choice(["type", "source", "category", "payload", "x"]):
              rng.choice(["s", 1, None, [], {}, True])
              for _ in range(rng.randint(0, 5))}
        assert isinstance(validate_event(ev), list)
