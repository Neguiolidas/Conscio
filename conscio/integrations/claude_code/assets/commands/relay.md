---
description: Send a relay message to a peer agent.
argument-hint: <peer> <message>
---

Parse $ARGUMENTS: the first token is the peer id, the rest is the message body.
Use the `conscio.relay_send` MCP tool with `to`=peer, `type`="chat", and payload
`{"text": <message>}`. Confirm the sent id in one line.
