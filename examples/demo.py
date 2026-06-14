#!/usr/bin/env python3
"""Quick CLI demo for the Conscio consciousness engine.

Usage:
    python examples/demo.py          # default model
    python examples/demo.py glm-5.1  # specify model
"""

import json
import sys
from conscio.engine import ConsciousnessEngine


def main():
    model = sys.argv[1] if len(sys.argv) > 1 else "glm-5.1"
    engine = ConsciousnessEngine(model_name=model)

    print("🧠 ConsciousnessEngine initialized")
    print(f"   Model:   {engine.model_info.name}")
    print(f"   Context: {engine.model_info.context_window // 1000}k")
    print(f"   Mode:    {engine.mode.value}")
    print(f"   Budget:  {engine.ctx.budget['total_max']} tokens")
    print()

    # Run a test reflection
    result = engine.reflect(
        world_state="Test initialization — all systems nominal",
        confidence=0.8,
    )

    print("📝 Reflection result:")
    print(result["summary"])
    print()
    print("💉 State injection preview:")
    print(engine.get_state_for_injection())
    print()
    print("📊 Full status:")
    print(json.dumps(engine.status(), indent=2))


if __name__ == "__main__":
    main()
