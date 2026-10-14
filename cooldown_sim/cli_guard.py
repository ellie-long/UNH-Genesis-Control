"""``guard`` and ``ports`` subcommands."""

from __future__ import annotations

from . import guard, ports


def add_parsers(sub) -> None:
    p = sub.add_parser("guard", help="check this host is safe for a simulated cool-down")
    p.set_defaults(func=_guard)

    p = sub.add_parser("ports", help="list replay ports and check they are free")
    p.add_argument("--port-offset", type=int, default=0)
    p.set_defaults(func=_ports)


def _guard(args) -> int:
    from .__main__ import EXIT_OK, EXIT_REFUSED
    refusals = guard.check()
    print(f"host: {guard.describe_host()}")
    if refusals:
        print("REFUSED - a simulated cool-down must not start here:")
        for r in refusals:
            print(f"  {r}")
        return EXIT_REFUSED
    print("OK - no live Genesis stack detected")
    return EXIT_OK


def _ports(args) -> int:
    from .__main__ import EXIT_OK, EXIT_SETUP
    endpoints = ports.replay_endpoints(offset=args.port_offset)
    taken = ports.unbindable_ports({e.port for e in endpoints})
    clash = {e.port for e in endpoints} & ports.live_ports()
    for e in sorted(endpoints, key=lambda e: e.port):
        flag = " TAKEN" if e.port in taken else ""
        flag += " LIVE-PORT" if e.port in clash else ""
        print(f"{e.port:6d}  {e.kind:9s} {e.owner}{flag}")
    return EXIT_SETUP if (taken or clash) else EXIT_OK
