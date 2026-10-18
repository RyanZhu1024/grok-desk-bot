#!/usr/bin/env bash
# Start the assistant once a display exists.
# Wayland and X11 are preferred so the Pi desktop keeps the screen.
# If neither shows up, draw straight to the console with kmsdrm.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

found=""
for _ in $(seq 1 60); do
  if [[ -S "${XDG_RUNTIME_DIR}/wayland-0" ]]; then
    found="wayland"
    break
  fi
  if [[ -S /tmp/.X11-unix/X0 ]]; then
    found="x11"
    break
  fi
  sleep 1
done

if [[ "${found}" == "wayland" ]]; then
  export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-wayland}"
elif [[ "${found}" == "x11" ]]; then
  export DISPLAY="${DISPLAY:-:0}"
  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-x11}"
  if [[ -z "${XAUTHORITY:-}" && -f "${HOME}/.Xauthority" ]]; then
    export XAUTHORITY="${HOME}/.Xauthority"
  fi
else
  export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-kmsdrm}"
fi

exec "${ROOT}/.venv/bin/desk-assistant"
