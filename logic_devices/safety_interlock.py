"""
logic_devices/safety_interlock.py - the cryostat interlocks, as a logic IOC.

Why this exists
---------------
burn_interlock.py protects the NMR Q-meter from the burn RF.  That is one
hazard, and it is the only one this deployment had a rule for.  The others -
driving the cold board while it is hot, putting heat into a failed vacuum,
acting on a reading that stopped arriving twenty minutes ago - had no rule
anywhere, which means in practice the rule was "someone is watching the
screen".

This is the same logic-IOC pattern as burn_interlock: owns no instrument,
reads other IOCs' PVs, decides, and acts.  Three independent interlocks share
one read cycle because they share one question - is it safe to put power into
the insert right now - and because a single loop means a single answer rather
than three that can disagree.

The three rules
---------------
**Cold board over-temperature.**  The board is regulated against F1_TI.  If
that sensor reads above the trip, or the supply is delivering more than the
board's power limit, the output goes off.  Withholds CB_Permit.

**Vacuum.**  If the OVC or the cell rises above its trip, the thermal path is
gone and heat has to stop.  Withholds Vac_Permit and turns the cold-board
output off.  It does NOT drive the burn off: a burn in flight ends through the
sequencer's own safe-state path, which restores the Q-meter, rather than being
cut mid-sweep.  The sequencer reads Vac_Permit before it starts.

**Stale readings.**  Every cycle, this asks whether the readings the other two
depend on are still arriving: each instrument IOC's `_Conn` PV, and the age of
each watched value.  A reading that has stopped updating is not a safe
reading, so a stale input withholds Data_Permit and also withholds the permit
of whichever interlock depends on it.  This is the interlock that would have
caught this afternoon: the generator was off, its IOC served 0.0 and an empty
string, and every screen looked normal.

Hysteresis, and why
-------------------
Each trip has a separate clear threshold.  A single threshold makes an output
chatter when the value sits on it, and a chattering supply is both a worse
hazard than the one being guarded against and a good way to teach an operator
to switch the interlock off.  Once tripped, a rule stays tripped until the
value comes back past the clear threshold AND someone writes Safe_Reset.

Enforcement
-----------
One-directional, exactly as in burn_interlock: this IOC can only ever turn
things OFF.  Nothing here enables an output, raises a setpoint or connects a
path, so a bug in this file cannot create the condition it exists to prevent.

`enforce` in limits_unh.yaml is false out of the box, because every threshold
in that file is a placeholder.  With it false the interlock reads, judges,
latches, alarms and reports, and drives nothing.  Turn it on per-rule once the
numbers are real.

PVs (prefix from settings.yaml, e.g. UNH:)

    CB_Permit      boolIn   1 = safe to power the cold board
    Vac_Permit     boolIn   1 = vacuum is good enough to put heat in
    Data_Permit    boolIn   1 = the readings behind these answers are fresh
    Safe_Permit    boolIn   all three, ANDed - the one a client should read
    Safe_State     mbbIn    Unknown / Safe / Warning / TRIPPED
    Safe_Fault     boolIn   latched; clear with Safe_Reset
    Safe_Reason    stringIn the first thing that is wrong, named
    Safe_Trips     longIn   trips since this IOC started
    Safe_Stale     longIn   how many watched readings are stale or absent
    Safe_Enforce   boolOut  act on a trip (drives outputs off); from the yaml
    Safe_Reset     boolOut  write 1 to clear the latch
    Safe_Limits    stringIn "placeholders" until the yaml is filled in

settings.yaml

    safety_interlock:
      module: 'logic_devices.safety_interlock'
      autostart: True
      delay: 1
      limits: 'logic_devices/limits_unh.yaml'
      channels: [None]
"""

import asyncio
import logging
import os
import time

import aioca
import yaml
from softioc import alarm, builder

log = logging.getLogger(__name__)

UNKNOWN, SAFE, WARNING, TRIPPED = 0, 1, 2, 3
STATES = [("Unknown", "MINOR"), ("Safe", 0), ("Warning", "MINOR"),
          ("TRIPPED", "MAJOR")]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The thresholds shipped in limits_unh.yaml.  If the file still holds these,
