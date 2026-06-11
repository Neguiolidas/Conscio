"""HTTP adapters against an in-process fake server (loopback only)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conscio.agency.adapter import AdapterConnectionError
from conscio.agency.adapters import (
    LlamaCppAdapter,
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


class TestErrors:
    def test_connection_refused_maps_to_adapter_error(self):
        adapter = OllamaAdapter(base_url="http://127.0.0.1:9", model="m",
                                timeout=0.3)
        with pytest.raises(AdapterConnectionError):
            adapter.generate("hi")
