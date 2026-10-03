#!/usr/bin/env bash
#
# UNH Genesis Phase-I operator helper
#
# Purpose:
#   One stable command surface for everyday Genesis development/operations.
#
# Safety policy:
#   - Normal startup uses the repository's read-only macOS startup script.
#   - Simulator activation is explicit and requires all localhost backends.
#   - The validated PHYSICAL_NO_RF workflow is exposed only through explicit ssRF commands.
#   - Amplified-RF / target-burn commissioning is NOT automated here.
#   - Forced shutdown requires an explicit acknowledgement token.
#   - This script never touches git stash.
#
# Recommended install:
#   cp genesisctl.sh scripts/genesisctl.sh
#   chmod +x scripts/genesisctl.sh
#   alias genesis='/absolute/path/to/genesis/scripts/genesisctl.sh'
#
set -u

die() {
    echo "ERROR: $*" >&2
    exit 1
}

note() {
    echo "[INFO] $*"
}

warn() {
    echo "[WARN] $*" >&2
}

pass() {
    echo "[OK] $*"
}

find_root() {
    local here script_dir candidate

    here="$(pwd)"
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    # Installed as <repo>/scripts/genesisctl.sh
    candidate="$(cd "${script_dir}/.." 2>/dev/null && pwd || true)"
    if [ -n "${candidate}" ] && [ -f "${candidate}/master_ioc.py" ]; then
        printf '%s\n' "${candidate}"
        return 0
    fi

    # Run from repository root.
    if [ -f "${here}/master_ioc.py" ]; then
        printf '%s\n' "${here}"
        return 0
    fi

    return 1
}

ROOT="$(find_root)" || die "Could not locate Genesis repository."
cd "${ROOT}" || die "Could not cd to ${ROOT}"

SIM_SCRIPT="${ROOT}/scripts/start_ssrf_sim_active.sh"
MICROWAVE_PHYSICAL_SCRIPT="${ROOT}/scripts/microwave_physical.sh"
SSRF_PHYSICAL_STATUS_SCRIPT="${ROOT}/scripts/check_ssrf_physical_readonly.sh"
SSRF_FINAL_START_SCRIPT="${ROOT}/scripts/start_ssrf_physical.sh"
SSRF_FINAL_STOP_SCRIPT="${ROOT}/scripts/stop_ssrf_physical.sh"
SSRF_KEYSIGHT_DEPLOY_SCRIPT="${ROOT}/scripts/deploy_ssrf_keysight_pi.sh"

require_file() {
    [ -f "$1" ] || die "Required file not found: $1"
}

require_env() {
    case "${CONDA_DEFAULT_ENV:-}" in
        genesis-p1)
            ;;
        *)
            die "Activate the Genesis environment first: conda activate genesis-p1"
            ;;
    esac
}

banner() {
    echo
    echo "============================================================"
    echo " UNH Genesis Phase-I :: genesisctl"
    echo " repo: ${ROOT}"
    echo "============================================================"
}

