"""Data subcommands: ``cooldowns`` and ``channel-map-report``."""

from __future__ import annotations

import os
from pathlib import Path

from . import DATA_ENV, REPO_ROOT, channel_map
from .data import records
from .data.timeline import Timeline

DOC = REPO_ROOT / "docs" / "COOLDOWN_SIM_CHANNEL_MAP.md"


def add_parsers(sub) -> None:
    p = sub.add_parser("cooldowns", help="list past cool-downs under GENESIS_REPLAY_DATA")
    p.add_argument("--data", default=os.environ.get(DATA_ENV), help="data root (default $%s)" % DATA_ENV)
    p.set_defaults(func=_cooldowns)

    p = sub.add_parser("channel-map-report",
                       help="write the channel-map cross-check document")
    p.add_argument("--out", default=str(DOC))
    p.add_argument("--coverage", action="store_true",
                   help="also tabulate column coverage across every past cool-down (slow)")
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.set_defaults(func=_report)

    p = sub.add_parser("genesis-to-labview",
                       help="write LabVIEW-style data_record copies of Genesis recordings")
    p.add_argument("inputs", nargs="+",
                   help="Genesis data root, archive/master folder, session folder or genesis_master.csv")
    p.add_argument("--out", required=True, help="NEW folder for the converted copies")
    p.add_argument("--pynmr", action="append", default=[], metavar="DIR",
                   help="pyNMR event_dir to take NMR sweeps from (repeatable)")
    p.add_argument("--strict", action="store_true",
                   help="use only 'valid' readings (default also uses 'stale' = unchanged)")
    p.add_argument("--max-sweep-lag", type=float, default=5.0, metavar="S",
                   help="attach a pyNMR sweep only to a row within S seconds after it ended")
    p.set_defaults(func=_genesis_to_labview)

    add_model_parsers(sub)


def _genesis_to_labview(args) -> int:
    from .__main__ import EXIT_OK, EXIT_REFUSED
    from .data import genesis_to_labview as g2l
    try:
        rep = g2l.convert(args.inputs, args.out, args.pynmr, strict=args.strict,
                          max_sweep_lag_s=args.max_sweep_lag)
    except g2l.Refused as exc:
        print(f"refused: {exc}")
        return EXIT_REFUSED
    for day, (rows, sweeps) in sorted(rep.days.items()):
        print(f"  data_record_{day}.csv  {rows} rows, {sweeps} with an NMR sweep")
    print(f"wrote {len(rep.days)} day file(s) and {g2l.REPORT_NAME} in {args.out}")
    return EXIT_OK


def _cooldowns(args) -> int:
    from .__main__ import EXIT_OK, EXIT_SETUP
    if not args.data:
        print(f"set {DATA_ENV} or pass --data")
        return EXIT_SETUP
    for cd in records.list_cooldowns(Path(args.data)):
        days = records.day_files(cd)
        print(f"{cd.name:30s} {len(days):2d} day file(s): " + ", ".join(p.name for p in days))
    return EXIT_OK


def _report(args) -> int:
    from .__main__ import EXIT_OK, EXIT_SETUP
    cmap = channel_map.load()
    problems = channel_map.validate(cmap)
    if problems:
        print("channel_map.yaml problems:\n  " + "\n  ".join(problems))
        return EXIT_SETUP
    coverage = None
    if args.coverage:
        if not args.data:
            print(f"--coverage needs {DATA_ENV} or --data")
            return EXIT_SETUP
        cols = sorted({c for spec in cmap.all_specs() for c in spec.source_columns})
        coverage = {}
        for cd in records.list_cooldowns(Path(args.data)):
            print(f"reading {cd.name} ...")
            tl = Timeline(records.read_cooldown(cd, cols))
            coverage[cd.name] = {c: tl.coverage(c) for c in cols
                                 if c in tl.table.numeric or c in tl.table.text}
    Path(args.out).write_text(channel_map.report_markdown(cmap, coverage), encoding="utf-8")
    print(f"wrote {args.out}")
    return EXIT_OK


def add_model_parsers(sub) -> None:
    p = sub.add_parser("fit-params", help="fit target-model parameters from past cool-downs")
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--cooldowns", help="comma list (default: every cool-down from 2021)")
    p.add_argument("--method", choices=("uca", "rf"), default="uca",
                   help="uca: logbook UCA power steps (default, writes the model parameters); "
                        "rf: the earlier RF-flip fit, printed for comparison only")
    p.add_argument("--dry-run", action="store_true", help="print the fit, do not write it")
    p.set_defaults(func=_fit_params)

    p = sub.add_parser("hindcast", help="score the target model against past cool-downs' recorded P")
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--cooldowns", help="comma list (default: every cool-down)")
    p.add_argument("--truth", choices=["online", "mcclellan"], default="online",
                   help="score against the record's online P, or the McClellan equilibrium line fits "
                        "($MCCLELLAN_RESULTS, default ../results beside the records)")
    p.add_argument("--defaults", action="store_true",
                   help="ignore the per-cool-down fitted DNP values (cooldown_dnp.yaml)")
    p.add_argument("--html", help="also write an HTML report with plots to this file")
    p.set_defaults(func=_hindcast)

    p = sub.add_parser("fit-dnp", help="fit per-cool-down DNP values from the record (slow)")
    p.add_argument("--data", default=os.environ.get(DATA_ENV))
    p.add_argument("--cooldowns", help="comma list (default: every cool-down with valid P)")
    p.add_argument("--max-evals", type=int, default=120)
    p.add_argument("--jobs", type=int, default=1, help="fit this many cool-downs in parallel")
    p.add_argument("--write", action="store_true", help="write cooldown_dnp.yaml (else print only)")
    p.set_defaults(func=_fit_dnp)


