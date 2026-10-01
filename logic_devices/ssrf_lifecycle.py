"""Physical ssRF process ownership, shared across local checkouts.

Never unlink lock files: flock protects an inode, not a pathname. Kernel locks
are released on close/process death; old files do not represent live owners.
"""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import uuid

DEFAULT_LOCK_DIR = Path('/tmp/genesis-ssrf-physical')


class OwnershipConflict(RuntimeError):
    pass


class OwnershipLock:
    def __init__(self, name, directory=None):
        directory = Path(directory) if directory is not None else DEFAULT_LOCK_DIR
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (name + '.lock')
        self.file = self.path.open('a+')
        self.acquired = False
        try:
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.file.close()
            raise OwnershipConflict(f'ssRF ownership conflict: {self.path}; no fallback writes permitted') from None
        except Exception:
            self.file.close()
            raise
        self.acquired = True
        self.token = f'{os.getpid()}:{uuid.uuid4().hex}'
        try:
            self.file.seek(0)
            self.file.truncate()
            self.file.write(self.token)
            self.file.flush()
        except Exception:
            self.file.close()
            raise

    def close(self):
        if not self.file.closed:
            try:
                if self.acquired:
                    # Clear the lease while still holding the kernel lock.
                    self.file.seek(0)
                    self.file.truncate()
                    self.file.flush()
            finally:
                self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def __del__(self):
        if hasattr(self, 'file'):
            self.close()


def startup_lifecycle_held(directory=None):
    """Validate the startup parent's lease, including stale inherited envs."""
    token = os.environ.get('GENESIS_SSRF_LIFECYCLE_TOKEN', '')
    if not token:
        return False
    path = (Path(directory) if directory is not None else DEFAULT_LOCK_DIR) / 'lifecycle.lock'
    try:
        if path.read_text() != token:
            return False
        os.kill(int(token.split(':')[0]), 0)
        return True
    except (OSError, ValueError):
        return False


def run_script(script, args):
    # Hold across every startup/shutdown step, including owner termination.
    with OwnershipLock('lifecycle') as lock:
        env = dict(os.environ, GENESIS_SSRF_LIFECYCLE_HELD='1',
                   GENESIS_SSRF_LIFECYCLE_TOKEN=lock.token)
        return subprocess.call(['bash', script, *args], env=env)


if __name__ == '__main__':
    try:
        if sys.argv[1] == '--held':
            sys.exit(0 if startup_lifecycle_held() else 1)
        sys.exit(run_script(sys.argv[1], sys.argv[2:]))
    except (OwnershipConflict, OSError) as exc:
        print(f'[FAIL] {exc}; retaining physical owners', file=sys.stderr)
        sys.exit(1)
