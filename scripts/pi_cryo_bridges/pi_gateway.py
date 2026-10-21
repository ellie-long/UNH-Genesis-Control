#!/usr/bin/env python3
r"""
tools/pi_gateway.py - run this ON the Raspberry Pi: serial in, TCP out.

    python3 pi_gateway.py --serial /dev/ttyUSB0 --baud 115200 --listen 5030

Why this exists
---------------
The Manometer Low gauge (Kurt J. Lesker, labelled KJL-SPARC-3.0) has a serial
port and no network interface.  It is wired to a Raspberry Pi, and the Pi can
read it - but nothing on the lab network can, so no IOC on the control PC can
serve it as a PV.

The four vacuum gauges already in this deployment have exactly the same shape:
RS-232 instruments behind a DIEWU serial-to-Ethernet gateway, where each serial
port of the box appears as a TCP port and the box passes bytes through
untouched.  devices/serial_tcp_base.py exists for precisely that arrangement.

So rather than invent a new protocol for this one gauge, this script turns the
Pi into the same kind of gateway.  The Pi passes bytes; the IOC on the control
PC parses them, next to every other gauge's parser, using
devices/instruments/manometer_low.py.

The alternative - having the Pi parse the reading and serve a tidy JSON number
- looks simpler and is worse: the parsing rule would then live on a second
machine, in a second language of its own, and would drift from the IOC that
depends on it.  One parser, in the tree that is version-controlled and tested.

Deliberately minimal
--------------------
  * only pyserial is needed, which the Pi already has for the reader script;
  * ONE client at a time, like a real gateway.  A second connection is
    accepted, told so, and closed, rather than silently interleaving two
    readers' bytes - that failure is very hard to diagnose from the IOC end;
  * the serial port is opened lazily and reopened if it disappears (a USB
    adapter that is unplugged and replaced comes back as the same device path);
  * nothing is written to the instrument unless a client sends something.  The
    gauge streams unprompted, so in normal use this is a one-way pipe.

Install it on the Pi
--------------------
    scp tools/pi_gateway.py unhpoltarg@192.168.1.24:~/
    ssh unhpoltarg@192.168.1.24
    python3 ~/pi_gateway.py --serial /dev/ttyUSB0 --baud 115200 --listen 5030

To keep it running across reboots, as a systemd unit
(/etc/systemd/system/pi-gateway.service):

    [Unit]
    Description=Serial-to-TCP gateway for the Manometer Low gauge
    After=network.target

    [Service]
    ExecStart=/usr/bin/python3 /home/unhpoltarg/pi_gateway.py \
              --serial /dev/ttyUSB0 --baud 115200 --listen 5030
    Restart=always
    RestartSec=5
    User=unhpoltarg

    [Install]
    WantedBy=multi-user.target

    sudo systemctl enable --now pi-gateway

Check it from the control PC before starting the IOC:

    python tools\probe_port.py 192.168.1.24 5030
"""

import argparse
import logging
import selectors
import socket
import sys
import time

log = logging.getLogger("pi_gateway")

BUSY_NOTICE = (b"# pi_gateway: another client already holds this port.\r\n"
               b"# One reader at a time, exactly like a hardware gateway.\r\n")


def open_serial(port, baud, timeout=0.2):
    try:
        import serial                       # noqa: WPS433 - optional at import
    except ImportError:
        raise SystemExit(
            "pi_gateway: pyserial is not installed.  On the Pi:\n"
            "    sudo apt install python3-serial     (or: pip3 install pyserial)")
    return serial.Serial(port, baud, timeout=timeout)


def serve(serial_port, baud, host, listen_port, idle_log=60.0):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, listen_port))
    server.listen(4)
    server.setblocking(False)
    log.info("listening on %s:%d, forwarding %s @ %d baud",
             host or "0.0.0.0", listen_port, serial_port, baud)

    sel = selectors.DefaultSelector()
    sel.register(server, selectors.EVENT_READ, "server")

    ser = None
    client = None
    bytes_in = 0
    last_report = time.monotonic()

    try:
        while True:
            # (Re)open the serial port lazily.  A USB adapter pulled and put
            # back comes up as the same path, so this recovers on its own
            # rather than needing the service restarted.
            if ser is None:
                try:
                    ser = open_serial(serial_port, baud)
                    sel.register(ser, selectors.EVENT_READ, "serial")
                    log.info("serial open: %s", serial_port)
                except Exception as e:                    # noqa: BLE001
                    log.error("serial %s unavailable (%s); retrying in 5 s",
                              serial_port, e)
                    time.sleep(5)
                    continue

            for key, _ in sel.select(timeout=1.0):
                if key.data == "server":
                    conn, addr = server.accept()
                    if client is not None:
                        # Say why, then close.  A gateway that quietly let two
                        # clients share one serial line would give each of them
                        # half the lines, which looks like a flaky instrument.
                        log.warning("refusing %s: %s already connected",
                                    addr[0], client[1][0])
                        try:
                            conn.sendall(BUSY_NOTICE)
                        except OSError:
                            pass
                        conn.close()
                        continue
                    conn.setblocking(False)
                    sel.register(conn, selectors.EVENT_READ, "client")
                    client = (conn, addr)
                    log.info("client connected: %s", addr[0])

                elif key.data == "serial":
                    try:
                        data = ser.read(ser.in_waiting or 1)
                    except Exception as e:                # noqa: BLE001
                        log.error("serial read failed (%s); reopening", e)
                        sel.unregister(ser)
                        try:
                            ser.close()
                        except Exception:                 # noqa: BLE001
                            pass
                        ser = None
                        break
                    if not data:
                        continue
                    bytes_in += len(data)
                    if client is not None:
                        try:
                            client[0].sendall(data)
                        except OSError as e:
                            log.info("client %s went away (%s)", client[1][0], e)
                            sel.unregister(client[0])
                            client[0].close()
                            client = None

                elif key.data == "client":
                    try:
                        data = key.fileobj.recv(4096)
                    except OSError:
                        data = b""
                    if not data:
                        log.info("client %s disconnected", client[1][0])
                        sel.unregister(key.fileobj)
                        key.fileobj.close()
                        client = None
                        continue
                    # Only reached if a client actually sends something: this
                    # gauge streams, so normally nothing ever comes this way.
                    if ser is not None:
                        ser.write(data)

            now = time.monotonic()
            if now - last_report >= idle_log:
                log.info("%d bytes forwarded, client=%s", bytes_in,
                         client[1][0] if client else "none")
                last_report = now
                if bytes_in == 0:
                    log.warning("nothing has arrived from %s - check the cable, "
                                "the baud rate, and that no other program on "
                                "this Pi holds the port", serial_port)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        for obj in (client[0] if client else None, ser, server):
            if obj is not None:
                try:
                    sel.unregister(obj)
                except Exception:                         # noqa: BLE001
                    pass
                try:
                    obj.close()
                except Exception:                         # noqa: BLE001
                    pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--serial", default="/dev/ttyUSB0",
                    help="serial device the gauge is on (default /dev/ttyUSB0)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--listen", type=int, default=5030,
                    help="TCP port to serve on (default 5030)")
    ap.add_argument("--bind", default="",
                    help="interface to bind; default all")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    serve(args.serial, args.baud, args.bind, args.listen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
