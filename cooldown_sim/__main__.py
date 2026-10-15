"""``python -m cooldown_sim`` -- simulated cool-down command line.

Subcommands are registered by the modules that implement them; each module
exposes ``add_parser(subparsers)`` returning a parser whose ``func`` handles
the command and returns a process exit code:

    0 ok / PASS    1 FAIL    2 refused by the guard    3 setup error
"""

from __future__ import annotations

import argparse
import importlib
import sys

EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_SETUP = 0, 1, 2, 3

#: modules providing subcommands, in --help order
_COMMAND_MODULES = (
    "cooldown_sim.cli_guard",
    "cooldown_sim.cli_data",
    "cooldown_sim.cli_run",
    "cooldown_sim.phoebus",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m cooldown_sim",
        description="Offline simulated cool-downs through the real Genesis stack.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in _COMMAND_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:          # command module not built yet
                continue
            raise
        module.add_parsers(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
