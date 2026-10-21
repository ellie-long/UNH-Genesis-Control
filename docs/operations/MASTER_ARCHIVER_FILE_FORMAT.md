# Genesis master archiver — files, columns, and data quality

**Applies to:** UNH-Genesis-Control, master archiver schema version 1.
**Reference deployment:** `sliferlab`, 2026-10-20, Git commit `7de6cec91cfd3f2ea6e922153068b12bb6f3cfd8`.
**Related guide:** [MASTER_ARCHIVER_OPERATOR.md](MASTER_ARCHIVER_OPERATOR.md) (startup, shutdown, health, and operating procedures).

## Purpose and scope

The master archiver is a **read-only EPICS PV recorder**. It combines configured Cryo, ssRF, and Microwave PVs into a synchronized, once-per-second snapshot CSV and a separate stream of received PV events. It does **not** start or stop subsystem IOCs or control physical equipment. A configured PV is a candidate for recording, **not proof that its EPICS record is connected or that the underlying scientific measurement is valid**.

For the October 20, 2026 release, the configuration contains **369 PVs**: Cryo **168**, ssRF **97**, Microwave **104**. The source list is maintained in `archive/master_sources.yaml`; the registry is built by `archive/master_registry.py`. Other subsystems, including standalone Magnet and NMR sources, must be explicitly registered before assuming they are in the master archive. Counts may change in later releases.

## Data directory and session layout

All data are stored **outside Git**. On the validated `sliferlab` flat checkout, the data root is:

```text
/home/sliferlab/UNH-Genesis-Control-local_data/
```

The location can differ by account and checkout layout, or if `GENESIS_DATA_ROOT` is set. For details, see `archive/data_paths.py` and the operator guide. The default organization is:

```text
local_data/
├── archive/
│   ├── master/
│   │   └── <UTC_START>_<GIT_SHA_PREFIX>_<PID>/
│   │       ├── session.json
│   │       ├── .genesis_master.lock                 # live writer ownership; do not remove
│   │       └── YYYY-MM-DD/                          # UTC date
│   │           ├── metadata.json                    # PV registry and snapshot metadata
│   │           ├── genesis_master.csv              # time-aligned snapshots
│   │           └── genesis_master.jsonl            # received events; may be absent
│   ├── subsystems/pv_csv/                           # independent per-PV CSVs
│   ├── events/ssrf/
│   └── sessions/ssrf/
├── logs/master-archive.log
└── runtime/
```

One **master session directory** is created per recorder start. Within it, files are grouped by **UTC calendar day**. Restarting the recorder creates a new session. Old sessions must not be overwritten, edited, or deleted during routine use.

**Example observed session:**

```text
archive/master/20261020T193406Z_7de6cec91cfd_201043/
```

It started at **2026-10-20 19:34:06 UTC** (15:34:06 EDT). Its last observed CSV write was around **15:46:30 EDT**, after the scheduled Cryo shutdown. The latest row consequently contained `disconnected` quality for stopped Cryo IOCs; that does not invalidate earlier rows.

## `genesis_master.csv`: one row per snapshot

The master recorder writes a wide CSV with **two shared timestamp columns** and **five columns per registered PV**. For 369 PVs, that is `2 + 5 × 369 = 1,847` columns.

| Column | Meaning | Interpretation |
| --- | --- | --- |
| `timestamp_utc` | UTC time of the synchronized snapshot. | Use for cross-system time alignment. |
| `timestamp_local` | Same snapshot time in the configured local timezone. | The observed metadata declares `America/New_York`; the CSV includes an offset. |
| `<PV>|value` | Latest value available to the recorder for this EPICS PV at snapshot time. | Can be numeric or text. An empty cell is **not zero**. |
| `<PV>|quality` | Connection/freshness classification of the value. | See quality table below; this is distinct from scientific validity. |
| `<PV>|age_s` | Seconds since the most recent PV update/event represented by the snapshot. | May reflect a disconnect event; low age does **not** guarantee a valid measurement. |
| `<PV>|received_utc` | UTC time when the recorder received the PV update/event. | Recorder receipt time, not necessarily instrument sampling time. |
| `<PV>|source_utc` | UTC timestamp associated with the upstream PV update. | Useful for latency comparisons, but not necessarily the underlying instrument acquisition time. |

`<PV>` is the full EPICS PV name, for example `UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM`. The five columns for this signal are:

```text
UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM|value
UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM|quality
UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM|age_s
UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM|received_utc
UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM|source_utc
```

The once-per-second snapshot **does not mean every instrument is sampled every second**. Each row contains the latest known value for each PV. Updates arrive asynchronously through EPICS subscriptions and may be less frequent, unchanged, or disconnected.

### Quality values

| `quality` | Meaning | Analysis treatment |
| --- | --- | --- |
| `valid` | Connected and updated within the configured freshness window. | A necessary freshness criterion, **not by itself** proof of scientifically valid data. |
| `stale` | Connected value or status has not been updated recently (default threshold: 60 seconds). | Avoid using as a fresh measurement. An unchanged status such as `OK` can become stale. |
| `missing` | No usable update has yet been received for that PV in the session. | Treat value as unavailable. |
| `disconnected` | EPICS connection to that PV is lost or closed. | Treat value as unavailable; shutdown commonly causes this. |

**Scientific validity is separate from archive quality.** A sensor can be connected while its driver reports `PARSE_ERR`, an empty value, or nonfinite `NaN`. For numeric analysis, check both `quality == "valid"` **and** that the value converts to a finite number. Also inspect related device status/communication PVs and calibration before accepting a measurement.

### Real examples from the Cryo acceptance run

