# Genesis Phase-I Operations Cheat Sheet

This kit gives you **one command** for routine Genesis work instead of remembering long shell blocks.

## 1. Install once

From your Genesis repository:

```bash
cd /Users/shrestha/project/unh/genesis/Development/genesis-phase1

cp /path/to/genesisctl.sh scripts/genesisctl.sh
chmod +x scripts/genesisctl.sh
```

Then add a permanent shortcut to `~/.zshrc`:

```bash
echo "alias genesis='/Users/shrestha/project/unh/genesis/Development/genesis-phase1/scripts/genesisctl.sh'" >> ~/.zshrc
source ~/.zshrc
```

From then on, activate the environment:

```bash
conda activate genesis-p1
```

and simply use:

```bash
genesis help
```

---

## 2. Commands you will use most

### Start the normal Genesis system

```bash
genesis start
```

This calls the repository's established:

```bash
./scripts/start_genesis_mac.sh
```

Normal startup is intended to remain **read-only**. Device IOCs connect/read state, but write gates stay disabled.

### See what is running

```bash
genesis status
```

This shows:

- branch and current commit;
- screen sessions;
- Genesis-related processes;
- SMC100A status;
- Keysight WaveGen status;
- NMR and ssRF switch states;
- interlock state and permits;
- sequencer readiness/execution state;
- archiver state.

For just screen sessions:

```bash
genesis screens
```

For just processes:

```bash
genesis processes
```

### Stop Genesis safely

```bash
genesis stop
```

This calls:

```bash
./scripts/stop_genesis_mac.sh
```

The repository shutdown script performs its own safety checks.

### Forced stop

Only when you deliberately need to bypass hardware-state verification:

```bash
genesis force-stop I_UNDERSTAND
```

This sets `GENESIS_FORCE_STOP=1`.

**Do not use this as the normal stop command.**

---

## 3. Git / repository state

```bash
genesis git
```

Shows:

- branch;
- HEAD;
- worktree changes;
- `devices` submodule state;
- stash list.

The helper **never modifies the stash**.

To verify that your current branch includes the frozen 5B.6F ssRF milestone:

```bash
genesis baseline
```

Frozen baseline:

```text
3ef33de  Added ssRF fault-response hardening and simulator validation
```

---

## 4. See what devices the normal configuration points to

```bash
genesis endpoints
```

This prints the configured SMC100A, Keysight WaveGen and switch endpoints plus:

- `write_enabled`;
- `initialize_on_connect`;
- sequencer execution configuration where present.

This command is particularly useful before physical 5C commissioning.

---

# ssRF simulator workflow

## 5. Check simulator backends

Before activating the simulator IOC stack:

```bash
genesis sim-check
```

Required localhost services:

| Device | Endpoint |
|---|---|
| SMC100A simulator | `127.0.0.1:5025` |
| Keysight WaveGen simulator | `127.0.0.1:5027` |
| NMR switch simulator | `127.0.0.1:8081` |
| ssRF switch simulator | `127.0.0.1:8082` |

`sim-check` does **not** write anything.

If one is missing, the helper refuses `sim-start` rather than inventing a backend launcher.

## 6. Activate the ssRF simulator commissioning stack

```bash
genesis sim-start
```

This calls the established simulator handoff:

```bash
./scripts/start_ssrf_sim_active.sh --activate
```

The simulator-active profile is intentionally different from normal Genesis:

- localhost only;
- device writes enabled for simulators;
- sequencer execution enabled;
- `initialize_on_connect=false`;
- a burn still requires explicit one-shot `ARM + START`.

**Never use `sim-start` as a physical-hardware commissioning command.**

## 7. Check simulator IOC state

```bash
genesis sim-status
```

This only reads EPICS PVs.

## 8. Leave simulator mode

```bash
genesis sim-stop
```

The helper will only execute this automatically if `start_ssrf_sim_active.sh` contains a canonical `--restore` or `--deactivate` mode.

If the repository does not expose such a mode, it **refuses to guess**. That is deliberate: blindly killing similarly named IOC sessions could leave the system in an ambiguous state.

A canonical simulator restore command should be added to the repository as part of the operations cleanup / 5C preparation.

---

# Testing

## 9. Run the ssRF regression tests

```bash
genesis test-ssrf
```

Equivalent conceptually to:

```bash
PYTHONPATH="$PWD" python -m unittest discover -s tests -p 'test_ssrf*.py'
git diff --check
```

This is software testing. It is not a physical RF test.

## 10. Run authorization/access-safety tests

```bash
genesis test-auth
```

It runs the available Genesis/ssRF authorization and access-security test files.

## 11. Run all unit tests

```bash
genesis test-all
```

## 12. Compile-check source

```bash
genesis compile
```

## 13. General code sanity check

```bash
genesis check
```

Runs:

- `git diff --check`;
- Python compile check;
- repository/submodule/stash state display.

---

# Logs

Show newest logs:

```bash
genesis logs
```

Tail matching logs:

```bash
genesis logs ssrf
genesis logs interlock
genesis logs keysight
genesis logs smc
```

---

# Recommended everyday workflow

## Normal development / monitoring

```bash
conda activate genesis-p1

genesis git
genesis start
genesis status

# Work / inspect GUI / read PVs

genesis status
genesis stop
genesis git
```

## Simulator work

```bash
conda activate genesis-p1

genesis git
genesis sim-check
genesis sim-start
genesis sim-status

# Run simulator-only activity deliberately.

genesis sim-stop
genesis status
genesis git
```

## Before committing code

```bash
genesis test-ssrf
genesis test-auth
genesis check
```

---

# What this helper intentionally does NOT do yet

The next project milestone is physical ssRF commissioning (5C). Therefore this helper currently does **not** provide commands that automatically:

- enable physical SMC100A writes;
- enable physical Keysight output;
- switch the real NMR/ssRF RF paths;
- ARM/START a physical burn;
- change real hardware IP addresses;
- treat the Raspberry Pi transport as commissioned.

Those operations should only be added after 5C.1/5C.2 establishes and documents the actual Mac → SMC100A / Keysight / switch connectivity.

This prevents a convenience script from accidentally turning simulator assumptions into physical-hardware actions.

---

# Current milestone baseline

As of the end of software/simulator ssRF integration:

```text
5B.6F
commit 3ef33de
Added ssRF fault-response hardening and simulator validation
```

5B is the software/simulator-complete baseline.

The next major block is:

```text
5C — physical ssRF commissioning
```

After 5C is validated, update `genesisctl.sh` with a separate, explicit physical commissioning command group rather than changing the meaning of the safe normal commands.