def _fit_params(args) -> int:
    from .__main__ import EXIT_OK, EXIT_SETUP
    from .model import fit_params
    if not args.data:
        print(f"set {DATA_ENV} or pass --data")
        return EXIT_SETUP
    cds = [c.strip() for c in args.cooldowns.split(",")] if args.cooldowns else None
    fitted = fit_params.fit(Path(args.data), cds, method=args.method)
    prov = fitted["provenance"]
    print(f"{prov['steps_used']} steps ({args.method}):\n  " + "\n  ".join(prov["steps"]))
    for ch, g in fitted["thermal"]["channels"].items():
        print(f"  {ch:8s} {g['gain_K_per_drive']:+.4f} K/drive  n={g['n']:<3d} {g['status']}")
    print(f"  tau {fitted['thermal']['tau_s']} s (n={fitted['thermal']['tau_n']})")
    if args.method != "uca" or args.dry_run:
        print("not written (" + ("comparison method" if args.method != "uca" else "--dry-run") + ")")
        return EXIT_OK
    path = fit_params.write(fit_params.build_params(fitted))
    print(f"wrote {path}")
    return EXIT_OK


#: cool-down folders that duplicate another (skip in hindcasts and fits)
_HINDCAST_SKIP = {"2026-07-Cooldown", "2025-12-Cooldown-Subset-001"}
_DNP_KEYS = ("P_max", "tau_up_s", "T1_P_s", "optimum_offset_GHz", "lobe_width_GHz",
             "neg_scale", "neg_side")


def _hindcast_dirs(args):
    root = Path(args.data)
    want = [c.strip() for c in args.cooldowns.split(",")] if args.cooldowns else None
    for cd in records.list_cooldowns(root):
        if want is None and cd.name in _HINDCAST_SKIP:
            continue
        if want and not any(cd.name.startswith(w) for w in want):
            continue
        yield cd


def _hindcast(args) -> int:
    from .__main__ import EXIT_OK, EXIT_SETUP
    from .model import hindcast
    from .model.target import load_params
    if not args.data:
        print(f"set {DATA_ENV} or pass --data")
        return EXIT_SETUP
    results = []
    for cd in _hindcast_dirs(args):
        params = load_params() if args.defaults else None
        tl = hindcast.load(cd)
        scores, series = hindcast.evaluate(cd, params, tl=tl, truth=args.truth)
        for s in scores:
            print(s.line(), flush=True)
        default = None
        if args.html and not args.defaults:
            default = hindcast.evaluate(cd, load_params(), tl=tl, truth=args.truth)[0]
        results.append((cd.name, scores, series, default))
    if args.html:
        print(f"wrote {hindcast.write_html(Path(args.html), results)}")
    return EXIT_OK


def _fit_dnp(args) -> int:
    import datetime as _dt
    import math

    import yaml

    from .__main__ import EXIT_OK, EXIT_SETUP
    from .model import hindcast
    from .model.target import COOLDOWN_DNP_FILE, load_params
    if not args.data:
        print(f"set {DATA_ENV} or pass --data")
        return EXIT_SETUP
    table = {}
    if COOLDOWN_DNP_FILE.exists():
        table = (yaml.safe_load(COOLDOWN_DNP_FILE.read_text(encoding="utf-8")) or {}).get("cooldowns") or {}
    dirs = list(_hindcast_dirs(args))
    if args.jobs > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(_fit_one, dirs, [args.max_evals] * len(dirs)))
    else:
        results = [_fit_one(cd, args.max_evals) for cd in dirs]
    for cd, r in zip(dirs, results):
        if not math.isfinite(r["cost_before"]):
            print(f"{cd.name}: no valid recorded P -- skipped", flush=True)
            continue
        print(f"{cd.name}: cost {r['cost_before']:.2f} -> {r['cost_after']:.2f} pp  "
              + "  ".join(f"{k} {r[k]:.4g}" for k in _DNP_KEYS), flush=True)
        entry = {k: (int(r[k]) if k == "neg_side" else round(float(r[k]), 4)) for k in _DNP_KEYS}
        entry["fit"] = {"cost_before_pp": round(r["cost_before"], 2),
                        "cost_after_pp": round(r["cost_after"], 2), "evals": r["evals"],
                        "date": _dt.date.today().isoformat()}
        table[cd.name] = entry
    if args.write:
        head = ("# Per cool-down DNP values, fitted to the record's online P by\n"
                "#   python -m cooldown_sim fit-dnp --write\n"
                "# (Nelder-Mead on the mean of the free-run and 6 h-anchored mean absolute\n"
                "# errors; bounds in model/hindcast.py FIT_BOUNDS).  Target materials,\n"
                "# radicals and field calibrations differ between cool-downs, so these do.\n"
                "# load_params(cooldown=...) applies them.  Derived numbers only.\n")
        COOLDOWN_DNP_FILE.write_text(head + yaml.safe_dump({"cooldowns": table}, sort_keys=True),
                                     encoding="utf-8")
        print(f"wrote {COOLDOWN_DNP_FILE}")
    return EXIT_OK


def _fit_one(cd, max_evals):
    from .model import hindcast
    from .model.target import load_params
    return hindcast.fit_dnp(hindcast.load(cd), load_params(), max_evals=max_evals)
