#!/usr/bin/env python3
"""Real ablation test with Qwen 0.8B via LM Studio."""
import time
from pathlib import Path
import tempfile

from conscio.agency.adapters import LMStudioAdapter
from conscio.agency.gateway import OutputGateway
from conscio.agency.contracts import PROPOSAL_SCHEMA
from conscio.token_account import TokenLedger
from conscio.prompt_zones import build_zoned_prompt
from conscio.context_manager import ConsciousnessState

print("=== Ablation Study: Qwen3.5-0.8B on LM Studio ===")
print()

adapter = LMStudioAdapter(model="qwen3.5-0.8b", base_url="http://localhost:1234/v1")

prompt = (
    'You are a helpful assistant. Respond with JSON: '
    '{"tool": "think", "args": {}, "rationale": "processing", "expected_outcome": "ok"}'
)

with tempfile.TemporaryDirectory() as d:
    ledger = TokenLedger(Path(d) / "tokens.db")
    gw = OutputGateway(adapter, max_retries=1)
    gw.attach_ledger(ledger)

    print("--- Baseline: raw string prompts ---")
    for i in range(3):
        try:
            start = time.monotonic()
            result = gw.request_action(prompt, PROPOSAL_SCHEMA, tool_names=["think"])
            elapsed = time.monotonic() - start
            print(f"  Task {i+1}: tool={result.tool}, {elapsed:.2f}s")
        except Exception as e:
            print(f"  Task {i+1}: FAILED - {type(e).__name__}: {e}")
            break

    s = ledger.summary()
    print()
    print("--- Token Accounting (Baseline) ---")
    print(f"  Count: {s['count']}")
    print(f"  Total tokens: {s['total_tokens']}")
    print(f"  Effective tokens: {s['effective_tokens']}")
    print(f"  CPM (q=1.0): {s['cpm_with_quality_1p0']:.1f}")

    print()
    print("--- Harness: PromptZones prompts ---")
    pz = build_zoned_prompt(
        state=ConsciousnessState(),
        goal_text="respond with thinking tool",
        catalog_text="think",
    )
    print(f"  Stable zone: {len(pz.stable)} chars (cacheable)")
    print(f"  Volatile zone: {len(pz.volatile)} chars (reconstructed)")
    print(f"  Stable hash: {pz.stable_hash}")

    for i in range(3):
        try:
            start = time.monotonic()
            result = gw.request_action(pz, PROPOSAL_SCHEMA, tool_names=["think"])
            elapsed = time.monotonic() - start
            print(f"  Task {i+1}: tool={result.tool}, {elapsed:.2f}s")
        except Exception as e:
            print(f"  Task {i+1}: FAILED - {type(e).__name__}: {e}")
            break

    s2 = ledger.summary()
    print()
    print("--- Token Accounting (Harness) ---")
    print(f"  Count: {s2['count']}")
    print(f"  Total tokens: {s2['total_tokens']}")
    print(f"  Effective tokens: {s2['effective_tokens']}")
    print(f"  CPM (q=1.0): {s2['cpm_with_quality_1p0']:.1f}")

    print()
    print("=== Summary ===")
    baseline_count = s['count']
    harness_count = s2['count'] - s['count']
    baseline_tokens = s['effective_tokens']
    harness_tokens = s2['effective_tokens'] - s['effective_tokens']
    print(f"  Baseline: {baseline_count} tasks, {baseline_tokens} effective tokens")
    print(f"  Harness:  {harness_count} tasks, {harness_tokens} effective tokens")
    if harness_tokens > 0 and baseline_tokens > 0:
        ratio = harness_tokens / baseline_tokens
        print(f"  Token ratio (harness/baseline): {ratio:.2f}x")
    print()
    print("=== End ===")