show_help() {
    cat <<'EOF'

Usage:
  genesis <command> [arguments]

EVERYDAY
  help                 Show this help.
  where                Show repository path, branch and HEAD.
  doctor               Check environment, required scripts and tools.
  status               Read-only summary of Git, screens and Genesis processes.
  physical-status      Read-only status of physical ssRF hardware and Keysight Pi.
  screens              Show detached/running screen sessions.
  processes            Show Genesis-related processes.
  logs [pattern]       Show newest log files, or tail logs matching pattern.
  git                  Show branch, HEAD, worktree and stash.
                       READ-ONLY; stash is never modified.
  endpoints            Show canonical physical/simulator endpoint and safety settings.

MICROWAVE PHYSICAL OPERATION
  mw-start              Start the validated Microwave physical/control stack.
  mw-status             Read-only status of the Microwave physical stack.
  mw-rf-ready           Verify the Microwave RF-control readiness conditions.
  mw-stop               Run the verified Microwave safe shutdown workflow.

SSRF PHYSICAL OPERATION
  ssrf-physical-status Read-only physical ssRF/Keysight status and safe baseline.
  ssrf-deploy-keysight Deploy the final Keysight IOC/runtime to the Raspberry Pi.
                       This does not start an IOC or send hardware commands.
  ssrf-start           Start the single final physical ssRF stack and operator GUI.
                       Startup remains disarmed with RF/FM/WaveGen output OFF.
  ssrf-stop            Request fail-safe ABORT, verify the restored baseline,
                       then stop the final physical ssRF stack.

SSRF SIMULATOR
  sim-check            Check required localhost simulator endpoints:
                         SMC100A  127.0.0.1:5025
                         WaveGen  127.0.0.1:5027
                         NMR SW   127.0.0.1:8081
                         ssRF SW  127.0.0.1:8082
  sim-start            Activate simulator commissioning IOC stack.
                       REFUSES unless all four simulator backends are listening.
                       This profile intentionally permits simulated writes.
  sim-status           Read-only EPICS status of the active ssRF stack.
  sim-stop             Stop only the active localhost simulator IOC stack.
                       Requires the dedicated ssrf-sim-manager session.
                       Leaves simulator backend processes untouched.

TESTS
  test-ssrf            Run all test_ssrf*.py unit/regression tests.
  test-mw              Run Microwave Phase-I unit/integration regression tests.
  archive-viewer       Launch common read-only Genesis EPICS archive viewer.
                       Includes Microwave/ssRF subsystem filtering.
  test-fitter          Run fitter/planner/stager/runtime integration tests.
  test-auth            Run Genesis/ssRF authorization-security tests.
  test-all             Run all Python unittest tests under tests/.
  compile              Compile-check Genesis Python source.
  check                git diff --check + unit compile + Git state.

LEGACY GENERIC COMMANDS
  start                Disabled; use an explicit subsystem workflow.
  stop                 Disabled; use the matching subsystem safe-stop command.
  force-stop           Disabled; no generic hardware-verification bypass is exposed.

BASELINE
  baseline             Check whether frozen ssRF 5B.6F commit 3ef33de
                       is contained in the current branch.

Important:
  * Generic start/stop commands are intentionally disabled.
  * Physical operation must use explicit subsystem commands.
  * initialize_on_connect=false must remain a safety invariant.
  * "sim-start" is for localhost simulator commissioning, not lab hardware.
  * "physical-status" remains a backwards-compatible read-only status alias.
  * ssrf-start is the single operator-facing physical ssRF workflow.
  * Startup never arms a burn and initialize_on_connect=false remains invariant.
  * GENERATE / LOAD prepares the source with all energy-producing outputs OFF.
  * This helper never runs git stash/pop/drop/clear.

Examples:
  genesis status
  genesis physical-status
  genesis mw-start
  genesis mw-status
  genesis mw-rf-ready
  genesis mw-stop

  genesis ssrf-physical-status
  genesis ssrf-deploy-keysight
  genesis ssrf-start
  genesis ssrf-stop
  genesis endpoints

  genesis sim-check
  genesis sim-start
  genesis sim-status
  genesis sim-stop

  genesis test-ssrf
  genesis archive-viewer
  genesis test-fitter
  genesis git

EOF
}

show_where() {
    echo "repo   = ${ROOT}"
    echo "branch = $(git branch --show-current 2>/dev/null || echo '?')"
    echo "HEAD   = $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
}

