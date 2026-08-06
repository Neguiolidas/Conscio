---
description: Stop the Conscio Awake daemon for this host's space (needs the full Conscio stack).
---

If `command -v conscio` finds nothing, this plugin is running standalone: the
daemon is a separate long-lived process and needs the full stack. Say exactly
that, print this line, and stop:

    pipx install conscio && conscio install

Otherwise: stop the Awake daemon for this host's Conscio space (terminate the
`conscio daemon` process bound to the configured `--storage`). Confirm it
stopped in one line.