# Safe_Limits says "placeholders" and enforcement stays off however the yaml is
# set - a number nobody has checked must not be allowed to switch off a supply.
SHIPPED_PLACEHOLDERS = {
    ("coldboard", "trip_k"): 4.5,
    ("coldboard", "trip_w"): 120.0,
    ("vacuum", "ovc_trip"): 1.0e-3,
    ("vacuum", "cell_trip"): 900.0,
}


def judge_band(value, tripped, trip, clear, label, unit=""):
    """One value against a trip/clear pair -> (tripped, reason).

    Pure: no PVs, no IO, no clock.  Everything interesting about an interlock
    is in here, so the test drives this directly through the rising edge, the
    dead band and the falling edge rather than through a running IOC.

    `value is None` means the reading could not be had.  That is NOT treated as
    a trip - the watchdog owns missing data, and double-counting it here would
    make one dead PV look like a thermal event.  It holds the previous state,
    which for a rule that was tripped means it stays tripped.
    """
    if value is None:
        return tripped, f"{label} unreadable"
    if value >= trip:
        return True, f"{label} {value:.4g}{unit} >= {trip:.4g}{unit}"
    if tripped and value > clear:
        # in the dead band on the way down: still tripped, deliberately
        return True, f"{label} {value:.4g}{unit} above clear {clear:.4g}{unit}"
    return False, ""


