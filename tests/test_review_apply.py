from conscio.agency import review_apply


class _FakeHostAct:
    def __init__(self, pending):
        self._pending = pending
        self.approved = []
        self.rejected = []

    def pending(self, n):
        return self._pending

    def approve(self, lid):
        self.approved.append(lid)
        return {"status": "approved", "packet": {"lid": lid}}

    def reject(self, lid, reason):
        self.rejected.append((lid, reason))
        return {"status": "rejected", "packet": None}


def test_none_host_act_noop(tmp_path):
    assert review_apply.apply_verdicts(None, tmp_path / "x.db", "me",
                                       ("rev",)) == []


def test_apply_approve_from_allowlisted(tmp_path, monkeypatch):
    from conscio.liaison import mailbox, review
    db = tmp_path / "liaison.db"
    pend = [{"id": 7, "goal_fp": "g", "tool": "t", "args_json": "{}"}]
    fp = review.fingerprint("me", "g", "t", {}, 7)
    mailbox.send(db, from_instance="rev", to_instance="me",
                 type="review_verdict",
                 payload=review.build_verdict(fp=fp, decision="approve",
                                              reason=""))
    ha = _FakeHostAct(pend)
    out = review_apply.apply_verdicts(ha, db, "me", ("rev",))
    assert out == [{"ledger_id": 7, "decision": "approve",
                    "status": "approved", "packet": {"lid": 7}}]
    assert ha.approved == [7]
    # verdict row was marked read (bound work)
    assert mailbox.inbox(db, "me", types=["review_verdict"],
                         unread_only=True) == []


def test_non_allowlisted_ignored(tmp_path):
    from conscio.liaison import mailbox, review
    db = tmp_path / "liaison.db"
    pend = [{"id": 7, "goal_fp": "g", "tool": "t", "args_json": "{}"}]
    fp = review.fingerprint("me", "g", "t", {}, 7)
    mailbox.send(db, from_instance="stranger", to_instance="me",
                 type="review_verdict",
                 payload=review.build_verdict(fp=fp, decision="approve",
                                              reason=""))
    ha = _FakeHostAct(pend)
    assert review_apply.apply_verdicts(ha, db, "me", ("rev",)) == []
    assert ha.approved == []
