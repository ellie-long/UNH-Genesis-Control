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

    add_model_parsers(sub)


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
