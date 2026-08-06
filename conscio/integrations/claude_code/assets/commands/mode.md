---
description: Read or change the Conscio tool surface (lite / balanced / ultra).
argument-hint: [lite|balanced|ultra]
---

Conscio serves three tool surfaces. `lite` (10 tools, flattened schemas) suits
small models; `balanced` (18) is the plugin default; `ultra` (35) exposes
everything. The choice is stored next to the space and survives restarts.

If $ARGUMENTS is empty, call `conscio.mode` with no arguments and report the
current mode and tool count in one line.

Otherwise call `conscio.mode` with set=<first token of $ARGUMENTS>. The tool
list refreshes without reconnecting. Confirm the new mode and count in one line.