doctor() {
    banner
    show_where
    echo
    echo "===== ENVIRONMENT ====="
    echo "CONDA_DEFAULT_ENV = ${CONDA_DEFAULT_ENV:-<not set>}"
    if [ "${CONDA_DEFAULT_ENV:-}" = "genesis-p1" ]; then
        pass "genesis-p1 environment active"
    else
        warn "Activate with: conda activate genesis-p1"
    fi

    echo
    echo "===== REQUIRED REPOSITORY SCRIPTS ====="
    for f in "${SIM_SCRIPT}" "${MICROWAVE_PHYSICAL_SCRIPT}" "${SSRF_PHYSICAL_STATUS_SCRIPT}" "${SSRF_FINAL_START_SCRIPT}" "${SSRF_FINAL_STOP_SCRIPT}" "${SSRF_KEYSIGHT_DEPLOY_SCRIPT}"; do
        if [ -f "${f}" ]; then
            pass "${f#${ROOT}/}"
        else
            warn "missing ${f#${ROOT}/}"
        fi
    done

    echo
    echo "===== TOOLS ====="
    for cmd in python git screen nc; do
        if command -v "${cmd}" >/dev/null 2>&1; then
            pass "$(command -v "${cmd}")"
        else
            warn "${cmd} not found"
        fi
    done

    echo
    echo "===== SAFETY BASELINE ====="
    if git merge-base --is-ancestor 3ef33de HEAD >/dev/null 2>&1; then
        pass "ssRF 5B.6F baseline 3ef33de is in current history"
    else
        warn "5B.6F baseline 3ef33de is not an ancestor of current HEAD"
    fi
}

git_state() {
    banner
    echo "===== REPOSITORY ====="
    echo "branch = $(git branch --show-current)"
    echo "HEAD   = $(git rev-parse --short HEAD)"
    git log -1 --oneline --decorate
    echo

    echo "===== WORKTREE ====="
    if [ -z "$(git status --porcelain)" ]; then
        pass "worktree clean"
    else
        git status --short
    fi
    echo

    echo "===== STASH (READ-ONLY DISPLAY) ====="
    git stash list
    echo
    note "genesisctl never modifies stash."
}

show_screens() {
    echo "===== SCREEN SESSIONS ====="
    screen -ls || true
}

show_processes() {
    echo "===== GENESIS PROCESSES ====="
    pgrep -fl \
        'master_ioc.py|ioc_manager.py|archiver.py|phoebus|CSS_Phoebus|ssrf|smc100a|keysight|spd1305x|vaunix_lms|microwave_supervisor|pcl602' \
        || echo "No matching Genesis processes found."
}

port_open() {
    local host="$1"
    local port="$2"
    nc -z "${host}" "${port}" >/dev/null 2>&1
}

sim_check() {
    local missing=0
    echo "===== SSRF SIMULATOR BACKENDS ====="

    while read -r name port; do
        if port_open 127.0.0.1 "${port}"; then
            printf '[OK]   %-10s 127.0.0.1:%s listening\n' "${name}" "${port}"
        else
            printf '[MISS] %-10s 127.0.0.1:%s NOT listening\n' "${name}" "${port}"
            missing=1
        fi
    done <<'EOF'
SMC100A 5025
WaveGen 5027
NMR_SW 8081
ssRF_SW 8082
EOF

    echo
    if [ "${missing}" -eq 0 ]; then
        pass "all required ssRF simulator backends are available"
        return 0
    fi

    warn "One or more simulator backends are missing."
    warn "genesisctl will NOT invent/start an unknown backend command."
    warn "Use the repository's canonical backend launcher once it is documented."
    return 1
}

extract_addr_list() {
    local cfg="$1"

    if [ ! -f "${cfg}" ]; then
        return 1
    fi

    python - "${cfg}" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    import yaml
except Exception:
    raise SystemExit(2)

data = yaml.safe_load(path.read_text()) or {}
value = (data.get("general") or {}).get("epics_addr_list")
if value and str(value).lower() != "none":
    print(value)
PY
}

