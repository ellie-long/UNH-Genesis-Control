@echo off
rem Start the Genesis live system in WSL (see scripts\live_laptop\README.md).
rem Double-click, or run from a Windows terminal.  The window stays open for
rem the prompts (magnet writes, microwave READY) and the final summary.
wsl.exe -e bash -lc "~/genesis-live/scripts/live_laptop/start.sh %*; echo; read -r -p 'Press Enter to close this window'"
