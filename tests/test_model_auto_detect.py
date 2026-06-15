"""Tests for ModelRegistry auto-detect context from endpoint."""
import json
import pytest
from unittest.mock import patch, MagicMock

from conscio.models import ModelRegistry, ContextMode


class TestAutoDetectContext:
    """Test querying /v1/models for context_length."""

    @pytest.fixture(autouse=True)
    def _isolate_config(self, monkeypatch, tmp_path):
        """Isolate tests from the real config file + ambient autodetect env."""
        monkeypatch.setattr(ModelRegistry, '_CONFIG_PATHS', [tmp_path / 'nope.json'])
        monkeypatch.delenv("CONSCIO_AUTODETECT", raising=False)
        monkeypatch.delenv("CONSCIO_CONTEXT_WINDOW", raising=False)

    def test_detect_with_explicit_context_window(self):
        """Baseline: explicit override still works."""
        info = ModelRegistry.detect("unknown-model", context_window=1_048_576)
        assert info.context_window == 1_048_576
        assert info.mode == ContextMode.STANDARD

    def test_detect_unknown_model_defaults_to_128k(self):
        """Unknown model without override gets 128k fallback (COMPACT)."""
        info = ModelRegistry.detect("totally-unknown-xyz")
        assert info.context_window == 128_000
        assert info.mode == ContextMode.COMPACT

    def test_detect_known_model_uses_registry(self):
        """Known models use hardcoded values."""
        info = ModelRegistry.detect("glm-5.1")
        assert info.context_window == 131_000

    def test_auto_detect_from_endpoint_returns_context(self):
        """When endpoint returns context_length, use it."""
        mock_response = {
            "data": [
                {
                    "id": "mimo-v2.5-pro",
                    "context_length": 1_048_576,
                }
            ]
        }

        def fake_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps(mock_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ctx = ModelRegistry.query_context_from_endpoint(
                "http://fake:1234/v1", "mimo-v2.5-pro"
            )

        assert ctx == 1_048_576

    def test_auto_detect_from_endpoint_returns_none_when_missing(self):
        """When endpoint doesn't return context_length, return None."""
        mock_response = {
            "data": [{"id": "some-model"}]
        }

        def fake_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps(mock_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ctx = ModelRegistry.query_context_from_endpoint(
                "http://fake:1234/v1", "some-model"
            )

        assert ctx is None

    def test_auto_detect_from_endpoint_returns_none_on_error(self):
        """When endpoint is unreachable, return None."""
        with patch("urllib.request.urlopen", side_effect=OSError("refused")):
            ctx = ModelRegistry.query_context_from_endpoint(
                "http://fake:1234/v1", "any-model"
            )

        assert ctx is None

    def test_auto_detect_from_endpoint_returns_none_when_model_not_found(self):
        """When model isn't in the endpoint's list, return None."""
        mock_response = {
            "data": [{"id": "other-model", "context_length": 128000}]
        }

        def fake_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps(mock_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ctx = ModelRegistry.query_context_from_endpoint(
                "http://fake:1234/v1", "missing-model"
            )

        assert ctx is None

    def test_detect_with_endpoint_auto_detect(self):
        """Full flow: detect() with base_url triggers endpoint probe."""
        mock_response = {
            "data": [
                {"id": "mimo-v2.5-pro", "context_length": 1_048_576}
            ]
        }

        def fake_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps(mock_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            info = ModelRegistry.detect(
                "mimo-v2.5-pro",
                base_url="http://fake:1234/v1",
            )

        assert info.context_window == 1_048_576
        assert info.mode == ContextMode.STANDARD
        assert "auto-detected" in info.notes.lower() or "endpoint" in info.notes.lower()

    def test_explicit_override_beats_endpoint(self):
        """Explicit context_window takes priority over endpoint probe."""
        info = ModelRegistry.detect(
            "mimo-v2.5-pro",
            context_window=524_288,
            base_url="http://fake:1234/v1",
        )
        assert info.context_window == 524_288

    def test_world_recognition_preserved(self):
        """Known models keep their strengths/notes even with different ctx."""
        mock_response = {
            "data": [
                {"id": "glm-5.1", "context_length": 207_872}
            ]
        }

        def fake_urlopen(req, timeout=30):
            resp = MagicMock()
            resp.read.return_value = json.dumps(mock_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            info = ModelRegistry.detect(
                "glm-5.1",
                base_url="http://fake:1234/v1",
            )

        # Context from endpoint, strengths from registry
        assert info.context_window == 207_872
        assert "complex_reasoning" in info.strengths


class TestJsonConfig:
    """Config is stdlib JSON (no optional PyYAML dependency) and opt-in."""

    def _write_config(self, tmp_path, monkeypatch, payload):
        import json as _json
        cfg = tmp_path / "config.json"
        cfg.write_text(_json.dumps(payload))
        monkeypatch.setattr(ModelRegistry, "_CONFIG_PATHS", [cfg])
        monkeypatch.delenv("CONSCIO_AUTODETECT", raising=False)
        monkeypatch.delenv("CONSCIO_CONTEXT_WINDOW", raising=False)

    def test_nested_json_config_under_autodetect(self, tmp_path, monkeypatch):
        self._write_config(tmp_path, monkeypatch,
                           {"models": {"foo-1": {"context_window": 777_000}}})
        assert ModelRegistry.detect("foo-1", autodetect=True).context_window == 777_000

    def test_flat_json_config_under_autodetect(self, tmp_path, monkeypatch):
        self._write_config(tmp_path, monkeypatch,
                           {"context_window": {"bar-2": 333_000}})
        assert ModelRegistry.detect("bar-2", autodetect=True).context_window == 333_000

    def test_config_ignored_without_autodetect(self, tmp_path, monkeypatch):
        # Config exists but autodetect is off -> unknown model falls to heuristic.
        self._write_config(tmp_path, monkeypatch,
                           {"models": {"baz-3": {"context_window": 999_000}}})
        assert ModelRegistry.detect("baz-3").context_window == 128_000  # heuristic, not config

    def test_config_enabled_via_env(self, tmp_path, monkeypatch):
        self._write_config(tmp_path, monkeypatch,
                           {"models": {"qux-4": {"context_window": 256_000}}})
        monkeypatch.setenv("CONSCIO_AUTODETECT", "1")
        assert ModelRegistry.detect("qux-4").context_window == 256_000

    def test_malformed_json_is_ignored(self, tmp_path, monkeypatch):
        cfg = tmp_path / "config.json"
        cfg.write_text("{not valid json")
        monkeypatch.setattr(ModelRegistry, "_CONFIG_PATHS", [cfg])
        # Must not raise; falls through to heuristic.
        assert ModelRegistry.detect("whatever-1", autodetect=True).context_window == 128_000

    def test_no_yaml_dependency_in_import_graph(self):
        import sys
        import importlib
        sys.modules.pop("yaml", None)
        importlib.import_module("conscio.models")
        assert "yaml" not in sys.modules
