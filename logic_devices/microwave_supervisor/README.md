# Microwave supervisor IOC

This IOC is the operator-facing layer above the existing `bridge12_mw` Vaunix
IOC and `spd1305x` UCA power-supply IOC. It does not open either device. It uses
their Channel Access PVs, and the operator screen uses only `MW_SUP:*` controls.

## Safety behavior

- RF OFF is always accepted.
- Only an OFF-to-ON request is interlocked. Both device IOCs must be connected
  and the measured UCA voltage (`CH1_VI`) must be 5.0 V +/- 0.10 V (configurable).
- Once RF is confirmed ON, a later UCA voltage reduction does not trip RF.
- Disconnection never causes an automatic RF-ON command. A running software
  sweep stops on Vaunix disconnect or command failure.
- `simulation: true` is the checked-in default. It serves all supervisor PVs
  but makes no writes to the hardware IOCs.

The original raw `MW_RF` PV remains available for device maintenance because
EPICS Channel Access has no authorization layer here. Do not put that raw PV on
operator screens. Production installations should additionally restrict raw
PV writes with the site's EPICS access-security configuration.

## Frequency and sweep

The public frequency is the microwave-box output in GHz. The box multiplies the
Vaunix frequency by 12, so an operator request of 140 GHz writes 11.6666667 GHz
to `MW_FC`. The accepted output range is 134--142 GHz. `MW_OutFI` is also now
configured as the x12 output readback. Hz conversion remains in the Vaunix
device layer and Pi shim.

The slow sweep is a non-blocking IOC state machine. It writes the initial point,
dwells, writes each next point, and always includes the exact stop frequency
once. The sign of the step must agree with sweep direction. Stop returns the
sweep to Idle; Cancel records a Cancelled state. Both are responsive during a
dwell and neither turns RF on or off.

## Magic constants still required

No gamma-d, dopant list, Magic+, or Magic- constants were found in the toolkit,
GUI, Vaunix SDK examples, or LabVIEW-derived material present in this checkout.
The YAML therefore contains intentional `null` placeholders. Magic actions are
disabled logically (they report `LastError`) until real values are supplied.

The implemented relationship is exactly:

`output_GHz = central_GHz / gamma_d_over_2pi * magic_plus_or_minus_factor`

Before enabling this feature, confirm the formula, the units of all three
terms, the dopant names, and each +/- factor with the experiment owner. The
result is also checked against the 134--142 GHz output range.

## Run

1. Leave `simulation: true`, start `python master_ioc.py -i microwave_supervisor`,
   and open `gui/microwave_supervisor.bob` with macro `P=MYLAB`.
2. Exercise RF permissive, conversion, Magic error reporting, and both sweep
   directions without hardware writes.
3. Start and verify the `spd1305x` and `bridge12_mw` IOCs independently.
4. Set `simulation: false`, restart the supervisor, and use only the supervisor
   screen for normal operation.

Run the pure-logic tests with:

`venv/bin/python -m unittest tests/test_microwave_supervisor.py`
