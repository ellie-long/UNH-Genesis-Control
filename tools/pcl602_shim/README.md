# PCL602USB robot shim and IOC

```text
EPICS clients → IOC computer → TCP port 1702 → Raspberry Pi → USB → PCL602USB
                                                  ↑
                                              USB joystick
```

The Genesis IOC lives in `microwave_ioc/pcl602/toolkit_device.py` and uses the
vendored Genesis `BaseDevice` foundation plus pythonSoftIOC. It needs neither
pyserial nor pygame. The complete Pi runtime is this folder; it does not import
the old `pcl602_ioc` package or EPICS.
Python 3.10 or newer is required on the Pi.

## Raspberry Pi setup

1. Copy this entire folder to `/opt/pcl602_shim` on the Pi.
2. Install dependencies:

   ```sh
   sudo apt-get update
   sudo apt-get install python3-serial python3-pygame
   ```

3. Find the controller's stable USB serial name:

   ```sh
   ls -l /dev/serial/by-id/
   ```

4. Edit `/opt/pcl602_shim/config.json`:
   - Set `serial_port` to that full `/dev/serial/by-id/...` path.
   - The controller uses address 0, 38400 baud, 8 data bits, no parity, one stop
     bit, no hardware flow control, and configurable XON/XOFF (default false).
   - Set `host` to the Pi's Ethernet address or leave `0.0.0.0` to listen on all
     interfaces. The IOC connects to the Pi's actual hostname/IP, never `0.0.0.0`.
   - X and Y both default to **-8388607 through +8388607 steps** as requested.
     Change `axes.X.minimum`, `axes.X.maximum`, and the Y equivalents here.
     Restart the shim after changing the file. Limits are owned and enforced by
     the Pi; the IOC displays the values returned by the Pi.
   - Preserved speeds: X 15000, Y 30000 steps/s; base speed 500; acceleration
     10000 steps/s² on both axes.
   - Joystick: axes 0/1, deadzone 0.4, button 0 stop, button 1 hold-to-move.
     Confirm button mapping before use; change the `joystick` entries as needed.
5. In `pcl602-shim.service`, replace `User=pi` if your Pi login is different.
   That user needs `dialout` and `input` group access. Install and start:

   ```sh
   sudo cp /opt/pcl602_shim/pcl602-shim.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now pcl602-shim
   journalctl -u pcl602-shim -f
   ```

For a foreground launch using a user already in `dialout` and `input`:

```sh
python3 /opt/pcl602_shim/pcl602_shim.py --config /opt/pcl602_shim/config.json
```

Only this service may open the USB controller. The serial connection requests
exclusive access on Linux. Do not run the old direct-USB IOC at the same time.
TCP control is unauthenticated: expose port 1702 only on the trusted control
network, and restrict access to the IOC host through the Pi/network firewall.

## IOC computer setup

The repository's `robot` entry uses this driver. Set its `ip` to the Pi's real
hostname/IP (`raspberrypi.local` is an editable placeholder):

```yaml
pcl602:
  module: microwave_ioc.pcl602.toolkit_device
  ip: mmWaveRobot.local
  port: 1702
  timeout: 3.0
  delay: 0.2
  channels: [X, Y]
  require_speed_profiles: true
```

Run from the Genesis repository root with the Genesis environment:

```sh
python master_ioc.py -s configs/microwave/pcl602 -i pcl602
```

The regular IOC read loop is the heartbeat. There is deliberately no separate
heartbeat thread that could keep the robot enabled after the IOC read loop stalls.
Keep the read interval well below the default 3-second watchdog.

## Operator behavior

- Startup and every new IOC connection enter **Disabled** mode. No reference,
  zeroing, or homing is required or performed automatically.
- Select **EPICS** (1) for signed relative moves, or **Joystick** (2) for the Pi's
  joystick. Changing modes stops motion first.
- After entering Joystick mode, center the stick and release the deadman, then
  hold the deadman to move. Releasing it stops both axes. Joystick removal, a
  stopped joystick worker (1-second timeout), or the stop button disables motion.
- Joystick commands are signed relative moves toward the corresponding configured
  limit, at the axis maximum speed. Each segment is capped at 8388607 steps; a
  held stick can start the next segment after the previous one finishes. They do not issue continuous slew commands. Thus
  the controller can finish at the bound even when busy firmware rejects
  position queries. Releasing the stick/deadman cancels the index with a hard stop. A reversal first
  sends a hard stop, then waits for an idle status on a subsequent joystick
  update before sending the latest requested direction.
- Relative moves read current controller position and reject targets outside the
  configured bounds. The limits use the controller's **VZ step-position counter**;
  this implementation does not read a separate encoder counter or enable encoder
  correction. Repeatability and the position counter's power-cycle behavior still
  need to be checked on the robot.
- Position queries are skipped during motion, matching the old IOC's firmware
  workaround. `_MI` holds the last reading and `_POSITION_FRESH` is false until
  idle position can be read again. `_BUSY` continues to update.
- Network disconnect or missed IOC heartbeats stops both axes and latches Disabled.
  Existing motor-current settings are preserved: the stop never sends motor-off.
  Reconnect, then explicitly select a mode. No motion command is replayed.
- Detection uses a 50 ms watchdog loop. Stop delivery also waits for an in-flight
  USB transaction and its timeout; this is a software stop, not a hardware E-stop.
  USB failure, Pi power loss, or a frozen OS can prevent stop delivery. USB errors
  latch a fault; inspect the hardware and restart the shim to clear it. The shim
  does not automatically resume motion or reset position after a fault.
- Switch-based homing is always rejected because the wiring is unreliable. `_HOME`
  remains present for existing clients but reports an error. The shim does not
  reconfigure the controller's hardware limit inputs.
