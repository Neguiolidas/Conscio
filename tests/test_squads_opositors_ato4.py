"""Tests for Opositors.DevilsAdvocate + Opositors.SkepticEngineer (Ato 4)."""
from __future__ import annotations

from conscio.squads._base import VoiceResult
from conscio.squads._router import OPOSITORS_VOICES, get_voice
from conscio.squads.opositors.devils_advocate import DevilsAdvocateVoice
from conscio.squads.opositors.skeptic_engineer import SkepticEngineerVoice

# ═══════════════════════════════════════════════════════════════════════
# Devil's Advocate voice
# ═══════════════════════════════════════════════════════════════════════


class TestDevilsAdvocateVoice:
    def test_name_role(self):
        v = DevilsAdvocateVoice()
        assert v.name == "devils_advocate"
        assert v.role == "devils_advocate"

    def test_clean_input_returns_result(self):
        v = DevilsAdvocateVoice()
        r = v.analyze({"question": "Deploy v2", "context": ""})
        assert isinstance(r, VoiceResult)
        assert r.role == "devils_advocate"

    def test_challenges_premise(self):
        v = DevilsAdvocateVoice()
        ctx = {
            "question": "Build the product with MCP + LLM + Parametric Motor",
            "context": "This will revolutionise the industry",
        }
        r = v.analyze(ctx)
        # Devil's Advocate MUST challenge the premise
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_challenges_unverified_claim(self):
        v = DevilsAdvocateVoice()
        ctx = {
            "question": "Release feature",
            "context": "Users definitely want this, no need to validate",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_no_hate_speech(self):
        v = DevilsAdvocateVoice()
        r = v.analyze({"question": "Build product", "context": ""})
        text = f"{r.analysis} {' '.join(r.concerns)}".lower()
        for banned in ("slur", "hate", "racist", "nazi", "retard"):
            assert banned not in text

    def test_registered_in_opositors(self):
        assert "devils_advocate" in OPOSITORS_VOICES
        assert get_voice("devils_advocate") is not None


# ═══════════════════════════════════════════════════════════════════════
# Skeptic Engineer voice
# ═══════════════════════════════════════════════════════════════════════


class TestSkepticEngineerVoice:
    def test_name_role(self):
        v = SkepticEngineerVoice()
        assert v.name == "skeptic_engineer"
        assert v.role == "skeptic_engineer"

    def test_clean_input_returns_result(self):
        v = SkepticEngineerVoice()
        r = v.analyze({"question": "Build API", "context": ""})
        assert isinstance(r, VoiceResult)
        assert r.role == "skeptic_engineer"

    def test_detects_over_engineering(self):
        v = SkepticEngineerVoice()
        ctx = {
            "question": "Build hello world",
            "context": "Using Kubernetes, Istio, 5 microservices, event sourcing, CQRS",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_unnecessary_ipc(self):
        v = SkepticEngineerVoice()
        ctx = {
            "question": "Build desktop app",
            "context": "Tauri + IPC bridge + WebView + separate Rust backend for each feature",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_framework_bloat(self):
        v = SkepticEngineerVoice()
        ctx = {
            "question": "Simple CRUD app",
            "context": "Using React, Redux, GraphQL, Apollo, Next.js, Prisma, 5 frameworks",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_reasonable_stack_proceeds(self):
        v = SkepticEngineerVoice()
        ctx = {
            "question": "Build web app",
            "context": "Using FastAPI + PostgreSQL + Redis. Simple stack for a CRUD app.",
        }
        r = v.analyze(ctx)
        # Reasonable stack → should proceed or hold at most
        assert r.vote in ("proceed", "hold")

    def test_registered_in_opositors(self):
        assert "skeptic_engineer" in OPOSITORS_VOICES
        assert get_voice("skeptic_engineer") is not None