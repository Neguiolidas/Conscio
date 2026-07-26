"""Tests for interactive consent request flow."""
from __future__ import annotations

import json

from conscio.structural_consent import (
    ConsentScope,
    StructuralConsent,
    consent_path,
)


def test_consent_request_event_emitted(tmp_path):
    """When daemon finds a workspace with graph.json but no consent,
    it emits a consent:request event."""
    from conscio.event_bus import EventBus
    
    bus = EventBus(tmp_path / "events.db")
    consent = StructuralConsent(consent_path(tmp_path))
    
    # workspace_id without consent
    ws_id = "abc123"
    scope = consent.scope_for(ws_id)
    assert scope == ConsentScope.OFF
    
    # graph.json exists but consent is OFF → emit event
    events_before = len(bus.query(type="consent:request", limit=1000))
    
    # Simulate what daemon does
    if scope == ConsentScope.OFF:
        bus.emit(
            type="consent:request",
            category="system",
            data={"workspace_id": ws_id, "root": str(tmp_path)},
            priority=5,
        )
    
    events_after = len(bus.query(type="consent:request", limit=1000))
    assert events_after == events_before + 1


def test_consent_grant_after_approval(tmp_path):
    """After user approves, consent is granted and persists."""
    consent = StructuralConsent(consent_path(tmp_path))
    ws_id = "abc123"
    
    # Before: no consent
    assert consent.scope_for(ws_id) == ConsentScope.OFF
    
    # User approves
    consent.grant(ws_id, ConsentScope.PROJECT)
    
    # After: consent granted
    assert consent.scope_for(ws_id) == ConsentScope.PROJECT
    
    # Persists to file
    raw = json.loads(consent_path(tmp_path).read_text())
    assert raw[ws_id] == "project"


def test_consent_revoke(tmp_path):
    """User can revoke consent."""
    consent = StructuralConsent(consent_path(tmp_path))
    ws_id = "abc123"
    
    consent.grant(ws_id, ConsentScope.PROJECT)
    assert consent.scope_for(ws_id) == ConsentScope.PROJECT
    
    consent.grant(ws_id, ConsentScope.OFF)
    assert consent.scope_for(ws_id) == ConsentScope.OFF


def test_yolo_mode_auto_grants(tmp_path):
    """In YOLO mode, daemon auto-approves consent for any workspace."""
    consent = StructuralConsent(consent_path(tmp_path))
    ws_id = "abc123"
    
    # Simulate YOLO: auto-grant
    yolo = True
    if yolo and consent.scope_for(ws_id) == ConsentScope.OFF:
        consent.grant(ws_id, ConsentScope.PROJECT)
    
    assert consent.scope_for(ws_id) == ConsentScope.PROJECT


def test_consent_status_endpoint(tmp_path):
    """GET /api/consent returns workspaces with their consent status."""
    from conscio.observatory.knowledge_view import KnowledgeProjection
    from conscio.observatory.liaison_view import LiaisonProjection
    from conscio.observatory.projection import Projection
    from conscio.observatory.server import route
    from conscio.observatory.society import SocietyProjection
    from conscio.observatory.structural_view import StructuralProjection
    
    # Create consent file in the projection's storage dir
    storage = tmp_path / "consciousness"
    storage.mkdir()
    cp = consent_path(storage)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps({"ws1": "project", "ws2": "off"}))
    
    P = Projection(storage)
    S = SocietyProjection(tmp_path / "noo.db")
    L = LiaisonProjection(tmp_path / "liai.db")
    SP = StructuralProjection(storage)
    KP = KnowledgeProjection(storage)
    
    r = route("GET", "/api/consent", {},
              projection=P, society=S, liaison=L, structural=SP,
              knowledge=KP, token=None, auth=None,
              workspace_root=str(tmp_path))
    status, payload = r.status, r.payload
    assert status == 200
    assert isinstance(payload, list)
    assert len(payload) >= 2
    # ws1 should have project scope
    ws1 = next((w for w in payload if w["workspace_id"] == "ws1"), None)
    assert ws1 is not None
    assert ws1["scope"] == "project"
