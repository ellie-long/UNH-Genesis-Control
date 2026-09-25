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

## 5C.4A-2 fitter-runtime event integration

The one-shot fitter runtime now writes structured subsystem events using one
shared `session_id`.

Successful dry-run sequence:

    session_started
        -> fit_received
        -> waveform_planned
        -> dry_run_completed
        -> session_completed

Controlled staging uses:

    session_started
        -> fit_received
        -> waveform_planned
        -> waveform_staged
        -> session_completed

Failures produce:

    session_failed

The event runtime was validated using a real TCP/NDJSON fitter message on:

    127.0.0.1:8778

A single calibrated ND3 fitter message produced one five-event correlated
session and recorded an 8192-point adaptive waveform.

No IOC was required for the dry-run validation and no EPICS write occurred.

Event logging remains metadata-only. Large NMR and waveform arrays are not
duplicated into JSONL.

Next:

1. derive a compact session/burn record from the append-only event stream
2. add before-NMR and after-NMR correlation fields
3. later attach sequencer execution and fault events
4. expose the correlated records to visualization

## 5C.4A-3 correlated session record

Genesis provides:

    ssrf_analysis/session_record.py

The append-only JSONL event stream remains the detailed subsystem history.

`SessionRecord` derives one compact correlated view from that event stream.

The record contains:

    before_nmr
    fit
    waveform
    staging
    execution
    fault
    after_nmr

along with:

    subsystem
    session_id
    status
    started_utc
    completed_utc
    event_count
    event_ids

The current software-only fitter workflow produces:

    fit
    waveform
    staging

while:

    before_nmr
    execution
    fault
    after_nmr

may remain empty.

A future full physical session can therefore use the same correlation model:

    before NMR
        -> fitter result
        -> waveform plan
        -> waveform stage/load
        -> burn execution
        -> execution result/fault
        -> after NMR

The append-only event stream remains authoritative; the session record is a
derived summary for analysis, visualization, and later run records.

Validation includes:

- current five-event fitter dry-run session
- future complete burn-session representation
- failed-session/fault representation
- duplicate event-ID rejection
- mixed-session rejection
- multi-session grouping
- no EPICS or hardware dependency

Next:

1. persist larger NMR/fit/waveform arrays as session data products
2. reference those products from event metadata
3. visualize session data and event state together

## 5C.4A-4A scientific session data products

Genesis provides:

    ssrf_analysis/session_data.py

The structured JSONL event layer remains metadata-oriented.

Larger scientific arrays are stored separately as numeric NPZ session
products under:

    archive/sessions/ssrf/<session_id>/

Possible products include:

    fit_data.npz
    waveform_data.npz

`fit_data.npz` may contain:

    freqs_mhz
    data
    total_fit
    I_plus_primary
    I_minus_primary
    I_plus_secondary
    I_minus_secondary
    raw

`waveform_data.npz` contains:

    f_peak_mhz
    f_shoulder_mhz
    peak_weights
    shoulder_weights
    control_time_s
    control_offset_mhz
    normalized_samples
    physical_voltage_v

The adaptive waveform data product is required to contain exactly:

    8192 samples

Session IDs are validated before filesystem use.

NPZ files are read with:

    allow_pickle = False

Object arrays and non-finite numeric values are rejected.

Processed/fitted frequency-domain arrays must match the frequency-axis
length whenever both are present.

The raw NMR array is allowed to have a different representation and length.

This layer performs no EPICS or hardware access.

Next:

1. connect fitter runtime sessions to NPZ persistence
2. reference NPZ paths from structured JSONL events
3. use both layers in the live visualization framework
