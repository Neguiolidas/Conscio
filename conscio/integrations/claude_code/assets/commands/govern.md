---
description: Show or change the Conscio context governor (ceiling, baseline, capture health).
---

Run `conscio govern status` and report it plainly. Four things matter:

- **Ceiling** — the context window cap. `OFF` means no cap is applied. It is read
  from `.claude/settings.local.json` in the *current working directory*, so a
  ceiling set for a project reads as `OFF` anywhere else. If it says `OFF` and
  the user expected otherwise, check which directory you are in before
  concluding it was never set.
- **obs.db** — size of the observation store, followed by the space it actually
  lives in. This is the capture space from the installed hook binding, not the
  CLI's own storage.
- **Capture hook** — only printed when something is wrong. `BROKEN` means the
  hook records nothing; it fails open by design, so this line is the only signal
  that exists. Relay the repair command verbatim.
- **Baseline** — `none` means nothing is being measured against anything, so any
  saving figure is meaningless rather than zero.

If the user passed an action, run `conscio govern $ARGUMENTS` instead of
`status`: `report` (per-session cost table, `--all` for every session), `prefix`
(the window-sizing math and what each candidate window would cost), `on` (freeze
a baseline and apply a ceiling), `off` (revert).

`on` and `off` rewrite `.claude/settings.local.json`, so run them only when the
user asked for them in this turn — never as a follow-up to a status check.
Report the backup path the command prints.
