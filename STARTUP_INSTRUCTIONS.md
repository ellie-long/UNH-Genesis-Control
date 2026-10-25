# Starting the whole Genesis system by hand

These are the steps that brought up the full live system on a Windows laptop
(with WSL) on 2026-10-23 and 2026-10-24: cryo, the magnet supply,
microwaves, NMR (PyNMR plus the online fitter), the trend history, the
master archiver and Phoebus with the Panopticon.

`scripts/live_laptop/start.sh` does the same thing in one command (see
`scripts/live_laptop/README.md`). Use this file when that script doesn't
work, or to see what it does.

Run every `bash` command in a **WSL (Ubuntu) terminal** unless it says
Windows.

## 0. Before you start

- **Only one computer may own the instruments.** Stop Genesis and LabVIEW on
  any other computer first. The serial servers (192.168.1.250, .251) and the
  Siglent supplies accept one connection per port. A port held elsewhere
  shows up as a connection that times out or is refused.
- **Network.** The laptop is on the lab Wi-Fi as 192.168.1.x. For the NMR it
  also needs a second address on 192.168.0.x with a **/24** mask (not /22:
  that overlaps 192.168.1.x and breaks WSL's connections to the
  instruments). In an **admin Windows PowerShell**:
  ```powershell
  New-NetIPAddress -InterfaceAlias "Wi-Fi" -IPAddress 192.168.0.232 -PrefixLength 24 -SkipAsSource $true
  ```
- **Check that the instruments answer** (WSL):
  ```bash
  for h in 192.168.1.12 192.168.1.250 192.168.1.251 192.168.1.19 192.168.1.23 192.168.1.24 192.168.0.72; do
    ping -c1 -W2 $h >/dev/null && echo "$h ok" || echo "$h NO REPLY"; done
  ```

## 1. One-time setup (skip any step already done)

```bash
sudo apt-get update && sudo apt-get install -y git screen curl lsof openjdk-21-jre libgtk-3-0 python3-venv
# a Linux-native clone: Utsav's .sh scripts fail from a Windows checkout (CRLF)
git -c core.autocrlf=false clone -b dev-visualizer https://github.com/ellie-long/UNH-Genesis-Control.git ~/genesis-live
python3 -m venv ~/.venvs/genesis-p1              # Python 3.11+ (3.13 used here)
~/.venvs/genesis-p1/bin/pip install -r ~/genesis-live/requirements.txt
# Phoebus 5.0.5 for Linux, unpacked to ~/phoebus/product-5.0.5 (needs Java 21)
```

**Raspberry Pi SSH keys.** You'll type each Pi's password; never store it.
- **Cryo Pi (192.168.1.24, hostname `keysight`).** It runs the right-Roots
  flow and Manometer-Lo bridges. The key is restricted to the
  bridge-control command, like the lab machines' keys:
  ```bash
  ssh-keygen -t ed25519 -N "" -f ~/.ssh/genesis_cryo_pi_ed25519
  printf 'restrict,command="/home/unhpoltarg/genesis_cryo_bridges/ssh_control.sh" %s\n' \
    "$(cat ~/.ssh/genesis_cryo_pi_ed25519.pub)" | ssh unhpoltarg@192.168.1.24 'cat >> ~/.ssh/authorized_keys'
  ```
- **Microwave Pi (192.168.1.23, hostname `mmWaveRobot`).** It runs the Vaunix
  and robot shims. The key must have **no passphrase**, because the
  microwave script logs in non-interactively:
  ```bash
  ssh-keygen -t ed25519 -N "" -f ~/.ssh/genesis_mw_pi_ed25519
  printf '\nHost 192.168.1.23\n\tUser unhpoltarg\n\tIdentityFile ~/.ssh/genesis_mw_pi_ed25519\n\tIdentitiesOnly yes\n' >> ~/.ssh/config
  ssh-copy-id -i ~/.ssh/genesis_mw_pi_ed25519.pub unhpoltarg@192.168.1.23
  ```

**PyNMR (Windows).** A conda env `pynmr` with the jlab_pynmr checkout on its
`unh` branch. To feed the Genesis fitter, set `enable: True` in its
`unh/data_streamer.yaml` (host 127.0.0.1, port 8777).

## 2. Every session: set up the terminal

```bash
cd ~/genesis-live && git pull --ff-only
export PATH=$HOME/.venvs/genesis-p1/bin:/usr/local/bin:/usr/bin:/bin
export CONDA_DEFAULT_ENV=genesis-p1 CONDA_PREFIX=$HOME/.venvs/genesis-p1   # Utsav's scripts check these
mkdir -p logs
```

