import inspect
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from conscio.agency.gateway import OutputGateway


def test_output_gateway_has_no_failure_governor_attribute():
    adapter = MagicMock()
    gw = OutputGateway(adapter)
    assert not hasattr(gw, "_failure_gov"), "OutputGateway still has dead attribute _failure_gov"


def test_output_gateway_init_has_no_failure_governor_parameter():
    sig = inspect.signature(OutputGateway.__init__)
    assert "failure_governor" not in sig.parameters, (
        "OutputGateway.__init__ still has dead parameter failure_governor"
    )

    adapter = MagicMock()
    with pytest.raises(TypeError):
        OutputGateway(adapter, failure_governor=None)  # type: ignore


def test_vulture_clean_and_no_failure_gov_in_repo():
    res = subprocess.run(
        [
            sys.executable,
            "-c",
            "import subprocess; p = subprocess.run(['grep', '-rn', '_failure_gov', 'conscio/'], capture_output=True, text=True); exit(0 if not p.stdout.strip() else 1)",
        ]
    )
    assert res.returncode == 0, "Found _failure_gov in conscio/"
