#!/usr/bin/env bash
#
# Genesis Phase-I ssRF physical read-only status.
#
# Queries only. No physical write commands are sent.
#

set -euo pipefail

ROOT="$(
    cd "$(dirname "${BASH_SOURCE[0]}")/.."
    pwd
)"

cd "$ROOT"

PI_SSH="${GENESIS_KEYSIGHT_PI_SSH:-unhpoltarg@192.168.1.24}"
SOCK="/tmp/genesis-physical-status-ssh-$$"

cleanup() {
    ssh -S "$SOCK" -O exit "$PI_SSH" >/dev/null 2>&1 || true
    rm -f "$SOCK" >/dev/null 2>&1 || true
}

trap cleanup EXIT

echo "============================================================"
echo " Genesis ssRF Physical Read-Only Status"
echo " NO STATE-CHANGING COMMANDS"
echo "============================================================"

echo
echo "===== LOCAL UTC ====="
date -u '+%Y-%m-%d_%H-%M-%S_UTC'

echo
echo "===== PHYSICAL PROFILE ====="

python - <<'PY2'
import yaml

path = (
    "configs/commissioning/"
    "ssrf_physical_readonly/"
    "keysight_wavegen/settings.yaml"
)

with open(path) as stream:
    cfg = yaml.safe_load(stream)

g = cfg["general"]
d = cfg["keysight_wavegen"]

print("prefix                =", g["prefix"])
print("resource              =", d["resource"])
print("write_enabled         =", d["write_enabled"])
print("initialize_on_connect =", d["initialize_on_connect"])

assert g["prefix"] == "UNH:GEN:SSRF:WGEN"
assert d["resource"] == (
    "USB0::10893::5990::MY57252012::0::INSTR"
)
assert d["visa_backend"] == "@py"
assert d["write_enabled"] is False
assert d["initialize_on_connect"] is False

for name in (
    "OUTPUT_CMD",
    "PERIOD_CMD",
    "VHIGH_CMD",
    "VLOW_CMD",
    "WAVEFORM_CMD",
    "LOAD_CMD",
):
    assert d["records"][name]["ASG"] == "GENESIS_SSRF_CMD"

print("[PASS] physical WaveGen profile is read-only")
PY2

echo
echo "===== SMC100A / SWITCH READBACK ====="

python - <<'PY2'
from ssrf_ioc.smc100a import DeviceConnection
from ssrf_ioc.minicircuits_switch import MiniCircuitsSwitchConnection

smc = DeviceConnection(
    "192.168.1.16",
    port=5025,
    timeout=2.0,
    settle=0.0,
)

try:
    state = smc.read_state()

    print("SMC frequency MHz =", state["freq_mhz"])
    print("SMC level dBm     =", state["level_dbm"])
    print("SMC RF            =", state["rf_on"])
    print("SMC FM            =", state["fm_on"])
    print("SMC power mode    =", state["pow_mode"])

    assert state["rf_on"] is False
    assert state["fm_on"] is False
    assert state["pow_mode"] == "CW"

finally:
    smc.close()

for label, ip, expected in (
    ("NMR", "192.168.1.8", 0),
    ("ssRF", "192.168.1.11", 3),
):
    switch = MiniCircuitsSwitchConnection(
        ip,
        80,
        2.0,
    )

    try:
        model = switch.model()
        serial = switch.serial()
        mask = switch.read_port_mask()

        print(f"{label} model  =", model)
        print(f"{label} serial =", serial)
        print(f"{label} mask   =", mask)

        assert model == "RC-2SPDT-A18"
        assert mask == expected

    finally:
        close = getattr(switch, "close", None)
        if callable(close):
            close()

print("[PASS] SMC/switch physical baseline safe")
PY2

echo
echo "===== ESTABLISH ONE PI SSH SESSION ====="

ssh     -M     -S "$SOCK"     -o ControlPersist=180     -o ConnectTimeout=5     -fN     "$PI_SSH"

echo "[PASS] SSH established"

echo
echo "===== MAC / PI CLOCK ====="

MAC_EPOCH="$(date +%s)"
PI_EPOCH="$(
    ssh -S "$SOCK" "$PI_SSH" 'date +%s'
)"

DIFF="$(( MAC_EPOCH - PI_EPOCH ))"
if [ "$DIFF" -lt 0 ]; then
    ABS_DIFF="$(( -DIFF ))"
else
    ABS_DIFF="$DIFF"
fi

echo "Mac epoch = $MAC_EPOCH"
echo "Pi epoch  = $PI_EPOCH"
echo "difference= ${DIFF} s"

if [ "$ABS_DIFF" -gt 10 ]; then
    echo "[FAIL] Pi clock differs by more than 10 seconds"
    echo "Run on Pi:"
    echo "  ~/genesis_keysight/scripts/sync_clock_https.sh"
    false
fi

echo "[PASS] clocks agree within 10 seconds"

echo
echo "===== PI GENESIS STATE ====="

ssh -S "$SOCK" "$PI_SSH" '
set -euo pipefail

echo "Pi UTC:"
date -u "+%Y-%m-%d_%H-%M-%S_UTC"

echo
echo "Relevant processes:"
ps aux     | grep -E       "master_ioc.py|keysight_wavegen|online_fitter"     | grep -v grep     || echo "[none]"

echo
echo "Port 8778:"
ss -ltn     | grep ":8778"     || echo "[FREE]"

echo
echo "Competing keysight-bridge service:"
if systemctl is-active --quiet keysight-bridge 2>/dev/null; then
    echo "[ACTIVE]"
else
    echo "[inactive]"
fi
'

IOC_ACTIVE="$(
    ssh -S "$SOCK" "$PI_SSH" '
    ps aux         | grep -E           "master_ioc.py.*keysight_wavegen"         | grep -v grep         || true
    '
)"

BRIDGE_ACTIVE="$(
    ssh -S "$SOCK" "$PI_SSH" '
    if systemctl is-active --quiet keysight-bridge 2>/dev/null; then
        echo yes
    else
        echo no
    fi
    '
)"

echo
echo "===== PHYSICAL KEYSIGHT ====="

if [ -n "$IOC_ACTIVE" ]; then
    echo "[INFO] Genesis Keysight IOC already owns the instrument."
    echo "[INFO] Direct USB probe skipped to avoid controller contention."
elif [ "$BRIDGE_ACTIVE" = "yes" ]; then
    echo "[FAIL] competing keysight-bridge service is active"
    false
else
    ssh -S "$SOCK" "$PI_SSH" '
    set -euo pipefail
    cd "$HOME/genesis_keysight"
    ./scripts/check_keysight.sh
    '
fi

echo
echo "============================================================"
echo " PHYSICAL READ-ONLY STATUS COMPLETE"
echo " NO STATE-CHANGING COMMANDS SENT"
echo "============================================================"
