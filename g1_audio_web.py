#!/usr/bin/env python3
"""
G1 Audio Driver — Web Management Interface

Lightweight web UI for managing the g1-audio-driver systemd service.
No external dependencies — uses Python stdlib only.

Usage:
  python3 g1_audio_web.py              # default port 8085
  python3 g1_audio_web.py --port 9000  # custom port
"""

import argparse
import json
import os
import subprocess
import sys
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SERVICE = "g1-audio-driver"


# ─── System helpers ──────────────────────────────────────────────────────────

def systemctl(cmd):
    """Run systemctl --user <cmd> g1-audio-driver. Returns (ok, output)."""
    try:
        r = subprocess.run(
            ["systemctl", "--user", cmd, SERVICE],
            capture_output=True, text=True, timeout=15)
        return r.returncode == 0, (r.stdout + r.stderr).strip()
    except Exception as e:
        return False, str(e)


def get_status():
    """Get structured service status."""
    ok, raw = systemctl("status")
    active = "active" in raw and "running" in raw
    # Extract PID and uptime from status output
    pid = None
    uptime = None
    for line in raw.splitlines():
        line = line.strip()
        if "Main PID:" in line:
            try:
                pid = int(line.split("Main PID:")[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
        if "Active:" in line and "since" in line:
            uptime = line.split("since")[-1].strip().rstrip(")")

    # Check PA devices
    pa_sources = ""
    pa_sinks = ""
    try:
        r = subprocess.run(["pactl", "list", "sources", "short"],
                           capture_output=True, text=True, timeout=5)
        pa_sources = r.stdout
    except Exception:
        pass
    try:
        r = subprocess.run(["pactl", "list", "sinks", "short"],
                           capture_output=True, text=True, timeout=5)
        pa_sinks = r.stdout
    except Exception:
        pass

    mic_loaded = "g1_microphone" in pa_sources
    spk_loaded = "g1_speaker" in pa_sinks

    return {
        "active": active,
        "pid": pid,
        "uptime": uptime,
        "mic_loaded": mic_loaded,
        "spk_loaded": spk_loaded,
        "raw": raw,
    }


def get_logs(lines=100):
    """Get recent journal logs."""
    try:
        r = subprocess.run(
            ["journalctl", "--user", "-u", SERVICE, "-n", str(lines), "--no-pager"],
            capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception as e:
        return f"Error reading logs: {e}"


def force_kill():
    """Force kill: stop service, pkill, unload PA modules, clean pipes."""
    systemctl("stop")
    try:
        subprocess.run(["pkill", "-9", "-f", "g1_audio_driver"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    # Unload PA modules and clean pipes
    for pipe in ["/tmp/g1_mic.pipe", "/tmp/g1_spk.pipe"]:
        try:
            out = subprocess.check_output(
                ["pactl", "list", "modules", "short"], text=True, timeout=5)
            for line in out.splitlines():
                if pipe in line:
                    idx = line.split()[0]
                    subprocess.run(["pactl", "unload-module", idx],
                                   capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            os.remove(pipe)
        except Exception:
            pass
    return "Force killed and cleaned up"


# ─── HTML ────────────────────────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>G1 Audio Driver</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, system-ui, sans-serif; background: #0f1117; color: #e1e4e8; padding: 20px; max-width: 800px; margin: 0 auto; }
  h1 { font-size: 1.4em; margin-bottom: 16px; }
  .card { background: #1c1f26; border-radius: 8px; padding: 16px; margin-bottom: 16px; border: 1px solid #2d3139; }
  .card h2 { font-size: 1em; color: #8b949e; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .status-row { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; }
  .badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 0.85em; font-weight: 600; }
  .badge.running { background: #1a7f37; color: #fff; }
  .badge.stopped { background: #9e2a2a; color: #fff; }
  .badge.unknown { background: #6e5a00; color: #fff; }
  .badge.loaded { background: #1a5276; color: #aed6f1; }
  .badge.missing { background: #2d3139; color: #6b7280; }
  .info { color: #8b949e; font-size: 0.9em; }
  .buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  button { padding: 8px 18px; border: none; border-radius: 6px; font-size: 0.9em; cursor: pointer; font-weight: 500; transition: opacity 0.15s; }
  button:hover { opacity: 0.85; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  .btn-start { background: #238636; color: #fff; }
  .btn-stop { background: #da3633; color: #fff; }
  .btn-restart { background: #1f6feb; color: #fff; }
  .btn-kill { background: #6e2a00; color: #f0a070; }
  .logs { background: #0d1117; border: 1px solid #2d3139; border-radius: 6px; padding: 12px; font-family: 'SF Mono', 'Consolas', monospace; font-size: 0.8em; line-height: 1.5; max-height: 400px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; color: #b0b8c1; }
  .toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 18px; border-radius: 6px; font-size: 0.9em; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
  .toast.show { opacity: 1; }
  .toast.ok { background: #1a7f37; color: #fff; }
  .toast.err { background: #9e2a2a; color: #fff; }
  .devices { display: flex; gap: 16px; margin-top: 8px; }
</style>
</head>
<body>
<h1>G1 Audio Driver</h1>

<div class="card">
  <h2>Service Status</h2>
  <div class="status-row">
    <span id="badge" class="badge unknown">checking...</span>
    <span id="pid" class="info"></span>
    <span id="uptime" class="info"></span>
  </div>
  <div class="devices">
    <span>Mic: <span id="mic" class="badge missing">--</span></span>
    <span>Speaker: <span id="spk" class="badge missing">--</span></span>
  </div>
</div>

<div class="card">
  <h2>Controls</h2>
  <div class="buttons">
    <button class="btn-start" onclick="action('start')">Start</button>
    <button class="btn-stop" onclick="action('stop')">Stop</button>
    <button class="btn-restart" onclick="action('restart')">Restart</button>
    <button class="btn-kill" onclick="action('kill')">Force Kill</button>
  </div>
</div>

<div class="card">
  <h2>Logs <span class="info" style="text-transform:none; letter-spacing:0">(last 100 lines)</span></h2>
  <div id="logs" class="logs">Loading...</div>
</div>

<div id="toast" class="toast"></div>

<script>
function toast(msg, ok) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + (ok ? 'ok' : 'err');
  setTimeout(() => t.className = 'toast', 2500);
}

async function refresh() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const b = document.getElementById('badge');
    if (d.active) { b.textContent = 'running'; b.className = 'badge running'; }
    else { b.textContent = 'stopped'; b.className = 'badge stopped'; }
    document.getElementById('pid').textContent = d.pid ? 'PID ' + d.pid : '';
    document.getElementById('uptime').textContent = d.uptime || '';
    const mic = document.getElementById('mic');
    mic.textContent = d.mic_loaded ? 'loaded' : 'not loaded';
    mic.className = 'badge ' + (d.mic_loaded ? 'loaded' : 'missing');
    const spk = document.getElementById('spk');
    spk.textContent = d.spk_loaded ? 'loaded' : 'not loaded';
    spk.className = 'badge ' + (d.spk_loaded ? 'loaded' : 'missing');
  } catch(e) { console.error(e); }
}

async function refreshLogs() {
  try {
    const r = await fetch('/api/logs?lines=100');
    const d = await r.json();
    const el = document.getElementById('logs');
    el.textContent = d.logs || '(no logs)';
    el.scrollTop = el.scrollHeight;
  } catch(e) { console.error(e); }
}

async function action(cmd) {
  document.querySelectorAll('button').forEach(b => b.disabled = true);
  try {
    const r = await fetch('/api/' + cmd, { method: 'POST' });
    const d = await r.json();
    toast(d.msg || (d.ok ? 'OK' : 'Failed'), d.ok);
    setTimeout(refresh, 1000);
    setTimeout(refreshLogs, 2000);
  } catch(e) { toast('Request failed', false); }
  document.querySelectorAll('button').forEach(b => b.disabled = false);
}

refresh();
refreshLogs();
setInterval(refresh, 3000);
setInterval(refreshLogs, 5000);
</script>
</body>
</html>"""


# ─── HTTP handler ────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Quieter logging — skip noisy per-request lines
        pass

    def _json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, content):
        body = content.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "":
            self._html(HTML)
        elif path == "/api/status":
            self._json(get_status())
        elif path == "/api/logs":
            lines = int(qs.get("lines", ["100"])[0])
            lines = max(1, min(lines, 1000))
            self._json({"ok": True, "logs": get_logs(lines)})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/start":
            ok, msg = systemctl("start")
            self._json({"ok": ok, "msg": msg or "Started"})
        elif path == "/api/stop":
            ok, msg = systemctl("stop")
            self._json({"ok": ok, "msg": msg or "Stopped"})
        elif path == "/api/restart":
            ok, msg = systemctl("restart")
            self._json({"ok": ok, "msg": msg or "Restarted"})
        elif path == "/api/kill":
            msg = force_kill()
            self._json({"ok": True, "msg": msg})
        else:
            self.send_error(404)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="G1 Audio Driver — Web Management Interface")
    parser.add_argument("--port", type=int, default=8085, help="HTTP port (default: 8085)")
    args = parser.parse_args()

    server = HTTPServer(("0.0.0.0", args.port), Handler)
    print(f"G1 Audio Driver web UI: http://0.0.0.0:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
