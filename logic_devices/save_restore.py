"""
logic_devices/save_restore.py - snapshot and restore every setpoint, from a PV.

The JLab talk lists "backup/restore soft IOC planned" under Not there yet.
This is that IOC, built the same way as the other logic IOCs: it owns no
instrument, it reads and writes other IOCs' PVs, and everything it does is
driven from a PV so it works from CS-Studio, from caput, or from a script.

What it saves
-------------
The writable PVs of every instrument in settings.yaml, discovered through
tools/pv_map.py rather than listed here, so adding an instrument adds its
setpoints to the snapshot with no edit to this file.  pv_map splits them:

    settings   a value - frequency, level, setpoint, pulse width
    enables    the ones that make something happen - RF on, output on, a switch

Both kinds are SAVED.  Only settings are RESTORED, unless Restore_Enables is
deliberately set: restoring an enable would turn RF back on, or throw the burn
switch, because of a file on disk and a button press - and nobody watching the
target would have asked for it.  Momentary triggers (_Fire) are in neither
list, so a restore can never fire an instrument.

A restore is also refused outright while the burn interlock reports a fault,
for the same reason the interlock exists.

Files
-----
    <snapshot_dir>/latest.yaml         rewritten by every save
    <snapshot_dir>/YYYYmmdd_HHMMSS.yaml  kept, so you can go back to Tuesday
    Each file: the PV name, its value, and the time it was read.

PVs

    SR_Save          boolOut   write 1: snapshot now
    SR_Restore       boolOut   write 1: restore from SR_File
    SR_File          stringOut which snapshot to restore ('latest' by default)
    SR_Auto          boolOut   snapshot every `interval` seconds
    SR_Restore_Enables boolOut allow RF/output/switch PVs to be restored too
    SR_Count         longIn    PVs in the last snapshot or restore
    SR_Failed        longIn    PVs that could not be read/written last time
    SR_Last          stringIn  time of the last snapshot
    SR_Status        stringIn  what happened last

settings.yaml

    save_restore:
      module: 'logic_devices.save_restore'
      delay: 5
      snapshot_dir: 'archive/snapshots'
      settings_file: 'settings.yaml'
      interval: 900               # auto snapshot period, seconds
      keep: 200                   # timestamped files to keep
      interlock_pv: 'Burn_Fault'  # a restore is refused while this is 1
      channels: [None]
"""

import asyncio
import datetime
import logging
import os
import sys
import time

import aioca
import yaml
from softioc import builder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import pv_map                                    # noqa: E402

log = logging.getLogger(__name__)


