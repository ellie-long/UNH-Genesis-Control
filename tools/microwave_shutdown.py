"""Verified, local-only process cleanup for the dedicated Microwave profile.

Hardware commands are issued only by run(), after local ownership inspection.
Importing this module performs no EPICS, network, or process operations.
"""
import asyncio
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

PROFILE = 'configs/commissioning/microwave_physical'
DEVICES = {
    'mw_supervisor': ('supervisor', 'microwave_supervisor'),
    'mw_pcl602': ('pcl602', 'pcl602'),
    'mw_vaunix_lms': ('vaunix_lms', 'vaunix_lms'),
    'mw_spd1305x': ('spd1305x', 'spd1305x'),
    'archiver': ('archiver', 'archiver'),
}
ORDER = tuple(DEVICES) + ('ioc-manager',)
# Only these exact same-repository profiles establish shared ownership.
SHARED_PROFILES = {'archiver': 'configs/archiver',
                   'ioc-manager': 'configs/ioc_manager'}


def screen_sessions(output):
    """Parse full IDs; names match exactly, regardless of screen's exit code."""
    result = {}
    for sid, name in re.findall(r'^\s*(\d+\.([^\s]+))\s+\(', output, re.M):
        result.setdefault(name, []).append(sid)
    return result


class LocalRuntime:
    def __init__(self, root):
        self.root = Path(root).resolve()

    async def close_gui(self):
        if __package__:
            from .phoebus_shutdown import close_instance
        else:
            from phoebus_shutdown import close_instance
        await close_instance(self.root / 'configs/phoebus/microwave_physical.ini',
                             self.root / 'gui/genesis_microwave.bob')

    def snapshot(self):
        listing = subprocess.run(['screen', '-ls'], capture_output=True, text=True, timeout=5)
        output = listing.stdout + listing.stderr
        if not re.search(r'(Sockets? in|No Sockets found)', output):
            raise RuntimeError('Cannot inspect Screen sessions: ' + output.strip())
        sessions = screen_sessions(output)
        rows = subprocess.run(['ps', '-axww', '-o', 'pid=,ppid=,command='],
                              capture_output=True, text=True, check=True, timeout=5)
        processes = {}
        owned = {}
        shared = {}
        for line in rows.stdout.splitlines():
            fields = line.strip().split(None, 2)
            if len(fields) != 3:
                continue
            pid, parent, command = fields
            processes[int(pid)] = int(parent)
            try:
                args = shlex.split(command)
            except ValueError:
                continue
            if not args or not re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?', Path(args[0]).name):
                continue
            script = next((a for a in args if Path(a).name in
                           ('master_ioc.py', 'ioc_manager.py')), None)
            if script is None or '-s' not in args:
                continue
            try:
                settings = args[args.index('-s') + 1]
                ioc = args[args.index('-i') + 1] if '-i' in args else None
            except IndexError:
                continue
            # Relative script/profile paths are meaningful only with a proven cwd.
            if not Path(script).is_absolute() or not Path(settings).is_absolute():
                cwd = subprocess.run(['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'],
                                     capture_output=True, text=True, check=False, timeout=5)
                if cwd.returncode:
                    if cwd.returncode == 1:
                        # ps may have listed a process that exited before lsof.
                        # Signal 0 only checks process existence; it sends no signal.
                        try:
                            os.kill(int(pid), 0)
                        except ProcessLookupError:
                            continue
                    # A living PID with an unverifiable cwd remains unowned;
                    # abort rather than weakening the safety ownership gate.
                    raise RuntimeError(f'Cannot establish cwd for live IOC PID {pid}: '
                                       f'lsof exit {cwd.returncode}; {cwd.stderr.strip()}')
                paths = [s[1:] for s in cwd.stdout.splitlines() if s.startswith('n')]
                if paths != [str(self.root)]:
                    continue
            script_path = Path(script) if Path(script).is_absolute() else self.root / script
            if script_path.resolve() != self.root / Path(script).name:
                continue
            folder = Path(settings) if Path(settings).is_absolute() else self.root / settings
            for name in ORDER:
                sub, expected_ioc = ('ioc_manager', None) if name == 'ioc-manager' else DEVICES[name]
                if (ioc != expected_ioc or Path(script).name !=
                        ('ioc_manager.py' if name == 'ioc-manager' else 'master_ioc.py')):
                    continue
                if folder.resolve() == self.root / PROFILE / sub:
                    owned[int(pid)] = name
                elif name in SHARED_PROFILES and folder.resolve() == self.root / SHARED_PROFILES[name]:
                    shared[int(pid)] = name
        return sessions, processes, owned, shared

    def quit(self, sid):
        subprocess.run(['screen', '-S', sid, '-X', 'quit'], check=True, timeout=3)


