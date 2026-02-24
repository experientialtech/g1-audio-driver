#!/bin/bash
# ──────────────────────────────────────────────────────────────────────────────
# Combined Speakers Manager
#
# Rebuilds PulseAudio combined sink from all available speakers:
#   - Any USB audio output sinks
#   - G1 onboard head speaker (g1_speaker)
#
# Safe to call repeatedly — tears down old combined sink before rebuilding.
# Typically triggered by udev on USB audio plug/unplug and at login.
#
# Usage:
#   ./combined-speakers.sh          Rebuild combined sink
#   ./combined-speakers.sh --remove Remove combined sink
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

COMBINED_NAME="combined_speakers"
COMBINED_DESC="All Speakers"
LOG_TAG="combined-speakers"

log() { echo "[$LOG_TAG] $*"; }

# Wait for PulseAudio (may not be ready at login or after USB event)
wait_for_pa() {
    local tries=0
    while ! pactl info &>/dev/null; do
        tries=$((tries + 1))
        if [[ $tries -ge 20 ]]; then
            log "ERROR: PulseAudio not available after 10s"
            exit 1
        fi
        sleep 0.5
    done
}

# Unload any existing combined sink module
unload_combined() {
    local mod_id
    mod_id=$(pactl list modules short 2>/dev/null | grep "module-combine-sink.*$COMBINED_NAME" | awk '{print $1}' || true)
    if [[ -n "$mod_id" ]]; then
        pactl unload-module "$mod_id" 2>/dev/null || true
        log "Unloaded old combined sink (module $mod_id)"
    fi
}

# ─── Remove mode ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--remove" ]]; then
    wait_for_pa
    unload_combined
    log "Removed"
    exit 0
fi

# ─── Rebuild ──────────────────────────────────────────────────────────────────
wait_for_pa

# Small delay to let PulseAudio finish registering new USB sinks
sleep 1

# Collect available speaker sinks
slaves=()

# USB audio sinks
while IFS= read -r sink; do
    [[ -n "$sink" ]] && slaves+=("$sink")
done < <(pactl list sinks short 2>/dev/null | grep -i "usb.*audio" | awk '{print $2}' || true)

# G1 onboard speaker
if pactl list sinks short 2>/dev/null | grep -q "^[0-9].*g1_speaker[[:space:]]"; then
    slaves+=("g1_speaker")
fi

if [[ ${#slaves[@]} -eq 0 ]]; then
    log "No speakers found — removing combined sink if present"
    unload_combined
    exit 0
fi

if [[ ${#slaves[@]} -eq 1 ]]; then
    log "Only one speaker found (${slaves[0]}) — setting as default, no combine needed"
    unload_combined
    pactl set-default-sink "${slaves[0]}" 2>/dev/null || true
    exit 0
fi

# Build comma-separated slave list
slave_list=$(IFS=,; echo "${slaves[*]}")

log "Found ${#slaves[@]} speakers: ${slaves[*]}"

# Tear down old combined sink
unload_combined

# Create new combined sink
if pactl load-module module-combine-sink \
    sink_name="$COMBINED_NAME" \
    sink_properties=device.description=All_Speakers \
    slaves="$slave_list" >/dev/null 2>&1; then
    pactl set-default-sink "$COMBINED_NAME"
    log "Combined sink active with ${#slaves[@]} speakers"
else
    log "ERROR: Failed to create combined sink"
    # Fall back to first available speaker
    pactl set-default-sink "${slaves[0]}" 2>/dev/null || true
    exit 1
fi
