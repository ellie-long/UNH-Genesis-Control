"""
logic_devices/target_status.py - the UNH target/DNP run-state logic IOC.

This is the UNH counterpart of the toolkit's status_ioc.py (J. Maxwell 2023,
Hall B cryotarget).  The design is his, unchanged:

  * an IOC that owns no instrument - it watches other IOCs' PVs, decides, and
    acts, exactly like a device IOC as far as master_ioc.py is concerned;
  * one mbbOut for the state the operator INTENDS and one for the material;
  * changing either one pushes a whole page of alarm limits and setpoints out
    to the instrument IOCs with caput, from a YAML table;
  * an mbbIn reports the state the system is ACTUALLY in, recomputed every
    `delay` seconds from the same PVs.

Three things differ from his, for reasons written at each one:

  * the state table is `states_file` in settings.yaml rather than a hard-coded
    'states.yaml', so this IOC can be run twice (target and, later, magnet);
  * transitions are only automatic when `auto` is set.  His cryotarget must
    stop filling by itself; a polarized target that changes its own run state
    without being asked is a surprise, so the default is to report and wait;
  * when conditions are not met, `unmet` names the first PV that failed and
    its value.  His sets a severity, which tells you something is wrong but
    not what - the question every operator asks next.

PVs (prefix from settings.yaml general:prefix, e.g. UNH:)

    status        mbbOut   intended state; writing applies the whole table
    material      mbbOut   Empty / ND3 / d-butanol / d-propanediol / NH3
    auto          boolOut  allow automatic state advances
    production    mbbIn    the state actually satisfied right now
    conditions    boolIn   1 = every condition of `status` is met
    unmet         stringIn first failing PV and its value, '' when all met
    state_applied stringIn '<state> / <material> at HH:MM:SS' of the last push

settings.yaml

    target_status:
      module: 'logic_devices.target_status'
      delay: 5
      states_file: 'logic_devices/states_unh.yaml'
      last_file:   'logic_devices/last_unh.yaml'
      channels: [None]
"""

import asyncio
import datetime
import logging
import math
import os

import aioca
import yaml
from softioc import alarm, builder

log = logging.getLogger(__name__)

# What `production` can report.  Index 0 is deliberately the unhappy one, so a
# dead or confused IOC reads "Not Ready" rather than something reassuring.
PRODUCTION = ["Not Ready", "Cooling", "Cold", "Polarizing", "TE Ready", "Annealing", "Warm"]


def _ok(value):
    """True when a reading is a real number.  UNH drivers report a sensor that
    is off, out of range or unplugged as NaN rather than raising, so every
    comparison below has to reject NaN before it compares."""
    try:
        return value is not None and not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


