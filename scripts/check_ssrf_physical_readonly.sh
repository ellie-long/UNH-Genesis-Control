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
echo "===== UBUNTU / PI CLOCK ====="

# Use a two-sided SSH measurement so command latency does not
# bias the estimated clock offset.
UBUNTU_BEFORE_MS="$(date +%s%3N)"
PI_EPOCH_MS="$(ssh -S "$SOCK" "$PI_SSH" 'date +%s%3N')"
UBUNTU_AFTER_MS="$(date +%s%3N)"

ROUND_TRIP_MS=$(( UBUNTU_AFTER_MS - UBUNTU_BEFORE_MS ))

if (( ROUND_TRIP_MS < 0 || ROUND_TRIP_MS > 2000 )); then
    echo "[FAIL] Clock measurement latency too high; retry preflight"
    false
fi

UBUNTU_MID_MS=$(( UBUNTU_BEFORE_MS + ROUND_TRIP_MS / 2 ))
DIFF_MS=$(( UBUNTU_MID_MS - PI_EPOCH_MS ))

if (( DIFF_MS < 0 )); then
    ABS_DIFF_MS=$(( -DIFF_MS ))
else
    ABS_DIFF_MS=$DIFF_MS
fi

echo "Ubuntu midpoint ms = $UBUNTU_MID_MS"
echo "Pi epoch ms         = $PI_EPOCH_MS"
echo "SSH round trip      = ${ROUND_TRIP_MS} ms"
echo "clock difference    = ${DIFF_MS} ms"

if (( ABS_DIFF_MS > 10000 )); then
    echo "[FAIL] Actual clock offset exceeds 10 seconds"
    false
fi

echo "[PASS] Clocks agree within 10 seconds"

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
