# Genesis Coding-Agent Instructions

## Project

Genesis is the UNH polarized-target control-system integration project.

The codebase integrates physical EPICS-controlled subsystems including:

- Microwave
- ssRF
- NMR
- Magnet
- later Cryogenic and supervisory systems

The current validated physical baseline is:

    mw-ssrf-phase1-physical-integration-2026-09-30

Do not modify or rewrite that Git tag.

## Safety invariants

Physical startup must always default to a safe state.

Never automatically enable:

- Microwave RF
- ssRF RF
- ssRF FM
- Keysight WaveGen output
- Robot motion

RF ON must require an explicit operator action.

Calibration validity is NOT an RF-enable requirement.

Calibration status may generate warnings or diagnostics, but it must not
silently prevent otherwise-valid operator RF control.

Hard safety conditions MAY block RF, including:

- failed required communications
- explicit hardware interlocks
- invalid operating configuration
- emergency stop / abort
- mutually exclusive hardware ownership

Do not remove, bypass, or weaken hard safety interlocks merely to make RF work.

Shutdown must command RF OFF and verify a safe state before terminating
physical device owners whenever the hardware interface permits verification.

Status/read-only commands must never change physical hardware state.

Simulation and physical profiles must remain explicitly separated.

Only one software owner may control a physical instrument at a time.

EPICS CA/PVA ports must remain unique across simultaneously running subsystems.

## Physical-control development

Do not perform physical hardware writes while implementing or analyzing code
unless the user explicitly requests a physical commissioning step.

For physical changes:

1. inspect the existing control path first;
2. identify command PVs and readback PVs;
3. identify hard interlocks separately from commissioning restrictions;
4. add or update regression tests;
5. validate software behavior without hardware first;
6. perform physical validation only as a distinct final step.

## Git workflow

Do not develop directly on main.

Use focused branches such as:

- fix/mw-clean-shutdown
- feature/ssrf-rf-operation
- feature/mw-rf-operation
- feature/mw-robot-joystick
- feature/ubuntu-deployment

Keep unrelated changes out of the same commit.

Do not amend or rewrite validated physical-baseline commits.

Before proposing a commit, run the relevant regression tests and:

    git diff --check

## Existing validated commands

Microwave complete subsystem:

    ./scripts/start_microwave_system.sh

Microwave hardware only:

    ./scripts/microwave_physical.sh start

Microwave GUI only:

    ./scripts/start_microwave_phoebus.sh

ssRF complete subsystem:

    ./scripts/start_ssrf_system.sh

ssRF hardware only:

    ./scripts/genesisctl.sh ssrf-start

ssRF GUI only:

    ./scripts/start_ssrf_phoebus.sh

Safe ssRF shutdown:

    ./scripts/genesisctl.sh ssrf-stop

Relevant regression commands include:

    ./scripts/genesisctl.sh test-mw
    ./scripts/genesisctl.sh test-ssrf
    ./scripts/genesisctl.sh test-fitter

## Engineering priorities

The current development sequence is:

1. deterministic Microwave shutdown;
2. normal operator ssRF RF-ON capability;
3. normal operator Microwave/Vaunix RF-ON capability;
4. Microwave robot joystick;
5. combined Mac physical validation;
6. freeze operational-RF baseline;
7. deploy that exact baseline to Ubuntu/WSL;
8. integrate PyNMR/NMR;
9. build unified Genesis supervisor;
10. later integrate ML control.

Preserve this sequencing unless explicitly instructed otherwise.
