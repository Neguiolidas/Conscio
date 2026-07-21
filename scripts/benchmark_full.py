#!/usr/bin/env python3
"""Full benchmark: Qwen 0.8B raw vs Qwen 0.8B through Conscio.

Baseline: raw adapter.generate() calls — no Conscio pipeline.
Conscio: full conscio-bench with agency pipeline (gateway, skeptic, breaker, audit).
"""
import json
import tempfile
from pathlib import Path

# We'll use subprocess to call conscio-bench with the right adapter
import subprocess

print("=== Full Benchmark: Qwen3.5-0.8B ===")
print()

# ─── BASELINE: raw adapter calls ───────────────────────────────────────
print("--- Baseline: raw adapter.generate() ---")

baseline_code = '''
import json, time, sys
from conscio.agency.adapters import LMStudioAdapter

adapter = LMStudioAdapter(model="qwen3.5-0.8b", base_url="http://localhost:1234/v1")

# 5 tasks: simple JSON generation
tasks = [
    "Respond with JSON: {\"tool\": \"think\", \"args\": {}, \"rationale\": \"processing\", \"expected_outcome\": \"ok\"}",
    "Respond with JSON: {\"tool\": \"read\", \"args\": {\"path\": \"/tmp/test\"}, \"rationale\": \"reading file\", \"expected_outcome\": \"content\"}",
    "Respond with JSON: {\"tool\": \"write\", \"args\": {\"path\": \"/tmp/out\", \"content\": \"hello\"}, \"rationale\": \"writing file\", \"expected_outcome\": \"written\"}",
    "Respond with JSON: {\"tool\": \"think\", \"args\": {\"thought\": \"analyzing\"}, \"rationale\": \"reflection\", \"expected_outcome\": \"insight\"}",
    "Respond with JSON: {\"tool\": \"exec\", \"args\": {\"cmd\": \"ls\"}, \"rationale\": \"listing\", \"expected_outcome\": \"files\"}",
]

results = []
total_tokens_in = 0
total_tokens_out = 0
total_latency = 0

for i, task in enumerate(tasks):
    start = time.monotonic()
    try:
        result = adapter.generate(task, max_tokens=200, temperature=0.2)
        elapsed = time.monotonic() - start
        tokens_in = result.tokens_in
        tokens_out = result.tokens_out
        total_tokens_in += tokens_in
        total_tokens_out += tokens_out
        total_latency += elapsed
        results.append({
            "task": i + 1,
            "latency_s": round(elapsed, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "valid_json": False,
        })
        try:
            parsed = json.loads(result.text)
            results[-1]["valid_json"] = True
            results[-1]["tool"] = parsed.get("tool", "?")
        except:
            results[-1]["tool"] = "INVALID"
            results[-1]["raw"] = result.text[:100]
        print(f"  Task {i+1}: {elapsed:.2f}s, in={tokens_in}, out={tokens_out}, tool={results[-1]['tool']}")
    except Exception as e:
        elapsed = time.monotonic() - start
        total_latency += elapsed
        results.append({"task": i + 1, "latency_s": round(elapsed, 2), "error": str(e)[:100]})
        print(f"  Task {i+1}: FAILED - {e}")

report = {
    "tasks": results,
    "total_latency_s": round(total_latency, 2),
    "total_tokens_in": total_tokens_in,
    "total_tokens_out": total_tokens_out,
    "total_tokens": total_tokens_in + total_tokens_out,
    "avg_latency_s": round(total_latency / len(tasks), 2),
    "valid_json_count": sum(1 for r in results if r.get("valid_json")),
    "total_tasks": len(tasks),
}
print()
print(f"Baseline: {report['valid_json_count']}/{report['total_tasks']} valid JSON")
print(f"Total tokens: {report['total_tokens']} (in={total_tokens_in}, out={total_tokens_out})")
print(f"Total latency: {report['total_latency_s']}s, avg: {report['avg_latency_s']}s")

# Save for comparison
with open("/tmp/bench_baseline.json", "w") as f:
    json.dump(report, f, indent=2)
'''

r1 = subprocess.run(["python3", "-c", baseline_code],
                    capture_output=True, text=True,
                    cwd="/home/ubuntu/clawd/Repos/Conscio",
                    timeout=600)
print(r1.stdout)
if r1.stderr:
    print("BASELINE ERR:", r1.stderr[:300])

# ─── CONSCIO: full bench ────────────────────────────────────────────────
print()
print("--- Conscio: full agency pipeline ---")

with tempfile.TemporaryDirectory() as workdir:
    r2 = subprocess.run([
        "python3", "-m", "conscio.bench",
        "--adapter", "lmstudio:qwen3.5-0.8b@http://localhost:1234/v1",
        "--cycles", "5",
        "--workdir", workdir,
        "--json", "/tmp/bench_conscio.json",
    ], capture_output=True, text=True,
       cwd="/home/ubuntu/clawd/Repos/Conscio",
       timeout=600)
    print(r2.stdout)
    if r2.stderr:
        print("CONSCIO ERR:", r2.stderr[:500])

# ─── COMPARISON ─────────────────────────────────────────────────────────
print()
print("=== COMPARISON ===")

try:
    with open("/tmp/bench_baseline.json") as f:
        baseline = json.load(f)
    with open("/tmp/bench_conscio.json") as f:
        conscio = json.load(f)

    print(f"{'Metric':<25} {'Baseline':>15} {'Conscio':>15} {'Delta':>15}")
    print("-" * 72)

    b_tokens = baseline["total_tokens"]
    c_tokens = conscio.get("total_tokens", 0) or sum(
        c.get("tokens", {}).get("total", 0) for c in conscio.get("cycles", [])
    )

    b_lat = baseline["total_latency_s"]
    c_lat = conscio.get("total_latency_s", 0) or sum(
        c.get("latency_ms", 0) for c in conscio.get("cycles", [])
    ) / 1000

    b_valid = baseline["valid_json_count"]
    b_total = baseline["total_tasks"]
    c_total = conscio.get("cycles_completed", len(conscio.get("cycles", [])))
    c_valid = sum(1 for c in conscio.get("cycles", []) if c.get("status") == "ok")

    print(f"{'Tasks completed':<25} {b_total:>15} {c_total:>15}")
    print(f"{'Valid JSON':<25} {b_valid:>15} {c_valid:>15}")
    print(f"{'Total tokens':<25} {b_tokens:>15} {c_tokens:>15}")
    if b_tokens > 0 and c_tokens > 0:
        print(f"{'Token ratio':<25} {'1.00x':>15} {c_tokens/b_tokens:>14.2f}x")
    print(f"{'Total latency (s)':<25} {b_lat:>15.2f} {c_lat:>15.2f}")

    # Token accounting from ledger
    try:
        from conscio.token_account import TokenLedger
        ledger = TokenLedger(Path(workdir) / "token_ledger.db")
        s = ledger.summary()
        print(f"{'Effective tokens':<25} {'N/A':>15} {s['effective_tokens']:>15}")
        print(f"{'CPM (q=1.0)':<25} {'N/A':>15} {s['cpm_with_quality_1p0']:>15.1f}")
    except Exception:
        pass

except Exception as e:
    print(f"Comparison error: {e}")

print()
print("=== Bench Complete ===")