ssrf_sim_pv_status() {
    require_env

    local cfg="${ROOT}/configs/commissioning/ssrf_sim_active/ioc_manager/settings.yaml"
    local addr_list=""

    require_file "${cfg}"

    addr_list="$(extract_addr_list "${cfg}" 2>/dev/null || true)"

    if [ -z "${addr_list}" ]; then
        die "ssRF simulator manager has no usable epics_addr_list: ${cfg#${ROOT}/}"
    fi

    EPICS_CA_AUTO_ADDR_LIST=NO     EPICS_CA_ADDR_LIST="${addr_list}"     PYTHONPATH="${ROOT}"     python - <<'PY'
import asyncio

try:
    from aioca import caget
except Exception as exc:
    print(f"[WARN] aioca unavailable: {exc}")
    raise SystemExit(0)

PVS = [
    ("SMC COMM",        "UNH:GEN:SSRF:GEN:COMM"),
    ("SMC writes",      "UNH:GEN:SSRF:GEN:WRITE_ENABLED"),
    ("SMC RF",          "UNH:GEN:SSRF:GEN:RF_RBV"),
    ("SMC FM",          "UNH:GEN:SSRF:GEN:FM_RBV"),
    ("SMC sweep arm",   "UNH:GEN:SSRF:GEN:SWEEP_ARM_RBV"),

    ("WaveGen COMM",    "UNH:GEN:SSRF:WGEN:COMM"),
    ("WaveGen writes",  "UNH:GEN:SSRF:WGEN:WRITE_ENABLED"),
    ("WaveGen output",  "UNH:GEN:SSRF:WGEN:OUTPUT_RBV"),

    ("NMR SW COMM",     "UNH:GEN:SSRF:NMR_SW:COMM"),
    ("NMR SW writes",   "UNH:GEN:SSRF:NMR_SW:WRITE_ENABLED"),
    ("NMR mask",        "UNH:GEN:SSRF:NMR_SW:MASK_RBV"),

    ("ssRF SW COMM",    "UNH:GEN:SSRF:RF_SW:COMM"),
    ("ssRF SW writes",  "UNH:GEN:SSRF:RF_SW:WRITE_ENABLED"),
    ("ssRF mask",       "UNH:GEN:SSRF:RF_SW:MASK_RBV"),

    ("ILK inputs",      "UNH:GEN:SSRF:ILK:INPUTS_OK"),
    ("ILK OK",          "UNH:GEN:SSRF:ILK:INTERLOCK_OK"),
    ("ILK state",       "UNH:GEN:SSRF:ILK:STATE"),
    ("NMR permit",      "UNH:GEN:SSRF:ILK:NMR_CONNECT_PERMIT"),
    ("ssRF permit",     "UNH:GEN:SSRF:ILK:SSRF_CONNECT_PERMIT"),
    ("RF permit",       "UNH:GEN:SSRF:ILK:RF_ENABLE_PERMIT"),

    ("SEQ ready",       "UNH:GEN:SSRF:SEQ:READY"),
    ("SEQ state",       "UNH:GEN:SSRF:SEQ:STATE"),
    ("Exec enabled",    "UNH:GEN:SSRF:SEQ:EXECUTION_ENABLED"),
    ("Exec armed",      "UNH:GEN:SSRF:SEQ:EXECUTION_ARMED"),
    ("Exec state",      "UNH:GEN:SSRF:SEQ:EXECUTION_STATE"),
]


async def get(label, pv):
    try:
        value = await caget(pv, timeout=1.0)
        return label, value
    except Exception:
        return label, "<unavailable>"


async def main():
    print("===== EPICS / SSRF SIMULATOR STATUS (READ-ONLY) =====")
    values = await asyncio.gather(*(get(*item) for item in PVS))
    for label, value in values:
        print(f"{label:18s} = {value}")


asyncio.run(main())
PY
}


show_status() {
    banner
    show_where
    echo
    show_screens
    echo
    show_processes
    echo
    note "PV status is profile-specific."
    note "Use: genesis mw-status"
    note "Use: genesis ssrf-physical-status"
    note "Use: genesis sim-status"
}


