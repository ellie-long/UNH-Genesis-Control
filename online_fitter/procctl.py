"""Find and hard-stop any OTHER running ``online_fitter.run`` instance -- and nothing else.

The DAQ machine runs many simultaneous Python scripts, so every one shows up as a bare
``python.exe`` in the process list; killing by image name would take them all out. This
module targets ONLY the fitter, two independent ways, and kills the union:

  1. **Command line** -- ``python.exe``/``pythonw.exe`` processes whose command line contains
     ``online_fitter.run`` (excluding our own PID). A *hung* fitter still lists its full
     command line, so this catches the stuck case a Reset button exists to recover from.
  2. **Port owner** -- whoever is LISTENING on the fitter's loopback port (default 8777) is,
     by construction, the running fitter; killed only if that owner is a python process.

Both are hard kills by PID (``Stop-Process -Force``), so a frozen instance that no longer
services its socket is still terminated. Windows-only (the DAQ is Windows); a no-op elsewhere.
"""
import os
import re
import subprocess
import sys

from . import protocol


def _powershell(script, timeout=20):
    """Run a PowerShell script, return CompletedProcess (or None if PowerShell is missing)."""
    for exe in ("powershell", "pwsh"):
        try:
            return subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            continue
    return None


def terminate_previous(port=None, exclude_pid=None, match="online_fitter.run", verbose=False):
    """Kill any other ``online_fitter.run`` (by command line) and the python owner of ``port``.

    Args:
        port: fitter loopback port to also clear (default ``protocol.DEFAULT_PORT``); None skips
              the port check.
        exclude_pid: PID never to kill (defaults to this process -- so ``run --replace`` and
              ``online_fitter.stop`` never kill themselves).
        match: substring that identifies the fitter in a command line. ``online_fitter.run``
              deliberately does NOT match ``online_fitter.stop``, so the stopper is self-safe.
        verbose: print each killed PID.
    Returns:
        Sorted list of PIDs that were terminated (empty if none / not Windows).
    """
    if sys.platform != "win32":
        return []                                    # DAQ is Windows; no-op elsewhere
    me = int(exclude_pid) if exclude_pid is not None else os.getpid()
    port = protocol.DEFAULT_PORT if port is None else int(port)
    # NB: $pid is a read-only PowerShell automatic; use $me / $victim instead.
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$me = {me}
$victims = New-Object System.Collections.Generic.HashSet[int]
# (1) python processes whose command line runs {match}, except ourselves
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object {{ $_.CommandLine -like '*{match}*' -and $_.ProcessId -ne $me }} |
  ForEach-Object {{ [void]$victims.Add([int]$_.ProcessId) }}
# (2) the python process (if any) LISTENING on the fitter port
try {{
  Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction Stop | ForEach-Object {{
    $op = [int]$_.OwningProcess
    $p  = Get-Process -Id $op -ErrorAction SilentlyContinue
    if ($p -and ($p.ProcessName -eq 'python' -or $p.ProcessName -eq 'pythonw') -and $op -ne $me) {{
      [void]$victims.Add($op)
    }}
  }}
}} catch {{}}
foreach ($victim in $victims) {{
  try {{ Stop-Process -Id $victim -Force -ErrorAction Stop; Write-Output ("KILLED " + $victim) }} catch {{}}
}}
"""
    res = _powershell(script)
    if res is None:
        return []
    pids = sorted({int(m) for m in re.findall(r"KILLED (\d+)", res.stdout or "")})
    if verbose:
        for p in pids:
            print(f"[online_fitter] terminated previous instance PID {p}", flush=True)
    return pids