def ownership(snapshot):
    """Select dedicated owners, preserve proven shared owners, reject unknowns."""
    sessions, parents, owned, shared = snapshot
    selected = {}

    def descendant(pid, screen_pid):
        seen = set()
        while pid in parents and pid not in seen:
            seen.add(pid)
            if pid == screen_pid:
                return True
            pid = parents[pid]
        return False

    for name in ORDER:
        ids = sessions.get(name, [])
        if len(ids) > 1:
            raise RuntimeError(f'Ambiguous duplicate session: {name}: {ids}')
        pids = [pid for pid, owner in owned.items() if owner == name]
        if ids:
            screen_pid = int(ids[0].split('.')[0])
            dedicated = [pid for pid in pids if descendant(pid, screen_pid)]
            generic = [pid for pid, owner in shared.items()
                       if owner == name and descendant(pid, screen_pid)]
            if len(dedicated) + len(generic) > 1:
                raise RuntimeError(f'Ambiguous owner processes for {name}: {ids[0]}; retaining all owners')
            if dedicated and not generic:
                if len(dedicated) != len(pids):
                    raise RuntimeError(f'Additional detached owner processes: {name}: {pids}')
                selected[name] = ids[0]
            elif generic and not pids:
                # A known generic profile in this exact session is expected to survive.
                print(f'[PRESERVE] Known shared {name}: {ids[0]}')
            else:
                raise RuntimeError(f'Ownership not established or ambiguous for {name}: '
                                   f'{ids[0]}; retaining all owners')
        elif pids:
            raise RuntimeError(f'Owner process without a cleanup session: {name}: {pids}')
    return selected


async def establish_safe(names, io, timeout=12, sleep=asyncio.sleep, now=time.time):
    """Require completed OFF commands and fresh vendor OFF/sweep confirmation."""
    commands = {
        'mw_supervisor': [('UNH:GEN:MW:SUP:RFEnable', 0)],
        'mw_vaunix_lms': [('UNH:GEN:MW:SRC:MW_RF_CMD', 0)],
        'mw_spd1305x': [('UNH:GEN:MW:PS:VSET_CMD', 5.0)],
        'mw_pcl602': [('UNH:GEN:MW:ROBOT:CONTROL_MODE', 0)],
    }
    failures = []
    rf_after = now()
    for name in ('mw_supervisor', 'mw_vaunix_lms', 'mw_spd1305x', 'mw_pcl602'):
        if name in names:
            for pv, value in commands.get(name, []):
                try:
                    if name == 'mw_vaunix_lms':
                        rf_after = now()
                    await io.caput(pv, value, wait=True, timeout=3)
                    print(f'[COMMAND] {pv} <- {value}')
                except Exception as exc:
                    failures.append(f'{pv}: {exc}')
    after = now()

    async def get(pv):
        value = float(await io.caget(pv, timeout=2))
        observed[pv] = value
        return value

    async def fresh(prefix, ioc):
        return await get(f'{prefix}:MAN:{ioc}_time') > after

    async def verified():
        checks = []
        if 'mw_vaunix_lms' in names or 'mw_supervisor' in names:
            p = 'UNH:GEN:MW:SRC'
            rf_time = await get(p + ':MW_RF_TIME')
            checks.extend([await fresh(p, 'vaunix_lms'), await get(p + ':MW_Connected') == 1,
                           await get(p + ':MW_Simulation') == 0, await get(p + ':MW_TestMode') == 0,
                           await get(p + ':MW_RF_RBV') == 0,
                           await get(p + ':MW_RF_VALID') == 1,
                           await get(p + ':MW_RF_STATE') == 1,
                           rf_after <= rf_time <= now(),
                           rf_time >= now() - 4.0,
                           rf_after <= await get(p + ':MW_RF_DONE_TIME') <= now(),
                           await get(p + ':MW_RF_READY') == 1,
                           await get(p + ':MW_SweepActive') == 0,
                           not str(await io.caget(p + ':MW_RF_ERROR', timeout=2))])
        if 'mw_supervisor' in names:
            p = 'UNH:GEN:MW:SUP'
            checks.extend([await fresh(p, 'microwave_supervisor'), await get(p + ':Simulation') == 0,
                           await get(p + ':RFActual') == 0, await get(p + ':RFValid') == 1,
                           await get(p + ':RFState') == 1,
                           await get(p + ':SweepState') in (0, 2, 3, 4)])
        if 'mw_spd1305x' in names:
            p = 'UNH:GEN:MW:PS'
            checks.extend([await fresh(p, 'spd1305x'), await get(p + ':COMM') == 1,
                           abs(await get(p + ':VSET_RBV') - 5) <= 0.1,
                           4.9 <= await get(p + ':VMEAS') <= 5.1,
                           await get(p + ':OUTPUT_RBV') == 1])
        if 'mw_pcl602' in names:
            p = 'UNH:GEN:MW:ROBOT'
            checks.extend([await fresh(p, 'pcl602'), await get(p + ':COMM_STATUS') == 1,
                           await get(p + ':SIMULATION') == 0, await get(p + ':CONTROL_MODE') == 0,
                           await get(p + ':X_BUSY') == 0, await get(p + ':Y_BUSY') == 0,
                           await get(p + ':JOYSTICK_ACTIVE') == 0])
        return all(checks)

    deadline = now() + timeout
    last = 'readbacks did not reach the safe baseline'
    observed = {}
    while now() < deadline:
        try:
            if await verified():
                if failures:
                    raise RuntimeError('Safe command failures: ' + '; '.join(failures))
                print('[PASS] Fresh physical safe-state readbacks verified')
                return
        except Exception as exc:
            last = str(exc)
        await sleep(0.2)
    raise RuntimeError('Safety verification failed; retaining physical owners: ' + last
                       + f'; command failures={failures}; readbacks={observed}')


