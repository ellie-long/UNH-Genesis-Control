"""Spin-1 rate-equation DNP: the target model's P and Q from first principles.

The deuteron (spin-1) / electron (spin-1/2) rate equations and their solver
are the Spin-1-Rates-Fit package ``spin1fit``, imported from a checkout next to
this repository (``../Spin-1-Rates-Fit``) or from ``$SPIN1_RATES_FIT_DIR``.
Only its physics modules are loaded (params, constants, solver and what they
import); the package ``__init__`` also pulls in plotting, which the sim does
not need.  Without the package, :func:`available` is False and the target
model keeps its phenomenological DNP.

What this module adds is the sim's side: per material, a fitted rate set and
the f/B of its P-up optimum; the microwave frequency dependence from the
measured TEMPO lineshape (``spin1_materials.yaml``); and scaling of every
microwave-driven rate by the delivered power.  One call to :meth:`Spin1.step`
propagates (Pe, Pn, Qn) over dt at fixed conditions with spin1fit's "bulk"
model -- the sim's 30 s steps are short compared with every nuclear time
constant, and the state carries over exactly between steps.

Units: s, T, K, GHz; polarizations as fractions.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import math
import os
import sys
import types
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml

MATERIALS_FILE = Path(__file__).with_name("spin1_materials.yaml")
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_DIR = "SPIN1_RATES_FIT_DIR"
#: microwave-driven rate keys and how they follow frequency: drive family or saturation
DRIVE_KEYS = ("k_alpha1", "k_alpha2", "k_beta1", "k_beta2")
SATURATION_KEYS = ("k_s", "k_zeta1", "k_zeta2")


def _package_dir() -> Path | None:
    for cand in (os.environ.get(ENV_DIR), REPO_ROOT.parent / "Spin-1-Rates-Fit"):
        if cand and (Path(cand) / "spin1fit" / "solver.py").exists():
            return Path(cand)
    return None


@lru_cache(maxsize=1)
def _spin1fit():
    """(RateParams, TEConditions, simulate) from spin1fit, or None."""
    root = _package_dir()
    if root is None:
        return None
    if "spin1fit" not in sys.modules:
        pkg = types.ModuleType("spin1fit")             # skip the plotting-heavy __init__
        pkg.__path__ = [str(root / "spin1fit")]
        sys.modules["spin1fit"] = pkg
    try:
        params = importlib.import_module("spin1fit.params")
        constants = importlib.import_module("spin1fit.constants")
        solver = importlib.import_module("spin1fit.solver")
    except Exception:  # noqa: BLE001 - any import problem: fall back to phenomenological DNP
        return None
    return params.RateParams, constants.TEConditions, solver.simulate


def available() -> bool:
    return _spin1fit() is not None


@lru_cache(maxsize=1)
def load_table(path: Path = MATERIALS_FILE) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def material_for(cooldown: str | None, t: float | None = None, table: dict | None = None) -> str:
    """Material of a past cool-down at time t (lab-local schedule), else the default."""
    table = table or load_table()
    if not cooldown:
        return table.get("default") or ""
    entries = None
    for key, sched in (table.get("cooldowns") or {}).items():
        if key == cooldown or key.split("-Cooldown")[0] == cooldown.split("-Cooldown")[0]:
            entries = sched
    if not entries:
        return ""                         # unknown: phenomenological with fitted values
    from ..data.records import lab_tz
    tz = lab_tz()
    name = entries[0]["material"]
    for e in entries:
        start = _dt.datetime.strptime(e["start"], "%Y-%m-%d %H:%M").replace(tzinfo=tz).timestamp()
        if t is None or t >= start:
            name = e["material"]
    return name


def magic_ratio(sign: str, material: str = "", table: dict | None = None) -> float:
    """GHz/T of the 'magic' frequency: '+' best P-up, '-' best P-down."""
    table = table or load_table()
    key = "plus" if sign.startswith("+") or sign.startswith("p") else "minus"
    spec = (table.get("materials") or {}).get(material) or {}
    return float((spec.get("magic") or table["magic"])[key])


def lineshape(x: float, table: dict | None = None) -> tuple[float, float]:
    """(B, A): P-up and P-down pump strength at f/B = x (TEMPO frame), normalized."""
    ls = (table or load_table())["lineshape"]
    xs, taper = np.asarray(ls["x"]), float(ls["taper"])
    out = []
    for key in ("B", "A"):
        ys = np.asarray(ls[key])
        if x < xs[0]:
            v = ys[0] * max(0.0, 1.0 - (xs[0] - x) / taper)
        elif x > xs[-1]:
            v = ys[-1] * max(0.0, 1.0 - (x - xs[-1]) / taper)
        else:
            v = float(np.interp(x, xs, ys))
        out.append(float(v))
    return out[0], out[1]


@dataclass
class Spin1State:
    Pe: float
    Pn: float
    Qn: float


class Spin1:
    """One material's rate model; steps (Pe, Pn, Qn) at given conditions."""

    def __init__(self, material: str, table: dict | None = None):
        lib = _spin1fit()
        if lib is None:
            raise RuntimeError("spin1fit not found (set %s)" % ENV_DIR)
        self.RateParams, self.TEConditions, self._simulate = lib
        self.table = table or load_table()
        spec = self.table["materials"][material]
        self.material = material
        self.base = dict(spec["rates"])
        self.x_opt = float(spec["x_opt"])
        self.peak_x = float(self.table["lineshape"]["peak_x"])
        self._B0, self._A0 = lineshape(self.peak_x, self.table)

    def rates(self, f_GHz: float, B_T: float, power: float):
        """Rate set at frequency f, field B and delivered power (0..1 of full)."""
        p = dict(self.base)
        if not (power > 0 and math.isfinite(f_GHz) and B_T > 0.5):
            for k in DRIVE_KEYS + SATURATION_KEYS:
                p[k] = 0.0
            return self.RateParams(**p)
        x = f_GHz / B_T - self.x_opt + self.peak_x       # into the lineshape's frame
        b, a = lineshape(x, self.table)
        b, a = b / self._B0, a / self._B0                 # 1 at the material's optimum
        base = self.base
        mirror = {"k_alpha1": "k_beta1", "k_alpha2": "k_beta2", "k_beta1": "k_alpha1", "k_beta2": "k_alpha2"}
        # the fitted set already holds the P-down pumping present at the optimum
        # (ratio A0/B0 of the P-up there): the mirrored drive adds only the excess
        extra = max(a - (self._A0 / self._B0) * b, 0.0)
        for k in DRIVE_KEYS:
            p[k] = power * (b * base[k] + extra * base[mirror[k]])
        sat = (a + b) / (1.0 + self._A0 / self._B0)
        for k in SATURATION_KEYS:
            p[k] = power * sat * base[k]
        return self.RateParams(**p)

    def te(self, B_T: float, T_K: float):
        return self.TEConditions(B=max(B_T, 1e-3), T=max(T_K, 0.05))

    def initial(self, Pn: float, B_T: float, T_K: float, Qn: float | None = None) -> Spin1State:
        from .physics import q_boltzmann
        te = self.te(B_T, T_K)
        return Spin1State(Pe=float(te.Pe0), Pn=Pn, Qn=q_boltzmann(Pn) if Qn is None else Qn)

    def step(self, s: Spin1State, dt: float, *, f_GHz: float, B_T: float, T_K: float,
             power: float) -> Spin1State:
        if dt <= 0:
            return s
        r = self._simulate(self.rates(f_GHz, B_T, power), self.te(B_T, T_K), np.array([dt]),
                           model="bulk", t0=0.0, P0=s.Pn, Q0=s.Qn, Pe_init=s.Pe)
        Pn, Qn, Pe = float(r.Pn[-1]), float(r.Qn[-1]), float(r.Pe[-1])
        if not (r.success and math.isfinite(Pn) and math.isfinite(Qn)):
            return s                                  # keep the state rather than poison it
        return Spin1State(Pe=Pe if math.isfinite(Pe) else s.Pe, Pn=Pn, Qn=Qn)