show_logs() {
    local pattern="${1:-}"

    if [ ! -d "${ROOT}/logs" ]; then
        die "logs/ directory not found"
    fi

    if [ -z "${pattern}" ]; then
        echo "===== NEWEST LOG FILES ====="
        find "${ROOT}/logs" -type f -print0 2>/dev/null \
            | xargs -0 ls -lt 2>/dev/null \
            | head -20 \
            || true
        echo
        note "Use: genesis logs <name-pattern>"
        return 0
    fi

    echo "===== LOGS MATCHING: ${pattern} ====="
    local files
    files="$(find "${ROOT}/logs" -type f -iname "*${pattern}*" -print 2>/dev/null | tail -10)"

    if [ -z "${files}" ]; then
        warn "No matching log files."
        return 1
    fi

    echo "${files}"
    echo
    echo "===== TAIL ====="
    while IFS= read -r f; do
        echo
        echo "----- ${f#${ROOT}/} -----"
        tail -80 "${f}"
    done <<EOF
${files}
EOF
}

show_endpoints() {
    require_env

    cd "${ROOT}"

    PYTHONPATH="${ROOT}" python - <<'PY'
from pathlib import Path

try:
    import yaml
except Exception as exc:
    print(f"ERROR: PyYAML is required: {exc}")
    raise SystemExit(1)


PROFILES = [
    (
        "MICROWAVE PHYSICAL",
        Path("configs/commissioning/microwave_physical"),
    ),
    (
        "MICROWAVE SIMULATOR",
        Path("configs/commissioning/microwave_sim"),
    ),
    (
        "SSRF PHYSICAL",
        Path("configs/ssrf_physical"),
    ),
    (
        "SSRF SIMULATOR",
        Path("configs/commissioning/ssrf_sim_active"),
    ),
]


def meaningful(value):
    return value is not None and str(value).lower() != "none"


for profile_label, root in PROFILES:
    print()
    print("=" * 68)
    print(profile_label)
    print(f"profile = {root}")
    print("=" * 68)

    if not root.is_dir():
        print(f"<missing profile directory: {root}>")
        continue

    settings_files = sorted(root.glob("*/settings.yaml"))

    if not settings_files:
        print("<no settings.yaml files>")
        continue

    for settings_file in settings_files:
        data = yaml.safe_load(settings_file.read_text()) or {}
        general = data.get("general") or {}

        print()
        print(f"[{settings_file.parent.name}]")
        print(f"  config = {settings_file}")

        prefix = general.get("prefix")
        if meaningful(prefix):
            print(f"  prefix = {prefix}")

        addr_list = general.get("epics_addr_list")
        if meaningful(addr_list):
            print(f"  epics_addr_list = {addr_list}")

        sections = [
            (name, cfg)
            for name, cfg in data.items()
            if name != "general" and isinstance(cfg, dict)
        ]

        for section, cfg in sections:
            print(f"  section = {section}")

            if "ip" in cfg or "port" in cfg:
                ip = cfg.get("ip", "?")
                port = cfg.get("port", "?")
                print(f"    endpoint = {ip}:{port}")

            if meaningful(cfg.get("resource")):
                print(f"    resource = {cfg.get('resource')}")

            if meaningful(cfg.get("settings_folder")):
                print(f"    settings_folder = {cfg.get('settings_folder')}")

            for key in (
                "simulation",
                "write_enabled",
                "initialize_on_connect",
                "execution_enabled",
                "execution_mode",
                "autostart",
                "ca_server_port",
                "pva_server_port",
            ):
                if key in cfg:
                    print(f"    {key} = {cfg.get(key)}")
PY
}


physical_status() {
    echo
    echo "===== PHYSICAL SSRF READ-ONLY STATUS ====="
    echo
    echo "+ ./scripts/check_ssrf_physical_readonly.sh"
    echo

    "${ROOT}/scripts/check_ssrf_physical_readonly.sh"
}

