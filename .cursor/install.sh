#!/usr/bin/env bash
# Idempotent development-environment bootstrap for Matreshka Remap Renderer.
#
# The application is a PySide6 (Qt) desktop tool. It needs:
#   - a Python virtual environment with PySide6, numpy, wgpu and av
#   - ffmpeg on the machine (the render pipeline's decoder/encoder)
#   - the Qt/OpenGL/X11 runtime libraries Qt loads at import time, so that the
#     window opens under X and the offscreen test suite can even import PySide6
#
# Everything here converges to the same state on a fresh or a re-run machine.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL_DIR="$REPO_DIR/tool"

# sudo only when we are not already root (cloud build user has passwordless sudo).
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

echo "==> Installing system packages (Qt runtime, ffmpeg, venv, virtual display)"
export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
$SUDO apt-get install -y --no-install-recommends \
  python3.12-venv python3-dev build-essential \
  ffmpeg xvfb x11-utils \
  libegl1 libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1 libfreetype6 \
  libxkbcommon0 libxkbcommon-x11-0 \
  libx11-6 libx11-xcb1 libxext6 libxrender1 libxcb1 libxcb-cursor0 \
  libxcb-glx0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 libxcb-sync1 \
  libxcb-util1 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1

echo "==> Creating the Python virtual environment at tool/.venv"
if [ ! -x "$TOOL_DIR/.venv/bin/python" ]; then
  python3 -m venv "$TOOL_DIR/.venv"
fi

echo "==> Installing Python dependencies from tool/requirements.txt"
"$TOOL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$TOOL_DIR/.venv/bin/python" -m pip install -r "$TOOL_DIR/requirements.txt"

echo "==> Done. Run the checks with:"
echo "    cd tool && .venv/bin/python tests/run.py"
echo "==> Run the app (needs a display) with:"
echo "    cd tool && .venv/bin/python main.py"
