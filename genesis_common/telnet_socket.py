"""Socket-backed stand-in for the stdlib telnetlib removed in Python 3.13.

PEP 594 dropped telnetlib from the standard library in 3.13, which would
otherwise strand every Telnet driver in this package. None of the instruments
here are actually Telnet servers -- they sit on serial-to-Ethernet adapter
channels (ports 1001-1003, 4444, 5555, ...) that carry raw bytes -- so what the
drivers really need is a socket with a buffer and a delimiter search, which is
all this provides.

Only the three calls the drivers use are implemented, with the same signatures
and the same return contracts as the stdlib class, so no driver has to change:

    write(buffer)                 -> None
    read_until(expected, timeout) -> bytes, up to and including expected
    expect(patterns, timeout)     -> (index, match, text)

Both readers return whatever is buffered rather than raising when they time out,
matching telnetlib, and both leave anything past the match in the buffer for the
next call.

Telnet option negotiation is refused rather than ignored: an adapter that has
been configured for Telnet rather than raw TCP will open with IAC sequences, and
letting those bytes reach a driver's regex would be a confusing failure. That
mirrors what telnetlib did with no option callback installed.
"""
import re
import socket
import time

IAC = 255    # interpret as command
DONT = 254
DO = 253
WONT = 252
WILL = 251
SB = 250     # subnegotiation begin
SE = 240     # subnegotiation end

_IAC_B = bytes([IAC])
_IAC_SE = bytes([IAC, SE])


class TelnetSocket:
    """A buffered TCP socket with telnetlib's read semantics."""

    def __init__(self, host, port=23, timeout=None):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.eof = False
        self.buf = b''      # data ready for the caller
        self._raw = b''     # bytes received but not yet scanned for IAC
        # Bound before connecting so __del__ has something to look at if
        # create_connection raises -- e.g. the far end is not listening yet.
        self.sock = None
        self.sock = socket.create_connection((host, self.port), timeout)

    # ------------------------------------------------------------------ write

    def write(self, buffer):
        """Send bytes, doubling any IAC byte as the protocol requires."""
        if self.sock is None:
            raise OSError('write on a closed connection')
        if _IAC_B in buffer:
            buffer = buffer.replace(_IAC_B, _IAC_B + _IAC_B)
        self.sock.sendall(buffer)

    # ------------------------------------------------------------------ reads

    def read_until(self, expected, timeout=None):
        """Read until expected appears; return through the end of it.

        On timeout or EOF, returns whatever has arrived so far instead of
        raising -- callers here treat a short read as the error signal.
        """
        def found():
            i = self.buf.find(expected)
            return -1 if i < 0 else i + len(expected)
        return self._read_until(found, timeout)

    def expect(self, patterns, timeout=None):
        """Wait for the first of `patterns` to match.

        Returns (index, match, text) with text running to the end of the match,
        or (-1, None, text) if it times out, exactly as telnetlib did.
        """
        compiled = [p if hasattr(p, 'search') else re.compile(p) for p in patterns]
        hit = {}

        def found():
            for i, pattern in enumerate(compiled):
                m = pattern.search(self.buf)
                if m:
                    hit['index'], hit['match'] = i, m
                    return m.end()
            return -1

        text = self._read_until(found, timeout)
        if 'match' in hit:
            return hit['index'], hit['match'], text
        return -1, None, text

    def _read_until(self, found, timeout=None):
        """Pump the socket until found() reports a cut point, or time runs out."""
        if timeout is None:
            timeout = self.timeout
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            end = found()
            if end >= 0:
                data, self.buf = self.buf[:end], self.buf[end:]
                return data
            if self.eof:
                break
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            if not self._fill(remaining):
                break

        # Timed out or hit EOF: hand back the partial buffer and drain it, the
        # way telnetlib's read_very_lazy() did.
        data, self.buf = self.buf, b''
        return data

    def _fill(self, timeout):
        """One recv into the buffer. False if it timed out or the peer closed."""
        if self.sock is None:
            self.eof = True
            return False
        self.sock.settimeout(timeout)
        try:
            chunk = self.sock.recv(4096)
        except (socket.timeout, TimeoutError):
            return False
        if not chunk:
            self.eof = True
            return False
        self._raw += chunk
        self._process()
        return True

    def _process(self):
        """Split raw bytes into caller data and refused option negotiation.

        Anything ending mid-sequence stays in self._raw so a command split
        across two packets is handled on the next pass rather than corrupted.
        """
        data = self._raw
        out = bytearray()
        replies = bytearray()
        i, n = 0, len(data)

        while i < n:
            byte = data[i]
            if byte != IAC:
                out.append(byte)
                i += 1
                continue
            if i + 1 >= n:
                break                       # need the command byte
            command = data[i + 1]
            if command == IAC:              # escaped literal 255
                out.append(IAC)
                i += 2
            elif command in (DO, DONT, WILL, WONT):
                if i + 2 >= n:
                    break                   # need the option byte
                option = data[i + 2]
                # Say no to everything, as telnetlib did with no callback.
                if command == DO:
                    replies += bytes([IAC, WONT, option])
                elif command == WILL:
                    replies += bytes([IAC, DONT, option])
                i += 3
            elif command == SB:
                end = data.find(_IAC_SE, i + 2)
                if end < 0:
                    break                   # subnegotiation not complete yet
                i = end + 2
            else:
                i += 2                      # two-byte command, nothing to do

        self._raw = data[i:]
        self.buf += bytes(out)
        if replies:
            try:
                self.sock.sendall(bytes(replies))
            except OSError:
                pass                        # refusing options is best effort

    # ------------------------------------------------------------------ close

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __del__(self):
        self.close()
