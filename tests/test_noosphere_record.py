from conscio.noosphere import artifact, record


def _entry(**kw):
    base = dict(seq=1, ts=1.0, goal_fp="ab12", tool="write", tier="low",
                status="executed", ok=1, verdict="PASS")
    base.update(kw)
    return record.RecordEntry(**base)


def test_build_body_has_only_whitelisted_keys():
    body = record.build_bundle_body([_entry()])
    assert body["schema_version"] == record.BUNDLE_SCHEMA
    assert set(body["entries"][0]) == {
        "seq", "ts", "goal_fp", "tool", "tier", "status", "ok", "verdict"}


def test_round_trip_through_canonical_bytes():
    entries = [_entry(seq=1), _entry(seq=2, status="failed", ok=0, verdict="")]
    body = record.build_bundle_body(entries)
    canon = artifact.canonical_bytes(body)
    import json
    back = record.entries_from_body(json.loads(canon.decode("utf-8")))
    assert [e.seq for e in back] == [1, 2]
    assert back[1].ok == 0 and back[1].status == "failed"


def test_well_typed_accepts_valid_and_rejects_bad():
    good = record.build_bundle_body([_entry()])
    assert record.well_typed_bundle(good) is True
    assert record.well_typed_bundle({"entries": "nope"}) is False
    assert record.well_typed_bundle(
        {"entries": [{"seq": 1}]}) is False                  # missing fields
    bad_status = record.build_bundle_body([_entry()])
    bad_status["entries"][0]["status"] = "frobnicate"
    assert record.well_typed_bundle(bad_status) is False
    bad_ok = record.build_bundle_body([_entry()])
    bad_ok["entries"][0]["ok"] = True                        # bool, not 0/1/None
    assert record.well_typed_bundle(bad_ok) is False
    none_ok = record.build_bundle_body([_entry(ok=None)])
    assert record.well_typed_bundle(none_ok) is True
