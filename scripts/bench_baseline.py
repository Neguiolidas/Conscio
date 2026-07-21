"""Baseline: raw Qwen 0.8B calls without Conscio pipeline."""
import json
import time
from conscio.agency.adapters import LMStudioAdapter

adapter = LMStudioAdapter(model="qwen3.5-0.8b", base_url="http://localhost:1234/v1")

tasks = [
    'Respond with JSON only: {"tool": "think", "args": {}, "rationale": "processing", "expected_outcome": "ok"}',
    'Respond with JSON only: {"tool": "read", "args": {"path": "/tmp/test"}, "rationale": "reading", "expected_outcome": "content"}',
    'Respond with JSON only: {"tool": "write", "args": {"path": "/tmp/out", "content": "hello"}, "rationale": "writing", "expected_outcome": "written"}',
    'Respond with JSON only: {"tool": "think", "args": {"thought": "analyzing"}, "rationale": "reflection", "expected_outcome": "insight"}',
    'Respond with JSON only: {"tool": "exec", "args": {"cmd": "ls"}, "rationale": "listing", "expected_outcome": "files"}',
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
        valid = False
        tool = "?"
        try:
            parsed = json.loads(result.text)
            valid = True
            tool = parsed.get("tool", "?")
        except Exception:
            tool = "INVALID"
        results.append({
            "task": i + 1,
            "latency_s": round(elapsed, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "valid_json": valid,
            "tool": tool,
        })
        print(f"  Task {i+1}: {elapsed:.2f}s in={tokens_in} out={tokens_out} tool={tool} valid={valid}")
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
print(f"Total latency: {report['total_latency_s']}s avg: {report['avg_latency_s']}s")

with open("/tmp/bench_baseline.json", "w") as f:
    json.dump(report, f, indent=2)
print("Saved to /tmp/bench_baseline.json")
