"""
devices/serial_tcp_base.py - instruments reached through a serial-to-Ethernet gateway.

The fourth transport in this library, beside telnet_base.py (telnet boxes),
modbus_base.py (register devices) and scpi_base.py (LXI instruments with their
own Ethernet port on TCP 5025).

The vacuum gauges and the fringe-field gaussmeter have RS-232 only.  They are
wired to a DIEWU / LINOVISION serial-to-Ethernet gateway in TCP-server mode:
each serial port of the box is a TCP port, and the box passes bytes through
untouched.  So the connection is a plain socket, but - unlike SCPI on 5025 -
what travels over it is whatever that instrument's own protocol happens to be:

    MKS 902B     ASCII, framed by ';FF'        @253PR4?;FF -> @253ACK7.600E+2;FF
    Pfeiffer TPG ASCII, ACK/ENQ handshake      PRX<CR><LF> ACK, ENQ -> data
    Lake Shore   ASCII, CR/LF terminated       RDGFIELD? -> +1.2345E+00
    IONIVAC      7-byte BINARY telegrams, sent continuously, never asked for

So this module gives a driver the raw moves - send bytes, read until a
terminator, read exactly N bytes, throw away what is stale - and lets the
driver spell out its own protocol.  Every failure becomes OSError, which is
what BaseDevice catches to alarm the PVs and reconnect.

settings.yaml keys are the same as every other transport:

    ip: '192.168.1.251'     the gateway, not the instrument
    port: 5004              which serial port of the gateway
    timeout: 2
    settle: 0.05

One gateway, several IOCs: each TCP port is a separate socket, so the four
instruments here run as four independent IOCs even though one box serves them.
"""

import logging
import socket

from .scpi_base import ScpiDevice

log = logging.getLogger(__name__)


class SerialTcpConnection:
    """A byte pipe to one serial port of the gateway.

    Deliberately not line-oriented: `eol` is only the default for
    :meth:`read_until`, and a binary instrument ignores it altogether.
    """

    #: default line terminator for ASCII instruments on this transport
    eol = b"\r\n"

    def __init__(self, host, port, timeout=2.0, settle=0.05):
        self.host, self.port = host, int(port)
        self.timeout, self.settle = float(timeout), float(settle)
        self._buf = bytearray()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
        except OSError as e:
            raise OSError(f"connect to gateway {self.host}:{self.port} failed: {e}") from e
        log.info("connected to serial gateway %s:%d", self.host, self.port)

    # ---------------------------------------------------------------- wire
    def send(self, data):
        """Write raw bytes (str is encoded as ASCII, with no terminator added)."""
        if isinstance(data, str):
            data = data.encode("ascii")
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise OSError(f"write {data!r} to {self.host}:{self.port}: {e}") from e

    def _fill(self):
        try:
            chunk = self.sock.recv(4096)
        except socket.timeout as e:
            raise OSError(f"no reply from {self.host}:{self.port} within "
                          f"{self.timeout:.1f} s") from e
        except OSError as e:
            raise OSError(f"read from {self.host}:{self.port}: {e}") from e
        if not chunk:
            raise OSError(f"{self.host}:{self.port} closed the connection")
        self._buf.extend(chunk)

    def read_until(self, terminator=None, limit=4096):
        """Read until `terminator` (default ``eol``) and return the bytes including it."""
        term = terminator if terminator is not None else self.eol
        if isinstance(term, str):
            term = term.encode("ascii")
        while term not in self._buf:
            if len(self._buf) > limit:
                raise OSError(f"{self.host}:{self.port}: {limit} bytes with no {term!r}")
            self._fill()
        end = self._buf.index(term) + len(term)
        out = bytes(self._buf[:end])
        del self._buf[:end]
        return out

    def read_exactly(self, count):
        while len(self._buf) < count:
            self._fill()
        out = bytes(self._buf[:count])
        del self._buf[:count]
        return out

    def read_available(self, max_bytes=4096):
        """Everything already waiting, without blocking.  For instruments that
        stream: the newest complete telegram is the reading, the rest is history."""
        self.sock.setblocking(False)
        try:
            while len(self._buf) < max_bytes:
                try:
                    chunk = self.sock.recv(4096)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError as e:
                    raise OSError(f"read from {self.host}:{self.port}: {e}") from e
                if not chunk:
                    raise OSError(f"{self.host}:{self.port} closed the connection")
                self._buf.extend(chunk)
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self.timeout)
        out = bytes(self._buf)
        self._buf.clear()
        return out

    def discard_input(self):
        """Drop anything the gateway is still holding.

        Serial gateways keep bytes buffered across TCP connections, so the first
        reply you read can belong to the PREVIOUS conversation.  Every ASCII
        driver here clears the pipe before a query for that reason.
        """
        self._buf.clear()
        self.sock.setblocking(False)
        try:
            while True:
                try:
                    if not self.sock.recv(4096):
                        break
                except (BlockingIOError, InterruptedError, OSError):
                    break
        finally:
            self.sock.setblocking(True)
            self.sock.settimeout(self.timeout)

    def query_line(self, command, terminator=None):
        """Send one ASCII command + terminator and return the reply, stripped."""
        term = terminator if terminator is not None else self.eol
        self.discard_input()
        self.send((command.rstrip("\r\n")).encode("ascii") + term)
        return self.read_until(term).decode("ascii", errors="replace").strip()

    def close(self):
        try:
            self.sock.close()
        except Exception:                        # noqa: BLE001 - best effort
            pass


class SerialTcpDevice(ScpiDevice):
    """BaseDevice for gateway instruments.

    Everything useful is inherited from ScpiDevice: the settings-driven
    connection, alarms by channel prefix (a dead gauge turns its whole block of
    PVs INVALID), reconnect-on-OSError, and the generic ``do_reads`` that calls
    the driver's ``read_all(channel, index)``.  Only the transport differs.
    """

    connection_class = SerialTcpConnection
