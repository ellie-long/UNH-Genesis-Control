"""Durable fail-closed NMR source reservation and process-bound reconciliation.

Never commands a fitter, RF source, or physical switch. The owning sequencer
must compare a trusted IPC snapshot with fresh alarm-validated EPICS readbacks
and verify RF OFF before calling reconcile().
"""
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile

from logic_devices.nmr_source_ack import SOURCES, validate_source_change_ack


class NMRSourceRecovery:
    def __init__(self, path, *, fitter_uid, fitter_pid):
        p = Path(path)
        if not p.is_absolute() or p.name in ('', '.', '..'):
            raise ValueError('Absolute durable NMR recovery file required')
        if type(fitter_uid) is not int or fitter_uid < 0 or type(fitter_pid) is not int or fitter_pid <= 0:
            raise ValueError('Trusted fitter UID and PID required')
        parent = p.parent
        info = parent.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022):
            raise PermissionError('Recovery directory must be owner-controlled')
        self.path = p
        self.fitter_uid = fitter_uid
        self.fitter_pid = fitter_pid
        self._lock_path = p.with_name(p.name + '.lock')
        self._lock_fd = None
        self.ready = False
        self.record = None
        try:
            self._lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError('Unsafe recovery lock permissions')
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.record = self._load()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None  # Requires separately approved initial commissioning.
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or info.st_size > 2048):
                raise PermissionError('Unsafe recovery file')
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                fd = None
                obj = json.load(stream)
        finally:
            if fd is not None:
                os.close(fd)
        if (type(obj) is not dict or obj.get('version') != 1
                or obj.get('state') not in ('ready', 'reserved', 'dispatching')
                or obj.get('source') not in SOURCES
                or type(obj.get('epoch')) is not int or obj['epoch'] < 0
                or type(obj.get('identity')) is not str or not obj['identity']):
            raise ValueError('Invalid or corrupt NMR source recovery record')
        if obj['state'] == 'dispatching' and obj.get('requested') not in SOURCES:
            raise ValueError('Invalid pending recovery target')
        return obj

    def process_identity(self):
        """Linux boot ID + process start ticks avoid PID-reuse false positives."""
        proc = Path('/proc') / str(self.fitter_pid)
        info = proc.stat()
        if info.st_uid != self.fitter_uid:
            raise PermissionError('Fitter process UID changed')
        raw = (proc / 'stat').read_text()
        end = raw.rfind(') ')
        if end < 0:
            raise RuntimeError('Invalid fitter process identity')
        start_ticks = raw[end + 2:].split()[19]  # stat field 22
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return f'{boot}:{self.fitter_uid}:{self.fitter_pid}:{start_ticks}'

    def _save(self, obj):
        payload = (json.dumps(obj, sort_keys=True, separators=(',', ':')) + '\n').encode()
        fd, tmp = tempfile.mkstemp(prefix='.nmr-recovery-', dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self.record = obj

    @staticmethod
    def _validated(snapshot):
        if (type(snapshot) is not dict
                or snapshot.get('selected_source') not in SOURCES
                or type(snapshot.get('route_epoch')) is not int
                or snapshot['route_epoch'] < 0):
            raise RuntimeError('Unverified NMR source/epoch')
        return snapshot['selected_source'], snapshot['route_epoch']

    def commission_initial(self, snapshot):
        """One-time operator-approved bootstrap; never silently resets history."""
        if self.record is not None:
            raise RuntimeError('Existing NMR recovery history cannot be replaced')
        source, epoch = self._validated(snapshot)
        self._save(dict(version=1, state='ready', source=source,
                        epoch=epoch, identity=self.process_identity()))
        self.ready = True

    def reconcile(self, snapshot):
        """Call only with authenticated fitter+fresh EPICS agreement and RF OFF."""
        self.ready = False
        source, epoch = self._validated(snapshot)
        old = self.record
        if old is None:
            raise RuntimeError('Initial NMR recovery commissioning required')
        if old['identity'] != self.process_identity():
            raise RuntimeError('Fitter process identity changed; recovery refused')
        stage = old['state']
        before = {'selected_source': old['source'], 'route_epoch': old['epoch']}
        if stage == 'reserved':
            if snapshot != before:
                raise RuntimeError('Pre-dispatch reservation baseline changed')
        elif stage == 'ready':
            if source != old['source'] or epoch < old['epoch']:
                raise RuntimeError('Fitter source or route epoch regressed')
        elif not validate_source_change_ack(
            requested=old['requested'], previous_source=old['source'],
            previous_epoch=old['epoch'], observed_source=source, observed_epoch=epoch,
        ):
            raise RuntimeError('Uncertain NMR transaction has no verified acknowledgment')
        if stage != 'ready' or epoch != old['epoch']:
            self._save(dict(version=1, state='ready', source=source,
                            epoch=epoch, identity=old['identity']))
        self.ready = True
        return snapshot

    def operator_rebaseline(self, snapshot, *, expected_identity):
        """Explicit identity-fenced commissioning after an old fitter is retired.

        Caller MUST verify fresh RF OFF, authenticated live IPC and EPICS
        agreement. Never invoked by the ordinary startup recovery path.
        """
        current = self.process_identity()
        if (type(expected_identity) is not str or not expected_identity
                or expected_identity != current):
            raise PermissionError('Operator-approved fitter identity mismatch')
        source, epoch = self._validated(snapshot)
        previous = self.record
        if previous is not None and previous['identity'] == current:
            raise RuntimeError('Manual rebaseline requires a retired fitter instance')
        self.ready = False
        self._save(dict(version=1, state='ready', source=source, epoch=epoch,
                        identity=current, previous_state=(
                            None if previous is None else previous['state'])))
        self.ready = True

    def reserve(self):
        if not self.ready or self.record is None or self.record['state'] != 'ready':
            raise RuntimeError('NMR recovery baseline not reconciled')
        if self.record['identity'] != self.process_identity():
            self.ready = False
            raise RuntimeError('Fitter process identity changed')
        self.ready = False
        self._save({**self.record, 'state': 'reserved'})

    def dispatch(self, requested, before):
        if self.record is None or self.record['state'] != 'reserved':
            raise RuntimeError('No durable NMR source reservation')
        if self.record['identity'] != self.process_identity():
            raise RuntimeError('Fitter process identity changed')
        source, epoch = self._validated(before)
        if ((source, epoch) != (self.record['source'], self.record['epoch'])
                or requested not in SOURCES or requested == source):
            raise RuntimeError('NMR source baseline changed; command refused')
        # Persist BEFORE socket transmission. Crash after this point is uncertain.
        self._save({**self.record, 'state': 'dispatching', 'requested': requested})

    def cancel_before_dispatch(self):
        if self.record is None or self.record['state'] != 'reserved':
            raise RuntimeError('Cannot clear uncertain NMR transaction')
        if self.record['identity'] != self.process_identity():
            raise RuntimeError('Fitter process identity changed')
        self._save({**self.record, 'state': 'ready'})
        self.ready = True

    def confirm(self, snapshot):
        if self.record is None or self.record['state'] != 'dispatching':
            raise RuntimeError('No dispatched NMR transaction to confirm')
        self.reconcile(snapshot)
