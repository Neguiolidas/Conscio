"""Context-first resolution: provider prefixes, quant suffixes, override wins.

Regression + feature suite for the v2.14 model/context resolution overhaul
(Option A: context is king, model is only a lookup key). Covers the three
production bugs that made ``z-ai/glm-5.2`` resolve to the wrong window:

  1. provider-prefixed IDs (``z-ai/glm-5.2``) must resolve like ``glm-5.2``
  2. quant/format suffixes (``glm-5.2-fp8``) must resolve like ``glm-5.2``
  3. a config ``models`` override must win even for a KNOWN model, and must
     match on the canonical (prefix/suffix-stripped) key.

Plus the regression guard: ``glm-5.2`` must NEVER collapse to ``glm-5`` (200k).
"""
import json
import pytest

from conscio.models import ModelRegistry, ContextMode


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(ModelRegistry, "_CONFIG_PATHS", [tmp_path / "nope.json"])
    monkeypatch.delenv("CONSCIO_AUTODETECT", raising=False)
    monkeypatch.delenv("CONSCIO_CONTEXT_WINDOW", raising=False)


class TestProviderPrefix:
    def test_vendor_prefixed_glm_resolves_to_full_window(self):
        assert ModelRegistry.detect("z-ai/glm-5.2").context_window == 1_000_000

    def test_org_prefixed_glm_resolves_to_full_window(self):
        assert ModelRegistry.detect("zai-org/glm-5.2").context_window == 1_000_000

    def test_vendor_prefixed_known_openai_still_resolves(self):
        # openai/gpt-4o -> gpt-4o (128k, known)
        assert ModelRegistry.detect("openai/gpt-4o").context_window == 128_000


class TestQuantSuffix:
    def test_fp8_suffix_resolves_to_base_window(self):
        assert ModelRegistry.detect("glm-5.2-fp8").context_window == 1_000_000

    def test_gguf_quant_tag_resolves_to_base_window(self):
        assert ModelRegistry.detect("glm-5.2:q4_k_m").context_window == 1_000_000

    def test_vendor_and_quant_combined(self):
        assert ModelRegistry.detect("z-ai/glm-5.2-fp8").context_window == 1_000_000


class TestNoGlmRegression:
    def test_glm_5_2_never_collapses_to_glm_5(self):
        info = ModelRegistry.detect("glm-5.2")
        assert info.context_window == 1_000_000
        assert info.context_window != 200_000

    def test_glm_5_1_still_200k(self):
        assert ModelRegistry.detect("glm-5.1").context_window == 200_000

    def test_unknown_glm_variant_falls_to_safe_default(self):
        # An unknown variant must NOT silently borrow glm-5.2's 1M window.
        assert ModelRegistry.detect("glm-5.9-turbo").context_window == 128_000


class TestClaudeFamily:
    """Claude models are all 200k — one family rule, no per-version catalog."""

    def test_sonnet_variant(self):
        assert ModelRegistry.detect("claude-sonnet-4-6").context_window == 200_000

    def test_opus_variant(self):
        assert ModelRegistry.detect("claude-opus-4-8").context_window == 200_000

    def test_haiku_variant(self):
        assert ModelRegistry.detect("claude-haiku-4-5").context_window == 200_000

    def test_vendor_prefixed_claude(self):
        assert ModelRegistry.detect("anthropic/claude-opus-4-8").context_window == 200_000


class TestConfigOverrideWins:
    def _write_config(self, tmp_path, monkeypatch, payload):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps(payload))
        monkeypatch.setattr(ModelRegistry, "_CONFIG_PATHS", [cfg])

    def test_override_wins_for_known_model(self, tmp_path, monkeypatch):
        # glm-5.2 is known (1M) but the user pins it lower — pin must win.
        self._write_config(tmp_path, monkeypatch,
                           {"models": {"glm-5.2": {"context_window": 300_000}}})
        assert ModelRegistry.detect("glm-5.2").context_window == 300_000

    def test_override_matches_canonical_key(self, tmp_path, monkeypatch):
        # config keyed by bare name, model requested with vendor prefix.
        self._write_config(tmp_path, monkeypatch,
                           {"models": {"glm-5.2": {"context_window": 500_000}}})
        assert ModelRegistry.detect("z-ai/glm-5.2").context_window == 500_000


class TestUnknownUnchanged:
    def test_totally_unknown_still_128k(self):
        info = ModelRegistry.detect("totally-unknown-xyz")
        assert info.context_window == 128_000
        assert info.mode == ContextMode.COMPACT
