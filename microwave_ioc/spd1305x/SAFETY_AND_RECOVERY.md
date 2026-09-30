# SPD1305X safety limits and recovery

Edit `SAFETY_MAX_VOLTAGE` and `SAFETY_MAX_CURRENT` in `controller.py`, then restart the IOC. Voltage is capped at 5.0 V. Current is capped at 0.1 A. Output ON is rejected if the code current ceiling is set to zero. Choose a current ceiling in increments of 0.001 A.

YAML `max_voltage` and `max_current` can further lower these ceilings. Existing 30 V / 5 A YAML values cannot raise them. Editing EPICS record limits cannot bypass controller checks. Nonfinite, negative, excessive, and rounding-above-limit requests are rejected before a setpoint command is sent. OVP/OCP are separate trip thresholds. Source constants DEFAULT_OVP = 5.1 V and DEFAULT_OCP = 0.11 A are both startup defaults and maximum protection PV values. Lower output limits in YAML do not lower these protection defaults.

On every successful new connection, the IOC turns output OFF, disables the timer, sets voltage/current to zero, and checks output/timer status and setpoints. It then programs OVP to 5.1 V and OCP to 0.11 A and verifies both readbacks before declaring the connection ready. These defaults are reapplied on every reconnect, replacing any lower protection values selected during the previous session. It never replays a disconnected write or restores output ON. Reapply desired setpoints and explicitly enable output after reconnection. Before output ON, the IOC disables/checks the timer and validates device setpoints. Polling also shuts output off if it detects unsafe setpoints or an active timer.

Communication failures close and discard the connection. The next normal polling cycle attempts a fresh connection. Failed handshakes and initial reads close their sockets too. Reads, writes, and reconnections share a lock; commands are paced using `command_delay` (default 0.05 seconds). Query errors include the failing SCPI command. The speculative SYSTem:REMote command has been removed from socket construction.

OVP/OCP commands remain firmware dependent. If Last_Error identifies OVP? or OCP? on every attempt, confirm the installed firmware's support and syntax before treating that as a network problem. Socket cleanup cannot make unsupported SCPI commands work.

These are IOC command limits, not a guarantee that physical output stays below 5 V during instrument faults, startup before the IOC connects, transients, remote sensing compensation, front-panel changes, or control from other clients. A physical never-above-5-V requirement needs independent hardware protection and margin below 5 V.

Validation: 21 offline unit tests cover bounds, NaN/infinity, rounding, configuration bypass, safe initialization, timer/output interlock, leaked-socket cleanup, disconnected writes, failed boot attempts, power-loss recovery, and fragmented replies. No physical supply was contacted. After restart, validate a power cycle with the load disconnected; confirm Connected returns, output remains OFF, and setpoints are zero before reconnecting the load.

Protection headroom does not raise the output command limits, but OVP at 5.1 V permits physical voltage above 5 V before a trip. These starting margins are not a guarantee against transient trips. Existing protection pop-ups are not automatically cleared. Readback updates use process=False to avoid sending device readings back as commands.

## Read-only commissioning mode

Genesis Phase-I defaults the SPD1305X IOC to `write_enabled: false`.

In this mode:

- Instrument identity and state may be queried normally.
- Periodic readback polling remains active.
- Connection does not run hardware initialization writes.
- EPICS command callbacks are rejected before reaching the instrument.
- The controller independently blocks hardware-changing SCPI writes.
- Unsafe hardware states are reported without automatically modifying the supply.

Hardware writes must be explicitly enabled in configuration after commissioning and safety validation.
