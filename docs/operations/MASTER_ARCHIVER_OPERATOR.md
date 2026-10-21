# Genesis Master Archiver — Operator Guide

**Purpose:** Continuously record configured Genesis scalar EPICS PVs without writing to instruments. The recorder operates independently of the Cryo, ssRF, Microwave, Magnet and NMR IOCs. **It does not start those IOCs or enable hardware output.**

**Applies to:** validated deployed release under the designated operator account, with the `genesis-p1` Conda environment active. Run the following commands from that deployed repository (not from a feature worktree). The laboratory's approved operating procedure determines when recording is enabled.

For the archive directory layout, file schemas, CSV column definitions, and quality interpretation, see [Master Archiver File Format](MASTER_ARCHIVER_FILE_FORMAT.md).

## Everyday commands

| Action | Command |
| --- | --- |
| See recorder health | `./scripts/genesisctl.sh master-archive-status` |
| Start the recorder | `./scripts/genesisctl.sh master-archive-start` |
| Request clean stop | `./scripts/genesisctl.sh master-archive-stop` |
| Open recorded-data viewer | `./scripts/genesisctl.sh archive-viewer` |
| See Genesis commands | `./scripts/genesisctl.sh help` |

- **Start:** expect `[UP] master recorder PID ...`. The recorder runs detached and continues after the SSH session closes. Starting it twice does not create another recorder.
- **Check:** `master-archive-status` prints `[UP]` and a JSON health report. `snapshots` should increase about once per second; `accepted_events` and `written_events` increase when EPICS subscriptions receive updates or disconnections. No live IOCs may mean zero events and `missing` PVs; this is not automatically an archiver failure.
- **Stop:** expect `[STOP REQUESTED]`. Wait a few seconds and check `master-archive-status` again until `[DOWN]`. It may briefly remain `[UP]` while files are closing. Stopping recording **does not stop subsystem IOCs**.
- **View:** open `http://127.0.0.1:8501` on the DAQ or use the approved SSH port-forwarding procedure from the operator workstation. In Streamlit choose **Recording source → Master snapshots**, then the session, date interval and PVs. Use **Subsystem PV CSV** for the separate per-PV archive. The viewer itself runs in the foreground and may be stopped with Ctrl+C without affecting recording.

## Read the status (not a debugging task)

| Field / indication | Operator meaning |
| --- | --- |
| `state: running`, `[UP]` | Recorder process exists and is reporting health. |
| `snapshots` | Count of synchronized wide-CSV rows; should continue increasing while running. |
| `configured_pvs` | Number of PV names in the release's registry; **not** the number currently connected. |
| `accepted_events` / `written_events` | Received / saved PV callbacks, including disconnect notifications; **not** a count of valid scientific readings. |
| `dropped_events` | Should remain **0**. A nonzero value requires escalation. |
| `overruns` | Snapshot timing misses; repeated or increasing overruns require escalation. |
| `free_bytes` | Available space on the data filesystem. Recorder has a default **2 GiB minimum-free-space shutdown threshold**. |
| `[DOWN]` | Recorder is not running; normal after an authorized stop, otherwise report it. |

In master CSVs, each PV's `quality` is authoritative: `valid` = recently received connected value; `missing` = no update yet; `disconnected` = IOC/PV connection lost; `stale` = connected value has aged past the recorder's threshold (60 s by default). **Never interpret a missing/disconnected/stale value as a measured zero.**

## Where the recordings go

All recording output is local and **outside Git**. With the standard installation, the root is:

`/home/<account>/UNH-Genesis-Control/local_data/`

The validated default on the current `utsav` DAQ deployment is `/home/utsav/UNH-Genesis-Control/local_data/`. Production under `sliferlab` uses its own account's root unless the deployment configures the absolute `GENESIS_DATA_ROOT` environment variable. The standard `UNH-Genesis-Control/repo` or `UNH-Genesis-Control/worktrees/<branch>` layout resolves to `UNH-Genesis-Control/local_data`; a flat clone in `~/UNH-Genesis-Control` instead resolves to the sibling `~/UNH-Genesis-Control-local_data` to keep files outside Git. Changing that root is an integrator task, not an operator action.

On first master-recorder startup, the software creates `archive/master`, `archive/subsystems/pv_csv`, `archive/events/ssrf`, `archive/sessions/ssrf`, `logs` and `runtime` automatically. Subsystem archivers and ssRF data writers also create their own output directories if run first. No operator `mkdir` command is necessary. These directories must be writable by the account running the recorder; `utsav` and `sliferlab` do not automatically share a recording root.

```text
local_data/
├── archive/
│   ├── master/
│   │   └── <UTC_START>_<GIT_SHA_PREFIX>_<PID>/
│   │       ├── session.json                    # session start / version / PV count
│   │       ├── .genesis_master.lock            # writer ownership (do not remove)
│   │       └── YYYY-MM-DD/                     # UTC day
│   │           ├── metadata.json               # PV registry and CSV schema
│   │           ├── genesis_master.csv          # all configured PVs, 1 row/second
│   │           └── genesis_master.jsonl        # received update/disconnect events
│   ├── subsystems/pv_csv/                       # separate legacy-style PV CSVs
│   ├── events/ssrf/                            # ssRF analysis event files
│   └── sessions/ssrf/                          # ssRF session products
├── logs/master-archive.log                     # recorder startup/errors
└── runtime/                                    # recorder status/ownership files
```

- **One master CSV per session per UTC date**, not a single ever-growing file for the experiment. It is a *wide* CSV: timestamp columns plus `value`, `quality`, `age_s`, `received_utc` and `source_utc` for every registered PV. The Streamlit master view lets you select a few PVs without loading the entire width.
- **JSONL is event-based.** `genesis_master.jsonl` may be absent when no PV events were received; that is expected. The wide CSV still records the `missing` quality state.
- **Provenance:** `session.json` contains the Git HEAD SHA, recording start time and registry/configuration information. Production runs must use a clean, validated release; a Git SHA alone cannot describe uncommitted developer changes.
- **File rotation:** UTC day folders are added as needed; restarting recording creates a new session folder. Older sessions are retained for inspection. Do not delete, move or edit live or historical recordings.

## If anything looks wrong

Operators are **not expected to debug or repair the archiver**. Use this simple response:

1. Capture the current `master-archive-status` text, local date/time, and the affected subsystem/PV. If the recorder is DOWN, report that; do not repeatedly restart it.
2. If values are `missing` or `disconnected`, first consult the normal subsystem status display; it may reflect a stopped IOC rather than a recorder issue. Do not start hardware IOCs just to make the archive green.
3. If the recorder reports an error, stops unexpectedly, stops advancing snapshots, has `dropped_events > 0`, or indicates low disk space, notify the Genesis integration lead/shift responsible person. Leave recordings and logs intact; do not clear locks, delete files, or adjust EPICS ports.

**Boundary:** this guide describes the master recorder and viewer only. It does not replace safety/interlock procedures, scientific data-quality review, subsystem commissioning instructions or backup/retention policy.
