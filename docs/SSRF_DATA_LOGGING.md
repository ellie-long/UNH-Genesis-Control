# Genesis ssRF Data Logging

## Purpose

Genesis uses two complementary data-recording layers.

### 1. EPICS PV archive

The existing Genesis archiver remains responsible for time-series PV
telemetry.

It writes per-PV CSV files under:

    archive/data/

This layer is appropriate for values such as:

- hardware readbacks
- communication state
- RF/FM state
- switch state
- interlock state
- sequencer state
- scalar operating parameters

The existing PV archiver is not replaced by the ssRF event logger.

### 2. Structured ssRF event records

Genesis additionally provides:

    ssrf_analysis/event_logger.py

This layer records correlated subsystem events as append-only JSONL.

Default location:

    archive/events/ssrf/YYYY-MM-DD.jsonl

The archive directory is already runtime data and is excluded from Git.

## Event schema

Each event contains:

    schema_version
    event_id
    timestamp_utc
    subsystem
    session_id
    event_type
    source
    level
    data

Timestamps use UTC ISO-8601 format.

`session_id` provides the correlation key that later connects:

    before-NMR
        -> fit
        -> waveform
        -> staging
        -> burn
        -> after-NMR

## Event-data policy

Event JSONL files contain metadata and state transitions.

Large scientific arrays should not be duplicated into the event log.

For example, full:

- NMR traces
- fitted spectra
- 8192-point ARB waveforms

should remain in their appropriate data products.

The event record should instead contain information such as:

- material
- fit timestamp
- fit quality
- waveform case
- waveform point count
- polarization settings
- waveform ID
- external data-file path
- sequencer state
- fault/result state

## Safety boundary

The event logger:

- performs no EPICS reads
- performs no EPICS writes
- performs no hardware communication
- cannot LOAD a waveform
- cannot enable RF or FM
- cannot change switches
- cannot ARM
- cannot START a burn

Logging therefore remains observational metadata infrastructure.

## 5C.4A-1 validated state

Validated:

- append-only JSONL storage
- daily UTC event files
- subsystem-scoped session IDs
- schema versioning
- JSON-safe NumPy scalar conversion
- rejection of non-finite values
- rejection of unsupported arbitrary objects
- no EPICS or hardware dependency

Next:

1. connect fitter runtime events to the structured logger
2. define burn/session records
3. correlate later sequencer execution and fault events
4. expose structured data to the live visualization layer