async def close_archive(io, timeout=8, sleep=asyncio.sleep, now=time.monotonic):
    await io.caput('UNH:GEN:Archive_Enable', 0, wait=True, timeout=3)
    deadline = now() + timeout
    while now() < deadline:
        if (int(await io.caget('UNH:GEN:Archive_Status', timeout=2)) == 0
                and int(await io.caget('UNH:GEN:Archive_PV_Count', timeout=2)) == 0):
            return
        await sleep(0.2)
    raise RuntimeError('Archiver did not close monitoring/files; retaining its process')


async def run(runtime, io, safety=establish_safe, archive=close_archive,
              sleep=asyncio.sleep, cleanup_timeout=5, now=time.monotonic):
    gui_error = None
    try:
        await runtime.close_gui()
    except Exception as exc:
        gui_error = f'Microwave Phoebus close failed: {exc}'
        print(f'[WARN] {gui_error}; still attempting hardware safety')
    try:
        selected = ownership(runtime.snapshot())
    except Exception as exc:
        raise RuntimeError(f'{gui_error + "; " if gui_error else ""}{exc}') from exc
    if not selected:
        if gui_error:
            raise RuntimeError(gui_error)
        print('[PASS] No dedicated Microwave owners remain; shared sessions preserved')
        return
    if 'mw_supervisor' in selected and 'mw_vaunix_lms' not in selected:
        raise RuntimeError('Supervisor source ownership is unavailable; retaining physical owners')
    try:
        await safety(selected, io)
    except Exception as exc:
        raise RuntimeError(f'{gui_error + "; " if gui_error else ""}{exc}') from exc
    if gui_error:
        raise RuntimeError(gui_error + '; safe state verified; retaining owners for recovery')
    for name in ORDER:
        if name not in selected:
            continue
        if name == 'archiver':
            await archive(io)
        # Re-establish identity before each local termination; never use name-only quit.
        current = ownership(runtime.snapshot())
        if current.get(name) != selected[name]:
            raise RuntimeError(f'Ownership changed during shutdown: {name}; cleanup stopped')
        error = None
        try:
            runtime.quit(selected[name])
        except Exception as exc:
            error = exc
        deadline = now() + cleanup_timeout
        while True:
            sessions, _, owned, _ = runtime.snapshot()
            if selected[name] not in sessions.get(name, []) and name not in owned.values():
                break
            if now() >= deadline:
                raise RuntimeError(f'Shutdown survivor: {name}, session {selected[name]}, '
                                   f'processes {[p for p, n in owned.items() if n == name]}; {error or "exit timeout"}')
            await sleep(0.2)
        print(f'[STOPPED] {name}')
    final = ownership(runtime.snapshot())
    if final:
        raise RuntimeError(f'Microwave owners remain: {final}')
    print('[PASS] Dedicated Microwave local sessions/processes stopped')


def main():
    import sys
    import aioca
    try:
        asyncio.run(run(LocalRuntime(Path(__file__).resolve().parents[1]), aioca))
    except Exception as exc:
        print(f'[FAIL] {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
