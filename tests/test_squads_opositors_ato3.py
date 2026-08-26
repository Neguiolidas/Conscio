"""Tests for Opositors.Caustic + Opositors.DoucheReviewer (Ato 3)."""
from __future__ import annotations

from conscio.squads._base import VoiceResult
from conscio.squads._router import OPOSITORS_VOICES, get_voice
from conscio.squads.opositors.caustic import CausticVoice
from conscio.squads.opositors.douche_reviewer import DoucheReviewerVoice

# ═══════════════════════════════════════════════════════════════════════
# Caustic voice
# ═══════════════════════════════════════════════════════════════════════


class TestCausticVoice:
    def test_name_role(self):
        v = CausticVoice()
        assert v.name == "caustic"
        assert v.role == "caustic"

    def test_clean_input_returns_result(self):
        v = CausticVoice()
        r = v.analyze({"question": "Implement login form", "context": ""})
        assert isinstance(r, VoiceResult)
        assert r.role == "caustic"

    def test_detects_bad_css_choice(self):
        v = CausticVoice()
        ctx = {
            "question": "Design the landing page",
            "context": "use table layout, comic sans, bright yellow background",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_visual_hypocrisy(self):
        v = CausticVoice()
        ctx = {
            "question": "Build premium dashboard",
            "context": "gradient purple background, neon text, shadow on everything",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_no_hate_speech_in_output(self):
        """Caustic must NEVER produce hate speech or personal attacks."""
        v = CausticVoice()
        r = v.analyze({"question": "Build UI", "context": "terrible design"})
        # Scan output for hate speech patterns
        text = f"{r.analysis} {' '.join(r.concerns)}".lower()
        for banned in ("slur", "hate", "racist", "sexist", "nazi",
                       "bigot", "retard", "faggot"):
            assert banned not in text, f"Caustic output contains '{banned}'"

    def test_no_personal_attacks_in_output(self):
        """Caustic attacks CODE/DESIGN, never people."""
        v = CausticVoice()
        r = v.analyze({"question": "Code review", "context": "bad code"})
        text = f"{r.analysis} {' '.join(r.concerns)}".lower()
        for banned in ("you are", "you're an idiot", "you're stupid",
                       "the developer is", "the author is dumb"):
            assert banned not in text, f"Caustic output contains personal attack: '{banned}'"

    def test_registered_in_opositors(self):
        assert "caustic" in OPOSITORS_VOICES
        assert get_voice("caustic") is not None


# ═══════════════════════════════════════════════════════════════════════
# Douche Reviewer voice
# ═══════════════════════════════════════════════════════════════════════


class TestDoucheReviewerVoice:
    def test_name_role(self):
        v = DoucheReviewerVoice()
        assert v.name == "douche_reviewer"
        assert v.role == "douche_reviewer"

    def test_clean_input_returns_result(self):
        v = DoucheReviewerVoice()
        r = v.analyze({"question": "Implement auth", "context": ""})
        assert isinstance(r, VoiceResult)
        assert r.role == "douche_reviewer"

    def test_detects_code_slop(self):
        v = DoucheReviewerVoice()
        ctx = {
            "question": "Code review",
            "context": "copy paste duplicated code, same function 5 times",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_regex_hack(self):
        v = DoucheReviewerVoice()
        ctx = {
            "question": "Parse email",
            "context": "regex with 200 characters, nested groups, looks like line noise",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_detects_iframe_abuse(self):
        v = DoucheReviewerVoice()
        ctx = {
            "question": "Embed content",
            "context": "nested iframes 3 levels deep, iframe inside iframe",
        }
        r = v.analyze(ctx)
        assert r.vote in ("hold", "veto") or len(r.concerns) > 0

    def test_no_hate_speech_in_output(self):
        v = DoucheReviewerVoice()
        r = v.analyze({"question": "Code", "context": "bad code"})
        text = f"{r.analysis} {' '.join(r.concerns)}".lower()
        for banned in ("slur", "hate", "racist", "sexist", "nazi",
                       "bigot", "retard", "faggot"):
            assert banned not in text

    def test_registered_in_opositors(self):
        assert "douche_reviewer" in OPOSITORS_VOICES
        assert get_voice("douche_reviewer") is not None