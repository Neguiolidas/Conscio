---
description: Turn Conscio's observation capture on or off for this space.
argument-hint: on|off
---

The DeepMiner hook records every tool call verbatim so `conscio_recall_observations`
can reach them after compaction. Capture is on by default and is switched by the
presence of a single file in the space.

The space is the `--storage` path the conscio MCP server was started with; when
this plugin is installed it is `${CLAUDE_PLUGIN_DATA}/space`.

For `off`: create the file `capture-off` inside the space. For `on`: delete it.
Then say which state capture is now in, and note that the change takes effect on
the next tool call. Do not delete anything already recorded.
