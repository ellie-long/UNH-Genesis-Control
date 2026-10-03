# Genesis Coding-Agent Instructions

## Project

Genesis is the UNH polarized-target control-system integration project.

The codebase integrates physical EPICS-controlled subsystems including:

- Microwave
- ssRF
- NMR
- Magnet
- later Cryogenic and supervisory systems

Genesis preserves validated physical milestones as immutable historical
references. In particular, the following validated simultaneous Microwave
and ssRF integration tag must never be moved or rewritten:

    mw-ssrf-phase1-physical-integration-2026-09-30

Later development, cleanup, or deployment commits do not retroactively change
what a validated tag represents. Do not describe an unvalidated development
commit as a physical baseline. Safety-critical changes require explicit
physical revalidation before promotion to a new accepted physical release.

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

Use a focused branch for one bounded task. Appropriate branch namespaces
include `fix/`, `feature/`, `integration/`, and `cleanup/`.

Keep unrelated changes out of the same commit.

Do not amend or rewrite validated physical-baseline commits.

Before proposing a commit, run the relevant regression tests and:

    git diff --check

## Canonical operator commands

Prefer the explicit `genesisctl` subsystem interface for safety-sensitive
physical operation.

Microwave:

    ./scripts/genesisctl.sh mw-start
    ./scripts/genesisctl.sh mw-status
    ./scripts/genesisctl.sh mw-rf-ready
    ./scripts/genesisctl.sh mw-stop

ssRF:

    ./scripts/genesisctl.sh ssrf-physical-status
    ./scripts/genesisctl.sh ssrf-deploy-keysight
    ./scripts/genesisctl.sh ssrf-start
    ./scripts/genesisctl.sh ssrf-stop

Relevant regression commands include:

    ./scripts/genesisctl.sh test-mw
    ./scripts/genesisctl.sh test-ssrf
    ./scripts/genesisctl.sh test-fitter
    ./scripts/genesisctl.sh test-auth

Subsystem system and GUI launchers may remain available for supported
workflows, but they do not replace the canonical explicit physical command
boundaries documented by `genesisctl`.

## Integration roadmap

Keep the following ordering principles:

1. keep the repository self-contained and portable across supported hosts;
2. validate development deployments before promoting them to production;
3. physically revalidate safety-critical changes before creating an accepted
   physical release tag;
4. integrate NMR/PyNMR through defined Genesis EPICS ownership boundaries;
5. integrate the Magnet subsystem using the same ownership and safety model;
6. build the unified Genesis supervisor only after subsystem contracts are
   stable;
7. add ML supervisory or feedback control only after deterministic control,
   safety, ownership, and fail-safe behavior are established.

Development HEAD is not automatically a production baseline. Production
deployment should use an explicitly accepted and validated commit or tag.
