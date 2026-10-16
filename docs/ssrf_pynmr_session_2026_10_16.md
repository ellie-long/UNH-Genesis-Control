# Genesis ssRF / PyNMR — October 16, 2026

Validated: live PyNMR Deuteron acquisition delivers 512-point spectra to the Genesis fitter and Phoebus.
PyNMR-only GUI cleanup, protected fitter startup, EPICS discovery, and IPC configuration committed.
Frozen commit: 38095318a3788b9e683156db3d6fef5fdce01f2a
Tag: pynmr-live-spectrum-validated-2026-10-16-104756-EDT
Focused regression tests: 26 passed.

Hardware: PyNMR A1/B1, target COM, A2/B2 terminated.
Normal NMR mask 0; ssRF switch mask 3.
RF/FM/WaveGen verified OFF. Target stick disconnected.

Outstanding: meaningful fitting/calibration, plot scaling, adaptive recipe WAITING_FOR_FIT, full BURN ONCE validation, and safe switch restoration.
No physical burn was performed.
Preserve separate jlab_pynmr local modifications.
