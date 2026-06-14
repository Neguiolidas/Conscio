"""Tests for ModelRegistry.autodiscover and world-context detection."""

from __future__ import annotations

from unittest.mock import patch, MagicMock
import json

import pytest

from conscio.models import ModelRegistry, ContextMode


@pytest.fixture(autouse=True)
def _clean_registry():
    """Ensure each test starts with a clean registry."""
    saved = ModelRegistry._world_registry.copy()
    ModelRegistry._world_registry.clear()
    yield
    ModelRegistry._world_registry = saved


def _mock_response(data: dict) -> MagicMock:
    """Create a mock urllib response with context manager protocol."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestWorldContextDetection:
    """detect() should prefer world_registry over heuristic extraction."""

    def test_detect_uses_world_registry(self):
        ModelRegistry._world_registry["my-model"] = 200_000
        info = ModelRegistry.detect("my-model")
        assert info.context_window == 200_000
        assert info.mode == ContextMode.COMPACT  # 200k < 256k → COMPACT
        assert "world_registry" in info.notes

    def test_detect_world_registry_standard(self):
        ModelRegistry._world_registry["big-model"] = 500_000
        info = ModelRegistry.detect("big-model")
        assert info.context_window == 500_000
        assert info.mode == ContextMode.STANDARD

    def test_detect_world_registry_override_heuristic(self):
        ModelRegistry._world_registry["model-128k"] = 500_000
        info = ModelRegistry.detect("model-128k")
        assert info.context_window == 500_000

    def test_detect_without_world_registry_falls_back(self):
        info = ModelRegistry.detect("unknown-model-256k")
        assert info.context_window == 256_000


class TestAutodiscover:
    """autodiscover() should probe endpoints and register models."""

    @patch("conscio.models.ModelRegistry._probe_lmstudio")
    @patch("conscio.models.ModelRegistry._probe_ollama")
    def test_autodiscover_lmstudio(self, mock_ollama, mock_lm):
        mock_lm.return_value = {"qwen3.5-0.8b": 32_768}
        mock_ollama.return_value = {}
        count = ModelRegistry.autodiscover()
        assert count == 1
        assert ModelRegistry._world_registry["qwen3.5-0.8b"] == 32_768

    @patch("conscio.models.ModelRegistry._probe_lmstudio")
    @patch("conscio.models.ModelRegistry._probe_ollama")
    def test_autodiscover_ollama(self, mock_ollama, mock_lm):
        mock_lm.return_value = {}
        mock_ollama.return_value = {"granite4.1:3b": 131_072}
        count = ModelRegistry.autodiscover()
        assert count == 1
        assert ModelRegistry._world_registry["granite4.1:3b"] == 131_072

    @patch("conscio.models.ModelRegistry._probe_lmstudio")
    @patch("conscio.models.ModelRegistry._probe_ollama")
    def test_autodiscover_merges(self, mock_ollama, mock_lm):
        mock_lm.return_value = {"qwen3.5-0.8b": 32_768}
        mock_ollama.return_value = {"granite4.1:3b": 131_072}
        count = ModelRegistry.autodiscover()
        assert count == 2

    @patch("conscio.models.ModelRegistry._probe_lmstudio")
    @patch("conscio.models.ModelRegistry._probe_ollama")
    def test_autodiscover_handles_failure(self, mock_ollama, mock_lm):
        mock_lm.side_effect = Exception("connection refused")
        mock_ollama.return_value = {}
        count = ModelRegistry.autodiscover()
        assert count == 0

    @patch("conscio.models.ModelRegistry._probe_lmstudio")
    @patch("conscio.models.ModelRegistry._probe_ollama")
    def test_autodiscover_env_override(self, mock_ollama, mock_lm):
        with patch.dict("os.environ", {"CONSCIO_ENDPOINTS": "http://custom:9999"}):
            mock_lm.return_value = {}
            mock_ollama.return_value = {}
            with patch.object(ModelRegistry, "_probe_openai_endpoint", return_value={"m": 1000}) as mock_ep:
                count = ModelRegistry.autodiscover()
                assert count == 1
                mock_ep.assert_called_once_with("http://custom:9999", timeout=2.0)


class TestProbeEndpoints:
    """Probe helpers should parse API responses correctly."""

    @patch("conscio.models.urlopen")
    def test_probe_lmstudio_loaded(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "data": [{"id": "qwen3.5-0.8b", "object": "model"}]
        })
        with patch("conscio.models.ModelRegistry._query_lmstudio_state", return_value=32_768):
            result = ModelRegistry._probe_lmstudio()
            assert result == {"qwen3.5-0.8b": 32_768}

    @patch("conscio.models.urlopen")
    def test_probe_ollama_show(self, mock_urlopen):
        # First call: /api/tags → list models
        # Second call: /api/show → model details
        mock_urlopen.side_effect = [
            _mock_response({"models": [{"name": "granite4.1:3b"}]}),
            _mock_response({"details": {"context_length": 131072}}),
        ]
        result = ModelRegistry._probe_ollama()
        assert result == {"granite4.1:3b": 131_072}

    @patch("conscio.models.urlopen")
    def test_probe_ollama_empty(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"models": []})
        result = ModelRegistry._probe_ollama()
        assert result == {}

    @patch("conscio.models.urlopen")
    def test_probe_openai_endpoint(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "data": [
                {"id": "model-a", "context_length": 500_000},
                {"id": "model-b", "context_length": 200_000},
            ]
        })
        result = ModelRegistry._probe_openai_endpoint("http://localhost:8777/v1")
        assert result == {"model-a": 500_000, "model-b": 200_000}

    @patch("conscio.models.urlopen")
    def test_probe_openai_endpoint_appends_v1_models(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"data": []})
        ModelRegistry._probe_openai_endpoint("http://localhost:8777")
        # Should have called with /v1/models appended
        call_args = mock_urlopen.call_args
        req = call_args[0][0]  # First positional arg is the Request object
        assert "/v1/models" in req.full_url
