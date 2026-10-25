# Genesis on a laptop as the lab machine (Windows + WSL)

What ran on 2026-10-23/24: the cryo stack, the magnet supply, the
microwaves, the NMR (PyNMR plus the Genesis online fitter), the trend
history, the master archiver and Phoebus with the Panopticon, all on one
laptop on the lab network.

## Once per computer

In a WSL (Ubuntu) terminal:

```bash
git clone -b dev-visualizer https://github.com/ellie-long/UNH-Genesis-Control.git ~/genesis-live \
  && ~/genesis-live/scripts/live_laptop/setup.sh
```

`setup.sh` installs the Ubuntu packages, the Python environment
(`~/.venvs/genesis-p1`) and Phoebus 5.0.5. It creates the two Raspberry Pi
SSH keys and installs them (you type each Pi's password). It also writes
`~/.genesis-live.env` with the PyNMR paths. It's safe to re-run.

On the Windows side, by hand:

- **NMR network.** The JLab NMR FPGA (192.168.0.72) and its SMA100A
  (192.168.0.50) are on 192.168.0.x on the same Wi-Fi. Give the Wi-Fi
  adapter a second address with a **/24** mask, no gateway, and
  SkipAsSource. A /22 overlaps 192.168.1.x and breaks WSL's connections to
  the instruments. In an admin PowerShell:
  ```powershell
  New-NetIPAddress -InterfaceAlias "Wi-Fi" -IPAddress 192.168.0.232 -PrefixLength 24 -SkipAsSource $true
  ```
  (The adapter must allow a static address alongside DHCP. Undo with
  `Remove-NetIPAddress -IPAddress 192.168.0.232 -Confirm:$false`.)
- **PyNMR.** A conda env `pynmr` with jlab_pynmr on its `unh` branch. Set
  `enable: True` in its `unh/data_streamer.yaml` to feed the Genesis fitter.
- **Other computers.** Stop Genesis and LabVIEW on any other computer that
  owns the instruments. Most serial-gateway ports allow one connection.

## Every session

```bash
~/genesis-live/scripts/live_laptop/start.sh     # or double-click genesis_live_start.bat
~/genesis-live/scripts/live_laptop/stop.sh      # or genesis_live_stop.bat
```

`start.sh` skips whatever is already running. It asks you two things:

1. Whether to enable magnet control from Phoebus.
2. When to start the microwaves. Plug the Vaunix USB in first. Utsav's start
   then sets the UCA and asks **you** to type READY.

`stop.sh` stops everything and copies the data to
`C:\Users\<you>\Genesis_live_<date>` (a copy; WSL keeps the originals). It
leaves PyNMR for you to close.

## Ports

| Service | CA port |
|---|---|
| Cryo IOCs | 5111-5129 |
| Trend history | 5130 |
| Magnet IOC | 5131 |
| Online fitter | 5100 (sweeps on 8777) |
| Microwave | 5094 (manager), 5066, 5069, 5085, 5087, 5095 |

## Known quirks

- Restarting an IOC that Phoebus is watching can leave Phoebus with dead
  connections (blank plots). Reopen Phoebus.
- **LS455.** It reports in tesla, so `start.sh` sets its `tesla_scale` to
  1.0 in this copy until the driver reads the unit itself.
- **UCA supply.** On 2026-10-23 its socket port (5025) refused connections
  for hours while VXI-11 worked, then recovered; see question 98.
