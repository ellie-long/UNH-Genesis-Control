# UNH Genesis Phase-I Control System

Genesis is the EPICS-based control, acquisition, and integration framework being
developed for the University of New Hampshire polarized-target laboratory.

The objective is not simply to replace individual LabVIEW programs with Python.
Genesis provides a common device and subsystem architecture so independently
controlled laboratory hardware can be integrated into coordinated target-system
operation, monitored through common EPICS interfaces, archived with common
timestamps, and later supervised by higher-level automation and machine-learning
systems.

## Current scope

The Phase-I architecture is organized around four target subsystems:

- Microwave
- Magnet
- NMR
- ssRF

The current repository contains the operational Microwave and ssRF integration
work, together with common EPICS infrastructure, Phoebus interfaces, archiving,
simulation profiles, fitter/Burn Setup software, safety/interlock logic, and
hardware communication shims.

NMR/PyNMR and Magnet integration are subsequent Genesis integration milestones.
Cryogenic and higher-level supervisory systems are later extensions.

Physical launchers existing in this repository must not be interpreted as blanket
hardware acceptance. RF-enabled operation remains subject to the current
commissioning status, subsystem safety procedures, and operator verification.

## Architecture

Genesis follows a layered control model:

    Physical hardware
        |
        v
    Device-specific Python driver/controller
        |
        v
    Device EPICS soft IOC
        |
        v
    Subsystem control and safety logic
        |
        v
    Genesis integrated EPICS system
        |
        +--> IOC/process management
        +--> Phoebus operator interfaces
        +--> archiving and logging
        +--> interlocks and supervisory logic
        |
        v
    Future ML/AI supervisory and feedback control

The design keeps physical device ownership at the device IOC layer while
coordinated operations are implemented above it through subsystem logic,
sequencers, supervisors, and EPICS PV contracts.

See `docs/GENESIS_PHASE1_INTEGRATION_ARCHITECTURE.md` for the detailed Phase-I
architecture.

## Safety principles

Genesis physical operation is designed around several invariants:

- Physical startup defaults to a safe state.
- Microwave RF, ssRF RF, ssRF FM, WaveGen output, and robot motion are never
  automatically enabled merely by starting the software stack.
- RF ON requires an explicit operator action.
- Status and read-only commands must not change physical hardware state.
- Physical and simulation profiles remain explicitly separated.
- Only one software owner may control a physical instrument at a time.
- Simultaneously running EPICS services must use non-conflicting CA/PVA ports.
- Hard communication, interlock, configuration, emergency-stop, and ownership
  failures may inhibit physical operation.
- Shutdown establishes and verifies the subsystem safe state before terminating
  physical hardware owners whenever verification is available.

Do not bypass hard safety interlocks to force hardware operation.

## Canonical physical operator paths

Activate the Genesis environment first:

    conda activate genesis-p1

### Microwave

The dedicated Microwave physical profile is:

    configs/commissioning/microwave_physical/

Canonical commands:

    ./scripts/microwave_physical.sh start
    ./scripts/microwave_physical.sh status
    ./scripts/microwave_physical.sh stop

The physical stack integrates the Siglent SPD1305X UCA supply, Vaunix/LabBrick
source, PCL602 robot, Microwave Supervisor, IOC Manager, Phoebus GUI, and Genesis
archiver.

Detailed procedures and RF constraints are documented in:

    docs/MICROWAVE_PHYSICAL_OPERATION.md
    docs/MICROWAVE_RF_OPERATION.md

Physical hardware procedure and current commissioning constraints take precedence
over general software documentation.

### ssRF

The final physical ssRF profile is:

    configs/ssrf_physical/

Canonical operator commands:

    ./scripts/genesisctl.sh ssrf-physical-status
    ./scripts/genesisctl.sh ssrf-deploy-keysight
    ./scripts/genesisctl.sh ssrf-start
    ./scripts/genesisctl.sh ssrf-stop

`ssrf-start` is the final operator-facing physical ssRF startup path.

The stack owns or coordinates:

- R&S SMC100A
- Keysight WaveGen
- NMR protection switch
- ssRF routing switch
- ssRF interlock
- ssRF sequencer
- online fitter
- Burn Setup / waveform preparation
- Genesis archiver
- Phoebus ssRF interface

The validated source-control workflow is described in:

    docs/SSRF_FINAL_PHYSICAL_OPERATION.md
    docs/SSRF_PHYSICAL_COMMISSIONING.md

The historical physical-no-RF demonstration path is retained only for
commissioning history and is not the final physical operator workflow.

## Simulation

Physical and simulation configurations are intentionally separate.

Current commissioning simulation profiles include:

    configs/commissioning/microwave_sim/
    configs/commissioning/ssrf_sim_active/

The active ssRF simulation can be controlled through:

    ./scripts/genesisctl.sh sim-start
    ./scripts/genesisctl.sh sim-status
    ./scripts/genesisctl.sh sim-stop

Simulation must not be silently substituted for a physical profile.

