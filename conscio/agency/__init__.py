"""
Conscio Agency — the volition layer (v1.0.0 "Spine").

Stateless LLM orchestration downstream of engine.reflect():
contracts -> adapter -> gateway -> tools -> ledger -> breaker -> act pipeline.
Core stays zero-deps (stdlib + sqlite3); HTTP adapters use urllib only.
"""
