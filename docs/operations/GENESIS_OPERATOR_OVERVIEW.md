# Genesis operator quick reference

**Applies to:** validated Genesis deployment running under the designated operator account. Use commands from the deployed repository, not a developer feature worktree. This page is an operational entry point, not a commissioning or troubleshooting manual.

## Routine commands

Activate the installed `genesis-p1` environment, enter the deployed Genesis repository, and run `./scripts/genesisctl.sh <command>`.

| Need | Command | What it does |
| --- | --- | --- |
| See supported commands | `help` | Show subsystem commands and safety notes. |
| See general software processes | `status` | Read-only summary; not a complete health check for every subsystem. |
| Check master recording | `master-archive-status` | Report recorder state, snapshots, events and storage free space. |
| Begin recording (authorized operator) | `master-archive-start` | Start a detached read-only PV subscriber. Does **not** start instrument IOCs. |
| End recording (authorized operator) | `master-archive-stop` | Request clean recorder shutdown; verify `status` reports DOWN afterward. |
| Inspect historical data | `archive-viewer` | Start the local read-only Streamlit viewer in the foreground. |
| Check Cryo | `cryo-status` | Read-only Cryo subsystem status. |
| Check ssRF | `ssrf-physical-status` | Read-only ssRF status. |
| Check Microwave | `mw-status` | Read-only Microwave status. |

For the master recorder's status meanings, data locations, and escalation guidance, see [Master Archiver: Operator Guide](MASTER_ARCHIVER_OPERATOR.md).

## Operator boundaries

- Start and stop physical subsystem stacks only under the approved experiment procedure. The master recorder is **not** a replacement for subsystem IOCs, interlocks, or alarms.
- Do not use the developer worktrees for production operation, manually edit archive files, change EPICS addresses, override safety settings, or kill unknown processes.
- If a service unexpectedly stops, reports a recording error, or produces suspect readings, preserve the files and report the time and status to the Genesis integration lead. Operators are not expected to debug software.
- `archive-viewer` only displays recorded data. Close it with Ctrl+C in its own terminal; closing the viewer does not stop recording.

**Other subsystem procedures:** refer to their separately validated operating procedures; this overview does not authorize hardware operations.