refuse_legacy_generic() {
    local command="$1"

    echo "REFUSED: 'genesis ${command}' is a retired generic Phase-I command." >&2
    echo >&2
    echo "Use an explicit workflow:" >&2
    echo >&2
    echo "  Microwave physical:" >&2
    echo "    genesis mw-start" >&2
    echo "    genesis mw-status" >&2
    echo "    genesis mw-rf-ready" >&2
    echo "    genesis mw-stop" >&2
    echo >&2
    echo "  ssRF physical:" >&2
    echo "    genesis ssrf-physical-status" >&2
    echo "    genesis ssrf-start" >&2
    echo "    genesis ssrf-stop" >&2
    echo >&2
    echo "  ssRF simulator:" >&2
    echo "    genesis sim-check" >&2
    echo "    genesis sim-start" >&2
    echo "    genesis sim-status" >&2
    echo "    genesis sim-stop" >&2
    echo >&2
    echo "The older combined macOS launcher is not invoked by genesisctl." >&2

    return 2
}


sim_start() {
    require_env
    require_file "${SIM_SCRIPT}"

    sim_check || die "Simulator backends incomplete; activation refused."

    echo
    echo "===== ACTIVATE LOCALHOST SSRF SIMULATOR STACK ====="
    echo "+ ./scripts/start_ssrf_sim_active.sh --activate"
    echo
    warn "Simulator profile intentionally permits writes to localhost simulators."
    warn "Do not use this command for physical ssRF hardware."

    exec "${SIM_SCRIPT}" --activate
}

sim_stop() {
    require_env

    banner

    echo "===== SSRF SIMULATOR STOP ====="

    local screen_text

    screen_text="$(
        screen -ls 2>&1 || true
    )"

    # The dedicated simulator manager is the identity marker that
    # distinguishes the simulator IOC stack from normal Genesis.
    if ! grep -E \
        '\.ssrf-sim-manager[[:space:]]' \
        <<< "${screen_text}" \
        >/dev/null
    then
        if grep -E \
            '\.(ssrf_smc100a|ssrf_keysight_wavegen|ssrf_nmr_switch|ssrf_rf_switch|ssrf_interlock|ssrf_sequencer)[[:space:]]' \
            <<< "${screen_text}" \
            >/dev/null
        then
            warn \
                "ssRF IOC sessions exist without ssrf-sim-manager; refusing to guess."
            return 1
        fi

        pass \
            "no simulator IOC stack is active"

        note \
            "simulator backend processes are not managed by genesisctl"

        return 0
    fi

    local names=(
        ssrf_sequencer
        ssrf_interlock
        ssrf_rf_switch
        ssrf_nmr_switch
        ssrf_keysight_wavegen
        ssrf_smc100a
        ssrf-sim-manager
    )

    local name
    local ref

    for name in "${names[@]}"
    do
        while IFS= read -r ref
        do
            [ -n "${ref}" ] || continue

            echo "[STOP] ${ref}"

            screen \
                -S "${ref}" \
                -X quit \
                >/dev/null 2>&1 \
                || true

        done < <(
            (
                screen -ls \
                    2>/dev/null \
                    || true
            ) \
            | awk \
                -v n="${name}" \
                '$1 ~ ("\." n "$") {print $1}'
        )
    done

    sleep 2

    local remaining

    remaining="$(
        (
            screen -ls \
                2>/dev/null \
                || true
        ) \
        | awk '
            $1 ~ /^[0-9]+\.(ssrf_smc100a|ssrf_keysight_wavegen|ssrf_nmr_switch|ssrf_rf_switch|ssrf_interlock|ssrf_sequencer|ssrf-sim-manager)$/ {
                print $1
            }
        '
    )"

    if [ -n "${remaining}" ]; then
        warn \
            "simulator IOC sessions remain"

        echo "${remaining}"

        return 1
    fi

    pass \
        "simulator IOC stack stopped"

    note \
        "localhost hardware-simulator backend processes were left unchanged"

    note \
        "normal Genesis was not automatically restarted"
}

test_ssrf() {
    require_env
    echo "===== SSRF UNIT / REGRESSION TESTS ====="
    PYTHONPATH="${ROOT}" \
        python -m unittest discover \
        -s tests \
        -p 'test_ssrf*.py'
    echo
    git diff --check
}