- `_ZERO` is an optional explicit operation, allowed only in Disabled mode while
  idle. It changes the counter coordinate system, so the same numeric limits then
  refer to that new coordinate system.

## EPICS records

Names retain the toolkit's configured prefix and the old robot channel names.

| Record (X or Y prefix) | Meaning |
| --- | --- |
| `X_MI` | Last controller position, steps |
| `X_POSITION_FRESH` | 1 = current idle reading; 0 = cached/disconnected |
| `X_LOW_LIMIT`, `X_HIGH_LIMIT` | Read-only bounds supplied by the Pi |
| `X_MOVE_REL` | Signed relative step command; requires EPICS mode |
| `X_BUSY`, `X_LIMITS` | Moving status and controller limit status bits |
| `X_MOTOR_ENABLE` | Change current-enable output; requires Disabled mode |
| `X_MAX_SPEED`, `X_ACCEL` | Speed/acceleration settings; require idle axis |
| `X_STOP`, `X_ZERO`, `X_HOME` | Stop, optional counter zero, disabled homing |

Shared records: `CONTROL_MODE` (0 Disabled / 1 EPICS / 2 Joystick), `ESTOP`,
`COMM_STATUS`, `LAST_ERROR`, `FIRMWARE`, `CONTROLLER_ERROR`, `READ_ERROR`,
`JOYSTICK_CONNECTED`, `JOYSTICK_ACTIVE`. `COMM_STATUS` requires both network and
controller health. `LAST_ERROR` is limited to 39 characters by the EPICS string
record; full server-side errors are available in responses/service logging.

Genesis exposes ownership selection in `gui/genesis_mw_robot.bob`. The main
Microwave display and robot details show joystick connection, X/Y busy state,
raw hardware limit bits, and `MOTION_INHIBITED`. This read-only IOC record means
Disabled mode or unhealthy control; it is not proof of physical standstill or a
hardware e-stop readback. Check both busy readbacks and communication health.
The existing `ESTOP` is a momentary software STOP ALL command, not a physical
e-stop. STOP ALL disables ownership; axis STOP disarms joystick input while
retaining the selected mode. Neither removes holding current. After joystick
loss, reselect Joystick and release the deadman with both axes neutral before
moving again. No command is replayed after reconnect.

For strictly in-process ownership tests without network or hardware:

```sh
python3 -m unittest discover -s tools/pcl602_shim -p test_joystick_ownership.py -v
```

## Test without hardware

The simulator never opens USB and does not start a physical joystick worker:

```sh
python3 tools/pcl602_shim/pcl602_shim.py --simulate --host 127.0.0.1
```

Point a copy of the IOC settings at `127.0.0.1:1702`, then start the IOC. Its
normal PVs can exercise relative moves, stops, mode changes, and disconnects.
The simulator approximates motion at constant speed; it does not model motor
physics or validate real hardware behavior.

Automated tests from the toolkit root (no EPICS or physical hardware required):

```sh
python3 -m unittest discover -s tools/pcl602_shim -v
```

Tests use real local TCP sockets and small simulated travel bounds, including
connection loss, stale sessions, conflicting clients, joystick deadman/bounds,
USB faults, and the IOC driver with stand-in EPICS records.

## Wire protocol

One JSON object per ASCII line, maximum 8192 bytes including newline. Every
accepted frame returns one response: `{"ok":true,"result":...}` or
`{"ok":false,"error":"..."}`. There is one owning TCP session; other connections
are rejected. Invalid/truncated/oversized frames may close the session. An expired
session cannot re-enable motion. Successful requests renew the heartbeat.

```json
{"op":"READ"}
{"op":"MODE","value":1}
{"op":"MOVE_REL","axis":"X","value":100}
{"op":"STOP","axis":"X"}
{"op":"STOP_ALL"}
```

Other commands: `MODE` (0/1/2), `READ_ERROR`, `ZERO` with `axis`, and
`MOTOR_ENABLE` (0/1), `MAX_SPEED`, `ACCEL` with `axis` and integer `value`.
`READ` returns protocol version, health, mode, firmware, joystick status and
per-axis state. There is no arbitrary serial passthrough and no homing command.
After a timeout the client must close its connection; a motion command whose
reply was lost must never be automatically retried.

### Real EPICS callback regression

From an environment with pythonSoftIOC installed, run:

```sh
python tools/pcl602_shim/check_epics_callbacks.py
```

This starts a local simulated shim and real EPICS records, then uses `dbpf` to
verify stop/reset, mode re-enabling, motion and status updates. It never connects
to the physical robot. IOC output readbacks and button resets use `process=False`
to prevent status updates from triggering commands again.

### Joystick reversal update

The previous joystick implementation could target the opposite full-range
boundary with an absolute P command whose computed index exceeded the N
register range. It also used comma (soft stop) on centering; the controller
manual specifies that comma continues an indexed move at base speed. The
updated implementation uses explicit signed relative moves within the index
range and period (hard stop) to cancel indexed motion. The direction change
waits for an idle report. Tests cover both axes, negative-first movement,
continuation at an intermediate segment boundary, and pending reversal cancellation.

Malformed numeric replies now report their exact command and raw reply, and
a numeric busy response other than 0 or 1 is rejected instead of being treated
as Moving. These checks do not guess the meaning of undocumented responses.
Reference: PCL602USB user guide, Direct Talk commands N/P, comma, and period:
https://anaheimautomation.com/media/anaheim/files/manuals/stepper/L011364_-_PCL602USB_Users_Guide.pdf

Deploy the updated `motion.py` and `controller.py` to the Pi while the shim is
stopped, then restart the service. The IOC and existing configuration do not
need to change. Physical direction and the reported parsing failure still
require verification on the controller.
