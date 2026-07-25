"""auto-reindex is opt-in and default OFF (v3.4 B2)."""
from conscio.daemon import Daemon
import inspect


def test_auto_reindex_default_off():
    sig = inspect.signature(Daemon.__init__)
    param = sig.parameters.get("auto_reindex")
    assert param is not None
    assert param.default is False