test_auth() {
    require_env
    echo "===== AUTHORIZATION / ACCESS-SAFETY TESTS ====="

    local ran=0
    local f

    for f in \
        tests/test_execution_authorization.py \
        tests/test_access_security_config.py \
        tests/test_ssrf_access_security.py \
        tests/test_ssrf_physical_authorization.py
    do
        if [ -f "${f}" ]; then
            echo
            echo "+ python ${f}"
            PYTHONPATH="${ROOT}" python "${f}"
            ran=1
        fi
    done

    [ "${ran}" -eq 1 ] || die "No authorization test files found."
}

test_all() {
    require_env
    echo "===== ALL PYTHON TESTS ====="
    PYTHONPATH="${ROOT}" \
        python -m unittest discover \
        -s tests \
        -p 'test_*.py'
}

compile_check() {
    require_env
    echo "===== PYTHON COMPILE CHECK ====="

    local dirs=""
    for d in genesis_common logic_devices ssrf_ioc tests; do
        [ -d "${d}" ] && dirs="${dirs} ${d}"
    done

    # shellcheck disable=SC2086
    python -m compileall -q ${dirs}
    pass "compile"
}

check_all() {
    require_env
    echo "===== DIFF CHECK ====="
    git diff --check
    pass "git diff --check"
    echo
    compile_check
    echo
    git_state
}

mw_physical_start() {
    require_env
    require_file "${MICROWAVE_PHYSICAL_SCRIPT}"

    echo
    echo "===== START MICROWAVE PHYSICAL STACK ====="
    echo "+ ./scripts/microwave_physical.sh start"

    "${MICROWAVE_PHYSICAL_SCRIPT}" start
}

mw_physical_status() {
    require_env
    require_file "${MICROWAVE_PHYSICAL_SCRIPT}"

    echo
    echo "===== MICROWAVE PHYSICAL STATUS ====="
    echo "+ ./scripts/microwave_physical.sh status"

    "${MICROWAVE_PHYSICAL_SCRIPT}" status
}

mw_rf_ready() {
    require_env
    require_file "${MICROWAVE_PHYSICAL_SCRIPT}"

    echo
    echo "===== MICROWAVE RF READINESS ====="
    echo "+ ./scripts/microwave_physical.sh rf-ready"

    "${MICROWAVE_PHYSICAL_SCRIPT}" rf-ready
}

mw_physical_stop() {
    require_env
    require_file "${MICROWAVE_PHYSICAL_SCRIPT}"

    echo
    echo "===== STOP MICROWAVE PHYSICAL STACK ====="
    echo "+ ./scripts/microwave_physical.sh stop"

    "${MICROWAVE_PHYSICAL_SCRIPT}" stop
}


ssrf_physical_status() {
    require_env
    echo
    echo "===== SSRF PHYSICAL READ-ONLY STATUS ====="
    echo
    echo "+ ./scripts/check_ssrf_physical_readonly.sh"
    "${SSRF_PHYSICAL_STATUS_SCRIPT}"
}

ssrf_deploy_keysight() {
    require_env
    echo
    echo "===== DEPLOY FINAL SSRF KEYSIGHT RUNTIME ====="
    "${SSRF_KEYSIGHT_DEPLOY_SCRIPT}"
}

ssrf_final_start() {
    require_env
    echo
    echo "===== START FINAL PHYSICAL SSRF OPERATION ====="
    "${SSRF_FINAL_START_SCRIPT}"
}

ssrf_final_stop() {
    require_env
    echo
    echo "===== STOP FINAL PHYSICAL SSRF OPERATION ====="
    "${SSRF_FINAL_STOP_SCRIPT}"
}

test_mw() {
    require_env

    (
        set -e
        cd "${ROOT}"
        export PYTHONPATH="${ROOT}"

        echo "===== MICROWAVE REGRESSION ====="
        python -m unittest discover \
            -s tests \
            -p 'test_microwave*.py' \
            -v

        echo
        echo "===== MICROWAVE / COMMON ARCHIVE VIEWER ====="
        python -m unittest \
            tests.test_archive_viewer_filters \
            -v

        echo
        echo "===== SPD1305X BASELINE REGRESSION ====="
        python -m unittest discover \
            -s tests \
            -p 'test_spd1000x.py' \
            -v
    )
}


