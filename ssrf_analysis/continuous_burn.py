"""Continuous ssRF operator request through existing sequencer only.

Never writes directly to hardware; never activates RF on startup. The physical
execute flag is intentionally explicit. Every cycle is independently prepared,
armed and executed. Signal handling requests ABORT then verifies safe readbacks.
"""
from __future__ import annotations
import argparse
import asyncio
import math
import signal
import time
import aioca

RECIPE = 'UNH:GEN:SSRF:RECIPE'
SEQ = 'UNH:GEN:SSRF:SEQ'


class OperatorStop(Exception):
    """Graceful Ctrl-C / SIGTERM after signal-safe event handling."""


async def _read(pv):
    return await aioca.caget(pv, timeout=3.0)


async def _put(pv, value):
    await aioca.caput(pv, value, wait=True, timeout=5.0)


async def _until(pv, accept, *, timeout, fail=None, stop=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            raise OperatorStop('STOP requested')
        value = str(await _read(pv))
        if fail and fail(value):
            raise RuntimeError(f'{pv}: {value}')
        if accept(value):
            return value
        await asyncio.sleep(0.15)
    raise TimeoutError(f'timed out waiting for {pv}')


async def _safe_baseline():
    checks = {
        'UNH:GEN:SSRF:GEN:RF_RBV': 0,
        'UNH:GEN:SSRF:GEN:FM_RBV': 0,
        'UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV': 0,
        'UNH:GEN:SSRF:WGEN:OUTPUT_RBV': 0,
        'UNH:GEN:SSRF:NMR_SW:MASK_RBV': 0,
        'UNH:GEN:SSRF:RF_SW:MASK_RBV': 3,
        SEQ+':EXECUTION_ARMED': 0,
    }
    for pv, expected in checks.items():
        got = int(await _read(pv))
        if got != expected:
            raise RuntimeError(f'unsafe/incomplete cleanup: {pv} = {got}, expected {expected}')


async def run(cycles, delay, expected_mode, stop=None, progress=None):
    if stop is None:
        stop = asyncio.Event()
    if progress is None:
        progress = lambda cycle, phase: None
    if int(await _read(SEQ+':EXECUTION_ENABLED')) != 1:
        raise RuntimeError('ssRF sequencer execution disabled')
    actual_mode = str(await _read(RECIPE+':BURN_MODE_RBV')).strip().upper()
    if actual_mode != expected_mode.upper():
        raise RuntimeError(f'burn mode mismatch: expected {expected_mode}, actual {actual_mode}')
    await _safe_baseline()
    # Never take over a manually prepared/armed one-shot operation.
    if int(await _read(SEQ+':PREPARED')) != 0:
        raise RuntimeError('manual burn already prepared; complete or ABORT first')
    if str(await _read(SEQ+':EXECUTION_STATE')) == 'RUNNING':
        raise RuntimeError('ssRF execution already running')
    n = 0
    try:
        while (cycles == 0 or n < cycles) and not stop.is_set():
            n += 1
            progress(n, 'PREPARING')
            print(f'[CYCLE {n}] prepare {actual_mode} waveform', flush=True)
            prior_session = str(await _read(RECIPE+':SESSION_ID'))
            await _put(RECIPE+':GENERATE_LOAD_CMD', 1)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise OperatorStop('STOP requested')
                status = str(await _read(RECIPE+':STATE'))
                if status == 'FAILED':
                    raise RuntimeError('generate/load failed: ' + str(await _read(RECIPE+':REASON')))
                session = str(await _read(RECIPE+':SESSION_ID'))
                if session and session != prior_session:
                    break
                await asyncio.sleep(0.15)
            else:
                raise TimeoutError('no new ssRF recipe session started')
            await _until(RECIPE+':STATE', lambda s: s == 'READY_TO_ARM', timeout=40,
                         fail=lambda s: s == 'FAILED', stop=stop)
            if int(await _read(RECIPE+':PLAN_VALID')) != 1 or int(await _read(SEQ+':PREPARED')) != 1:
                raise RuntimeError('source not prepared despite READY_TO_ARM')
            if int(await _read(SEQ+':READY')) != 1:
                raise RuntimeError('sequencer not READY; operator intervention required')
            if stop.is_set():
                raise OperatorStop('STOP requested')
            progress(n, 'ARMING')
            await _put(SEQ+':ARM_CMD', 1)
            await _until(SEQ+':EXECUTION_ARMED', lambda s: int(s) == 1,
                         timeout=4, stop=stop)
            if stop.is_set():
                raise OperatorStop('STOP requested')
            await _put(SEQ+':START_CMD', 1)
            progress(n, 'BURNING')
            # START_CMD is asynchronous: a previous cycle's COMPLETE can still
            # be visible before the IOC accepts the new start. A real completion
            # consumes PREPARED; do not accept a stale COMPLETE readback.
            deadline = time.monotonic() + 96.0
            while time.monotonic() < deadline:
                if stop.is_set():
                    raise OperatorStop('STOP requested')
                state = str(await _read(SEQ+':EXECUTION_STATE'))
                if state in ('FAILED', 'CLEANUP_FAILED', 'ABORTED'):
                    raise RuntimeError(f'cycle {n} failed: {state}')
                if state == 'COMPLETE' and int(await _read(SEQ+':PREPARED')) == 0:
                    break
                await asyncio.sleep(0.15)
            else:
                raise TimeoutError(f'cycle {n} never completed and cleaned up')
            await _safe_baseline()
            if int(await _read(SEQ+':PREPARED')) != 0:
                raise RuntimeError('preparation was not consumed')
            print(f'[CYCLE {n}] completed with outputs OFF', flush=True)
            if cycles == 0 or n < cycles:
                progress(n, 'WAITING')
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
    finally:
        # The signal handler only sets an event, so these awaits are not cancelled.
        # Retain the original fault if ABORT cannot finish, but never report success.
        try:
            previous = int(await _read(SEQ+':SAFE_OFF_ID'))
        except Exception:
            previous = None
        await _put(SEQ+':ABORT_CMD', 1)
        if previous is not None:
            await _until(SEQ+':SAFE_OFF_ID', lambda s: int(s) > previous, timeout=15)
        await _safe_baseline()
        progress(n, 'SAFE_OFF')
        print('[DONE] Sequencer ABORT and safe readbacks verified', flush=True)


async def _run_from_cli(args):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await run(args.cycles, args.delay_s, args.mode, stop=stop)
    except OperatorStop:
        print('[STOP] Operator requested shutdown; verified ABORT', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Repeated guarded Genesis ssRF burn sequence')
    parser.add_argument('--mode', choices=('fit','operator'), default='fit')
    parser.add_argument('--delay-s', type=float, default=10.0)
    parser.add_argument('--cycles', type=int, default=0, help='0 = repeat until Ctrl-C')
    parser.add_argument('--execute', action='store_true', help='required to permit any EPICS write')
    args = parser.parse_args()
    if not 0 <= args.cycles <= 100000 or not math.isfinite(args.delay_s) or not 0 <= args.delay_s <= 3600:
        parser.error('invalid cycle count or delay')
    if not args.execute:
        print('[DRY RUN] No EPICS writes. Run again with --execute after physical authorization.')
        print(f'Mode={args.mode}, cycles={args.cycles or "continuous"}, delay={args.delay_s}s')
        return
    try:
        asyncio.run(_run_from_cli(args))
    except KeyboardInterrupt:
        raise SystemExit('Unexpected interrupt during cleanup: verify ssRF hardware safe state')


if __name__ == '__main__':
    main()
