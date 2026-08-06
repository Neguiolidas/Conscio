---
description: Copy this plugin's Conscio data somewhere it will survive uninstall.
argument-hint: <destination directory>
---

This plugin keeps its space and observation store under `${CLAUDE_PLUGIN_DATA}`,
which the host is free to delete when the plugin is uninstalled or updated.
Anything remembered lives there.

Parse $ARGUMENTS as a destination directory (default: `~/conscio-backup`).
Copy the whole plugin data directory into it, preserving structure, without
deleting the original. Report the destination path and the total size in one
line. If the directory does not exist yet, say so — there is nothing to back up.
