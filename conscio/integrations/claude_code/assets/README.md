# Conscio

Persistent memory, reflection and an agent society for Claude Code.

Conscio gives a session a past. It remembers what you did across restarts, lets
Claude search its own history instead of guessing, and — when you want it — puts
several agents in the same room to review each other's work.

## Install

```
/plugin marketplace add Neguiolidas/Conscio
/plugin install conscio@conscio
```

The MCP server is fetched with `uvx` at an exact version, so the tools Claude
sees always match the assets this plugin shipped.

## Modes

The tool surface is sized on purpose: a small model drowns in 35 tools, and a
large one is wasted on 10. Switch at any time with `/conscio:mode`, or ask for
`conscio.mode` directly.

| Mode | Tools | For |
|------|-------|-----|
| `lite` | 10 | small or local models — memory, recall, state, and the way back out |
| `balanced` | 18 | the default: memory, reflection, structure, governance |
| `ultra` | 35 | everything, including the society, relay and act surfaces |

The plugin installs in `balanced`. A mode change persists to the space, so the
next session starts where you left it, and Claude is told the tool list changed
without needing a restart.

## Commands

| Command | What it does |
|---------|--------------|
| `/conscio:remember` | store a fact durably, with a tag you can recall by |
| `/conscio:recall` | search everything remembered, not just this session |
| `/conscio:state` | current cognitive state — pressure, drives, budget |
| `/conscio:reflect` | look back over recent work and draw conclusions |
| `/conscio:awake` | start the daemon that keeps perceiving between turns |
| `/conscio:sleep` | stop it, consolidating what it learned |
| `/conscio:handoff` | write a handoff for the next session or agent |
| `/conscio:govern` | inspect and tune the context governor |
| `/conscio:propose` | propose an action for approval instead of taking it |
| `/conscio:society` | see the other agents and what they are working on |
| `/conscio:relay` | send work to another agent and wait for the verdict |
| `/conscio:mode` | read or switch the tool surface |
| `/conscio:capture` | turn the session recorder on or off |
| `/conscio:backup` | copy the space somewhere you control |

## What gets recorded

Hooks record every tool call of the session — the command and its result — into
a local database, so that after a compaction Claude can recover the detail it
would otherwise have lost. Nothing leaves your machine.

To stop recording:

```
/conscio:capture off
```

It stays off until you turn it back on. Already-recorded observations are kept;
capture off only stops new ones.

## Where your data lives

Everything is written under the plugin's own data directory
(`${CLAUDE_PLUGIN_DATA}`): the memory space, the observation database, and the
mode you chose.

Uninstalling the plugin can remove that directory. `/conscio:backup` is the way
to keep a copy — run it before uninstalling or before moving machines.

## Requirements

Python 3.10+ and `uvx` on PATH.

## License

AGPL-3.0-or-later. Source: https://github.com/Neguiolidas/Conscio
