# Panopticon: quick start

The Panopticon (`gui/genesis_panopticon.bob`) is a one-screen overview of the
UNH polarized-target system, in the style of the old LabVIEW DAQ overview. It
shows the magnet, fridge, shells and lab spaces; the target insert drawn to
scale with every thermometer; NMR and polarization; hole burning (ssRF); the
superconducting magnet; microwaves; EPR; and the slow sweep. It is a Phoebus
display: it runs inside Phoebus, connected to a running Genesis system or to
the cool-down simulator (`cooldown_sim`).

Without the lab hardware, run it against the simulator, as below. The
simulator never touches hardware: it refuses to start beside a live Genesis
stack and uses private loopback ports only.

## 1. Set up once

Follow the setup table in [COOLDOWN_SIM.md](COOLDOWN_SIM.md) (section 1):

* **Python:** `conda env create -f environment.yml`, `conda activate
  genesis-p1`, `pip install -r requirements-dev.txt` (Windows, or Linux/WSL).
  Linux/WSL runs every part; native Windows runs everything except the
  POSIX-only ssRF sequencer.
* **Phoebus:** Java 21 and Phoebus 5.0.5 from
  github.com/ControlSystemStudio/phoebus/releases, unzipped into `~/phoebus/`
  (or set `PHOEBUS_BIN`).
* **Optional:** the [Spin-1-Rates-Fit](https://github.com/ellie-long/Spin-1-Rates-Fit)
  repository checked out next to this one (or `SPIN1_RATES_FIT_DIR`). It
  gives the rate-equation polarization model; without it the simulator uses a
  simpler phenomenological model.

Then check the host:

    python -m cooldown_sim guard      # exit 0 = safe to run here
    python -m cooldown_sim ports      # are the simulator's ports free?

## 2. Run it on the simulator

**Synthetic day (no data needed):**

    python -m cooldown_sim run --mode interactive --source synthetic --legs cryo,nmr,mw,ssrf

In a second terminal, once the first prints `Sim Control IOC up`:

    python -m cooldown_sim phoebus --screens panopticon,cryo,cryo_safety,mw,fitter

**A past cool-down instead** (needs the lab's cool-down records, which are not
in git: ask the UNH polarized-target group, then set `GENESIS_REPLAY_DATA` to
the folder of `*-Cooldown` directories):

    python -m cooldown_sim run --mode interactive --cooldown 2026-06 \
        --start "2026-06-17 16:00" --speed 30 --legs cryo,nmr,mw,ssrf --autopilot mw,fit

The session starts **paused**: press **Run** on the Sim Control screen. Sim
Control also lets you drive the microwaves (UCA voltage, Magic +/-), move the
NMR window, inject faults, quench the target and seek in time. `python -m
cooldown_sim ctl status` reports the sim time from a terminal; `ctl stop` ends
the session.

## 3. Using the screen

* **Size:** the screen is 1900 x 915 and fits a 1920 x 1080 monitor at 100 %
  with Phoebus maximized. On a smaller screen pick zoom **All** in the
  display's toolbar.
* **Plots:** each plot has a toolbar (zoom, pan, auto-scale, and a settings
  dialog to type axis limits). Ctrl + scroll wheel zooms without picking a
  tool. Trends come from the trend-history IOC (`UNH:GEN:HIST`), so they are
  full as soon as the screen opens; their x axes are minutes (or hours) back
  from now.
* **Buttons** in the headers open the Cryo, Microwave, ssRF and NMR-fitter
  screens; **Magnet** and **Fridge** open the magnet and fridge screens.
* **Grey "not in Genesis yet" boxes** are LabVIEW-era readouts that Genesis
  does not have an instrument IOC for yet (Pirani, lab thermocouples, magnet
  power supply, EPR, SIG, slow sweep). The LHe levels, flowmeters and
  Manometer-Lo are live.
* **Pressures are all in Torr.** The TPG 262 reads in whatever unit its front
  panel is set to (`Pump_Unit`); the trend-history IOC follows that unit and
  converts, so the Roots gauges show Torr either way (1 atm = 760 Torr).
* **Target material and dopant** are set on the Cryo Safety screen (Target
  Operating State card).

## 4. Regenerating the screen

The `.bob` file is generated; edit the generator, not the file:

    python tools/make_panopticon.py
    python -m pytest -q tests/cooldown_sim/test_cooldown_sim_panopticon.py

## On the live system

In the lab, open `gui/genesis_panopticon.bob` in the operator Phoebus like any
other Genesis screen. The trend plots need the trend-history IOC
(`logic_devices/trend_history.py`) running; until it is added to the IOC
manager they stay empty there.
