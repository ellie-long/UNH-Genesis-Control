# Genesis Provenance Notice

Genesis contains software derived from earlier EPICS control-system work by
James Maxwell and subsequent UNH Genesis development.

## Vendored BaseDevice

Genesis vendors the upstream `BaseDevice` implementation in:

    genesis_common/upstream_base_device.py

The implementation was copied from:

    Repository: https://github.com/jdmax/epics-device-lib.git
    File:       devices/base_device.py
    Commit:     b8ddaacc73a7ef41ecc90c5b157326f671850bfc

Within that upstream repository, `base_device.py` was introduced by:

    Commit: 96abd0d
    Date:   2026-05-28
    Author: James Maxwell
    Subject: Initial import: raw copy of devices/ from softioc_toolkit v1.0.0

Genesis-specific extensions to this foundation are maintained separately in:

    genesis_common/base_device.py

The repository's existing MIT license and James Maxwell copyright notice are
retained unchanged.

Additional subsystem-specific provenance and third-party dependencies should be
documented as Genesis cleanup and integration continue.
