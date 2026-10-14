"""Shared emulator machinery: loopback TCP server, framing, faults, write log.

An emulator subclass implements a small protocol interface:

    frame(buffer)      -> (list of complete requests, remaining buffer)
    respond(request)   -> bytes to send back, or None for no reply
    garbage(request)   -> an unparseable reply for the 'garbage' fault
    nak(request)       -> the instrument's own refusal for the 'nak' fault
    is_write(request)  -> True if the request changes instrument state
    stream()           -> bytes to push unprompted (IONIVAC only), or None

Fault modes (settable at any time, e.g. from Sim Control):

    none      normal
    refuse    stop listening; existing connections dropped (instrument
              unplugged / gateway port dead)
    absent    as refuse, but reported as "instrument not present"
    silent    accept and read, never answer (driver times out)
    garbage   answer with bytes the driver cannot parse
    nak       answer with the instrument's protocol-level refusal
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass, field

from ..ports import LOOPBACK

log = logging.getLogger(__name__)

FAULT_MODES = ("none", "refuse", "absent", "silent", "garbage", "nak")


@dataclass
class Exchange:
    wall: float
    request: bytes
    reply: bytes | None
    write: bool


@dataclass
class FaultController:
    mode: str = "none"
    changed: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, mode: str) -> None:
        if mode not in FAULT_MODES:
            raise ValueError(f"fault mode {mode!r} not in {FAULT_MODES}")
        with self._lock:
            self.mode = mode
            self.changed = time.time()

    def get(self) -> str:
        with self._lock:
            return self.mode


class Emulator:
    """Protocol base: line-oriented by default."""

    name = "emulator"
    eol = b"\r\n"

    def __init__(self) -> None:
        self.fault = FaultController()
        self.exchanges: list[Exchange] = []
        self.max_exchanges = 20000
        self._log_lock = threading.Lock()

    # ---------------------------------------------------- protocol interface
    def frame(self, buf: bytes) -> tuple[list[bytes], bytes]:
        out = []
        while True:
            i = buf.find(b"\n")
            if i < 0:
                return out, buf
            out.append(buf[: i + 1])
            buf = buf[i + 1:]

    def respond(self, request: bytes) -> bytes | None:            # pragma: no cover
        raise NotImplementedError

    def garbage(self, request: bytes) -> bytes:
        return b"\x00\x7f??GARBAGE??" + self.eol

    def nak(self, request: bytes) -> bytes | None:
        return self.garbage(request)

    def is_write(self, request: bytes) -> bool:
        return False

    def stream(self) -> bytes | None:
        return None

    stream_interval_s = 0.1

    # ------------------------------------------------------------- dispatch
    def handle(self, request: bytes) -> bytes | None:
        mode = self.fault.get()
        if mode == "silent":
            reply = None
        elif mode == "garbage":
            reply = self.garbage(request)
        elif mode == "nak":
            reply = self.nak(request)
        else:
            try:
                reply = self.respond(request)
            except Exception:          # noqa: BLE001 - never kill the server thread
                log.exception("%s: error answering %r", self.name, request)
                reply = self.garbage(request)
        self._record(request, reply)
        return reply

    def _record(self, request: bytes, reply: bytes | None) -> None:
        with self._log_lock:
            if len(self.exchanges) >= self.max_exchanges:
                del self.exchanges[: self.max_exchanges // 10]
            self.exchanges.append(Exchange(time.time(), request, reply, self.is_write(request)))

    def writes(self) -> list[Exchange]:
        with self._log_lock:
            return [e for e in self.exchanges if e.write]


class LoopbackServer:
    """Threaded TCP server for one emulator, bound to 127.0.0.1 only."""

    def __init__(self, emulator: Emulator, port: int = 0, host: str = LOOPBACK):
        if host != LOOPBACK:
            raise ValueError(f"emulators bind to {LOOPBACK} only, not {host!r}")
        self.emulator = emulator
        self.host = host
        self.requested_port = int(port)
        self.port: int | None = None
        self._sock: socket.socket | None = None
        self._clients: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._accept_thread: threading.Thread | None = None
        self._listening = False

    # ------------------------------------------------------------- lifecycle
    def start(self) -> "LoopbackServer":
        self._stop.clear()
        self._open_listener()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._close_listener()
        self._drop_clients()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def apply_fault(self) -> None:
        """Bring the listening state in line with the emulator's fault mode."""
        mode = self.emulator.fault.get()
        if mode in ("refuse", "absent"):
            if self._listening:
                self._close_listener()
                self._drop_clients()
        elif not self._listening and not self._stop.is_set():
            self._open_listener()

    def set_fault(self, mode: str) -> None:
        self.emulator.fault.set(mode)
        self.apply_fault()

    # -------------------------------------------------------------- internal
    def _open_listener(self) -> None:
        if self.emulator.fault.get() in ("refuse", "absent"):
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):          # Windows: no port sharing
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        port = self.port if self.port is not None else self.requested_port
        sock.bind((self.host, port))
        sock.listen(8)
        sock.settimeout(0.2)
        self._sock = sock
        self.port = sock.getsockname()[1]
        self._listening = True
        self._accept_thread = threading.Thread(target=self._accept_loop, args=(sock,),
                                               name=f"{self.emulator.name}-accept", daemon=True)
        self._accept_thread.start()

    def _close_listener(self) -> None:
        self._listening = False
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        t = self._accept_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2)

    def _drop_clients(self) -> None:
        with self._lock:
            clients, self._clients = self._clients, set()
        for c in clients:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass

    def _accept_loop(self, sock: socket.socket) -> None:
        while self._listening and not self._stop.is_set() and sock is self._sock:
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.05)
            with self._lock:
                self._clients.add(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True,
                             name=f"{self.emulator.name}-conn").start()

    def _serve(self, conn: socket.socket) -> None:
        emu = self.emulator
        buf = b""
        next_push = time.monotonic()
        try:
            while not self._stop.is_set():
                with self._lock:
                    if conn not in self._clients:
                        break
                # unprompted stream (IONIVAC)
                now = time.monotonic()
                if now >= next_push:
                    next_push = now + emu.stream_interval_s
                    if emu.fault.get() == "garbage":
                        data = emu.garbage(b"") if emu.stream() is not None else None
                    elif emu.fault.get() in ("silent", "nak"):
                        data = None
                    else:
                        data = emu.stream()
                    if data:
                        conn.sendall(data)
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                requests, buf = emu.frame(buf)
                for req in requests:
                    reply = emu.handle(req)
                    if reply:
                        conn.sendall(reply)
        except OSError:
            pass
        finally:
            with self._lock:
                self._clients.discard(conn)
            try:
                conn.close()
            except OSError:
                pass
