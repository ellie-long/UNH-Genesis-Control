@echo off
rem Stop the Genesis live system in WSL and copy the data to Windows.
rem Pass --cryo-readonly-kill if the cryo stop refuses (cold-board output ON).
wsl.exe -e bash -lc "~/genesis-live/scripts/live_laptop/stop.sh %*; echo; read -r -p 'Press Enter to close this window'"