class Device:
    def __init__(self, device_name, settings):
        self.device_name = device_name
        self.settings = settings
        self.dir = settings.get("snapshot_dir", "archive/snapshots")
        self.interval = float(settings.get("interval", 900))
        self.keep = int(settings.get("keep", 200))
        self.interlock_pv = settings.get("interlock_pv", "Burn_Fault")
        self.last_auto = 0.0

        full = pv_map.load_settings(settings.get("settings_file") or None)
        self.writable = pv_map.writable_pv_names(full, include_enables=True)
        os.makedirs(self.dir, exist_ok=True)

        self.pvs = {}
        self._create_pvs()
        log.info("save/restore knows %d writable PVs (%d of them enables)",
                 len(self.writable), sum(1 for v in self.writable.values() if v == "enable"))

    def _create_pvs(self):
        self.pvs["SR_Save"] = builder.boolOut("SR_Save", ZNAM="", ONAM="Save",
                                              always_update=True, on_update=self._on_save)
        self.pvs["SR_Restore"] = builder.boolOut("SR_Restore", ZNAM="", ONAM="Restore",
                                                 always_update=True, on_update=self._on_restore)
        self.pvs["SR_File"] = builder.stringOut("SR_File", initial_value="latest")
        self.pvs["SR_Auto"] = builder.boolOut("SR_Auto", ZNAM="Manual", ONAM="Auto",
                                              initial_value=1)
        self.pvs["SR_Restore_Enables"] = builder.boolOut(
            "SR_Restore_Enables", ZNAM="Settings only", ONAM="Enables too", initial_value=0)
        self.pvs["SR_Count"] = builder.longIn("SR_Count", initial_value=0)
        self.pvs["SR_Failed"] = builder.longIn("SR_Failed", initial_value=0)
        self.pvs["SR_Last"] = builder.stringIn("SR_Last", initial_value="never")
        self.pvs["SR_Status"] = builder.stringIn("SR_Status", initial_value="idle")

    def connect(self):
        self.pvs["SR_Status"].set("ready")

    # ------------------------------------------------------------- plumbing
    def _status(self, text):
        self.pvs["SR_Status"].set(str(text)[:39])
        log.info("save/restore: %s", text)

    def _path(self, name):
        name = (name or "latest").strip()
        if not name.endswith(".yaml"):
            name += ".yaml"
        # a snapshot name is a file in the snapshot directory, never a path
        return os.path.join(self.dir, os.path.basename(name))

    # These are coroutines on purpose.  pythonSoftIOC awaits an async on_update
    # on the dispatcher's own loop; a sync callback that tried to
    # asyncio.ensure_future() its work would have no running loop to attach to
    # and the save would silently never happen.
    async def _on_save(self, value):
        if value:
            await self.save()

    async def _on_restore(self, value):
        if value:
            await self.restore()

    # ----------------------------------------------------------------- save
    async def save(self):
        names = sorted(self.writable)
        try:
            values = await aioca.caget(names, timeout=3, throw=False)
        except Exception as e:                      # noqa: BLE001
            self._status(f"save failed: {e}")
            return None

        now = datetime.datetime.now()
        data, failed = {}, 0
        for name, value in zip(names, values):
            if getattr(value, "ok", False):
                data[name] = {"value": (value.item() if hasattr(value, "item") else
                                        (float(value) if not isinstance(value, str) else str(value))),
                              "kind": self.writable[name]}
            else:
                failed += 1

        snapshot = {"taken": now.isoformat(timespec="seconds"),
                    "prefix": self.device_name,
                    "pvs": data}
        stamp = now.strftime("%Y%m%d_%H%M%S")
        try:
            for path in (self._path(stamp), self._path("latest")):
                with open(path, "w") as f:
                    yaml.safe_dump(snapshot, f, default_flow_style=False, sort_keys=True)
        except OSError as e:
            self._status(f"save failed: {e}")
            return None

        self._prune()
        self.pvs["SR_Count"].set(len(data))
        self.pvs["SR_Failed"].set(failed)
        self.pvs["SR_Last"].set(now.strftime("%Y-%m-%d %H:%M:%S"))
        self._status(f"saved {len(data)} PVs" + (f", {failed} unreadable" if failed else ""))
        return snapshot

    def _prune(self):
        """Keep the newest `keep` timestamped snapshots; latest.yaml is not one."""
        try:
            files = sorted(f for f in os.listdir(self.dir)
                           if f.endswith(".yaml") and f != "latest.yaml")
        except OSError:
            return
        for name in files[:-self.keep] if len(files) > self.keep else []:
            try:
                os.remove(os.path.join(self.dir, name))
            except OSError:
                pass

    # -------------------------------------------------------------- restore
    async def _interlock_clear(self):
        """True when it is safe to write setpoints.  A fault, or an interlock
        IOC that cannot be reached at all, both count as 'not now' only when a
        restore would touch enables; settings alone are harmless."""
        if not self.interlock_pv:
            return True
        name = self.interlock_pv if ":" in self.interlock_pv \
            else f"{self.device_name}:{self.interlock_pv}"
        try:
            return not int(await aioca.caget(name, timeout=1))
        except Exception:                           # noqa: BLE001
            # Enable restoration is safety-relevant.  An unavailable
            # interlock is therefore not permission to restore enables.
            return False

    async def restore(self):
        path = self._path(self.pvs["SR_File"].get())
        try:
            with open(path) as f:
                snapshot = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as e:
            self._status(f"restore failed: {e}")
            return 0

        want_enables = bool(self.pvs["SR_Restore_Enables"].get())
        if want_enables and not await self._interlock_clear():
            self._status("restore refused: interlock fault")
            return 0

        items = []
        skipped = 0
        for name, entry in (snapshot.get("pvs") or {}).items():
            kind = entry.get("kind", "setting")
            if kind == "enable" and not want_enables:
                skipped += 1
                continue
            if name not in self.writable:           # the deployment changed since
                skipped += 1
                continue
            items.append((name, entry["value"]))

        results = await asyncio.gather(*(self._put(n, v) for n, v in items),
                                       return_exceptions=True)
        done = sum(1 for r in results if r is True)
        failed = len(items) - done
        self.pvs["SR_Count"].set(done)
        self.pvs["SR_Failed"].set(failed)
        self._status(f"restored {done} PVs from {os.path.basename(path)}"
                     + (f", {failed} failed" if failed else "")
                     + (f", {skipped} skipped" if skipped else ""))
        return done

    async def _put(self, name, value):
        try:
            await aioca.caput(name, value, timeout=2)
            return True
        except Exception as e:                      # noqa: BLE001
            log.warning("restore could not write %s = %r (%s)", name, value, e)
            return False

    # ------------------------------------------------------------ the loop
    async def do_reads(self):
        if self.pvs["SR_Auto"].get() and time.time() - self.last_auto >= self.interval:
            self.last_auto = time.time()
            await self.save()
        return True
