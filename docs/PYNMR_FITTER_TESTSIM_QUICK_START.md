# PyNMR TestSim -> Genesis online fitter quick start

## Purpose

This procedure reproduces the validated software-only PyNMR-to-Genesis online
fitter integration without starting the physical ssRF stack.

Validated data path:

    PyNMR TestSim
        |
        | baseline-subtracted NMR sweep (basesub)
        | TCP 127.0.0.1:8777
        v
    Genesis online fitter
        |
        +--> fit-result publisher 127.0.0.1:8778
        |
        +--> EPICS CA 127.0.0.1:5100
        +--> EPICS PVA 127.0.0.1:5101
                    |
                    v
            Genesis Phoebus NMR fitter screen

This workflow does not start the physical WaveGen, SMC100A, RF/NMR switches,
ssRF sequencer, Microwave subsystem, or Cryogenic subsystem.

## Validated source milestone

Genesis:

    repository: UNH-Genesis-Control
    branch: feature/pynmr-fitter-integration
    accepted SHA: 5ed67aa2b7a25a3c4ee94db2810496d071d862df
    tag: pynmr-fitter-integration-validated-2026-10-08

PyNMR:

    repository: jlab_pynmr
    branch: unh_dev/genesis_fitter_interface
    validated SHA: 656aa3558c5da9b2c394a364dde54dea620c4111

Ubuntu/WSL acceptance demonstrated:

- PyNMR TestSim streaming to Genesis TCP port 8777.
- Genesis fit-result publication on TCP port 8778.
- Fitter EPICS CA on port 5100.
- Fitter EPICS PVA on port 5101.
- ND3 single-component model.
- Equilibrium-mode processing.
- 512-point NMR sweeps.
- Approximately 32.30 to 33.10 MHz acquisition window.
- Finite processed-data and total-fit arrays.
- Live Phoebus fitter readbacks and spectrum visualization.
- No physical ssRF hardware started.

## Repository locations

Ubuntu/WSL validation locations:

    PyNMR:
        /home/utsav/jlab_pynmr

    Genesis:
        /home/utsav/UNH-Genesis-Control/repo

Development and source-control edits are performed on the Mac. Accepted source
is then deployed to Ubuntu/WSL by exact Git SHA before runtime validation.

## Conda environments

PyNMR and Genesis use separate conda environments:

    PyNMR:
        environment = pynmr

    Genesis:
        environment = genesis-p1

Changing the active conda environment in a shell does not alter an already
running process. The process retains the environment from which it was started.

Separate terminals are recommended to avoid launching a command with the wrong
Python environment.

## Canonical ports

| Function | Endpoint |
|---|---|
| PyNMR -> fitter input | 127.0.0.1:8777 |
| Fitter result publisher | 127.0.0.1:8778 |
| Fitter EPICS Channel Access | 127.0.0.1:5100 |
| Fitter EPICS PVA | 127.0.0.1:5101 |

The normal ssRF Phoebus profile includes the canonical fitter Channel Access
endpoint 127.0.0.1:5100.

Do not intentionally substitute the default CA port 5064 for this validated
standalone Genesis fitter workflow.

## 1. Verify PyNMR source

In the PyNMR terminal:

    cd "$HOME/jlab_pynmr"

    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate pynmr

    git branch --show-current
    git rev-parse HEAD
    git status --short

For the validated milestone expect:

    branch = unh_dev/genesis_fitter_interface
    SHA    = 656aa3558c5da9b2c394a364dde54dea620c4111

## 2. Start PyNMR TestSim

From the PyNMR repository:

    python pynmr_main.py -p TestSim

The TestSim profile enables the Genesis data streamer and uses the
baseline-subtracted PyNMR signal, basesub, for the Genesis fitter feed.

The physical Deuteron profile remains disabled for Genesis streaming until
physical NMR commissioning is completed.

## 3. Verify Genesis source

In the Genesis terminal:

    cd "$HOME/UNH-Genesis-Control/repo"

    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate genesis-p1

    git branch --show-current
    git rev-parse HEAD
    git status --short

The validated Genesis tag resolves to:

    5ed67aa2b7a25a3c4ee94db2810496d071d862df

## 4. Verify fitter ports are free

Before starting another fitter:

    ss -ltnup 2>/dev/null |
        grep -E ':(5100|5101|8777|8778)[[:space:]]' ||
        true

Do not start a second online_fitter.epics_run while an existing fitter owns the
input, publisher, CA, or PVA endpoints.

## 5. Start the Genesis fitter only

For the accepted ND3 TestSim equilibrium visualization:

    cd "$HOME/UNH-Genesis-Control/repo"

    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate genesis-p1

    env \
        EPICS_CAS_SERVER_PORT=5100 \
        EPICS_PVA_SERVER_PORT=5101 \
        EPICS_CA_MAX_ARRAY_BYTES=262144 \
        PYTHONPATH="$PWD" \
        nohup python -u -m online_fitter.epics_run \
            --material ND3 \
            --mode equilibrium \
            >/tmp/genesis_pynmr_fitter.log 2>&1 &

    echo $! > /tmp/genesis_pynmr_fitter.pid

The fitter now owns:

    8777  NMR sweep input
    8778  fit-result publisher
    5100  EPICS CA
    5101  EPICS PVA

For transport-only or baseline-path testing, --mode baseline may be used
instead of equilibrium.

