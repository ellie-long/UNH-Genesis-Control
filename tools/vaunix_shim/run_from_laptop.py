#!/usr/bin/env python3
"""Deploy/start the Pi shim from settings.yaml, then run the local IOC."""
from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time

import yaml

# Keep authentication interactive so password-only Pi accounts work. Never
# accept a password as a command-line argument: that would expose it in shell
# history and process listings.
SSH_OPTIONS = ("-o", "BatchMode=no", "-o", "ConnectTimeout=8")


def checked(command):
    print("+", shlex.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True)


def load_config(settings_file, ioc_name):
    with settings_file.open(encoding="utf-8") as stream:
        settings = yaml.safe_load(stream)
    try: ioc, shim = settings[ioc_name], settings[ioc_name]["shim"]
    except (KeyError, TypeError) as exc:
        raise SystemExit(f"missing {exc} in {settings_file} for IOC {ioc_name}")
    required = ("ssh_host", "ssh_user", "remote_dir", "sdk_object", "test_mode")
    missing = [name for name in required if name not in shim]
    if missing: raise SystemExit(f"missing shim settings: {', '.join(missing)}")
    return ioc, shim


def ssh_target(shim):
    """Return exactly one OpenSSH ``user@host`` argument."""
    user, host = shim["ssh_user"], shim["ssh_host"]
    if not isinstance(user, str) or not isinstance(host, str):
        raise SystemExit("shim.ssh_user and shim.ssh_host must be YAML strings")
    if not user or not host or "@" in user or "@" in host:
        raise SystemExit(
            "configure shim.ssh_user as only the username and shim.ssh_host "
            "as only the hostname")
    return f"{user}@{host}"


def remote_command(target, command):
    checked(["ssh", *SSH_OPTIONS, target, command])


def deploy(project, target, shim):
    source = Path(shim["sdk_object"]).expanduser()
    if not source.is_file(): raise SystemExit(f"ARM vendor object not found: {source}")
    remote_dir = shim["remote_dir"].rstrip("/")
    remote_command(target, f"mkdir -p {shlex.quote(remote_dir)}")
    checked(["scp", *SSH_OPTIONS, project / "tools/vaunix_shim/vaunix_shim.py", source,
             f"{target}:{remote_dir}/"])
    build = (f"cd {shlex.quote(remote_dir)} && gcc -shared -o liblmshid.so "
             f"{shlex.quote(source.name)} -lpthread -lm -lrt -lusb-1.0")
    remote_command(target, build)


def start(target, ioc, shim):
    """Install/update the user service, then restart it in the selected mode."""
    remote_dir = shim["remote_dir"].rstrip("/")
    test_flag = " --test-mode" if shim["test_mode"] else ""
    serial_flag = ("" if shim.get("hardware_serial") is None
                   else f" --serial {int(shim['hardware_serial'])}")
    test_serial_flag = ("" if shim.get("test_serial") is None
                        else f" --test-serial {int(shim['test_serial'])}")
    limits = ioc["frequency_limits"]
    multiplier = float(ioc.get("multiplier", 1))
    if multiplier <= 0:
        raise SystemExit("bridge12_mw.multiplier must be positive")
    minimum_hz = float(limits["minimum_output_ghz"]) * 1e9 / multiplier
    maximum_hz = float(limits["maximum_output_ghz"]) * 1e9 / multiplier
    if minimum_hz >= maximum_hz:
        raise SystemExit("frequency_limits minimum must be less than maximum")
    limit_flags = (
        f" --minimum-hz {minimum_hz:.0f}"
        f" --maximum-hz {maximum_hz:.0f}")
    arguments = (f"--library {remote_dir}/liblmshid.so "
                 f"--bind {shim.get('bind', '0.0.0.0')} --port {int(ioc['port'])}"
                 f"{test_flag}{serial_flag}{test_serial_flag}{limit_flags}")
    unit = f"""[Unit]
Description=Vaunix Lab Brick TCP shim
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={remote_dir}
ExecStart=/usr/bin/python3 {remote_dir}/vaunix_shim.py {arguments}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""
    encoded = base64.b64encode(unit.encode("utf-8")).decode("ascii")
    command = (
        "mkdir -p $HOME/.config/systemd/user && "
        # One-time migration from the old nohup/PID-file launcher. Only kill
        # the recorded process when its command is this shim.
        f"legacy={shlex.quote(remote_dir + '/vaunix-shim.pid')}; "
        "if test -f \"$legacy\"; then pid=$(cat \"$legacy\"); "
        "case \"$(ps -p \"$pid\" -o args= 2>/dev/null)\" in "
        "*vaunix_shim.py*) kill \"$pid\" 2>/dev/null || true;; esac; "
        "rm -f \"$legacy\"; fi; "
        f"printf %s {shlex.quote(encoded)} | base64 -d > "
        "$HOME/.config/systemd/user/vaunix-shim.service && "
        "systemctl --user daemon-reload && "
        "systemctl --user enable vaunix-shim.service && "
        "systemctl --user restart vaunix-shim.service")
    remote_command(target, command)


def show_remote_log(target, shim):
    """Print the Pi-side startup error after a failed readiness check."""
    remote_dir = shim["remote_dir"].rstrip("/")
    print("\nPi shim log:", flush=True)
    subprocess.run(
        ["ssh", *SSH_OPTIONS, target,
         "journalctl --user -u vaunix-shim.service -n 100 --no-pager"])


def wait_until_ready(host, port, timeout):
    deadline, last_error = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1) as connection:
                connection.sendall(b"*IDN?\n")
                reply = connection.makefile("rb").readline().decode("ascii").strip()
                if reply.startswith("VAUNIX,"):
                    print("Pi shim ready:", reply, flush=True)
                    return
                last_error = RuntimeError(f"unexpected reply: {reply!r}")
        except OSError as exc: last_error = exc
        time.sleep(0.5)
    raise SystemExit(f"Pi shim did not become ready: {last_error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", default="settings.yaml")
    parser.add_argument("--ioc", default="bridge12_mw")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--shim-only", action="store_true")
    args = parser.parse_args()
    settings_file = Path(args.settings).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    ioc, shim = load_config(settings_file, args.ioc)
    target = ssh_target(shim)
    if not args.no_deploy: deploy(project, target, shim)
    start(target, ioc, shim)
    try:
        wait_until_ready(ioc["ip"], int(ioc["port"]),
                         float(shim.get("startup_timeout", 15)))
    except SystemExit:
        show_remote_log(target, shim)
        raise
    if args.shim_only: return
    os.chdir(project)
    python = project / "venv/bin/python"
    executable = python if python.exists() else Path(sys.executable)
    os.execv(str(executable), [str(executable), "master_ioc.py", "-s",
                               str(settings_file.parent), "-i", args.ioc])


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"remote setup failed (exit {exc.returncode}); check the Pi hostname, "
            "network connection, username, and SSH password") from None
