# tests/test_mcp_server_cli.py
from conscio.mcp import server


def test_cli_parses_act_flags():
    args = server._arg_parser().parse_args(["--enable-act", "--awake"])
    assert args.enable_act is True and args.awake is True


def test_cli_defaults_act_off():
    args = server._arg_parser().parse_args([])
    assert args.enable_act is False and args.awake is False
