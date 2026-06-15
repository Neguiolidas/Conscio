"""HTTP adapters against an in-process fake server (loopback only)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conscio.agency.adapter import AdapterBadResponse, AdapterConnectionError
from conscio.agency import adapters
from conscio.agency.adapters import (
    AnthropicAdapter,
    GeminiAdapter,
    LlamaCppAdapter,
    LMStudioAdapter,
    OllamaAdapter,
    OpenAICompatAdapter,
)


class _Handler(BaseHTTPRequestHandler):
    responses = {}
    captured = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Handler.captured.append((self.path, payload))
        body = json.dumps(_Handler.responses.get(self.path, {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def server():
    _Handler.responses, _Handler.captured = {}, []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}", _Handler
    httpd.shutdown()


class TestOllama:
    def test_generate_parses_response_and_tokens(self, server):
        url, handler = server
        handler.responses["/api/generate"] = {
            "response": '{"x": 1}', "prompt_eval_count": 12, "eval_count": 4}
        adapter = OllamaAdapter(base_url=url, model="hermes")
        result = adapter.generate("hi", schema={"x": {}})
        assert result.text == '{"x": 1}'
        assert (result.tokens_in, result.tokens_out) == (12, 4)
        path, payload = handler.captured[0]
        assert path == "/api/generate"
        assert payload["model"] == "hermes" and payload["stream"] is False
        assert payload["format"] == "json"   # schema present -> json mode

    def test_no_schema_means_no_format_key(self, server):
        url, handler = server
        handler.responses["/api/generate"] = {"response": "plain"}
        OllamaAdapter(base_url=url, model="m").generate("hi")
        assert "format" not in handler.captured[0][1]

    def test_caps(self):
        caps = OllamaAdapter(base_url="http://localhost:11434",
                             model="hermes").capabilities()
        assert caps.json_mode is True and caps.grammar is False
        assert caps.model_name == "hermes"


class TestLlamaCpp:
    def test_generate_passes_grammar_through(self, server):
        url, handler = server
        handler.responses["/completion"] = {
            "content": "out", "tokens_evaluated": 3, "tokens_predicted": 2}
        adapter = LlamaCppAdapter(base_url=url)
        result = adapter.generate("hi", grammar='root ::= "x"')
        assert result.text == "out"
        assert handler.captured[0][1]["grammar"] == 'root ::= "x"'

    def test_caps_advertise_grammar(self):
        caps = LlamaCppAdapter(base_url="http://localhost:8080").capabilities()
        assert caps.grammar is True


class TestOpenAICompat:
    def test_generate_chat_payload_and_response(self, server):
        url, handler = server
        handler.responses["/v1/chat/completions"] = {
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        adapter = OpenAICompatAdapter(base_url=url + "/v1", model="local")
        result = adapter.generate("ask", schema={"x": {}})
        assert result.text == "answer"
        assert result.tokens_in == 7
        payload = handler.captured[0][1]
        assert payload["messages"][0]["content"] == "ask"
        assert payload["response_format"] == {"type": "json_object"}

    def test_default_base_url_is_localhost(self):
        adapter = OpenAICompatAdapter(model="m")
        assert "localhost" in adapter.base_url


class TestLMStudio:
    def test_default_base_url_is_localhost_1234(self):
        adapter = LMStudioAdapter(model="m")
        assert adapter.base_url == "http://localhost:1234/v1"

    def test_caps_json_mode_no_grammar(self):
        caps = LMStudioAdapter(model="qwen3.5-0.8b").capabilities()
        assert caps.json_mode is True and caps.grammar is False
        assert caps.model_name == "qwen3.5-0.8b"

    def test_speaks_openai_chat_shape(self, server):
        # LM Studio is OpenAI-compatible: same chat/completions surface.
        url, handler = server
        handler.responses["/v1/chat/completions"] = {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        adapter = LMStudioAdapter(model="local", base_url=url + "/v1")
        result = adapter.generate("hi", schema={"x": {}})
        assert result.text == "ok"
        payload = handler.captured[0][1]
        assert payload["messages"][0]["content"] == "hi"

    def test_omits_unsupported_json_object_format(self, server):
        # LM Studio 400s on response_format=json_object, so we never send it
        # (the gateway elicits JSON via prompt instructions instead).
        url, handler = server
        handler.responses["/v1/chat/completions"] = {
            "choices": [{"message": {"content": "{}"}}], "usage": {}}
        LMStudioAdapter(model="local",
                        base_url=url + "/v1").generate("hi", schema={"x": {}})
        assert "response_format" not in handler.captured[0][1]


class TestAnthropic:
    def test_generate_messages_shape_and_response(self, server):
        url, handler = server
        handler.responses["/v1/messages"] = {
            "content": [{"type": "text", "text": "claude says hi"}],
            "usage": {"input_tokens": 9, "output_tokens": 4}}
        adapter = AnthropicAdapter(base_url=url, model="claude-x",
                                   api_key="sk-ant-test")
        result = adapter.generate("ask", schema={"x": {}})
        assert result.text == "claude says hi"
        assert (result.tokens_in, result.tokens_out) == (9, 4)
        path, payload = handler.captured[0]
        assert path == "/v1/messages"
        assert payload["model"] == "claude-x"
        assert payload["messages"][0]["content"] == "ask"
        assert payload["max_tokens"] == 512

    def test_concatenates_text_blocks(self, server):
        url, handler = server
        handler.responses["/v1/messages"] = {
            "content": [{"type": "text", "text": "a"},
                        {"type": "text", "text": "b"}], "usage": {}}
        out = AnthropicAdapter(base_url=url, model="m",
                               api_key="k").generate("hi")
        assert out.text == "ab"

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(AdapterConnectionError):
            AnthropicAdapter(model="m").generate("hi")

    def test_reads_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
        assert AnthropicAdapter(model="m").api_key == "env-key"

    def test_sends_versioned_auth_headers(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout, headers=None):
            captured["url"], captured["headers"] = url, headers
            return {"content": [{"type": "text", "text": "ok"}], "usage": {}}

        monkeypatch.setattr(adapters, "_post_json", fake_post)
        AnthropicAdapter(model="m", api_key="sk-ant-xyz").generate("hi")
        assert captured["headers"]["x-api-key"] == "sk-ant-xyz"
        assert "anthropic-version" in captured["headers"]
        assert captured["url"].endswith("/v1/messages")

    def test_caps(self):
        caps = AnthropicAdapter(model="claude-x", api_key="k").capabilities()
        assert caps.json_mode is True and caps.grammar is False
        assert caps.model_name == "claude-x"


class TestGemini:
    def test_generate_parses_candidates(self, server):
        url, handler = server
        handler.responses["/v1beta/models/gemini-x:generateContent"] = {
            "candidates": [{"content": {"parts": [{"text": "gemini hi"}]}}],
            "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 5}}
        adapter = GeminiAdapter(base_url=url, model="gemini-x", api_key="g-key")
        result = adapter.generate("ask")
        assert result.text == "gemini hi"
        assert (result.tokens_in, result.tokens_out) == (11, 5)
        path, payload = handler.captured[0]
        assert path == "/v1beta/models/gemini-x:generateContent"
        assert payload["contents"][0]["parts"][0]["text"] == "ask"

    def test_schema_enables_native_json_mime(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout, headers=None):
            captured["url"], captured["headers"], captured["payload"] = (
                url, headers, payload)
            return {"candidates": [{"content": {"parts": [{"text": "g"}]}}],
                    "usageMetadata": {}}

        monkeypatch.setattr(adapters, "_post_json", fake_post)
        GeminiAdapter(model="gemini-x", api_key="g-key").generate(
            "hi", schema={"x": {}})
        assert "gemini-x:generateContent" in captured["url"]
        assert captured["headers"]["x-goog-api-key"] == "g-key"
        assert (captured["payload"]["generationConfig"]["responseMimeType"]
                == "application/json")

    def test_no_schema_omits_json_mime(self, monkeypatch):
        captured = {}

        def fake_post(url, payload, timeout, headers=None):
            captured["payload"] = payload
            return {"candidates": [{"content": {"parts": [{"text": "g"}]}}],
                    "usageMetadata": {}}

        monkeypatch.setattr(adapters, "_post_json", fake_post)
        GeminiAdapter(model="m", api_key="k").generate("hi")
        assert "responseMimeType" not in captured["payload"]["generationConfig"]

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(AdapterConnectionError):
            GeminiAdapter(model="m").generate("hi")

    def test_reads_key_from_either_env(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "gem-env")
        assert GeminiAdapter(model="m").api_key == "gem-env"

    def test_caps(self):
        caps = GeminiAdapter(model="gemini-x", api_key="k").capabilities()
        assert caps.json_mode is True and caps.grammar is False
        assert caps.model_name == "gemini-x"


class TestErrors:
    def test_connection_refused_maps_to_adapter_error(self):
        adapter = OllamaAdapter(base_url="http://127.0.0.1:9", model="m",
                                timeout=0.3)
        with pytest.raises(AdapterConnectionError):
            adapter.generate("hi")

    def test_http_error_maps_to_bad_response(self, monkeypatch):
        # HTTPError subclasses URLError: a 4xx/5xx (server responded badly,
        # e.g. Ollama "model not found") must NOT be a connection error.
        import io
        import urllib.error
        import urllib.request

        from conscio.agency import adapters

        def fake_urlopen(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://x", 500, "Server Error", {}, io.BytesIO(b"boom"))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(AdapterBadResponse):
            adapters._post_json("http://x", {}, 1.0)