baseline() {
    banner
    echo "Frozen ssRF software/simulator milestone:"
    echo "  5B.6F = 3ef33de"
    echo

    if git merge-base --is-ancestor 3ef33de HEAD >/dev/null 2>&1; then
        pass "current branch contains 5B.6F"
    else
        warn "current branch does not contain 5B.6F"
        return 1
    fi

    git show -s --oneline 3ef33de 2>/dev/null || true
}

cmd="${1:-help}"
shift || true

case "${cmd}" in
    help|-h|--help)
        show_help
        ;;
    where)
        show_where
        ;;
    doctor)
        doctor
        ;;
    start|stop|force-stop)
        refuse_legacy_generic "${cmd}"
        ;;
    status)
        require_env
        show_status
        ;;
    screens)
        show_screens
        ;;
    processes)
        show_processes
        ;;
    logs)
        show_logs "${1:-}"
        ;;
    git)
        git_state
        ;;
    endpoints)
        show_endpoints
        ;;
    mw-start)
        mw_physical_start
        ;;
    mw-status)
        mw_physical_status
        ;;
    mw-rf-ready)
        mw_rf_ready
        ;;
    mw-stop)
        mw_physical_stop
        ;;

    ssrf-physical-status)
        ssrf_physical_status
        ;;
    ssrf-deploy-keysight)
        ssrf_deploy_keysight
        ;;
    ssrf-start)
        ssrf_final_start
        ;;
    ssrf-stop)
        ssrf_final_stop
        ;;

    sim-check)
        sim_check
        ;;
    physical-status)
        physical_status
        ;;

    sim-start)
        sim_start
        ;;
    sim-status)
        ssrf_sim_pv_status
        ;;
    sim-stop)
        sim_stop
        ;;
    archive-viewer)
        PORT="${GENESIS_ARCHIVE_VIEWER_PORT:-8501}"

        if ! python -c \
            'import pandas, plotly, streamlit' \
            >/dev/null 2>&1
        then
            echo "ERROR: archive-viewer Python dependencies are missing."
            echo "Install requirements in the active Genesis environment."
            false
        fi

        echo "Starting Genesis Archive Viewer"
        echo "URL: http://127.0.0.1:${PORT}"
        echo

        (
            cd "$ROOT/archive"

            PYTHONPATH="$ROOT" \
            python -m streamlit run \
                archive_viewer.py \
                --server.port "$PORT" \
                --server.address 127.0.0.1 \
                --server.headless true \
                --browser.gatherUsageStats false
        )
        ;;

    test-fitter)
        FITTER_ROOT="$(
            cd "$(dirname "${BASH_SOURCE[0]}")/.." &&
            pwd
        )"

        (
            set -e
            cd "$FITTER_ROOT"
            export PYTHONPATH="$FITTER_ROOT"

            echo "===== FITTER RUNTIME ====="
            python tests/test_fitter_stage_runtime.py

            echo
            echo "===== FITTER BRIDGE ====="
            python tests/test_fitter_waveform_bridge.py

            echo
            echo "===== WAVEFORM PLANNER ====="
            python tests/test_ssrf_waveform_planner.py

            echo
            echo "===== SAFE WAVEFORM STAGER ====="
            python tests/test_ssrf_waveform_stager.py
        )
        ;;

    test-ssrf)
        test_ssrf
        ;;

    test-mw)
        test_mw
        ;;
    test-auth)
        test_auth
        ;;
    test-all)
        test_all
        ;;
    compile)
        compile_check
        ;;
    check)
        check_all
        ;;
    baseline)
        baseline
        ;;
    *)
        echo "Unknown command: ${cmd}" >&2
        echo >&2
        show_help >&2
        exit 2
        ;;
esac
