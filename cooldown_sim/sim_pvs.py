"""Sim Control PV vocabulary shared by the controller IOC, the screen generator
and tests (no EPICS imports here)."""

FAULT_MODES = ("none", "refuse", "absent", "silent", "garbage", "nak")
FAULT_INSTRUMENTS = ("ls224_fridge", "ls224_magnet", "mks902b", "tpg262", "ionivac", "ls455",
                     "siglent_coldboard", "mw_spd1305x")
AUTOPILOT_LEGS = ("mw", "ssrf", "fit")
