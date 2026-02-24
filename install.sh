#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# G1 Audio Driver — Install / Uninstall
#
# Usage:
#   ./install.sh           Install and enable (auto-start on login)
#   ./install.sh --start   Install, enable, and start immediately
#   ./install.sh --remove  Stop, disable, and remove the service
#
# Environment variables (optional):
#   UNITREE_SDK_PATH    Path to unitree_sdk2_python repo (default: auto-detect)
#   G1_DDS_INTERFACE    Network interface for DDS (default: auto-detect from 192.168.123.0/24)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="g1-audio-driver.service"
SERVICE_SRC="$SCRIPT_DIR/$SERVICE_NAME"
SERVICE_DST="$HOME/.config/systemd/user/$SERVICE_NAME"

# ─── Auto-detect DDS interface ───────────────────────────────────────────────
detect_dds_interface() {
    # Find the interface that has a 192.168.123.x address
    local iface
    iface=$(ip -o -4 addr show | grep '192\.168\.123\.' | awk '{print $2}' | head -1)
    if [[ -n "$iface" ]]; then
        echo "$iface"
    else
        echo "eth0"
    fi
}

# ─── Auto-detect SDK path ───────────────────────────────────────────────────
detect_sdk_path() {
    # Check common locations
    local candidates=(
        "$SCRIPT_DIR/../repos/unitree-sdk2-python"
        "$HOME/unitree-sdk2-python"
        "$HOME/unitree_sdk2-main"
    )
    for path in "${candidates[@]}"; do
        if [[ -d "$path/unitree_sdk2py" ]]; then
            echo "$(cd "$path" && pwd)"
            return
        fi
    done
    echo "$HOME/unitree-sdk2-python"
}

# ─── Uninstall ────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--remove" ]]; then
    echo "Stopping and removing $SERVICE_NAME..."
    systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl --user disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "$SERVICE_DST"
    systemctl --user daemon-reload
    rm -f /tmp/g1_mic.pipe /tmp/g1_spk.pipe
    echo "✅ Removed"
    exit 0
fi

# ─── Resolve configuration ──────────────────────────────────────────────────
DDS_INTERFACE="${G1_DDS_INTERFACE:-$(detect_dds_interface)}"
SDK_PATH="${UNITREE_SDK_PATH:-$(detect_sdk_path)}"

# ─── Pre-flight checks ───────────────────────────────────────────────────────
echo "Checking prerequisites..."

if ! command -v pactl &>/dev/null; then
    echo "❌ pactl not found — is PulseAudio installed?"
    exit 1
fi

if ! pactl info &>/dev/null; then
    echo "❌ PulseAudio is not running"
    exit 1
fi

if ! python3 -c "
import sys
sys.path.insert(0, '$SDK_PATH')
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
" 2>/dev/null; then
    echo "❌ unitree_sdk2py not found at: $SDK_PATH"
    echo "   Set UNITREE_SDK_PATH or install at ~/unitree-sdk2-python"
    exit 1
fi

# Check PA modules exist
for mod in module-pipe-source module-pipe-sink; do
    if ! find /usr/lib/pulse-*/modules/ -name "${mod}.so" 2>/dev/null | grep -q .; then
        echo "❌ $mod not found — install pulseaudio-module-pipe or equivalent"
        exit 1
    fi
done

echo "  ✅ PulseAudio running"
echo "  ✅ unitree_sdk2py available ($SDK_PATH)"
echo "  ✅ pipe modules available"
echo "  ✅ DDS interface: $DDS_INTERFACE"

# ─── Install ──────────────────────────────────────────────────────────────────
# Stop existing instance if running
systemctl --user stop "$SERVICE_NAME" 2>/dev/null || true

mkdir -p "$(dirname "$SERVICE_DST")"

# Template the service file with detected paths
sed \
    -e "s|__INSTALL_DIR__|$SCRIPT_DIR|g" \
    -e "s|__UNITREE_SDK_PATH__|$SDK_PATH|g" \
    -e "s|__DDS_INTERFACE__|$DDS_INTERFACE|g" \
    "$SERVICE_SRC" > "$SERVICE_DST"
echo "Installed → $SERVICE_DST"

systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME"
echo "✅ Enabled (will auto-start on login)"

if [[ "${1:-}" == "--start" ]]; then
    systemctl --user start "$SERVICE_NAME"
    sleep 3
    if systemctl --user is-active --quiet "$SERVICE_NAME"; then
        echo "✅ Running"
        echo ""
        echo "Devices available:"
        pactl list sources short 2>/dev/null | grep g1_ || true
        pactl list sinks short 2>/dev/null | grep g1_ || true
    else
        echo "❌ Failed to start. Check logs:"
        echo "  journalctl --user -u g1-audio-driver -n 20"
        exit 1
    fi
fi

echo ""
echo "Commands:"
echo "  systemctl --user start g1-audio-driver    # start"
echo "  systemctl --user stop g1-audio-driver     # stop"
echo "  systemctl --user restart g1-audio-driver   # restart"
echo "  journalctl --user -u g1-audio-driver -f   # live logs"
echo "  ./install.sh --remove                     # uninstall"
