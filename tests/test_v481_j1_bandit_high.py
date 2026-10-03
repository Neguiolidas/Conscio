import hashlib
import subprocess
from unittest.mock import MagicMock

from conscio.integrations.neurata import NeurataBridge
from conscio.outcomes import capture_council_outcome


def test_neurata_digest_known_input():
    bridge = NeurataBridge()
    bridge.available = True
    bridge._run_json = MagicMock(return_value={"result": "ok"})

    args = ["arg1", "arg2"]
    expected_hash = hashlib.sha1(" ".join(args).encode(), usedforsecurity=False).hexdigest()[:12]
    assert expected_hash == "1e8249248409"

    bridge._cached("test_label", None, args)
    assert "1e8249248409" in bridge._cache


def test_outcomes_digest_known_input():
    store = MagicMock()
    store.append.return_value = "event-123"

    question = "Should we deploy to production?"
    expected_digest = hashlib.sha1(
        question.encode("utf-8", "replace"), usedforsecurity=False
    ).hexdigest()[:10]
    assert expected_digest == "4a511de5c1"

    result = {
        "question": question,
        "mode": "deterministic",
        "agreement": {},
        "votes_summary": {},
    }
    eid = capture_council_outcome(store, result)
    assert eid == "event-123"

    call_args = store.append.call_args[0][0]
    assert f":{expected_digest}" in call_args.decision_ref


def test_bandit_reports_zero_high_severity():
    import shutil
    bandit_bin = shutil.which("bandit") or "/home/ubuntu/.local/bin/bandit"
    res = subprocess.run(
        [bandit_bin, "-r", "conscio/", "-lll", "-q"],
        capture_output=True,
        text=True,
    )
    # Must exit 0 and have no High severity findings
    assert res.returncode == 0, f"Bandit High findings present:\n{res.stdout}\n{res.stderr}"