**Laptop-only settings** in this copy (don't commit them):
- **LS455:** it reports in tesla, so its scale must be 1.0. Otherwise the
  fringe field reads 10,000x too small.
  ```bash
  sed -i 's/^  tesla_scale: 0.0001$/  tesla_scale: 1.0/' configs/target_physical/ls455/settings.yaml
  ```
- **Magnet supply control from Phoebus** (only if you want it; the default
  is read-only):
  ```bash
  sed -i 's/^  write_enabled: false$/  write_enabled: true/' configs/magnet_physical/ips120/settings.yaml
  ```

## 3. Start everything, in this order

**1. Cryo stack** (19 read-only IOCs, the Pi bridges, the cryo archiver):
```bash
./scripts/cryo_physical.sh start      # ends with "CRYO STACK READY"
```

**2. Trend history** (fills the Panopticon's plots; CA 5130):
```bash
screen -dmS genesis-hist env EPICS_CAS_SERVER_PORT=5130 EPICS_PVAS_SERVER_PORT=5230 \
  bash -c "python -u master_ioc.py -s configs/target_physical/trend_history -i trend_history >> logs/trend_history_live.log 2>&1"
```

**3. Magnet supply IOC** (IPS120-10; CA 5131):
```bash
screen -dmS genesis-magnet env EPICS_CAS_SERVER_PORT=5131 EPICS_PVAS_SERVER_PORT=5231 \
  bash -c "python -u master_ioc.py -s configs/magnet_physical/ips120 -i ips120 >> logs/magnet_ips120_live.log 2>&1"
```

**4. Online NMR fitter** (CA 5100; receives PyNMR's sweeps on 8777):
```bash
screen -dmS genesis-fitter env EPICS_CAS_SERVER_PORT=5100 EPICS_PVA_SERVER_PORT=5101 EPICS_CA_MAX_ARRAY_BYTES=262144 \
  EPICS_CA_AUTO_ADDR_LIST=NO EPICS_CA_ADDR_LIST=127.0.0.1:5071 PYTHONPATH=$HOME/genesis-live \
  bash -c "python -u -m online_fitter.epics_run --material d-butanol --mode equilibrium >> logs/genesis-fitter-live.log 2>&1"
```

**5. Master archiver** (read-only; records every subsystem's PVs to
`~/genesis-live-local_data/archive/master`):
```bash
python -m archive.master_service start && python -m archive.master_service status
```

**6. PyNMR** (Windows PowerShell, in the jlab_pynmr `unh` checkout):
```powershell
conda run -n pynmr --no-capture-output python pynmr_main.py -p Deuteron
```

**7. Phoebus with the Panopticon.** Run it in a screen session so it
survives closing the terminal:
```bash
screen -dmS genesis-phoebus bash -c "cd ~/genesis-live && java -jar ~/phoebus/product-5.0.5/product-5.0.5.jar \
  -settings configs/phoebus/panopticon_live.ini -resource file://$HOME/genesis-live/gui/genesis_panopticon.bob"
```

**8. Microwaves.** Last, and **you** answer its prompt. Plug the Vaunix USB
into the microwave Pi first. The start checks the Vaunix shim, then sets the
UCA to 5.0 V / 0.05 A / OVP 5.1 V / OCP 0.11 A. Then it asks you to type
**READY** once the VDI-175T green light is on, the UCA is at 5 V, and the
USB was connected after both.
```bash
./scripts/microwave_physical.sh start
```

## 4. Check that it's all up

```bash
screen -ls                                   # genesis-cryo-* (19), genesis-hist, -magnet, -fitter, -phoebus,
                                             # ioc-manager and mw_* (microwaves)
./scripts/cryo_physical.sh status
./scripts/microwave_physical.sh status
python -m archive.master_service status      # written_events rising, dropped_events 0
```

On the Panopticon:
- the trend plots fill;
- the magnet card shows the current and field;
- the NMR plot updates on each PyNMR event, about every 2 s.

## 5. Stop everything

1. Close PyNMR yourself. Stop its run first: it asks "Exit While Running?",
   which only a person should answer.
2. Stop the rest:
   ```bash
   ./scripts/microwave_physical.sh stop
   screen -S genesis-phoebus -X quit; screen -S genesis-fitter -X quit
   screen -S genesis-magnet -X quit;  screen -S genesis-hist -X quit
   python -m archive.master_service stop
   ./scripts/cryo_physical.sh stop
   ```
   - **If the cryo stop refuses with "Cold-board output is ON",** that's
     Utsav's guard. Genesis never writes to the cold board, so its read-only
     IOCs can be stopped directly with `screen -S <session> -X quit` (cold
     board first); the cold-board output stays as it is.
   - **Check for leftover IOC processes after quitting a screen session.**
     The IOC under it can keep running. Look with
     `pgrep -af "master_ioc|online_fitter"` and stop leftovers with `kill`.
3. Copy (never move) the data to Windows:
   ```bash
   d=/mnt/c/Users/<your Windows user name>/Genesis_live_$(date +%F); mkdir -p "$d"
   cp -a ~/genesis-live-local_data ~/genesis-live/logs "$d"/
   ```

## 6. When something doesn't connect

- **Magnet IOC or LHe meter stays disconnected, and port 5001 or 23 times
  out.** Something else holds that serial-server port. Stop the other
  program. If nothing else is connected, power-cycle the TXI017 serial
  server. The magnet IOC reconnects by itself.
- **The UCA IOC refuses the supply** ("device voltage must be in [0, 5]").
  The supply's setpoint is above 5.0 V (it was 5.1 V once). Set it to
  5.000 V on the panel, with the output off, before the microwave start.
- **UCA port 5025 resets every connection while VXI-11 works.** Seen on
  2026-10-23; it cleared by the next day. See question 98 in
  `docs/COOLDOWN_SIM_OPEN_QUESTIONS.md`.
- **The microwave start stops at "Vaunix Pi service/version verification
  failed".** The Vaunix USB isn't plugged in, or SSH to 192.168.1.23 failed.
  Check with `ssh unhpoltarg@192.168.1.23 systemctl --user is-active vaunix-shim.service`.
- **Phoebus plots go blank after an IOC is restarted.** Close Phoebus and
  start it again (step 7). Reload Display isn't enough.
- **Right-Roots flow or Manometer-Lo blank.** Check the cryo Pi bridges:
  `./scripts/pi_cryo_bridges/ubuntu_control.sh status`, then `start`.
- **NMR FPGA (192.168.0.72) refuses connections.** Another computer is
  running PyNMR. Close it there.
