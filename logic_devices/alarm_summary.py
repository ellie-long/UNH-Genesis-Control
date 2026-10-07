"""
logic_devices/alarm_summary.py - one place that knows what is in alarm.

Why this exists
---------------
EPICS already puts a severity on every record: the HIHI/HIGH/LOW/LOLO fields in
settings.yaml do the comparing, and Phoebus colours the widget that shows the
value.  That is per-value annunciation and it works, but it only answers the
question you already thought to ask.  It cannot answer the two an operator
actually has:

    is anything wrong right now?
    did anything go wrong while I was away?

Nothing in the deployment answered either.  A MAJOR on F7_TI at 03:00 that
cleared by 04:00 left no trace anywhere except a colour nobody was looking at,
and finding it meant opening eleven detail screens or reading the archive.

This IOC scans every PV the deployment serves - the list comes from
tools/pv_map.py, the same table the archiver, the screen generator and
check_pvs.py use - and reduces them to a handful of PVs that a screen can show
in one line, plus a LATCH that survives the alarm clearing.

Why not the Phoebus Alarm Server
--------------------------------
Phoebus has a proper alarm system, and for a hall-sized installation it is the
right answer.  It also wants Kafka, Zookeeper and a configuration database -
three more services to run, keep up and restart, on a PC that currently runs
eighteen IOCs and nothing else.  That is a poor trade for one cryostat.  What
this gives up is per-alarm acknowledge state shared between operators and a
persistent server-side history; what it keeps is the part that matters here:
one honest summary and a latch nobody can miss.

Latching, and why acknowledge is separate from clearing
-------------------------------------------------------
`Alarm_Worst` is live - it follows reality and goes back to NO_ALARM when the
last alarm clears.  `Alarm_Latch` does not: it holds the worst severity seen
since the last acknowledge, with the name and time of the PV that caused it.
Writing Alarm_Ack clears the latch and nothing else.  Acknowledging is a
statement that a person has SEEN something, which is a different fact from it
having stopped, and conflating the two is how a transient gets lost.

PVs (prefix from settings.yaml, e.g. UNH:)

    Alarm_Count    longIn   PVs currently in MINOR or worse
    Alarm_Major    longIn   of those, how many are MAJOR or INVALID
    Alarm_Worst    mbbIn    None / MINOR / MAJOR / INVALID - live
    Alarm_First    stringIn the worst one, named (ties break by scan order)
    Alarm_Latch    mbbIn    worst seen since the last acknowledge
    Alarm_LatchPV  stringIn which PV caused the latch
    Alarm_LatchAt  stringIn when
    Alarm_Ack      boolOut  write 1 to clear the latch
    Alarm_Scanned  longIn   PVs in the scan (0 means the scan failed)
    Alarm_Missing  longIn   PVs that did not answer at all

settings.yaml

    alarm_summary:
      module: 'logic_devices.alarm_summary'
      autostart: True
      delay: 2
      exclude: ['_IDN', '_Error']   # substrings; never scanned
      log_file: 'archive/alarm_log.csv'   # '' to disable
      channels: [None]
"""

import csv
import datetime
import logging
import os
import sys

import aioca
from softioc import alarm, builder

log = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NO_ALARM, MINOR, MAJOR, INVALID = 0, 1, 2, 3
SEVERITIES = [("None", 0), ("MINOR", "MINOR"), ("MAJOR", "MAJOR"),
              ("INVALID", "MAJOR")]
NAMES = {NO_ALARM: "NONE", MINOR: "MINOR", MAJOR: "MAJOR", INVALID: "INVALID"}


