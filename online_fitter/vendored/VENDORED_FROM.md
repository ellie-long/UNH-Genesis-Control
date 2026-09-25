# Vendored from Optimizied-McClellan-Fits

These modules are a **verbatim copy** of the deuteron-NMR fitter from the sibling
repository. They are vendored (not imported from a sibling path) so this repo is
self-contained on the lab acquisition machine.

| field | value |
|-------|-------|
| Source repo | `Optimizied-McClellan-Fits` |
| Source path | `fitter/` |
| Source commit | `32e75b5` (`32e75b56d17dd02b103e716f9259675669f1a4d6`) |
| Commit date | 2026-07-23 |
| Commit subject | docs(cooldowns): record 2026-06 re-run + re-baseline after the 06-15 fix |
| Vendored on | 2026-07-24 |

## Files

- `deuteron_model.py` — Dulya spin-1 lineshape, `FastModel`, `MATERIALS`, `ratio_P/Q`,
  `Q_equilibrium`, `area_polarizations`, `component_signed`, `bonds`.
- `fit_deuterons.py` — `fit_signal` (full open fit), `fit_signal_fast` (rapid r-only),
  `fit_freqhop`, `fit_freqhop_area`.
- `holeburn.py` — hole-burnt / ssRF area method (`wing_subtract`, `equilibrium_reference`,
  `HoleBurnSolver`, `run`).
- `cooldown_io.py` — raw `data_record_*.csv` reader (`resolve_columns`, `read_cooldown`,
  `detect_runs`, baseline averaging). Used only by the mock/replay tooling to parse a
  previous cooldown, not on the live fit path.
- `classify.py` — sweep classification helpers imported by `fit_deuterons`.

## Local overrides (applied in ../engine.py, NOT by editing these files)

So the vendored files stay a clean copy, the online tool tweaks a couple of full-fit
(Update Constants) behaviours at runtime instead of editing the source:

- **Full-fit r upper bound 3.0 → 4.0** (P ceiling 61.5% → 71.4%): `engine.py` wraps
  `fd.fitter_spec` (`FULL_FIT_R_MAX`). The per-line fast fit already used [0.3, 4.0].
- **Full-fit r initial guess = r at P=20%** (~1.356, not the generic 1.5): set in the
  `p0_override` passed to `fit_signal` (`FULL_FIT_R0`).

If these ever move upstream, drop the overrides here and re-vendor.

## Rules

- **Do NOT edit these files.** They are unedited so re-syncing upstream is a plain copy.
  Fix bugs in `Optimizied-McClellan-Fits/fitter/` and re-vendor, updating the table above.
- Internal imports are bare module names; `__init__.py` puts this directory on `sys.path`
  so they resolve to these copies.

## Re-vendor

```
cp ../Optimizied-McClellan-Fits/fitter/{deuteron_model,fit_deuterons,holeburn,cooldown_io,classify}.py \
   online_fitter/vendored/
# then update the commit hash/date/subject in the table above
```
