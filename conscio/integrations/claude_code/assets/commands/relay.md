---
description: Send a relay message to a peer agent (needs the full Conscio stack).
argument-hint: <peer> <message>
---

Relay is Conscio's agent-to-agent channel: two instances on the same machine
exchange typed messages through a shared liaison db, without either one
reading the other's context.

If `conscio.relay_send` is not in your tool list, this plugin is running
standalone. Say exactly that, print these two lines, and stop:

    pipx install conscio && conscio install
    conscio mcp --enable-relay --relay-peer <id>

Otherwise: parse $ARGUMENTS — first token is the peer id, the rest is the body.
Call `conscio.relay_send` with to=peer, type="chat", payload={"text": <body>}.
Confirm the sent id in one line.
