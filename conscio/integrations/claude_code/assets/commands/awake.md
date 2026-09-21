---
description: Start the Conscio Awake daemon for this host's space (needs the full Conscio stack).
---

Awake is proactive cognition: a long-lived process that keeps thinking between
your turns. It cannot live inside the plugin — it outlives the session.

If `command -v conscio` finds nothing, this plugin is running standalone: the
daemon is a separate long-lived process and needs the full stack. Say exactly
that, print this line, and stop:

    pipx install conscio && conscio install

Otherwise: run `conscio daemon --awake`. It resolves this host's live space on
its own — from the directory card the running agent publishes — so no path needs
to be passed here. Add `--storage <path>` only if the user names a different
space. Report the resulting PID. Awake enables proactive cognition; only run it
intentionally.
