"""Magnet operations: the student's sequences (Magnet_V3, updated to V4), with the review's fixes.

Student -> here (MagnetSequencer methods unless noted):
    next_ramp_target           next_ramp_target           (module function; with V4's one 3 A/min
                                                           band it returns the target: no segments)
    choose_sweep_rate          choose_sweep_rate          (module function, issue 1)
    verify_safe_for_H1 (V4)    verify_safe_for_H1         (V3 verify_safe_for_H2; issues 4, 7)
    heater_turn_on_progress    _heater_wait("on")         (20 s, Ells)
    heater_turn_off_progress   _heater_wait("off")        (V4: its own bar)
    verify_heater_on_readback  heater_on_verified         (V4: X = H1 and R20 heater current)
    verify_heater_off_readback heater_off_verified        (V4, with the R20 meaning, see below)
    switch_heater_on           heater_on                  (V4: H1, the supply's own match check)
    switch_heater_off          heater_off
    validate_current           validate_current           (module function)
    check_abort                _check                     (+ permit, faults, issue 8)
    recover_persistent_mode    _match_to                  (deadline, issue 11)
    match_psu_to_magnet        match
    require_driven_mode_for_ramp   _require_driven         (V4: a ramp never opens the switch)
    verify_driven_mode_during_ramp _check_driven           (V4: HOLD if driven mode is lost)
    startup_inductance_check / require_inductance_ok   check_inductance / _require_inductance
    inductance_override_changed    inductance_override (attribute; the IGNORE_INDUCTANCE PV)
    execute_ramp               ramp                       (issues 1, 6, 11, 12, 17)
    hold_magnet                hold                       (never blocked: V4 blocks HOLD on a bad
                                                           R24; not ported)
    (new)                      outage                     (the red button, issue 19)

Review issues fixed here (magnet_student/docs/3_ISSUES.md):
    1  rate from the band the segment lies in, not from the current reading
    2  quench / fault in the status word stops everything
    3  one operation at a time (HOLD and the outage button excepted; V4 has the same gate)
    4  R0 / R16 read twice before the switch opens
    6  independent 3 A/min cap: before S, R6 read back, and while ramping
    8  the abort flag is cleared when an operation starts
    11 every wait has a deadline
    12 a segment ends at rest, not only on R0
    13 the cryogenics / ramp-block permit before and during every move
    17 the inductance checked before (R24, V4's 0.3 H) and while ramping (V / dI/dt)
    + Ells: 20 s after heater ON and OFF with no current change; persistent
      mode keeps the supply energized this cool-down (a setting)

R20 (the switch heater current, mA): the manual lists it with the stored
settings (Sup 15, p52); V4 reads it as the measured current (about 0 with the
heater off).  ``r20_meaning`` is "setting", "measured" or "auto" (decided the
first time the heater is seen OFF: about 0 means measured).  Heater ON needs
X = H1 and R20 above ``heater_on_min_ma`` in either meaning; heater OFF
needs X = H0 / H2, plus R20 about 0 when R20 is the measured current.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field

from .ips120_protocol import IPSClient, IPSError, Status


class Refused(Exception):
    """A check refused the operation before anything was sent."""


class Aborted(Exception):
    """The operation was stopped (HOLD, a trip, a fault, the outage button)."""


# ------------------------------------------------------------------ config
@dataclass
class MagnetConfig:
    max_current: float = 65.0                 # A, software limit (AMI rated 67.9 A)
    #: (upper current of the band, A; rate in it, A/min), ascending.  V4: one
    #: fixed 3 A/min for every ramp; V3 had 3 / 2 / 1 below 20 / 40 / 65 A.
    rate_bands: tuple = ((65.0, 3.0),)
    #: independent cap (Ells): never more, whatever the band table says
    max_sweep_rate: float = 3.0               # A/min
    current_tolerance: float = 0.01           # A
    match_tolerance: float = 0.01             # A
    #: V4: the supply's own H1 check needs the supply on the persistent
    #: current more tightly than the general match
    h1_match_tolerance: float = 0.0005        # A
    heater_wait_s: float = 20.0               # after heater ON and after heater OFF (Ells)
    poll_s: float = 1.0
    inductance_h: float = 38.0                # AMI specification
    inductance_tolerance_h: float = 0.3       # R24, V4 (question 92)
    measured_inductance_tolerance: float = 0.25   # from V / dI/dt while ramping (fraction)
    measured_inductance_window_s: float = 30.0
    heater_ma: float = 40.0                   # AMI specification (warned, not enforced)
    heater_ma_tolerance: float = 5.0
    heater_on_min_ma: float = 1.0             # V4: heater ON needs R20 clearly above zero
    heater_off_max_ma: float = 0.5            # V4: heater OFF, R20 measured: about zero
    r20_meaning: str = "auto"                 # "auto" | "setting" | "measured"
    #: V4: a ramp never opens the switch (match and heater ON are separate,
    #: explicit operator steps).  True = V3's behaviour (review issue 7).
    ramp_opens_switch: bool = False
    #: the rate seen in successive R0 readings is a backstop for gross errors
    #: (the cap itself is enforced exactly on S and the R6 read-back)
    observed_rate_margin: float = 0.25
    observed_rate_window_s: float = 30.0
    deadline_factor: float = 1.5              # expected sweep time x this + slack
    deadline_slack_s: float = 60.0
    persistent_supply: str = "hold"           # "hold" (this cool-down, Ells) or "run_down"
    outage_leads: str = "hold"                # red button: "hold" or "zero" (question 94)

    @classmethod
    def from_settings(cls, s: dict) -> "MagnetConfig":
        c = cls()
        for k in c.__dataclass_fields__:
            if k in s:
                v = s[k]
                if k == "rate_bands":
                    v = tuple((float(a), float(b)) for a, b in v)
                elif isinstance(getattr(c, k), bool):
                    v = bool(v)
                elif isinstance(getattr(c, k), float):
                    v = float(v)
                setattr(c, k, v)
        c.validate()
        return c

    def validate(self):
        if self.persistent_supply not in ("hold", "run_down"):
            raise ValueError("persistent_supply must be 'hold' or 'run_down'")
        if self.outage_leads not in ("hold", "zero"):
            raise ValueError("outage_leads must be 'hold' or 'zero'")
        if self.r20_meaning not in ("auto", "setting", "measured"):
            raise ValueError("r20_meaning must be 'auto', 'setting' or 'measured'")
        uppers = [b[0] for b in self.rate_bands]
        if uppers != sorted(uppers) or uppers[-1] < self.max_current:
            raise ValueError("rate_bands must ascend and cover max_current")
        # review issue 6: the table may never exceed the independent cap
        for upper, rate in self.rate_bands:
            if not (0 < rate <= self.max_sweep_rate):
                raise ValueError(f"band rate {rate} A/min (to {upper} A) exceeds the "
                                 f"{self.max_sweep_rate} A/min cap")


# ------------------------------------------------------------------ the student's helpers
def validate_current(current: float, name: str, max_current: float) -> None:
    if not (math.isfinite(current) and 0.0 <= current < max_current):
        raise Refused(f"{name} outside 0-{max_current:g} A: {current:.3f}")


def next_ramp_target(current: float, target: float, tolerance: float, bands) -> float:
    """Split a ramp at the band edges (V3's 20 A and 40 A; none with V4's one band)."""
    if abs(current - target) <= tolerance:
        return target
    edges = [b[0] for b in bands[:-1]]
    if target > current:
        for edge in edges:
            if current < edge - tolerance and target > edge:
                return edge
        return target
    for edge in reversed(edges):
        if current > edge + tolerance and target < edge:
            return edge
    return target


def choose_sweep_rate(current: float, next_target: float, bands) -> float:
    """The rate of the band the segment lies in.

    Review issue 1: V3 took the band of ``min(current, next)``, so a segment
    starting at 19.995 A (within tolerance of the 20 A edge) was swept at the
    0-20 A rate all the way to 40 A.  The segment never crosses an edge
    (next_ramp_target splits there), so its midpoint is inside its band."""
    mid = 0.5 * (current + next_target)
    for upper, rate in bands:
        if mid < upper:
            return rate
    return bands[-1][1]


def expected_sweep_s(delta_a: float, rate_a_per_min: float) -> float:
    return abs(delta_a) / rate_a_per_min * 60.0


# ------------------------------------------------------------------ sequencer
@dataclass
class _Track:
    """Recent (time, current, voltage) samples while ramping."""
    samples: list = field(default_factory=list)

    def add(self, t, i, v):
        self.samples.append((t, i, v))
        self.samples = [s for s in self.samples if t - s[0] <= 120.0]

    def rate_a_per_min(self, window_s: float) -> float | None:
        if len(self.samples) < 2:
            return None
        t1, i1, _ = self.samples[-1]
        old = [s for s in self.samples if t1 - s[0] >= window_s]
        if not old:
            return None
        t0, i0, _ = old[-1]
        return (i1 - i0) / (t1 - t0) * 60.0

    def inductance(self, window_s: float, r_leads_ohm: float) -> float | None:
        old = [s for s in self.samples if self.samples[-1][0] - s[0] >= window_s]
        if not old or len(self.samples) < 3:
            return None
        t0, i0, _ = old[-1]
        recent = [s for s in self.samples if s[0] >= t0]
        t1, i1, _ = recent[-1]
        didt = (i1 - i0) / (t1 - t0)
        if abs(didt) < 0.5 / 60.0:                # below 0.5 A/min: too slow to tell
            return None
        v = sum(s[2] - s[1] * r_leads_ohm for s in recent) / len(recent)
        return v / didt


class MagnetSequencer:
    """Runs one magnet operation at a time against an IPSClient.

    permit():       async () -> (ok, reason): the cryogenics precondition
    event():        (kind, message, **data) -> None, recorded
    state():        (text) -> None, the operation state for the screen
    progress():     (percent) -> None, the heater-ON wait bar
    progress_off(): (percent) -> None, the heater-OFF wait bar (V4)
    heater():       (text) -> None, the heater verification text (V4)
    """

    def __init__(self, ips: IPSClient, cfg: MagnetConfig, *, permit, event, state,
                 progress=lambda pct: None, progress_off=lambda pct: None,
                 heater=lambda text: None, sleep=asyncio.sleep, clock=time.monotonic):
        self.ips, self.cfg = ips, cfg
        self.permit, self.event, self.set_state, self.progress = permit, event, state, progress
        self.progress_off, self.set_heater_text = progress_off, heater
        self.sleep, self.clock = sleep, clock
        self._task: asyncio.Task | None = None
        self._op = ""
        self._abort = asyncio.Event()
        self.last_inductance_h = math.nan
        #: R24 against this magnet (V4): None until read, then True / False
        self.inductance_ok: bool | None = None
        self.inductance_r24 = math.nan
        #: the operator's IGNORE_INDUCTANCE (V4); never on at start-up
        self.inductance_override = False
        self.r20_meaning = None if cfg.r20_meaning == "auto" else cfg.r20_meaning

    # ------------------------------------------------------------ plumbing
    async def _io(self, fn, *args):
        return await asyncio.to_thread(fn, *args)

    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def operation(self) -> str:
        return self._op if self.busy else ""

    def start(self, name: str, coro_fn, *args) -> bool:
        """Start an operation, unless one is running (review issue 3; V4's
        "TOO MANY COMMANDS - one at a time")."""
        if self.busy:
            self.event("refused", f"{name}: too many commands, '{self._op}' is still running")
            self.set_state(f"REJECTED: {self._op} active"[:39])
            return False
        self._op = name
        self._abort.clear()                      # review issue 8: never a stale abort
        self._task = asyncio.ensure_future(self._run(name, coro_fn, *args))
        return True

    async def _run(self, name, coro_fn, *args):
        try:
            await coro_fn(*args)
        except Refused as exc:
            self.event("refused", f"{name}: {exc}")
            self.set_state(f"REFUSED: {exc}"[:39])
        except Aborted as exc:
            await self._hold_quietly()
            self.event("stopped", f"{name}: {exc}")
            self.set_state(f"STOPPED: {exc}"[:39])
        except Exception as exc:                 # noqa: BLE001 - always end in HOLD
            await self._hold_quietly()
            self.event("error", f"{name}: {type(exc).__name__}: {exc}")
            self.set_state(f"ERROR: {exc}"[:39])
        finally:
            self.progress(0.0)
            self.progress_off(0.0)

    async def _hold_quietly(self):
        try:
            st = await self._io(self.ips.read_status)
            if not st.clamped:
                await self._io(self.ips.hold)
        except Exception:                        # noqa: BLE001 - best effort
            pass

    async def _check(self, *, need_permit=True, status: Status | None = None):
        """Stop on HOLD / outage, on a lost permit, and on a supply fault."""
        if self._abort.is_set():
            raise Aborted("HOLD requested")
        if status is not None and status.fault:
            raise Aborted(f"supply status {status.text()['system']}")
        if need_permit:
            ok, reason = await self.permit()
            if not ok:
                raise Aborted(f"blocked: {reason}")

    async def _require_permit(self):
        """Before anything is sent: a blocked permit refuses (nothing moved)."""
        ok, reason = await self.permit()
        if not ok:
            raise Refused(f"blocked: {reason}")

    async def _wait(self, seconds: float, *, need_permit=True, bar=None):
        """Wait in poll steps, checking as we go."""
        t0 = self.clock()
        while True:
            elapsed = self.clock() - t0
            if bar is not None:
                bar(min(100.0, 100.0 * elapsed / seconds) if seconds > 0 else 100.0)
            if elapsed >= seconds:
                return
            await self._check(need_permit=need_permit)
            await self.sleep(min(self.cfg.poll_s, seconds - elapsed))

    # ------------------------------------------------------------ inductance (V4)
    async def check_inductance(self) -> bool:
        """R24 against this magnet's 38 H (V4: at start-up, a read only)."""
        return self.note_inductance(await self._io(self.ips.read_inductance))

    def note_inductance(self, L: float) -> bool:
        """Record an R24 reading (the start-up read, the slow poll, a move)."""
        self.inductance_r24 = L
        ok = math.isfinite(L) and abs(L - self.cfg.inductance_h) <= self.cfg.inductance_tolerance_h
        if ok != self.inductance_ok:
            self.event("inductance", f"R24 {L:g} H: {'OK' if ok else 'MISMATCH'} "
                       f"(expected {self.cfg.inductance_h:g} +/- {self.cfg.inductance_tolerance_h:g} H)")
        self.inductance_ok = ok
        return ok

    def inductance_text(self) -> str:
        if self.inductance_override:
            return "BYPASS"
        if self.inductance_ok is None:
            return "NOT CHECKED"
        return (f"OK - {self.inductance_r24:.2f} H" if self.inductance_ok
                else f"MISMATCH - {self.inductance_r24:.2f} H")

    async def _require_inductance(self):
        """Every move (not HOLD, heater OFF or the outage button): R24 read
        again now and within tolerance, unless the operator bypassed it."""
        ok = await self.check_inductance()
        if not ok and not self.inductance_override:
            raise Refused(f"inductance mismatch: R24 {self.inductance_r24:g} H")
        if not ok:
            self.event("inductance", "R24 check bypassed by the operator (IGNORE_INDUCTANCE)")

    # ------------------------------------------------------------ heater read-back (V4)
    def observe_r20(self, st: Status, ma: float) -> None:
        """Decide what R20 is, the first time the heater is seen OFF
        (``r20_meaning: auto``)."""
        if self.r20_meaning is None and st.heater_off and math.isfinite(ma):
            self.r20_meaning = "measured" if abs(ma) <= self.cfg.heater_off_max_ma else "setting"
            self.event("heater", f"R20 reads {ma:g} mA with the heater OFF: R20 is the "
                       f"{'measured heater current' if self.r20_meaning == 'measured' else 'heater current setting'}")

    def heater_on_verified(self, st: Status, ma: float) -> tuple[bool, str]:
        ok = st.heater_on and ma >= self.cfg.heater_on_min_ma
        return ok, (f"SWITCH HEATER ON - {ma:.2f} mA" if ok
                    else f"ON NOT VERIFIED - H{st.heater} {ma:.2f} mA")

    def heater_off_verified(self, st: Status, ma: float) -> tuple[bool, str]:
        self.observe_r20(st, ma)
        quiet = self.r20_meaning != "measured" or abs(ma) <= self.cfg.heater_off_max_ma
        ok = st.heater_off and quiet
        return ok, (f"SWITCH HEATER OFF - {ma:.2f} mA" if ok
                    else f"OFF NOT VERIFIED - H{st.heater} {ma:.2f} mA")

    def heater_text(self, st: Status, ma: float) -> str:
        """The live heater verification text (V4's HEATER_CURRENT_STATE)."""
        if st.heater_on:
            return self.heater_on_verified(st, ma)[1]
        if st.heater_off:
            return self.heater_off_verified(st, ma)[1]
        return f"HEATER H{st.heater} - {ma:.2f} mA"

    # ------------------------------------------------------------ HOLD
    async def hold(self, source: str = "operator"):
        """Stop at once.  Always allowed: never needs the cryogenics or the
        inductance check.  A clamped supply is left clamped (no manual unclamp)."""
        self._abort.set()
        self.set_state("HOLD REQUESTED")
        try:
            await self._io(self.ips.remote_unlocked)
            st = await self._io(self.ips.read_status)
            if st.clamped:
                self.set_state("CLAMPED")
            else:
                await self._io(self.ips.hold)
                self.set_state("HOLD")
            self.event("hold", f"HOLD ({source})")
        except Exception as exc:                 # noqa: BLE001
            self.event("error", f"HOLD failed: {exc}")
            self.set_state(f"HOLD ERROR: {exc}"[:39])

    # ------------------------------------------------------------ preflight
    async def preflight(self) -> Status:
        """Before anything moves: no fault, the supply set up for this magnet."""
        st = await self._io(self.ips.read_status)
        if st.fault:
            raise Refused(f"supply status {st.text()['system']}")
        await self._require_inductance()                     # issue 17, V4
        return st

    async def _read_currents_twice(self):
        """Review issue 4: R0 and R16 each read twice and required to agree,
        so a late or mis-assigned reply cannot pass the switch check."""
        r0a = await self._io(self.ips.read_demand_current)
        r16a = await self._io(self.ips.read_persistent_current)
        r0b = await self._io(self.ips.read_demand_current)
        r16b = await self._io(self.ips.read_persistent_current)
        tol = self.cfg.match_tolerance
        if abs(r0a - r0b) > tol or abs(r16a - r16b) > tol:
            raise Refused("R0 / R16 readings disagree; communication not trusted")
        return r0b, r16b

    async def _hold_and_settle(self) -> Status:
        await self._io(self.ips.remote_unlocked)
        await self._io(self.ips.hold)
        await self.sleep(1.0)                     # give HOLD time to take effect (V4)
        return await self._io(self.ips.read_status)

    # ------------------------------------------------------------ H1 check
    async def verify_safe_for_H1(self) -> Status:
        """V4's software check before H1 (H1 also runs the supply's own
        current-match interlock).  HOLD, at rest, R0 and R16 in range and
        within ``h1_match_tolerance``; a closed switch with the supply not
        matched is matched first (the magnet is untouched)."""
        st = await self._hold_and_settle()
        if st.heater_on:
            return st
        if not st.heater_off:
            raise Refused(f"unsafe heater state H{st.heater}")
        if st.activity != "0":
            raise Refused(f"supply not in HOLD (A{st.activity})")
        if not st.at_rest:
            raise Refused(f"supply not at rest (M{st.motion})")
        r0, r16 = await self._read_currents_twice()
        validate_current(r0, "supply current", self.cfg.max_current)
        validate_current(r16, "persistent current", self.cfg.max_current)
        if abs(r0 - r16) > self.cfg.h1_match_tolerance:
            await self._match_to(r16, tolerance=self.cfg.h1_match_tolerance)
            r0, r16 = await self._read_currents_twice()
        if abs(r0 - r16) > self.cfg.h1_match_tolerance:
            raise Refused(f"H1 match failed: R0 {r0:.4f} A, R16 {r16:.4f} A")
        st = await self._hold_and_settle()                   # re-check after any matching
        if st.activity != "0" or not st.at_rest:
            raise Refused(f"H1 precheck failed: A{st.activity} M{st.motion}")
        return st

    verify_safe_for_H2 = verify_safe_for_H1                  # the V3 name

    # ------------------------------------------------------------ heater
    async def _heater_wait(self, which: str, *, need_permit: bool):
        """20 s after heater ON and after heater OFF, with no current change
        (Ells): the operation lock keeps every other command out meanwhile."""
        bar = self.progress if which == "on" else self.progress_off
        self.progress(0.0)
        self.progress_off(0.0)
        await self._wait(self.cfg.heater_wait_s, need_permit=need_permit, bar=bar)

    async def heater_on(self):
        await self._require_permit()
        await self.preflight()
        st = await self.verify_safe_for_H1()
        if not st.heater_on:
            self.set_state("OPENING SWITCH (H1)")
            self.event("heater", "heater ON (H1) after the R0/R16 check")
            try:
                await self._io(self.ips.heater_on)
            except IPSError as exc:
                r0 = await self._io(self.ips.read_demand_current)
                r16 = await self._io(self.ips.read_persistent_current)
                raise Aborted(f"H1 rejected: R0 {r0:.4f}, R16 {r16:.4f}") from exc
            await self._heater_wait("on", need_permit=True)
            st = await self._io(self.ips.read_status)
            await self._check(status=st)
            if not st.heater_on:
                raise Aborted(f"switch did not open after H1 (H{st.heater})")
        ma = await self._io(self.ips.read_heater_current)
        ok, text = self.heater_on_verified(st, ma)
        self.set_heater_text(text)
        if not ok:
            raise Aborted(f"heater ON not verified: H{st.heater}, R20 {ma:.2f} mA")
        if abs(ma - self.cfg.heater_ma) > self.cfg.heater_ma_tolerance:
            self.event("heater", f"R20 {ma:g} mA; the AMI specification is {self.cfg.heater_ma:g} mA")
        self.set_state("HEATER ON / HOLD")
        self.event("heater", f"switch open (driven), R20 {ma:g} mA")

    async def heater_off(self):
        """Close the switch: always allowed (it makes the magnet persistent)."""
        st = await self._hold_and_settle()
        if not st.at_rest:
            raise Refused(f"supply not at rest (M{st.motion})")
        self.set_state("CLOSING SWITCH (H0)")
        self.event("heater", "heater OFF (H0)")
        await self._io(self.ips.heater_off)
        self.set_state("HEATER OFF - COOLING 20 s")
        await self._heater_wait("off", need_permit=False)
        st = await self._io(self.ips.read_status)
        if not st.heater_off:
            raise Aborted(f"heater did not turn off (H{st.heater})")
        ma = await self._io(self.ips.read_heater_current)
        ok, text = self.heater_off_verified(st, ma)
        self.set_heater_text(text)
        if not ok:
            raise Aborted(f"heater OFF not verified: H{st.heater}, R20 {ma:.2f} mA")
        self.event("heater", f"switch closed (persistent), R20 {ma:g} mA")
        if self.cfg.persistent_supply == "run_down":
            await self._leads_to_zero()
        self.set_state("PERSISTENT / SUPPLY HELD" if self.cfg.persistent_supply == "hold"
                       else "PERSISTENT / LEADS AT ZERO")

    async def _leads_to_zero(self):
        """With the switch closed only the leads move (IMMEDIATE mode)."""
        self.set_state("LEADS TO ZERO")
        self.event("leads", "supply to zero (A2), switch closed")
        await self._io(self.ips.to_zero)
        deadline = self.clock() + self.cfg.deadline_slack_s * 3
        while True:
            await self.sleep(self.cfg.poll_s)
            st = await self._io(self.ips.read_status)
            r0 = await self._io(self.ips.read_demand_current)
            if abs(r0) <= self.cfg.current_tolerance and st.at_rest:
                await self._io(self.ips.hold)
                return
            if self.clock() > deadline:
                raise Aborted("leads did not reach zero in time")

    # ------------------------------------------------------------ match
    async def _match_to(self, persistent: float, tolerance: float | None = None):
        """The student's recover_persistent_mode: bring the supply to the
        persistent current with the switch closed (the magnet is untouched)."""
        tol = self.cfg.match_tolerance if tolerance is None else tolerance
        self.set_state("MATCHING SUPPLY TO MAGNET")
        self.event("match", f"supply to the persistent current {persistent:.4f} A")
        await self._io(self.ips.hold)
        await self._io(self.ips.set_target_current, persistent)
        await self._io(self.ips.to_setpoint)
        # immediate mode is fast; a generous deadline (review issue 11)
        deadline = self.clock() + self.cfg.deadline_slack_s * 3
        while True:
            await self.sleep(self.cfg.poll_s)
            await self._check()
            st = await self._io(self.ips.read_status)
            await self._check(status=st)
            if not st.heater_off:
                raise Aborted(f"the switch opened while matching (H{st.heater})")
            r0 = await self._io(self.ips.read_demand_current)
            validate_current(r0, "supply current", self.cfg.max_current)
            if abs(r0 - persistent) <= tol and st.at_rest:
                await self._io(self.ips.hold)
                return r0
            if self.clock() > deadline:
                raise Aborted("supply did not reach the persistent current in time")

    async def match(self):
        await self._require_permit()
        await self.preflight()
        await self._io(self.ips.remote_unlocked)
        st = await self._io(self.ips.read_status)
        if not st.heater_off:
            raise Refused("matching needs the switch closed (heater OFF)")
        r0, r16 = await self._read_currents_twice()
        validate_current(r0, "supply current", self.cfg.max_current)
        validate_current(r16, "persistent current", self.cfg.max_current)
        if abs(r0 - r16) <= self.cfg.h1_match_tolerance:
            self.set_state("SUPPLY ALREADY MATCHED")
            return
        await self._match_to(r16, tolerance=self.cfg.h1_match_tolerance)
        self.set_state("SUPPLY MATCHED / HOLD")

    # ------------------------------------------------------------ ramp
    async def _require_driven(self) -> Status:
        """V4: a ramp only in driven mode: X = H1, R20 above the minimum, at rest."""
        st = await self._io(self.ips.read_status)
        if not st.heater_on:
            if st.heater_off:
                raise Refused("persistent mode: match, then turn the heater ON first")
            raise Refused(f"unsafe heater state H{st.heater}")
        ma = await self._io(self.ips.read_heater_current)
        ok, text = self.heater_on_verified(st, ma)
        self.set_heater_text(text)
        if not ok:
            raise Refused(f"driven mode not verified: R20 {ma:.2f} mA")
        if not st.at_rest:
            raise Refused(f"supply not at rest (M{st.motion})")
        return st

    async def _check_driven(self, st: Status):
        """V4: during a ramp, HOLD at once if the switch leaves driven mode."""
        ma = await self._io(self.ips.read_heater_current)
        ok, text = self.heater_on_verified(st, ma)
        self.set_heater_text(text)
        if not ok:
            raise Aborted(f"driven mode lost: H{st.heater}, R20 {ma:.2f} mA")

    async def _set_rate_checked(self, rate: float):
        # review issue 6: an independent cap, checked before S and read back
        if not (math.isfinite(rate) and 0.0 < rate <= self.cfg.max_sweep_rate):
            raise Refused(f"sweep rate {rate} A/min above the {self.cfg.max_sweep_rate} A/min cap")
        await self._io(self.ips.set_sweep_rate, rate)
        rbv = await self._io(self.ips.read_sweep_rate)
        if rbv > self.cfg.max_sweep_rate + 1e-6 or abs(rbv - rate) > 0.01:
            raise Aborted(f"supply sweep rate reads {rbv} A/min after setting {rate}")

    async def ramp(self, target: float):
        cfg = self.cfg
        validate_current(target, "target current", cfg.max_current)
        await self._require_permit()                                   # issue 13: permit first
        await self.preflight()                                         # issues 2, 17
        self.set_state("PREPARING")
        self.event("ramp", f"ramp to {target:.3f} A requested")
        await self._io(self.ips.remote_unlocked)
        r0, r16 = await self._read_currents_twice()
        validate_current(r0, "supply current", cfg.max_current)
        validate_current(r16, "persistent current", cfg.max_current)
        st = await self._io(self.ips.read_status)
        if not st.heater_on and cfg.ramp_opens_switch:                 # V3 only (issue 7)
            await self.heater_on()
        await self._require_driven()                                   # V4
        await self._check()
        r23 = await self._io(self.ips.read_lead_resistance)
        current = await self._io(self.ips.read_demand_current)
        track = _Track()
        while abs(current - target) > cfg.current_tolerance:
            nxt = next_ramp_target(current, target, cfg.current_tolerance, cfg.rate_bands)
            rate = choose_sweep_rate(current, nxt, cfg.rate_bands)
            self.set_state(f"RAMP {current:.2f}->{nxt:.2f} A @{rate:g}"[:39])
            self.event("segment", f"{current:.3f} -> {nxt:.3f} A at {rate:g} A/min",
                       start=current, end=nxt, rate=rate)
            await self._set_rate_checked(rate)
            await self._io(self.ips.set_target_current, nxt)
            await self._io(self.ips.to_setpoint)
            deadline = self.clock() + expected_sweep_s(nxt - current, rate) * cfg.deadline_factor \
                + cfg.deadline_slack_s                        # issue 11
            while True:
                await self.sleep(cfg.poll_s)
                await self._check()                           # HOLD, permit (issue 13)
                st = await self._io(self.ips.read_status)
                await self._check(status=st, need_permit=False)   # issue 2
                await self._check_driven(st)                  # V4
                current = await self._io(self.ips.read_demand_current)
                volts = await self._io(self.ips.read_voltage)
                if not (0.0 <= current < cfg.max_current):
                    raise Aborted(f"current limit: {current:.3f} A")
                rbv = await self._io(self.ips.read_sweep_rate)
                if rbv > cfg.max_sweep_rate + 1e-6:          # issue 6, during the ramp
                    raise Aborted(f"supply sweep rate {rbv} A/min above the cap")
                now = self.clock()
                track.add(now, current, volts)
                seen = track.rate_a_per_min(cfg.observed_rate_window_s)
                if seen is not None and abs(seen) > cfg.max_sweep_rate * (1 + cfg.observed_rate_margin):
                    raise Aborted(f"current changing at {abs(seen):.2f} A/min")
                L = track.inductance(cfg.measured_inductance_window_s, r23 / 1000.0)
                if L is not None:
                    self.last_inductance_h = L
                    if abs(L - cfg.inductance_h) > cfg.measured_inductance_tolerance * cfg.inductance_h:
                        raise Aborted(f"inductance {L:.1f} H while ramping, expected "
                                      f"{cfg.inductance_h:g} H")          # issue 17
                if abs(current - nxt) <= cfg.current_tolerance and st.at_rest:   # issue 12
                    break
                if now > deadline:
                    raise Aborted(f"segment to {nxt:.2f} A not finished in time")
        await self._io(self.ips.hold)
        self.set_state("COMPLETE / HOLD")
        self.event("ramp", f"ramp complete at {current:.3f} A")

    # ------------------------------------------------------------ outage
    async def outage(self):
        """The red button (issue 19): make the magnet safe for a power cut.
        Always allowed.  Stops any operation, HOLDs, makes a driven magnet
        persistent (20 s), then leaves the leads energized ("hold", this
        cool-down's rule) or runs them to zero ("zero"; question 94)."""
        self.event("outage", "POWER OUTAGE button pressed")
        self._abort.set()
        running = self._task
        if running is not None and not running.done() and running is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(running), timeout=10.0)
            except asyncio.TimeoutError:
                running.cancel()
        # take the operation slot: nothing else may start until this is done
        self._task, self._op = asyncio.current_task(), "outage"
        self._abort.clear()
        try:
            await self._outage_body()
        except Exception as exc:                 # noqa: BLE001
            self.event("error", f"outage: {type(exc).__name__}: {exc}")
            self.set_state(f"OUTAGE ERROR: {exc}"[:39])
        finally:
            self.progress(0.0)
            self.progress_off(0.0)

    async def _outage_body(self):
        await self._io(self.ips.remote_unlocked)
        st = await self._io(self.ips.read_status)
        if not st.clamped:
            await self._io(self.ips.hold)
        st = await self._io(self.ips.read_status)
        if st.heater_on:
            self.set_state("OUTAGE: CLOSING SWITCH")
            await self.sleep(1.0)
            await self._io(self.ips.heater_off)
            await self._heater_wait("off", need_permit=False)
            st = await self._io(self.ips.read_status)
            if not st.heater_off:
                self.set_state("OUTAGE: SWITCH DID NOT CLOSE")
                self.event("error", "outage: the switch did not close")
                return
            ma = await self._io(self.ips.read_heater_current)
            ok, text = self.heater_off_verified(st, ma)
            self.set_heater_text(text)
            if not ok:                            # recorded; the switch reads closed
                self.event("error", f"outage: heater OFF not verified, R20 {ma:.2f} mA")
        if self.cfg.outage_leads == "zero" and not st.clamped:
            await self._leads_to_zero()
        self.set_state("SAFE FOR POWER OUTAGE")
        self.event("outage", "safe for power outage")
