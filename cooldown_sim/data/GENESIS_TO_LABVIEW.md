# Converting Genesis recordings to the old LabVIEW `data_record` format

`genesis_to_labview.py` reads what Genesis records and writes **new** files in
the layout the LabVIEW DAQ used (2026-06 version): one
`data_record_YYYY-MM-DD.csv` per lab day, TAB-separated, with the same 93
column titles in the same order. Code written for the old files (the
McClellan fits, the cool-down simulator) can then read Genesis-era data
unchanged.

**It never deletes, edits or overwrites anything.** It only reads its inputs,
creates every output file new, and refuses to run (before writing anything)
if an output file already exists or the output folder is inside an input
folder. Keep the original Genesis recordings: they hold more than the
converted copy (every second, every PV, a quality flag on each reading).

## What you need

- **Python 3.9 or newer.** No extra packages for the converter itself.
- **The Genesis recordings:** the `local_data/` folder (or any part of it)
  that Genesis's master recorder writes. The files that matter are
  `archive/master/<session>/<UTC day>/genesis_master.csv`.
- **Optional, the NMR sweeps:** pyNMR's data folder (its `event_dir`, by
  default `data/` inside the pyNMR checkout), holding files named like
  `2026-10-05_17-43-30__2026-10-05_18-14-57.txt`.

## Run it inside the Genesis repository

From the top of an `UNH-Genesis-Control` checkout (the `dev-sim` branch, or any
branch that has `cooldown_sim/`). The full command line needs `numpy` and
`pyyaml` installed:

```bash
python -m cooldown_sim genesis-to-labview /path/to/local_data --out /path/to/converted
```

with pyNMR sweeps:

```bash
python -m cooldown_sim genesis-to-labview /path/to/local_data \
    --pynmr /path/to/jlab_pynmr/data --out /path/to/converted
```

## Run it on its own (no repository)

Put two files in one folder:

- `genesis_to_labview.py` (this folder);
- `its90_helium.py`, from `genesis_common/` in the same repository. It turns
  the manometer pressures into helium vapour-pressure temperatures.

Then:

```bash
python genesis_to_labview.py /path/to/local_data --out /path/to/converted
python genesis_to_labview.py /path/to/local_data --pynmr /path/to/pynmr/data --out /path/to/converted
```

On Windows, write the paths with backslashes or forward slashes, either works:
`python genesis_to_labview.py C:\data\local_data --out C:\data\converted`.

## Options

| Option | What it does |
|---|---|
| `INPUT ...` | One or more Genesis folders or files: the whole `local_data/`, `archive/master/`, one session folder, one day folder, or one `genesis_master.csv`. Every `genesis_master.csv` underneath is used. |
| `--out DIR` | Where to write. Use a new or empty folder, outside the inputs. It is created if missing. |
| `--pynmr DIR` | A pyNMR data folder to take NMR sweeps from. Repeat it for several folders. |
| `--strict` | Use only readings Genesis marks `valid`. By default `stale` readings are used too: Genesis marks a value stale when it has not changed for 60 s, which for a steady gauge is normal. |
| `--max-sweep-lag S` | A pyNMR sweep is placed on the first recorded second at or after the sweep ended, if that second is within S seconds (default 5). |

## What you get

In `--out`:

- `data_record_YYYY-MM-DD.csv`, one per lab-local day (days are split at lab
  midnight, not at UTC midnight as Genesis's folders are);
- `genesis_to_labview_report.md`, which records the inputs, the Genesis
  version of each session, row and sweep counts per day, anything skipped,
  and where every column came from.

Reading the files:

- **One row per second** (Genesis's rate). The old DAQ wrote one 20 s
  average per row. Reducing the row rate is planned but not done.
- `Timestamp` is lab local time, `6/15/2026 1:00:05 PM`; `Seconds from
  Midnight` too.
- `NOT_IN_GENESIS` means Genesis does not record that column yet (for
  example the magnet current, EPR, the lock-in, Pirani). `NaN` means
  Genesis has the channel but had no usable reading at that second.
- `Vapor Pressure Temp (K)`: Genesis's recorded Manometer-Lo temperature.
  Where that is blank but the pressure was recorded (the 2026-10-24 run,
  before the sub-1.25 K branch was in the IOC), the converter works it out
  from the pressure with the same function the IOC now uses.
- `Vapor Pressure Range` says which manometer the vapour-pressure
  temperature came from: `Lo: He-II superfluid` (Manometer-Lo) or
  `Hi: He-I normal` (Manometer-Hi).
- `Area/Polarization` is the bare NMR area before a TE or calibration
  constant, and the polarization with a `%` sign after (`12.3456%`), as the
  old DAQ wrote it.
- NMR: a row that carries a new pyNMR sweep has `NMR Number` = the number
  of points, followed by the sweep. Other rows have `NMR Number` 0.

Run time: about 15 MB of Genesis recording per second on a laptop, so a
full day (~2 GB) takes a few minutes.

## If it refuses

`refused: ... already exist ...` means an earlier conversion is in that
folder. Pick a new `--out` folder; the earlier files are left as they are.
`refused: output folder ... is inside input ...` means the output would land
inside the recordings; choose a folder outside them.