class Device:
    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.exclude = list(settings.get("exclude") or ["_IDN", "_Error"])
        self.log_file = settings.get("log_file", "archive/alarm_log.csv")
        if self.log_file and not os.path.isabs(self.log_file):
            self.log_file = os.path.join(ROOT, self.log_file)

        self.names = []
        self._previous = {}          # pv -> severity last cycle
        self._latch = NO_ALARM

        self.pvs = {}
        self._create_pvs()

    # ----------------------------------------------------------------- PVs
    def _create_pvs(self):
        self.pvs["Alarm_Count"] = builder.longIn("Alarm_Count", initial_value=0)
        self.pvs["Alarm_Major"] = builder.longIn("Alarm_Major", initial_value=0)
        self.pvs["Alarm_Worst"] = builder.mbbIn("Alarm_Worst", *SEVERITIES)
        self.pvs["Alarm_First"] = builder.stringIn("Alarm_First",
                                                   initial_value="")
        self.pvs["Alarm_Latch"] = builder.mbbIn("Alarm_Latch", *SEVERITIES)
        self.pvs["Alarm_LatchPV"] = builder.stringIn("Alarm_LatchPV",
                                                     initial_value="")
        self.pvs["Alarm_LatchAt"] = builder.stringIn("Alarm_LatchAt",
                                                     initial_value="")
        self.pvs["Alarm_Ack"] = builder.boolOut(
            "Alarm_Ack", ZNAM="", ONAM="Ack", always_update=True,
            on_update=self.on_ack)
        self.pvs["Alarm_Scanned"] = builder.longIn("Alarm_Scanned",
                                                   initial_value=0)
        self.pvs["Alarm_Missing"] = builder.longIn("Alarm_Missing",
                                                   initial_value=0)

    def on_ack(self, value):
        if not value:
            return
        self._latch = NO_ALARM
        self.pvs["Alarm_Latch"].set(NO_ALARM)
        self.pvs["Alarm_LatchPV"].set("")
        self.pvs["Alarm_LatchAt"].set("")
        log.info("alarm latch acknowledged by operator")

    # ------------------------------------------------------------ the list
    def connect(self):
        """Build the PV list once, from tools/pv_map.py.

        Deliberately the same source as the archiver and the screen generator:
        a PV that exists is scanned whether or not anyone wrote a `records:`
        block for it, and a PV that is renamed is renamed everywhere at once.
        Our own PVs are excluded - an alarm summary that alarms on its own
        alarm count is a loop, not a diagnostic.
        """
        try:
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from tools import pv_map

            full = pv_map.load_settings(
                self.settings.get("settings_file") or None
            )
            owners = pv_map.all_pv_names(full, skip=("_Fire",), heartbeats=False)
            self.names = sorted(
                n for n in owners
                if not n.split(":")[-1].startswith("Alarm_")
                and not any(pat in n for pat in self.exclude))
        except Exception as e:                      # noqa: BLE001
            # No list means no summary.  Say so on the PV rather than
            # reporting a comfortable zero, which is what an empty scan would
            # otherwise look like on a screen.
            log.error("alarm summary: cannot build the PV list (%s) - "
                      "Alarm_Scanned will stay 0", e)
            self.names = []
        self.pvs["Alarm_Scanned"].set(len(self.names))
        log.info("alarm summary watching %d PVs", len(self.names))

    # -------------------------------------------------------------- the log
    def _log_transitions(self, current):
        """Append every change of severity to a CSV.

        Transitions, not states: the interesting fact is WHEN a PV went into
        alarm and when it came out, and writing that is a few rows an hour
        rather than a few thousand.  Whoever is reading it after a run wants a
        list of events, not a sampled signal - the archive already has the
        signal.
        """
        rows = []
        for name, severity in current.items():
            was = self._previous.get(name, NO_ALARM)
            if severity != was:
                rows.append((name, NAMES[was], NAMES[severity]))
        self._previous = current

        if not rows or not self.log_file:
            return rows
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            new = not os.path.exists(self.log_file)
            with open(self.log_file, "a", newline="") as f:
                writer = csv.writer(f)
                if new:
                    writer.writerow(["Timestamp", "PV", "From", "To"])
                stamp = datetime.datetime.now().isoformat(timespec="seconds")
                for name, was, now in rows:
                    writer.writerow([stamp, name, was, now])
                f.flush()
        except OSError as e:
            log.error("alarm summary: cannot write %s: %s", self.log_file, e)
        return rows

    # ------------------------------------------------------------- the scan
    @staticmethod
    def severity_of(value):
        """Severity of one augmented value from a list caget, or INVALID.

        aioca returns augmented values carrying .ok and .severity when the read
        succeeded.  A PV that did not answer is INVALID rather than absent: a
        reading nobody can get is a worse state than one that is merely high,
        and it is exactly the state a dead IOC produces.
        """
        if value is None or not getattr(value, "ok", False):
            return INVALID
        return min(int(getattr(value, "severity", 0)), INVALID)

    async def do_reads(self):
        if not self.names:
            return True
        try:
            values = await aioca.caget(self.names, timeout=3, throw=False)
        except Exception as e:                      # noqa: BLE001
            log.error("alarm summary: scan failed: %s", e)
            return True

        current, missing = {}, 0
        for name, value in zip(self.names, values):
            severity = self.severity_of(value)
            if severity == INVALID:
                missing += 1
            current[name] = severity

        in_alarm = {n: s for n, s in current.items() if s > NO_ALARM}
        worst = max(in_alarm.values(), default=NO_ALARM)
        first = next((n for n, s in sorted(in_alarm.items()) if s == worst), "")

        self.pvs["Alarm_Count"].set(len(in_alarm))
        self.pvs["Alarm_Major"].set(sum(1 for s in in_alarm.values() if s >= MAJOR))
        self.pvs["Alarm_Worst"].set(worst)
        self.pvs["Alarm_First"].set(first[:39])
        self.pvs["Alarm_Missing"].set(missing)

        if worst > self._latch:
            self._latch = worst
            self.pvs["Alarm_Latch"].set(worst)
            self.pvs["Alarm_LatchPV"].set(first[:39])
            self.pvs["Alarm_LatchAt"].set(
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        for name, was, now in self._log_transitions(current):
            log.info("alarm %s: %s -> %s", name, was, now)
        return True