## Repository structure

    genesis_common/
        Common Genesis device infrastructure, access control, SCPI/VISA helpers,
        and the vendored upstream BaseDevice foundation.

    microwave_ioc/
        Microwave device IOCs and controllers for the SPD1305X, Vaunix/LabBrick,
        and PCL602 robot.

    ssrf_ioc/
        ssRF hardware IOCs including SMC100A, Keysight WaveGen, Mini-Circuits
        switching, and supporting device interfaces.

    logic_devices/
        Subsystem-level safety, sequencing, lifecycle, supervisor, and archiver
        logic.

    online_fitter/
        EPICS-connected NMR fitter and adaptive waveform/Burn Setup interface.

    ssrf_analysis/
        ssRF analysis and waveform-generation support.

    configs/
        Device, subsystem, commissioning, access-security, Phoebus, and archiver
        configuration profiles.

    gui/
        Genesis Phoebus `.bob` operator screens.

    archive/
        Genesis archive viewer and archive-analysis support.

    scripts/
        Operator startup, shutdown, deployment, validation, and subsystem helper
        scripts.

    tools/
        Hardware shims, simulation tools, validation utilities, and CLI support.

    tests/
        Repository regression and safety tests.

    docs/
        Architecture, commissioning, operator, and subsystem documentation.

    master_ioc.py
        Generic Genesis soft-IOC runtime.

    ioc_manager.py
        EPICS IOC process manager.

## EPICS conventions

Genesis uses a common UNH namespace rooted at:

    UNH:GEN

Subsystems use dedicated namespaces such as:

    UNH:GEN:MW
    UNH:GEN:SSRF

The architecture separates commands from readbacks where required and exposes
communication state, error information, safety state, and process health through
EPICS.

Device IOCs should own hardware communication. Higher-level subsystem code should
coordinate devices through defined EPICS interfaces rather than bypassing device
ownership.

## Phoebus

Current Genesis operator displays are stored in `gui/`.

Primary subsystem displays include:

    gui/genesis_microwave.bob
    gui/genesis_ssrf.bob

Device/detail displays are maintained alongside the subsystem screens.

Subsystem-specific Phoebus launchers are preferred over broad process matching so
Microwave, ssRF, and unrelated Java applications remain independently owned.

## Archiving

Genesis provides a common EPICS archiver and a local historical archive viewer.

Start the viewer with:

    ./scripts/genesisctl.sh archive-viewer

The viewer runs locally through Streamlit and uses the Genesis archive data
without taking ownership of physical hardware.

## Python environment

Current Python runtime dependencies are listed in:

    requirements.txt

The validated development/commissioning environment is named:

    genesis-p1

The physical launch scripts currently require that environment to be active.

## Testing

The canonical repository-wide regression command is:

    python -m pytest -q

At cleanup checkpoint `113bd27`, the full repository suite completed with:

    741 passed, 140 subtests passed

Focused regression entry points are also available:

    ./scripts/genesisctl.sh test-mw
    ./scripts/genesisctl.sh test-ssrf
    ./scripts/genesisctl.sh test-fitter
    ./scripts/genesisctl.sh test-auth

Before committing code changes, also run:

    git diff --check

and the relevant focused regression tests.

## Development workflow

Do not develop physical-control changes directly on the main branch.

Use focused feature or fix branches, keep unrelated changes in separate commits,
and preserve validated physical milestones and tags.

For physical-control changes:

1. Inspect the existing hardware-control path.
2. Identify command and readback PVs.
3. Separate hard safety interlocks from commissioning restrictions.
4. Add or update regression tests.
5. Validate behavior without physical hardware first.
6. Perform physical validation as a distinct final step.

Repository cleanup, Ubuntu deployment, NMR/PyNMR integration, Magnet integration,
and later unified supervisory/ML control should preserve the same ownership and
safety architecture.

## Transitional and historical paths

Some scripts and documents remain because they record earlier Phase-I integration
or support transitional development workflows.

In particular:

- generic `genesisctl start` / `genesisctl stop` currently route through the older
  combined macOS launcher;
- `start_genesis_mac.sh` and `stop_genesis_mac.sh` are not the canonical dedicated
  physical Microwave or final ssRF operator paths;
- the physical-no-RF ssRF demo is historical commissioning material;
- older configuration directories may remain while migration to the final
  independent Genesis layout is completed.

For physical operation, use the subsystem-specific canonical commands documented
above.

## Documentation

Important references include:

    docs/GENESIS_PHASE1_INTEGRATION_ARCHITECTURE.md
    docs/MICROWAVE_PHYSICAL_OPERATION.md
    docs/MICROWAVE_RF_OPERATION.md
    docs/SSRF_FINAL_PHYSICAL_OPERATION.md
    docs/SSRF_PHYSICAL_COMMISSIONING.md
    docs/GENESIS_COMMANDS.md

Some older documents preserve commissioning history and may describe superseded
entry points. The canonical physical commands are identified in this README and
the current subsystem physical-operation documents.

## Provenance

Genesis incorporates and extends earlier EPICS software developed for the UNH
polarized-target program.

The common BaseDevice foundation derived from James Maxwell's earlier EPICS work
is vendored into this repository so Genesis no longer requires the former
`devices` Git submodule at runtime.

Additional subsystem implementations include work adapted from earlier UNH DAQ
and ssRF development.

Detailed source provenance is maintained in:

    NOTICE.md

## License

This repository retains its existing MIT license.

See:

    LICENSE
    NOTICE.md
