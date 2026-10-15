"""The scrubbed, loopback-only EPICS environment every replay process gets.

Starting from the caller's environment, every ``EPICS_*`` variable is removed
(so nothing inherited from a lab shell can point a replay process at the lab
network), then:

* servers bind and beacon on 127.0.0.1 only;
* clients search only an explicit list of replay CA ports, never broadcast;
* the CA repeater and PVA broadcast ports are moved into the replay range.
"""

from __future__ import annotations

import os

from . import REPO_ROOT
from .ports import LOOPBACK


def scrubbed(base: dict | None = None) -> dict:
    env = dict(os.environ if base is None else base)
    for key in [k for k in env if k.startswith("EPICS_")]:
        del env[key]
    return env


def client_env(ca_ports: list[int], stacks: dict, base: dict | None = None) -> dict:
    """Environment for a CA *client* (probe, autopilot, Phoebus helper)."""
    env = scrubbed(base)
    env.update({
        "EPICS_CA_AUTO_ADDR_LIST": "NO",
        "EPICS_CA_ADDR_LIST": " ".join(f"{LOOPBACK}:{p}" for p in sorted(ca_ports)),
        "EPICS_CA_REPEATER_PORT": str(stacks["epics"]["ca_repeater_port"]),
        "EPICS_CA_MAX_ARRAY_BYTES": "262144",
        "EPICS_PVA_AUTO_ADDR_LIST": "NO",
        "EPICS_PVA_ADDR_LIST": LOOPBACK,
        "EPICS_PVA_BROADCAST_PORT": str(stacks["epics"]["pva_broadcast_port"]),
    })
    return env


def server_env(ca: int, pva: int, ca_ports: list[int], stacks: dict,
               base: dict | None = None) -> dict:
    """Environment for one replay IOC (server and, for logic IOCs, client).
    ``ca``/``pva`` of 0 means the process serves no EPICS (a simulator)."""
    env = client_env(ca_ports, stacks, base)
    env.update({
        "EPICS_CAS_SERVER_PORT": str(ca),
        "EPICS_PVA_SERVER_PORT": str(pva),
        "EPICS_CAS_INTF_ADDR_LIST": LOOPBACK,
        "EPICS_CAS_BEACON_ADDR_LIST": LOOPBACK,
        "EPICS_CAS_AUTO_BEACON_ADDR_LIST": "NO",
        "EPICS_PVAS_INTF_ADDR_LIST": LOOPBACK,
        "EPICS_PVAS_BROADCAST_PORT": str(stacks["epics"]["pva_broadcast_port"]),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONUNBUFFERED": "1",
    })
    return env


def apply_client_env_here(ca_ports: list[int], stacks: dict) -> None:
    """Configure THIS process as a loopback CA client.  Must run before
    aioca/epicscorelibs is imported, because libca reads the environment once."""
    env = client_env(ca_ports, stacks)
    for key in [k for k in os.environ if k.startswith("EPICS_")]:
        del os.environ[key]
    os.environ.update({k: v for k, v in env.items() if k.startswith("EPICS_")})
