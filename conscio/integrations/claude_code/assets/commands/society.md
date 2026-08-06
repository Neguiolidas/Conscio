---
description: Show the Conscio society — peer agents + published skills/records (needs the full Conscio stack).
---

The society is the set of Conscio instances on this machine that publish proven
skills and behavioral records to each other. It lives in files outside this
plugin, so it is read through the CLI, not through a tool.

If `command -v conscio` finds nothing, this plugin is running standalone and
there is no society to show. Say exactly that, print this line, and stop:

    pipx install conscio && conscio install

Otherwise: run `conscio noosphere list --catalog` for the published skills and
`conscio noosphere audit` for the peers' behavioral records. Report the peer
agents and the counts in a few lines. Keep it concise.
