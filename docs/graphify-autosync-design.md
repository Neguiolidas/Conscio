# Graphify Auto-Sync — Design Notes for v1.5 Live

## Viability: CONFIRMED ✅

Graphify supports three auto-rebuild mechanisms:
1. `graphify ./src --watch` — instant AST rebuild on code save
2. `graphify hook install` — post-commit git hook (no daemon)
3. `graphify ./src --update` — incremental re-extraction of changed files

## Recommended Integration Pattern

### Layer 1: Graphify handles rebuilds (external)
```bash
# Option A: Git hook (lightweight, no daemon)
cd /path/to/conscio && graphify hook install

# Option B: Watch mode (real-time, during dev sessions)
graphify ./conscio --watch --no-viz
```

### Layer 2: Conscio detects graph changes (internal)

Add to `graphify_bridge.py`:
- `_graph_hash()` — SHA256 of graph.json
- `_last_indexed_hash()` — stored in ContentStore metadata
- `auto_index_if_changed()` — only re-indexes when hash differs

### Layer 3: Inner Monologue / Sensor trigger (v1.5)

In the SensorAdapter pipeline (v1.5 Live):
```python
class GraphifySensor:
    """Watches graphify-out/ for changes and triggers re-index."""
    
    def poll(self) -> bool:
        current = self._graph_hash()
        if current != self._last:
            self._last = current
            return True  # triggers re-index
        return False
```

### Integration with reflect() cycle

After `engine.reflect()` detects new files (via ContentLayer or
AutoEvolution proposals), check if graphify-out changed and re-index.
This is NOT blocking — runs in background after reflect completes.

## Key Insight

Graphify and Conscio are complementary but independent:
- Graphify = codebase structure (AST, relationships, communities)
- Conscio = runtime consciousness (reflections, events, goals)
- The bridge connects them without coupling

## Blockers for v1.5

- SensorAdapter not yet implemented (v1.5 Live phase)
- Daemon/watch mode not yet implemented
- No ContentStore metadata for tracking index hashes yet

## Recommendation for Claude

When implementing v1.5 Live:
1. Add `_graph_hash` tracking to GraphifyBridge
2. Create GraphifySensor as a SensorAdapter implementation
3. Wire into the daemon's poll cycle
4. Document `graphify hook install` as the recommended setup
