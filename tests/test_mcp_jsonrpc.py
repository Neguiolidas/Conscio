# tests/test_mcp_jsonrpc.py
import io

from conscio.mcp import jsonrpc as j


def test_make_response_shape():
    assert j.make_response(1, {"ok": True}) == {
        "jsonrpc": "2.0", "id": 1, "result": {"ok": True}}


def test_make_error_shape_and_optional_data():
    assert j.make_error(2, j.METHOD_NOT_FOUND, "nope") == {
        "jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "nope"}}
    assert j.make_error(None, j.PARSE_ERROR, "bad", data="x")["error"]["data"] == "x"


def test_read_frames_splits_and_skips_blank():
    stream = io.StringIO('{"a":1}\n\n  \n{"b":2}\n')
    assert list(j.read_frames(stream)) == ['{"a":1}', '{"b":2}']


def test_read_frames_oversize_is_sentinel_and_keeps_going():
    # line 2 exceeds the cap; reader must NOT buffer it whole, must drain it,
    # emit OVERSIZE, and still deliver line 3.
    stream = io.StringIO('{"ok":1}\n' + '{"big":"' + "a" * 200 + '"}\n' + '{"ok":2}\n')
    out = list(j.read_frames(stream, max_bytes=20))
    assert out[0] == '{"ok":1}'
    assert out[1] is j.OVERSIZE
    assert out[2] == '{"ok":2}'


def test_read_frames_trailing_line_without_newline():
    assert list(j.read_frames(io.StringIO('{"a":1}'))) == ['{"a":1}']
