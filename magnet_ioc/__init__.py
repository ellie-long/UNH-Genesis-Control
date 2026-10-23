"""Genesis magnet subsystem: the Oxford IPS120-10 power supply for the AMI magnet.

Ported from the student's Magnet_V3 IOC (reviewed 2026-10-22). The student's
structure is kept so later versions of their code can be carried across:

    ips120_protocol.py   their IPSClient, read_* helpers and status decoding
    ips120_sequences.py  their ramp, heater, match and H2-check sequences
    device.py            the Genesis wrapper (settings, PVs, write gate,
                         cryogenics precondition, recording)

PORTING.md maps each student function to its counterpart here and lists the
review issues fixed in the port.
"""