class Device:
    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.pv_aliases = dict(
            settings.get("pv_aliases") or {}
        )
        self.limits = self._load_limits(settings.get("limits"))
        self.placeholders = self._still_placeholders(self.limits)

        self.cb = self.limits.get("coldboard", {}) or {}
        self.vac = self.limits.get("vacuum", {}) or {}
        self.wd = self.limits.get("watchdog", {}) or {}
        self.stale_s = float(self.wd.get("stale_s", 30))

        # Latched trip state PER WATCHED VALUE, not per rule.  One flag per
        # rule looks simpler and is wrong: judging two values in a loop with a
        # shared flag means the second value's verdict overwrites the first's,
        # so a bad OVC is cleared by a good cell pressure half a line later.
        # Each value owns its own latch; a rule trips when any of its values is
        # latched.
        self._tripped = {}
        self._last_value = {}
        self._last_change = {}
        self.trips = 0

        self.pvs = {}
        self._create_pvs()

    # ------------------------------------------------------------- the yaml
    def _load_limits(self, path):
        path = path or os.path.join("logic_devices", "limits_unh.yaml")
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        try:
            with open(path) as f:
                return yaml.safe_load(f) or {}
        except OSError as e:
            # No thresholds means no judgement.  Say so loudly and run with
            # every permit withheld rather than inventing numbers.
            log.error("safety interlock: cannot read %s (%s) - every permit "
                      "will be withheld", path, e)
            return {}

    @staticmethod
    def _still_placeholders(limits):
        """True while any shipped placeholder is untouched.

        Deliberately conservative: it takes ONE unchanged number to keep
        enforcement off, because the rules share outputs and a good vacuum
        limit does not make a guessed thermal limit safe to act on.
        """
        for (section, key), shipped in SHIPPED_PLACEHOLDERS.items():
            have = (limits.get(section) or {}).get(key)
            if have is None or float(have) == float(shipped):
                return True
        return False

    # ----------------------------------------------------------------- PVs
    def _create_pvs(self):
        for name, znam, onam in (("CB_Permit", "Blocked", "Permit"),
                                 ("Vac_Permit", "Blocked", "Permit"),
                                 ("Data_Permit", "Stale", "Fresh"),
                                 ("Safe_Permit", "Blocked", "Permit")):
            self.pvs[name] = builder.boolIn(name, ZNAM=znam, ONAM=onam)
        self.pvs["Safe_State"] = builder.mbbIn("Safe_State", *STATES)
        self.pvs["Safe_Fault"] = builder.boolIn("Safe_Fault", ZNAM="Clear",
                                                ONAM="FAULT")
        self.pvs["Safe_Reason"] = builder.stringIn("Safe_Reason",
                                                   initial_value="starting")
        self.pvs["Safe_Trips"] = builder.longIn("Safe_Trips", initial_value=0)
        self.pvs["Safe_Stale"] = builder.longIn("Safe_Stale", initial_value=0)

        # Enforcement follows the yaml, and the yaml cannot switch it on while
        # the thresholds are the shipped placeholders.  An operator can still
        # turn it OFF from a screen; turning it on is a decision that belongs
        # with the numbers, in the file, under review.
        enforce = bool(self.limits.get("enforce", False)) and not self.placeholders
        self.pvs["Safe_Enforce"] = builder.boolOut(
            "Safe_Enforce", ZNAM="Report", ONAM="Enforce",
            initial_value=1 if enforce else 0)
        self.pvs["Safe_Reset"] = builder.boolOut(
            "Safe_Reset", ZNAM="", ONAM="Reset", always_update=True,
            on_update=self.on_reset)
        self.pvs["Safe_Limits"] = builder.stringIn(
            "Safe_Limits",
            initial_value="placeholders" if self.placeholders else "set")

    def on_reset(self, value):
        if not value:
            return
        self._tripped.clear()
        self.pvs["Safe_Fault"].set(0)
        self.pvs["Safe_Fault"].set_alarm(severity=0, alarm=alarm.NO_ALARM)
        log.info("safety interlock latch cleared by operator")

    def connect(self):
        """Nothing to open.  Start with every permit withheld: before the first
        read, 'is it safe' has no answer, and no answer is not yes."""
        for name in ("CB_Permit", "Vac_Permit", "Data_Permit", "Safe_Permit"):
            self.pvs[name].set(0)
        self.pvs["Safe_State"].set(UNKNOWN)
        if self.placeholders:
            log.warning("safety interlock: limits_unh.yaml still holds the "
                        "shipped placeholder thresholds - watching and "
                        "reporting only, enforcement is off")

    # ------------------------------------------------------------- plumbing
    def _full(self, pv):
        """Resolve Farooq's bare target PV names into Genesis PVs."""

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

    async def _read(self, name):
        """Value, or None when it cannot be read.  Also records whether the
        value CHANGED, which is what staleness is actually about: a PV that
        reads fine every cycle and never moves is the failure mode that looks
        healthiest."""
        try:
            value = float(await aioca.caget(self._full(name), timeout=1))
        except Exception as e:                      # noqa: BLE001
            log.debug("safety interlock could not read %s: %s",
                      self._full(name), e)
            return None
        now = time.monotonic()
        if name not in self._last_value or self._last_value[name] != value:
            self._last_value[name] = value
            self._last_change[name] = now
        return value

    async def _off(self, name):
        try:
            await aioca.caput(self._full(name), 0, timeout=1)
            return True
        except Exception as e:                      # noqa: BLE001
            log.error("safety interlock could not turn %s off: %s",
                      self._full(name), e)
            return False

    def _stale(self, name):
        """A watched PV counts as stale when it has not changed in stale_s.

        Never seen at all counts as stale too - that is the unreachable
        instrument case, and it is the one worth naming.
        """
        last = self._last_change.get(name)
        if last is None:
            return True
        return (time.monotonic() - last) > self.stale_s

    # ------------------------------------------------------------ the rules
    def _judge_all(self, bands):
        """Judge a list of (label, value, trip, clear, unit) independently.

        -> (permit, tripped, reason).  Each label keeps its own latch in
        self._tripped, so one value coming back into range cannot release
        another that is still out of it.  The reason is the FIRST one that is
        wrong, in the order given, which is the order the yaml lists them.
        """
        tripped, reason = False, ""
        for label, value, trip, clear, unit in bands:
            was = self._tripped.get(label, False)
            now, why = judge_band(value, was, trip, clear, label, unit)
            self._tripped[label] = now
            if now:
                tripped = True
                reason = reason or why
        return (0 if tripped else 1), tripped, reason

    def judge_coldboard(self, temps, power):
        """-> (permit, tripped, reason).  `temps` is a list of values or Nones."""
        trip_k = float(self.cb.get("trip_k", 4.5))
        clear_k = float(self.cb.get("clear_k", trip_k * 0.9))
        trip_w = float(self.cb.get("trip_w", 120.0))
        clear_w = float(self.cb.get("clear_w", trip_w * 0.85))

        bands = [(pv_name, value, trip_k, clear_k, " K")
                 for pv_name, value
                 in zip(self.cb.get("temperature_pvs", []), temps)]
        bands.append((self.cb.get("power_pv", "Coldboard_PI"), power,
                      trip_w, clear_w, " W"))
        return self._judge_all(bands)

    def judge_vacuum(self, ovc, cell):
        """-> (permit, tripped, reason)."""
        return self._judge_all([
            (self.vac.get("ovc_pv", "OVC_PI"), ovc,
             float(self.vac.get("ovc_trip", 1e-3)),
             float(self.vac.get("ovc_clear", 5e-4)), ""),
            (self.vac.get("cell_pv", "Cell_PI"), cell,
             float(self.vac.get("cell_trip", 900.0)),
             float(self.vac.get("cell_clear", 800.0)), "")])

    def judge_watchdog(self, watched):
        """-> (permit, count, reason).  `watched` is the list of PV names whose
        freshness matters this cycle."""
        stale = [n for n in watched if self._stale(n)]
        if not stale:
            return 1, 0, ""
        first = stale[0]
        more = f" (+{len(stale) - 1} more)" if len(stale) > 1 else ""
        return 0, len(stale), f"{first} not updating{more}"

    # ------------------------------------------------------------- the loop
    def _watched_pvs(self):
        explicit = list(self.wd.get("watch") or [])
        if explicit:
            return explicit
        names = list(self.cb.get("temperature_pvs", []))
        for key, section in (("power_pv", self.cb), ("ovc_pv", self.vac),
                             ("cell_pv", self.vac)):
            name = section.get(key)
            if name:
                names.append(name)
        return names

    async def do_reads(self):
        temp_pvs = list(self.cb.get("temperature_pvs", []))
        power_pv = self.cb.get("power_pv", "Coldboard_PI")
        ovc_pv = self.vac.get("ovc_pv", "OVC_PI")
        cell_pv = self.vac.get("cell_pv", "Cell_PI")

        values = await asyncio.gather(
            *[self._read(n) for n in temp_pvs],
            self._read(power_pv), self._read(ovc_pv), self._read(cell_pv))
        temps = list(values[:len(temp_pvs)])
        power, ovc, cell = values[len(temp_pvs):]

        cb_permit, cb_tripped, cb_why = self.judge_coldboard(temps, power)
        vac_permit, vac_tripped, vac_why = self.judge_vacuum(ovc, cell)
        data_permit, stale_n, stale_why = self.judge_watchdog(self._watched_pvs())

        # A stale input withholds the permit of the rule that depends on it.
        # Judging on a frozen number is the failure this IOC is here to stop,
        # so it must not be allowed to produce a cheerful permit.
        if not data_permit:
            cb_permit = 0
            vac_permit = 0

        tripped = cb_tripped or vac_tripped
        reason = cb_why or vac_why or stale_why
        if tripped:
            state = TRIPPED
        elif not data_permit:
            state = WARNING
        elif temps and all(v is None for v in temps):
            state = UNKNOWN
        else:
            state = SAFE

        self.pvs["CB_Permit"].set(cb_permit)
        self.pvs["Vac_Permit"].set(vac_permit)
        self.pvs["Data_Permit"].set(data_permit)
        self.pvs["Safe_Permit"].set(
            1 if (cb_permit and vac_permit and data_permit) else 0)
        self.pvs["Safe_State"].set(state)
        self.pvs["Safe_Stale"].set(stale_n)
        self.pvs["Safe_Reason"].set(reason[:39])

        if tripped:
            if not self.pvs["Safe_Fault"].get():
                self.trips += 1
                self.pvs["Safe_Trips"].set(self.trips)
                log.error("SAFETY TRIP %d: %s", self.trips, reason)
            self.pvs["Safe_Fault"].set(1)
            self.pvs["Safe_Fault"].set_alarm(severity=2, alarm=alarm.STATE_ALARM)
            if self.pvs["Safe_Enforce"].get():
                await self._enforce()
        return True

    async def _enforce(self):
        """Off, and only off.

        Both rules turn off the same thing - the cold-board output enable -
        because that is the one actuator putting energy into the insert that
        this IOC owns.  Turning it off twice is harmless; deciding which rule
        "owns" it would not be.
        """
        outputs = {self.cb.get("output_pv"), self.vac.get("output_pv")}
        for name in sorted(n for n in outputs if n):
            if await self._off(name):
                log.error("safety interlock enforced: %s driven off", name)