- Right flowmeter `RPump_SLM`: approximately `-0.3944` SLM, `quality=valid` in a live snapshot. The same numerical value was observed in the Cryo per-PV CSV.
- Fridge `F1_TI`: approximately `298.76` K, `quality=valid` in a live snapshot, also present in the subsystem CSV.
- Low manometer `Mano_Status`: `PARSE_ERR` was recorded. `Mano_PI` was `nan` in the subsystem CSV and blank/unavailable in master snapshots; this is **not** a usable pressure result.
- Final row after shutdown: some PVs had blank `value` and `quality=disconnected`. Inspect pre-shutdown rows, not only the last row, when checking whether data were recorded.

## `metadata.json`: schema and PV catalog

Stored in each UTC-day directory alongside the master CSV. The observed schema has:

| Field | Meaning |
| --- | --- |
| `schema_version` | Format version (observed `1`). |
| `timezone` | Local timezone identifier (observed `America/New_York`). |
| `timestamp_standard` | Timestamp reference standard (observed `UTC`). |
| `snapshot_interval_s` | Planned interval between snapshot rows (observed `1.0`). |
| `pv_registry` | Mapping/catalog of the PVs recorded, including configured source/ownership metadata. This registry is configuration-derived, not a list of verified live connections. |

Use `pv_registry` to associate a long PV column name with its subsystem and source configuration. Refer to the actual JSON keys in the release being analyzed; do not assume every entry contains engineering units, physical calibration, or live connection status.

## `session.json`: provenance of an archive run

Stored at the session root. The October 20 session included:

| Field | Meaning |
| --- | --- |
| `schema_version` | Session metadata schema version. |
| `started_utc` | Start time of the recorder session in UTC. |
| `git_sha` | Exact Genesis software commit used (observed `7de6cec91cfd3f2ea6e922153068b12bb6f3cfd8`). |
| `git_branch` | Checked-out branch at recorder start (observed `main`). |
| `git_dirty` | Whether working files differed from that commit at session start (observed `false`). |
| `host` | Computer hostname (observed `DESKTOP-M34HHNQ`). |
| `snapshot_interval_s` | Intended synchronized recording interval. |
| `pv_count` | Number of configured master PVs (observed `369`). |
| `master_sources_sha256` | SHA-256 fingerprint of the source manifest/configuration used for the registry. |
| `ca_addresses` | EPICS Channel Access addresses configured for subscription; these are **not** a guarantee every PV connected. |

Preserve this file whenever exporting data so that later analyses can identify the code and registry configuration that generated it.

## `genesis_master.jsonl`: update-level events

This newline-delimited JSON file stores received EPICS update and disconnect events, where available. Each line is one JSON object. The precise event keys are defined by the recorder implementation for that release; inspect a representative line before writing analysis code. Unlike the master CSV, it is **event-driven, not one row per second**, and it can be absent when no events were received. Do not infer zero events simply because a snapshot CSV exists.

## Cryo subsystem per-PV CSV files

The **separate Cryo subsystem archiver** writes individual PV files under `archive/subsystems/pv_csv/`. An observed example:

```text
UNH_GEN_CRYO_FLOW_RIGHT_RPump_SLM_2026-10-20.csv
```

Its observed columns were:

| Column | Meaning |
| --- | --- |
| `Timestamp` | Timestamp written by the subsystem recorder (the observed files use local wall-clock time without an embedded offset). |
| `Value` | Recorded PV value, such as a flow rate, temperature, status string, or literal `nan`. |

The subsystem CSV is **not** the same format as the master snapshot CSV. It has no built-in `quality`, `age_s`, or `received_utc` columns. Logging occurs on the subsystem archiver's own update/deadband/interval policy, so do not assume a complete one-second row sequence for every PV. The master archive adds snapshot time alignment and explicit quality classifications.

On the verified 2026-10-20 release, the Cryo subsystem recorder configured **168 PVs**, and **168 per-PV CSV files** were observed. Those file counts show recording coverage, **not** that every instrument returned a scientifically valid reading.

## Safe read-only inspection

From the relevant Genesis repo checkout, set the session path to the one you want to study. This example shows the first three registry entries and the final **valid finite** numeric reading before shutdown for two signals:

```bash
python - <<'PY'
from pathlib import Path
import csv, json, math

session = Path(
    '/home/sliferlab/UNH-Genesis-Control-local_data/archive/master/'
    '20261020T193406Z_7de6cec91cfd_201043'
)
day = session / '2026-10-20'
meta = json.loads((day / 'metadata.json').read_text())
run = json.loads((session / 'session.json').read_text())
print('Git commit:', run['git_sha'])
print('Configured PV count:', run['pv_count'])
print('Timezone:', meta['timezone'])
print('First three registry names:', list(meta['pv_registry'])[:3])

wanted = [
    'UNH:GEN:CRYO:FRIDGE:F1_TI',
    'UNH:GEN:CRYO:FLOW:RIGHT:RPump_SLM',
]
last_valid = {}
with (day / 'genesis_master.csv').open(newline='') as f:
    for row in csv.DictReader(f):
        for pv in wanted:
            if row.get(pv + '|quality') != 'valid':
                continue
            try:
                value = float(row.get(pv + '|value', ''))
            except (ValueError, TypeError):
                continue
            if math.isfinite(value):
                last_valid[pv] = (row['timestamp_local'], value)
for pv in wanted:
    print(pv, last_valid.get(pv, 'no valid finite reading'))
PY
```

Use the operator guide's `master-archive-status` command to verify recorder health. Do not modify archived CSV/JSONL files, session locks, or hardware state while inspecting data.

## Complete PV catalog

For the meanings and engineering units of all 369 registered signals, see [Master Archiver PV Catalog](MASTER_ARCHIVER_PV_CATALOG.md).
