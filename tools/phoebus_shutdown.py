"""Close one independent Phoebus instance by its exact startup arguments.

No PID file is needed: the launcher starts Java in the background, so its shell
PID is not the GUI owner. Recheck command and start time before signaling Java.
"""
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time


@dataclass(frozen=True)
class Process:
    pid: int
    started: str
    command: str


def belongs_to(process, settings, resource):
    try:
        args = shlex.split(process.command)
    except ValueError:
        return False
    if not args or Path(args[0]).name != 'java':
        return False
    expected = {'-settings': str(Path(settings).resolve()),
                '-resource': str(Path(resource).resolve())}
    if args.count('-jar') != 1:
        return False
    try:
        jar = args[args.index('-jar') + 1]
        if not re.fullmatch(r'(?:product|phoebus)-.+\.jar', Path(jar).name):
            return False
        for flag, value in expected.items():
            if args.count(flag) != 1 or args[args.index(flag) + 1] != value:
                return False
    except IndexError:
        return False
    return True


class ProcessRuntime:
    def snapshot(self):
        result = subprocess.run(['ps', '-axww', '-o', 'pid=,lstart=,command='],
                                capture_output=True, text=True, check=True, timeout=3)
        processes = {}
        for line in result.stdout.splitlines():
            fields = line.strip().split(None, 6)
            if len(fields) != 7:
                raise RuntimeError('Cannot parse process identity: ' + line)
            pid = int(fields[0])
            processes[pid] = Process(pid, ' '.join(fields[1:6]), fields[6])
        return processes

    def terminate(self, pid):
        os.kill(pid, signal.SIGTERM)


async def close_instance(settings, resource, runtime=None, timeout=8,
                         sleep=asyncio.sleep, now=time.monotonic):
    runtime = runtime or ProcessRuntime()
    targets = {pid: process for pid, process in runtime.snapshot().items()
               if belongs_to(process, settings, resource)}
    errors = []
    for pid, identity in targets.items():
        # Stale/disappeared/reused PIDs must never receive a signal.
        if runtime.snapshot().get(pid) != identity:
            continue
        try:
            runtime.terminate(pid)
        except ProcessLookupError:
            pass
        except Exception as exc:
            errors.append(f'PID {pid}: {exc}')
    deadline = now() + timeout
    while True:
        processes = runtime.snapshot()
        survivors = [pid for pid, process in processes.items()
                     if belongs_to(process, settings, resource)]
        if not survivors:
            if errors:
                raise RuntimeError('Phoebus close errors: ' + '; '.join(errors))
            print('[PASS] Subsystem Phoebus absent; all its display windows closed')
            return
        if now() >= deadline:
            raise RuntimeError(f'Phoebus close failed; surviving PIDs {survivors}; errors={errors}')
        await sleep(0.2)