## 6. Verify fitter startup

    ps -fp "$(cat /tmp/genesis_pynmr_fitter.pid)"

    ss -ltnup 2>/dev/null |
        grep -E ':(5100|5101|8777|8778)[[:space:]]' ||
        true

    tail -60 /tmp/genesis_pynmr_fitter.log

Expected fitter configuration for the accepted screenshot test:

    material = ND3
    mode     = equilibrium
    CA       = 5100
    PVA      = 5101
    input    = 8777
    publish  = 8778

## 7. Open the Genesis Phoebus fitter display

With the fitter running on the canonical ports:

    cd "$HOME/UNH-Genesis-Control/repo"

    source "$HOME/miniforge3/etc/profile.d/conda.sh"
    conda activate genesis-p1

    ./scripts/start_ssrf_phoebus.sh

Open the NMR Fitter sub-screen from the main ssRF screen.

The fitter screen reads the EPICS prefix:

    UNH:GEN:SSRF:FIT:*

Launching this GUI alone does not start the physical ssRF hardware stack.

## 8. Generate a TestSim event

In the PyNMR GUI:

1. Press Run.
2. Allow at least one complete TestSim NMR event.
3. Press Stop.

One operator Run period can produce more than one completed PyNMR event.

A successful Genesis update should show at least:

    FITTER_UP            = 1
    PUBLISHER_CONNECTED  = 1
    MATERIAL_RBV         = ND3
    NPTS                 = 512

and a fresh LAST_TS value.

For equilibrium mode, the accepted test also produced finite DATA and
TOTAL_FIT arrays.

## 9. Read fitter state directly

The PyNMR conda environment contains pyepics and can be used for this read-only
diagnostic:

    EPICS_CA_ADDR_LIST="127.0.0.1:5100" \
    EPICS_CA_AUTO_ADDR_LIST="NO" \
    "$HOME/miniforge3/envs/pynmr/bin/python" - <<'PY'
    import epics

    P = "UNH:GEN:SSRF:FIT:"

    for name in (
        "FITTER_UP",
        "PUBLISHER_CONNECTED",
        "MATERIAL_RBV",
        "MODE_RBV",
        "FIT_TYPE",
        "MODEL",
        "NPTS",
        "LAST_TS",
    ):
        print(f"{name:20s} = {epics.caget(P + name, timeout=2)!r}")
    PY

The Genesis fitter itself remains in the genesis-p1 environment. Using the
pynmr Python above is only a convenient read-only diagnostic.

## 10. Validated spectrum record

The accepted Ubuntu/WSL event recorded:

    FITTER_UP            = 1
    PUBLISHER_CONNECTED  = 1
    MATERIAL_RBV         = ND3
    MODE_RBV             = equilibrium
    FIT_TYPE             = equilibrium
    MODEL                = single
    NPTS                 = 512

    frequency window     = approximately 32.30 -> 33.10 MHz

Both DATA and TOTAL_FIT contained 512 finite values.

The processed spectral structure displayed in the Genesis Phoebus fitter screen
was visibly consistent with the NMR structure displayed by PyNMR.

The closest conceptual visual comparison is:

    PyNMR Fit Subtracted
            <->
    Genesis Processed NMR

They are not expected to be numerically identical because PyNMR and Genesis do
not apply exactly the same background and wing-processing sequence.

Quantitative polarization agreement was not part of this acceptance. Genesis
calibration constants must be independently accepted before fitted polarization
is interpreted as a calibrated physics result.

## 11. Plot-display status

For the accepted development screenshot, the Genesis fitter plot is bounded to
a deuteron display range that includes the validated 32.30 to 33.10 MHz TestSim
sweep.

This is a development display choice, not a general NMR acquisition rule.

Future viewer work should make plot-window handling follow the incoming sweep
cleanly and should remove the observed Phoebus waveform display artifact.

## 12. Clean shutdown

First stop acquisition in the PyNMR GUI.

Close the standalone ssRF Phoebus window if it was opened only for this test.

Then stop the Genesis fitter:

    if [[ -f /tmp/genesis_pynmr_fitter.pid ]]; then
        PID="$(cat /tmp/genesis_pynmr_fitter.pid)"
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
        fi
    fi

Verify the fitter endpoints are released:

    ss -ltnup 2>/dev/null |
        grep -E ':(5100|5101|8777|8778)[[:space:]]' ||
        true

Close PyNMR normally after acquisition is stopped.

Temporary validation files may be removed only after the associated processes
are confirmed stopped:

    /tmp/genesis_pynmr_fitter.pid
    /tmp/genesis_pynmr_fitter.log
    /tmp/genesis_ssrf_pynmr_test.ini
    /tmp/genesis_ssrf_pynmr_phoebus.log

Do not remove repository test_data/ as part of runtime cleanup.

## Known deferred work

The validated software milestone intentionally leaves the following work open:

- physical PyNMR/Q-meter/FPGA commissioning;
- quantitative calibration acceptance;
- PyNMR NoneType/event-file runtime error cleanup;
- PyNMR TE-tab empty-ydata callback cleanup;
- richer fitter/operator visualization;
- Phoebus waveform display artifact cleanup;
- dynamic plot-window handling;
- broader online history/trend viewer work;
- final physical PyNMR integration into the full ssRF stack.

These deferred items do not invalidate the accepted
PyNMR-TestSim-to-Genesis transport milestone.
