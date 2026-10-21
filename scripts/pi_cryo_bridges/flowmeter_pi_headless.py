#!/usr/bin/env python3
"""Read a serial flowmeter on a headless Raspberry Pi and serve it over TCP."""

import argparse
import glob
import re
import socket
import threading
import time
from datetime import datetime

import serial


def find_serial_port(requested):
    if requested != "auto":
        return requested

    # The Teledyne Hastings flowmeter is a USB CDC device (ttyACM), not the
    # FTDI adapter (ttyUSB) - that one belongs to the Manometer Low gauge.
    candidates = (
        [p for p in sorted(glob.glob("/dev/serial/by-id/*"))
         if "hastings" in p.lower() or "teledyne" in p.lower()]
        + sorted(glob.glob("/dev/ttyACM*"))
    )
    if not candidates:
        raise FileNotFoundError(
            "Flowmeter not found. Check: ls -l /dev/serial/by-id/ /dev/ttyACM*"
        )
    return candidates[0]


class Flowmeter:
    def __init__(self, port, baud, command, correction):
        self.port = find_serial_port(port)
        self.command = command
        self.correction = correction
        self.lock = threading.Lock()
        self.ser = serial.Serial(
            port=self.port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.2,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        time.sleep(1.0)
        self.ser.reset_input_buffer()

    def read(self):
        with self.lock:
            self.ser.reset_input_buffer()
            self.ser.write((self.command + "\r").encode("ascii"))
            self.ser.flush()

            data = bytearray()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                byte = self.ser.read(1)
                if byte:
                    data.extend(byte)
                    if byte == b">":
                        break

        response = data.decode("ascii", errors="replace").replace(">", "").strip()
        match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", response)
        if not match:
            raise ValueError(f"No numeric value found in response {response!r}")

        raw = float(match.group(0))
        return raw, raw * self.correction, response

    def close(self):
        self.ser.close()


class LatestReading:
    def __init__(self):
        self.lock = threading.Lock()
        self.raw = None
        self.corrected = None
        self.response = ""
        self.error = "No reading yet"

    def update(self, raw, corrected, response):
        with self.lock:
            self.raw = raw
            self.corrected = corrected
            self.response = response
            self.error = ""

    def set_error(self, error):
        with self.lock:
            self.raw = None
            self.corrected = None
            self.response = ""
            self.error = str(error)

    def tcp_reply(self):
        with self.lock:
            if self.raw is None:
                return f"ERROR {self.error}>\r\n"
            # The Windows program extracts the first numeric value.
            return f"{self.raw:.6f}>\r\n"


def serial_reader(port, baud, command, correction, latest, interval, stop_event):
    """Retry serial access while keeping the TCP bridge available."""
    meter = None

    print("timestamp                  raw_flow       corrected_slm  raw_response",
          flush=True)

    try:
        while not stop_event.is_set():
            try:
                if meter is None:
                    meter = Flowmeter(port, baud, command, correction)
                    print(f"Serial connected: {meter.port}", flush=True)

                raw, corrected, response = meter.read()
                latest.update(raw, corrected, response)

                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                print(
                    f"{stamp}  {raw:14.6f}  {corrected:18.6f}  {response!r}",
                    flush=True,
                )
                stop_event.wait(interval)

            except Exception as exc:
                latest.set_error(exc)
                print(f"SERIAL_UNAVAILABLE: {exc}", flush=True)

                if meter is not None:
                    try:
                        meter.close()
                    except Exception:
                        pass
                    meter = None

                stop_event.wait(max(interval, 3.0))

    finally:
        if meter is not None:
            meter.close()


def serve_tcp(latest, host, port, stop_event):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen()
        server.settimeout(1.0)
        print(f"TCP server listening on {host}:{port}", flush=True)

        while not stop_event.is_set():
            try:
                client, address = server.accept()
            except socket.timeout:
                continue

            print(f"TCP client connected: {address[0]}:{address[1]}", flush=True)
            with client:
                client.settimeout(1.0)
                while not stop_event.is_set():
                    try:
                        request = client.recv(1024)
                    except socket.timeout:
                        continue
                    if not request:
                        break
                    client.sendall(latest.tcp_reply().encode("ascii"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="auto")
    parser.add_argument("--baud", type=int, default=19200)
    parser.add_argument("--command", default="F")
    parser.add_argument("--correction", type=float, default=28.0 / 19.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--tcp-port", type=int, default=5010)
    args = parser.parse_args()

    stop_event = threading.Event()
    latest = LatestReading()

    tcp_thread = threading.Thread(
        target=serve_tcp,
        args=(latest, args.host, args.tcp_port, stop_event),
        daemon=True,
    )
    tcp_thread.start()

    try:
        serial_reader(
            args.port, args.baud, args.command, args.correction,
            latest, args.interval, stop_event,
        )
    except KeyboardInterrupt:
        print("Stopping...", flush=True)
    finally:
        stop_event.set()
        tcp_thread.join(timeout=3.0)


if __name__ == "__main__":
    main()
