#!/usr/bin/env bash
# One-time setup of a laptop as the Genesis lab machine (WSL Ubuntu).
#
#   bash setup.sh            # from a copy of this file, or:
#   git clone -b dev-visualizer https://github.com/ellie-long/UNH-Genesis-Control.git ~/genesis-live \
#     && ~/genesis-live/scripts/live_laptop/setup.sh
#
# Safe to re-run: every step checks first and skips what is already done.
# Never stores a password: SSH keys are installed with the Pi's own password
# prompt, typed by you.
set -uo pipefail

REPO_URL="${GENESIS_REPO_URL:-https://github.com/ellie-long/UNH-Genesis-Control.git}"
BRANCH="${GENESIS_BRANCH:-dev-visualizer}"
ROOT="${GENESIS_ROOT:-$HOME/genesis-live}"
VENV="${GENESIS_VENV:-$HOME/.venvs/genesis-p1}"
PHOEBUS_VERSION="${PHOEBUS_VERSION:-5.0.5}"
PHOEBUS_DIR="$HOME/phoebus/product-$PHOEBUS_VERSION"
PHOEBUS_URL="${PHOEBUS_URL:-https://github.com/ControlSystemStudio/phoebus/releases/download/v$PHOEBUS_VERSION/phoebus-$PHOEBUS_VERSION-linux.tar.gz}"
ENV_FILE="$HOME/.genesis-live.env"
CRYO_PI=192.168.1.24
MW_PI=192.168.1.23

step() { printf '\n===== %s =====\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
todo() { printf '[TODO] %s\n' "$*"; }

step "1. Ubuntu packages (asks for your WSL password once)"
need=()
for pkg in git screen curl lsof openjdk-21-jre libgtk-3-0 python3-venv; do
    dpkg -s "$pkg" >/dev/null 2>&1 || need+=("$pkg")
done
if ((${#need[@]})); then
    sudo apt-get update && sudo apt-get install -y "${need[@]}"
else
    ok "all present"
fi

step "2. Genesis repository: $ROOT ($BRANCH)"
if [[ -d "$ROOT/.git" ]]; then
    git -C "$ROOT" fetch -q origin && git -C "$ROOT" checkout -q "$BRANCH" && git -C "$ROOT" pull -q --ff-only \
        && ok "updated to $(git -C "$ROOT" log --oneline -1)"
else
    # a Linux-native clone (LF line endings): Utsav's .sh scripts do not run
    # from a Windows checkout
    git -c core.autocrlf=false clone -q -b "$BRANCH" "$REPO_URL" "$ROOT" && ok "cloned $(git -C "$ROOT" log --oneline -1)"
fi

step "3. Python environment: $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then
    if command -v uv >/dev/null; then
        uv venv --python 3.13 "$VENV"
    else
        python3 -m venv "$VENV"
    fi
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -r "$ROOT/requirements.txt" && ok "requirements installed ($("$VENV/bin/python" --version))"

step "4. Phoebus $PHOEBUS_VERSION: $PHOEBUS_DIR"
if [[ -f "$PHOEBUS_DIR/product-$PHOEBUS_VERSION.jar" ]]; then
    ok "present"
else
    mkdir -p "$HOME/phoebus/download"
    tarball="$HOME/phoebus/download/phoebus-$PHOEBUS_VERSION-linux.tar.gz"
    if curl -fL -o "$tarball" "$PHOEBUS_URL"; then
        tar -xzf "$tarball" -C "$HOME/phoebus" && ok "installed"
    else
        todo "download failed: put phoebus-$PHOEBUS_VERSION-linux.tar.gz in ~/phoebus/download and re-run"
    fi
fi

step "5. SSH keys for the two Raspberry Pis"
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
[[ -f "$HOME/.ssh/genesis_cryo_pi_ed25519" ]] || ssh-keygen -q -t ed25519 -N "" -C "genesis-cryo-$(hostname)" -f "$HOME/.ssh/genesis_cryo_pi_ed25519"
[[ -f "$HOME/.ssh/genesis_mw_pi_ed25519" ]] || ssh-keygen -q -t ed25519 -N "" -C "genesis-mw-$(hostname)" -f "$HOME/.ssh/genesis_mw_pi_ed25519"
if ! grep -q "^Host $MW_PI$" "$HOME/.ssh/config" 2>/dev/null; then
    printf '\nHost %s\n\tUser unhpoltarg\n\tIdentityFile ~/.ssh/genesis_mw_pi_ed25519\n\tIdentitiesOnly yes\n' "$MW_PI" >> "$HOME/.ssh/config"
fi
if ssh -o BatchMode=yes -o ConnectTimeout=6 -i "$HOME/.ssh/genesis_cryo_pi_ed25519" -o IdentitiesOnly=yes \
        "unhpoltarg@$CRYO_PI" status >/dev/null 2>&1; then
    ok "cryo Pi key works"
else
    echo "Installing the cryo Pi key (restricted to the bridge-control command). Type the Pi's password when asked:"
    printf 'restrict,command="/home/unhpoltarg/genesis_cryo_bridges/ssh_control.sh" %s\n' \
        "$(cat "$HOME/.ssh/genesis_cryo_pi_ed25519.pub")" |
        ssh -o PubkeyAuthentication=no "unhpoltarg@$CRYO_PI" 'cat >> ~/.ssh/authorized_keys' && ok "cryo Pi key installed"
fi
if ssh -o BatchMode=yes -o ConnectTimeout=6 "unhpoltarg@$MW_PI" true >/dev/null 2>&1; then
    ok "microwave Pi key works"
else
    echo "Installing the microwave Pi key. Type the Pi's password when asked:"
    ssh-copy-id -i "$HOME/.ssh/genesis_mw_pi_ed25519.pub" "unhpoltarg@$MW_PI" && ok "microwave Pi key installed"
fi

step "6. Laptop settings file: $ENV_FILE"
if [[ ! -f "$ENV_FILE" ]]; then
    win_user="$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')"
    cat > "$ENV_FILE" <<EOF
# Genesis live-laptop settings (read by start.sh and stop.sh)
WIN_USER=$win_user
# PyNMR (Windows): the jlab_pynmr checkout on its 'unh' branch, and conda
PYNMR_WIN_DIR='C:\\Users\\$win_user\\OneDrive\\Documents\\GitHub\\jlab_pynmr-unh'
CONDA_WIN_EXE='C:\\Users\\$win_user\\anaconda3\\Scripts\\conda.exe'
PYNMR_CONDA_ENV=pynmr
PYNMR_PROFILE=Deuteron
# where stop.sh copies the night's data (a copy; WSL keeps the originals)
DATA_COPY_DIR=/mnt/c/Users/$win_user
EOF
    ok "written: check the PyNMR paths in it"
else
    ok "present"
fi

step "7. Things only you can do (Windows side)"
todo "NMR network: the Wi-Fi adapter needs a second address 192.168.0.x with mask 255.255.255.0 (/24, NOT /22),"
todo "  no gateway, SkipAsSource (admin PowerShell; see scripts/live_laptop/README.md)"
todo "PyNMR: a Windows conda env '${PYNMR_CONDA_ENV:-pynmr}' with jlab_pynmr (unh branch); its unh/data_streamer.yaml"
todo "  needs 'enable: True' to feed the Genesis fitter"
todo "Stop Genesis / LabVIEW on any other computer that owns the instruments before starting here"
echo
echo "Setup done. Start everything with:  $ROOT/scripts/live_laptop/start.sh"