class Device:
    """Logic IOC: no connection, no instrument, only PVs and other PVs."""

    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.pv_aliases = dict(
            settings.get("pv_aliases") or {}
        )
        self.states_file = settings.get("states_file", "logic_devices/states_unh.yaml")
        self.last_file = settings.get("last_file", "logic_devices/last_unh.yaml")

        # Genesis physical deployments may carry an upstream state table
        # before its thresholds/channel mapping have been validated locally.
        # In that condition this IOC is strictly advisory: it may display the
        # requested state/material, but it must not push a state page or
        # auto-advance from placeholder conditions.
        self.apply_enabled = bool(
            settings.get("apply_enabled", True)
        )
        self.state_table_validated = bool(
            settings.get("state_table_validated", True)
        )

        with open(self.states_file) as f:
            self.states = yaml.safe_load(f)

        opts = self.states["options"]
        self.status_names = list(opts["status"])
        self.material_names = list(opts["material"])
        self.thresholds = opts.get("thresholds", {})
        self.advance = opts.get("advance", {})
        # which production index each state reports when its conditions hold
        self.reports = {k: PRODUCTION.index(v) for k, v in opts.get("reports", {}).items()
                        if v in PRODUCTION}

        self.pvs = {}
        self._create_pvs()

    # ------------------------------------------------------------------ PVs
    def _create_pvs(self):
        self.pvs["status"] = builder.mbbOut("status", *self.status_names,
                                            on_update_name=self.stat_update)
        self.pvs["material"] = builder.mbbOut("material", *self.material_names,
                                              on_update_name=self.stat_update)
        self.pvs["auto"] = builder.boolOut("auto", ZNAM="Manual", ONAM="Auto")
        self.pvs["production"] = builder.mbbIn("production", *PRODUCTION)
        self.pvs["conditions"] = builder.boolIn("conditions", ZNAM="Unmet", ONAM="Met")
        self.pvs["unmet"] = builder.stringIn("unmet", initial_value="")
        self.pvs["state_applied"] = builder.stringIn("state_applied", initial_value="never")

    # -------------------------------------------------------------- helpers
    def _selected(self):
        return (self.status_names[self.pvs["status"].get()],
                self.material_names[self.pvs["material"].get()])

    def _full_name(self, pv):
        """Resolve Farooq's state-table PV names into Genesis PVs."""

        if ":" in pv:
            return pv

        base, dot, field = pv.partition(".")
        resolved = self.pv_aliases.get(base)

        if resolved:
            return (
                resolved
                + (dot + field if dot else "")
            )

        return f"{self.device_name}:{pv}"

    async def try_put(self, pv, value):
        try:
            await aioca.caput(self._full_name(pv), value, timeout=2)
        except Exception as e:                      # noqa: BLE001 - one bad PV, not the IOC
            log.warning("put failed: %s = %r (%s)", self._full_name(pv), value, e)

    async def a_get(self, into, pv):
        try:
            into[pv] = await aioca.caget(self._full_name(pv), timeout=2)
        except Exception as e:                      # noqa: BLE001
            log.debug("get failed: %s (%s)", self._full_name(pv), e)
            into[pv] = float("nan")

    # ------------------------------------------------- applying a new state
    async def stat_update(self, value, pv):
        """status or material was written: push the whole page out.

        A list in the table is [HIHI, HIGH, LOW, LOLO] for that PV's alarm
        fields; a scalar is a value to write to the PV itself.  Same rule as
        his states.yaml, so a table written for one reads in the other."""
        status, material = self._selected()

        if not self.state_table_validated or not self.apply_enabled:
            self.pvs["state_applied"].set(
                "advisory only: unvalidated"
                if not self.state_table_validated
                else "advisory only: apply disabled"
            )
            self._remember()
            log.warning(
                "state %s / %s selected but not applied "
                "(validated=%s apply_enabled=%s)",
                status,
                material,
                self.state_table_validated,
                self.apply_enabled,
            )
            return

        table = self.states.get(status, {}) or {}
        log.info("applying state %s / %s (%d PVs)", status, material, len(table))

        group = []
        for name, by_material in table.items():
            if material not in by_material:
                continue
            target = by_material[material]
            if isinstance(target, list):
                for field, v in zip(("HIHI", "HIGH", "LOW", "LOLO"), target):
                    group.append(self.try_put(f"{name}.{field}", v))
            else:
                group.append(self.try_put(name, target))
        if group:
            await asyncio.gather(*group)

        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.pvs["state_applied"].set(f"{status}/{material} {stamp}"[:39])
        self._remember()
        log.info("state %s / %s applied", status, material)

    def _remember(self):
        # indices plus names: restore by name, so reordering a menu never
        # restores a different state or material
        si, mi = int(self.pvs["status"].get()), int(self.pvs["material"].get())
        last = {"status": si, "material": mi,
                "status_name": self.status_names[si], "material_name": self.material_names[mi]}
        try:
            os.makedirs(os.path.dirname(self.last_file) or ".", exist_ok=True)
            with open(self.last_file, "w") as f:
                yaml.dump(last, f)
        except OSError as e:
            log.warning("could not save last state: %s", e)

    def connect(self):
        """Restore the state this IOC was in when it was last stopped.

        Synchronous on purpose: master_ioc.py calls connect() without awaiting
        it, so the upstream `async def connect` never actually ran and the
        restore silently did nothing."""
        try:
            with open(self.last_file) as f:
                last = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            last = {}
        names = {"status": self.status_names, "material": self.material_names}
        for key in ("status", "material"):
            name = last.get(f"{key}_name")
            if name in names[key]:
                last[key] = names[key].index(name)
            if key in last:
                try:
                    self.pvs[key].set(int(last[key]))
                except Exception as e:              # noqa: BLE001
                    log.warning("could not restore %s: %s", key, e)
        log.info("restored previous state: %s", last or "defaults")

    # -------------------------------------------------------------- reading
    def _check(self, current, conditions):
        """Return (met, reason).  A condition is [low, high]: the reading must
        lie strictly between them, and NaN never counts as inside."""
        for name, limits in (conditions or {}).items():
            value = current.get(name)
            if not _ok(value):
                return False, f"{name}=no reading"
            low, high = float(limits[0]), float(limits[1])
            if not low < float(value) < high:
                return False, f"{name}={float(value):.4g} not in ({low:g},{high:g})"
        return True, ""

    async def do_reads(self):
        """Recompute the actual state from the PVs the table names."""
        status, material = self._selected()

        if not self.state_table_validated:
            self.pvs["conditions"].set(0)
            self.pvs["unmet"].set(
                "state table unvalidated"
            )
            self.pvs["production"].set(0)
            self.pvs["production"].set_alarm(
                severity=1,
                alarm=alarm.STATE_ALARM,
            )
            return True

        per_material = self.thresholds.get(material, {})
        conditions = per_material.get(status)

        if conditions is None:              # no rule for this state: nothing to judge
            self.pvs["conditions"].set(1)
            self.pvs["unmet"].set("")
            self.pvs["production"].set(self.reports.get(status, 0))
            return True

        names = sorted({n for c in per_material.values() for n in (c or {})})
        current = {}
        await asyncio.gather(*(self.a_get(current, n) for n in names))

        met, reason = self._check(current, conditions)
        self.pvs["conditions"].set(1 if met else 0)
        self.pvs["unmet"].set(reason[:39])

        if met:
            self.pvs["production"].set(self.reports.get(status, 0))
            self.pvs["production"].set_alarm(severity=0, alarm=alarm.NO_ALARM)
            nxt = self.advance.get(status)
            if nxt and self.pvs["auto"].get() and nxt in self.status_names:
                log.info("auto-advance %s -> %s (conditions met)", status, nxt)
                self.pvs["status"].set(self.status_names.index(nxt))
                await self.stat_update(self.status_names.index(nxt), "status")
        else:
            self.pvs["production"].set(0)           # Not Ready
            self.pvs["production"].set_alarm(severity=1, alarm=alarm.STATE_ALARM)
            log.debug("%s/%s not satisfied: %s", status, material, reason)

        return True
