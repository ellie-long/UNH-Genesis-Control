"""Process/session-only ssRF launcher decisions; no EPICS or instrument access."""
import os
import re
import shlex
import subprocess

EXPECTED = {
    'genesis-ssrf-smc': 'configs/ssrf_physical/smc100a',
    'genesis-ssrf-nmr-sw': 'configs/ssrf_physical/nmr_switch',
    'genesis-ssrf-rf-sw': 'configs/ssrf_physical/rf_switch',
    'genesis-ssrf-interlock': 'configs/ssrf_physical/interlock',
    'genesis-ssrf-sequencer': 'configs/ssrf_physical/sequencer',
    'genesis-ssrf-fitter': 'online_fitter.epics_run',
    'genesis-ssrf-recipe': 'configs/ssrf_physical/burn_recipe',
    'genesis-ssrf-archiver': 'configs/ssrf_physical/archiver',
}


def classify_stack(screens, commands, alive):
    sessions = re.findall(r'^\s*(\d+)\.(genesis-ssrf-[\w-]+)\s', screens, re.M)
    owners = []
    for command in commands:
        try:
            words = shlex.split(command)
        except ValueError:
            continue
        if words and re.fullmatch(r'python(?:\d+(?:\.\d+)*)?', os.path.basename(words[0]).lower()):
            owners.append(words)
    counts = {name: sum(any(word == signature or
                           (signature.startswith('configs/') and word.rstrip('/').endswith('/' + signature))
                           for word in words) for words in owners)
              for name, signature in EXPECTED.items()}
    if not sessions and not any(counts.values()):
        return 'ZERO', 'No local ssRF physical/control owners'
    names = [name for _pid, name in sessions]
    if set(names) != set(EXPECTED) or len(names) != len(EXPECTED):
        return 'PARTIAL', 'Missing, duplicate or unexpected ssRF sessions; refusing duplicate owners'
    for pid, name in sessions:
        if not alive(int(pid)) or counts[name] != 1:
            return 'PARTIAL', f'{name}: stale session or missing/duplicate IOC process'
    return 'COMPLETE', 'Expected local owners/session processes live; preserving RF/FM/routing/arm'


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


if __name__ == '__main__':
    import sys
    try:
        screens = subprocess.run(['screen', '-ls'], capture_output=True, text=True)
        listing = screens.stdout + '\n' + screens.stderr
        # GNU Screen can return 1 even when it lists valid detached sessions.
        has_sessions = re.search(r'^\s*\d+\.\S+\s', listing, re.M)
        if screens.returncode and not has_sessions and 'No Sockets found' not in listing:
            raise RuntimeError('Cannot determine Screen ownership')
        processes = subprocess.run(['ps', 'axww', '-o', 'command='], capture_output=True,
                                   text=True, check=True)
        state, reason = classify_stack(listing, processes.stdout.splitlines(), pid_alive)
        print(state)
        print(reason, file=sys.stderr)
        if state == 'PARTIAL':
            sys.exit(1)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'[FAIL] {exc}; refusing duplicate owners', file=sys.stderr)
        sys.exit(1)
