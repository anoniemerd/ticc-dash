# ticc-dash.py
from flask import Flask, jsonify, render_template_string, request
import subprocess
import os
from datetime import datetime
import socket
import json
import threading
import time
from collections import deque

use_sudo = os.getenv("CHRONY_USE_SUDO", "true").lower() in ("true", "1")
# Already root: sudo adds nothing, but would still log every call
if use_sudo and os.geteuid() == 0:
    use_sudo = False
chrony_socket = os.getenv("CHRONY_SOCKET")

def chronyc_cmd(*args):
    cmd = ["chronyc"]
    if use_sudo:
        cmd.insert(0, "sudo")
    if chrony_socket:
        cmd.extend(["-n", "-h", chrony_socket])
    cmd.extend(args)
    return cmd

command = chronyc_cmd("clients")

# Sync-status history: one sample every SYNC_SAMPLE_INTERVAL seconds, kept in memory
# for SYNC_HISTORY_SECONDS (lost on restart).
SAMPLE_INTERVAL = int(os.getenv("SYNC_SAMPLE_INTERVAL", "10"))
HISTORY_SECONDS = int(os.getenv("SYNC_HISTORY_SECONDS", "86400"))
CPU_TEMP_PATH = os.getenv("CPU_TEMP_PATH", "/sys/class/thermal/thermal_zone0/temp")
MAX_POINTS = 720
LASTRX_NEVER = 4294967295

app = Flask(__name__)

def _is_ipv4(addr: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET, addr)
        return True
    except OSError:
        return False

def _is_ipv6(addr: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6, addr)
        return True
    except OSError:
        return False

def _parse_client_line(line: str):
    parts = line.split()
    if not parts:
        return None
    addr = parts[0]
    f = parts[1:]
    def g(i): return f[i] if i < len(f) else ""
    return {
        "addr": addr,
        "NTP": g(0),
        "Drop": g(1),
        "Int": g(2),
        "IntL": g(3),
        "Last": g(4),
        "Cmd": g(5)
    }

def get_chrony_clients():
    try:
        output = subprocess.check_output(command, universal_newlines=True)
    except Exception as e:
        return [], 0, f"Error: {e}"

    lines = output.strip().split("\n")
    if len(lines) < 3:
        return [], 0, ""

    body = [ln.rstrip() for ln in lines[2:] if ln.strip() != ""]
    hostnames, ipv4s, ipv6s = [], [], []
    for ln in body:
        addr = (ln.split() or [""])[0]
        if _is_ipv4(addr):
            ipv4s.append(ln)
        elif _is_ipv6(addr):
            ipv6s.append(ln)
        else:
            hostnames.append(ln)

    hostnames.sort(key=lambda x: x.split()[0].lower())
    ipv4s.sort(key=lambda x: tuple(map(int, (x.split()[0]).split("."))))
    ipv6s.sort(key=lambda x: x.split()[0])
    sorted_lines = hostnames + ipv4s + ipv6s

    parsed = []
    for ln in sorted_lines:
        row = _parse_client_line(ln)
        if row:
            parsed.append(row)
    return parsed, len(parsed), ""

def get_local_time():
    return datetime.now().strftime("%d-%m-%Y, %H:%M:%S")

def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def get_tracking():
    out = subprocess.check_output(chronyc_cmd("-c", "tracking"), universal_newlines=True, timeout=5)
    f = out.strip().split(",")
    return {
        "ref_id": f[0],
        "ref_name": f[1],
        "stratum": int(f[2]),
        "ref_time": _float(f[3]),
        # chronyc -c reports the correction still to apply; negate it so that
        # positive = local clock ahead of NTP time (same sign as `chronyc sources`)
        "system_offset": -_float(f[4]),
        "last_offset": _float(f[5]),
        "rms_offset": _float(f[6]),
        "frequency": _float(f[7]),
        "residual_freq": _float(f[8]),
        "skew": _float(f[9]),
        "root_delay": _float(f[10]),
        "root_dispersion": _float(f[11]),
        "update_interval": _float(f[12]),
        "leap": f[13],
    }

def get_sources():
    out = subprocess.check_output(chronyc_cmd("-n", "-c", "sources"), universal_newlines=True, timeout=5)
    sources = []
    for ln in out.strip().split("\n"):
        f = ln.split(",")
        if len(f) < 10:
            continue
        last_rx = int(f[6])
        sources.append({
            "mode": f[0],                     # ^ server, = peer, # reference clock
            "state": f[1],                    # * selected, + combined, - not combined, ? unusable, x falseticker, ~ too variable
            "name": f[2],
            "stratum": int(f[3]),
            "poll": int(f[4]),
            "reach": int(f[5], 8),
            "last_rx": None if last_rx == LASTRX_NEVER else last_rx,
            "offset": _float(f[7]),
            "measured_offset": _float(f[8]),
            "error": _float(f[9]),
        })
    return sources

def get_cpu_temp():
    try:
        with open(CPU_TEMP_PATH) as fh:
            return int(fh.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None

GPSD_HOST = os.getenv("GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.getenv("GPSD_PORT", "2947"))
_gps_cache = {"t": 0, "data": None}

def get_gps():
    """Fix and satellite summary from gpsd (TPV + SKY reports), cached for 15 s.
    Returns None when gpsd is not reachable."""
    if time.time() - _gps_cache["t"] < 15:
        return _gps_cache["data"]
    data = None
    try:
        with socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=2) as s:
            s.sendall(b'?WATCH={"enable":true,"json":true}\n')
            buf, tpv, sky, deadline = b"", None, None, time.time() + 3
            while time.time() < deadline and not (tpv and sky):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for ln in lines:
                    try:
                        msg = json.loads(ln)
                    except ValueError:
                        continue
                    if msg.get("class") == "TPV":
                        tpv = msg
                    elif msg.get("class") == "SKY" and "satellites" in msg:
                        sky = msg
        sats = (sky or {}).get("satellites", [])
        data = {
            "mode": (tpv or {}).get("mode", 0),   # 0/1 no fix, 2 = 2D, 3 = 3D
            "time": (tpv or {}).get("time"),
            "used": sum(1 for x in sats if x.get("used")),
            "seen": len(sats),
            "hdop": (sky or {}).get("hdop"),
        }
    except OSError:
        data = None
    _gps_cache.update(t=time.time(), data=data)
    return data

def classify_sync(tracking, sources):
    selected = next((s for s in sources if s["state"] == "*"), None)
    refclocks = [s for s in sources if s["mode"] == "#"]
    if selected is None or tracking["leap"] == "Not synchronised":
        return "bad", "Not synchronised", "chronyd has no selected time source"
    if selected["mode"] == "#":
        return "ok", f"Synchronised to {selected['name']}", "Stratum %d, disciplined by a local reference clock" % tracking["stratum"]
    if refclocks:
        idle = ", ".join(f"{s['name']} ({'no samples' if s['reach'] == 0 else 'not selected'})" for s in refclocks)
        return "warn", f"Synchronised over the network to {selected['name']}", f"Reference clock not in use: {idle}"
    return "ok", f"Synchronised to {selected['name']}", "Stratum %d, network source" % tracking["stratum"]

def get_sync_status():
    tracking = get_tracking()
    sources = get_sources()
    level, title, detail = classify_sync(tracking, sources)
    return {
        "level": level,
        "title": title,
        "detail": detail,
        "tracking": tracking,
        "sources": sources,
        "cpu_temp": get_cpu_temp(),
        "gps": get_gps(),
        "now": time.time(),
    }

_history = deque(maxlen=max(1, HISTORY_SECONDS // SAMPLE_INTERVAL))
_history_lock = threading.Lock()

def _sample_loop():
    while True:
        try:
            st = get_sync_status()
            t = st["tracking"]
            point = {
                "t": st["now"],
                "offset": t["system_offset"],
                "rms": t["rms_offset"],
                "freq": t["frequency"],
                "temp": st["cpu_temp"],
                "level": st["level"],
                "src": {s["name"]: s["offset"] for s in st["sources"] if s["reach"] and s["last_rx"] is not None},
            }
            with _history_lock:
                _history.append(point)
        except Exception as e:
            app.logger.warning("sync sample failed: %s", e)
        time.sleep(SAMPLE_INTERVAL)

def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None

def get_history(seconds):
    cutoff = time.time() - seconds
    with _history_lock:
        pts = [p for p in _history if p["t"] >= cutoff]
    # average into at most MAX_POINTS buckets so long ranges stay light
    step = max(1, -(-len(pts) // MAX_POINTS))
    out = []
    for i in range(0, len(pts), step):
        b = pts[i:i + step]
        names = {n for p in b for n in p["src"]}
        out.append({
            "t": b[-1]["t"],
            "offset": _mean(p["offset"] for p in b),
            "rms": _mean(p["rms"] for p in b),
            "freq": _mean(p["freq"] for p in b),
            "temp": _mean(p["temp"] for p in b),
            "level": b[-1]["level"],
            "src": {n: _mean(p["src"].get(n) for p in b) for n in names},
        })
    with _history_lock:
        since = _history[0]["t"] if _history else None
    return {"points": out, "since": since, "interval": SAMPLE_INTERVAL * step}

threading.Thread(target=_sample_loop, name="sync-sampler", daemon=True).start()

@app.route("/data")
def data():
    parsed, count, err = get_chrony_clients()
    payload = {
        "clients_parsed": parsed,
        "count": count,
        "local_time": get_local_time(),
    }
    if err:
        payload["error"] = err
    return jsonify(payload)

@app.route("/sync")
def sync():
    try:
        return jsonify(get_sync_status())
    except Exception as e:
        return jsonify({"level": "bad", "title": "Sync status unavailable", "detail": f"Error: {e}"}), 500

@app.route("/sync/history")
def sync_history():
    seconds = min(max(request.args.get("range", 3600, type=int), 60), HISTORY_SECONDS)
    return jsonify(get_history(seconds))

@app.route("/")
def dashboard():
    html = """
    <!DOCTYPE html>
    <html lang="en" data-bs-theme="dark">
    <head>
        <meta charset="UTF-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title>TICC-DASH | Time Information of Chrony Clients – Dashboard</title>

        <!-- Favicons -->
        <link rel="icon" type="image/png" href="/static/img/favicon.png"/>
        <link rel="icon" type="image/png" href="/static/img/ticc-dash-logo.png"/>

        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"/>
        <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js" integrity="sha384-9nhczxUqK87bcKHh20fSQcTGD4qq5GhayNYSYWqwBkINBhOfQLg/P5HG5lF1urn4" crossorigin="anonymous"></script>
        <style>
            :root{
                --ok:#198754; --warn:#ffc107; --bad:#dc3545;
                --bg:#0f1720; --card:#1f2630; --text-dim:#9aa3ad;
                --border: rgba(255,255,255,.10); --row-sep: rgba(255,255,255,.06);
                --tile-bg: rgba(255,255,255,0.06); --detail-bg: rgba(255,255,255,0.03);
                --title-color:#8be1dd; --toolbar-bg: rgba(18,22,28,.65);
                --toolbar-border: rgba(255,255,255,.12);
                --pill-bg: rgba(255,255,255,.04); --pill-border: rgba(255,255,255,.08);
                --info-bg: #0ea5e9; --info-btn-size: 22px;
            }
            html[data-bs-theme="light"]{
                --bg:#eef2f3; --card:#ffffff; --text-dim:#475569;
                --border: rgba(0,0,0,.12); --row-sep: rgba(0,0,0,.06);
                --tile-bg: rgba(0,0,0,0.06); --detail-bg: rgba(0,0,0,0.03);
                --title-color:#2c7a7b; --toolbar-bg: rgba(255,255,255,.85);
                --toolbar-border: rgba(0,0,0,.12); --pill-bg: rgba(0,0,0,.03);
                --pill-border: rgba(0,0,0,.08); --info-bg: #0284c7;
            }

            body { font-family: system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif; background: var(--bg); }
            .page { max-width: 1100px; margin: 0 auto; padding: 12px 16px 28px; }

            /* Brand header with logo + centered title */
            .brand-wrap{
                display:flex; align-items:center; justify-content:center;
                gap: 12px; margin-top: 12px; margin-bottom: 2px;
            }
            .brand-logo{
                height: 68px; width: auto; object-fit: contain; user-select:none;
                filter: drop-shadow(0 2px 8px rgba(0,0,0,.12));
            }
            @media (max-width: 600px){
                .brand-logo{ height: 56px; }
            }
            .brand-wrap .title{
                font-weight:800; font-size: clamp(1.9rem, 1.2rem + 1.8vw, 2.8rem);
                letter-spacing:.3px; color: var(--title-color);
                text-shadow: 0 2px 12px rgba(0,0,0,.18);
            }

            .subtitle{ text-align:center; margin-top:2px; color: var(--text-dim); margin-bottom: 22px; }
            .datetime-block{ text-align:center; font-size: 1.15rem; font-weight: 700; letter-spacing: .2px; }
            .datetime-block .sep{ display:block; height:4px; }

            .toolbar { position: fixed; right: 16px; top: 12px; z-index: 1000; display:flex; align-items:center; gap:10px;
                padding: 6px 10px; border-radius: 999px; background: var(--toolbar-bg); border: 1px solid var(--toolbar-border);
                box-shadow: 0 8px 24px rgba(0,0,0,.25); backdrop-filter: blur(8px);
            }
            .switch { display:flex; align-items:center; gap:8px; user-select:none; }
            .switch input { display:none; }
            .slider { position: relative; width: 46px; height: 22px; background: #cbd5e1; border-radius: 999px; cursor: pointer; transition: .25s; box-shadow: inset 0 0 0 1px rgba(0,0,0,.06); }
            .slider::before{ content: ""; position: absolute; height: 18px; width: 18px; left: 2px; bottom: 2px; background: #fff; border-radius: 50%; transition: .25s; box-shadow: 0 1px 2px rgba(0,0,0,.25); }
            .switch input:checked + .slider { background:#64748b; } .switch input:checked + .slider::before { transform: translateX(24px); }
            .ico{ width:18px; height:18px; opacity:.9; }
            .info-wrap{ position: relative; }
            .info-btn{ width: var(--info-btn-size); height: var(--info-btn-size); border-radius:50%; display:flex; align-items:center; justify-content:center;
                background: var(--info-bg); color:#fff; font-weight:800; cursor:pointer; box-shadow: 0 3px 10px rgba(0,0,0,.2); user-select:none; line-height:1; }
            .info-pop{ position:absolute; right: 0; top: 42px; min-width: 260px; max-width: 320px; background: var(--card); border: 1px solid var(--border);
                color: inherit; border-radius: 10px; padding: 10px 12px; box-shadow: 0 10px 26px rgba(0,0,0,.28); display:none; z-index: 3000; }
            .info-pop:after{ content:""; position:absolute; top:-8px; right: calc(var(--info-btn-size) / 2); width:0;height:0;
                border-left:8px solid transparent; border-right:8px solid transparent; border-bottom:8px solid var(--card);
                filter: drop-shadow(0 -1px 0 var(--border)); }
            .info-pop a{ color: inherit; text-decoration: underline; }

            .subinfo { text-align:center; margin-bottom: 6px; line-height: 1.45rem; }
            .badge-clients { font-size: .95rem; border-radius: 999px; padding: .45rem .75rem; font-weight:700; }
            .summary-bar { display:flex; gap:10px; justify-content:center; align-items:center; flex-wrap:wrap; margin-top: 18px; margin-bottom: 28px; }
            .summary-pill{ display:inline-flex; align-items:center; gap:8px; font-size: .92rem; line-height: 1.2rem; padding: 4px 10px; border-radius: 999px;
                background: var(--pill-bg); border: 1px solid var(--pill-border); color: inherit; font-weight: 600; }
            .summary-pill::before{ content:""; width:9px; height:9px; border-radius:50%; background: currentColor; }
            .pill-ok{ color: var(--ok); } .pill-warn{ color: #b88400; } .pill-bad{ color: #b02a37; }

            .controls{ display:flex; gap:8px; justify-content:center; align-items:center; flex-wrap:wrap; margin-top: 6px; margin-bottom: 10px; }
            .controls .form-select { width: 260px; } .controls .form-control { width: min(420px, 58vw); }

            .table-wrap{ padding: 0; }
            .client-table{ font-size:.95rem; border-collapse: separate; border-spacing: 0; border-radius: 12px; overflow:hidden; background: var(--card);
                box-shadow: 0 8px 24px rgba(0,0,0,.18); }
            .client-table thead th{ background: transparent; font-weight:700; border-bottom: 1px solid var(--border); }
            .client-table tbody td{ border-top: 1px solid var(--row-sep); }
            .client-table tbody tr td, .client-table thead th{ padding: .48rem .6rem; }
            .addr-cell{ font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
            .td-num{ text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }
            .last-cell{ white-space:nowrap; }
            .caret-cell{ width:30px; text-align:center; cursor:pointer; } .caret{ user-select:none; }
            .sev-0{ border-left: 6px solid var(--ok); } .sev-1{ border-left: 6px solid var(--warn); } .sev-2{ border-left: 6px solid var(--bad); }
            .detail-row td{ padding: .75rem .6rem; background: var(--detail-bg); }
            .metrics{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:12px; }
            @media (max-width: 900px){ .metrics{ grid-template-columns: repeat(2, minmax(0,1fr)); } }
            @media (max-width: 560px){ .metrics{ grid-template-columns: repeat(1, minmax(0,1fr)); } }
            .metric{ border-radius:10px; background: var(--tile-bg); padding:10px 12px; display:flex; align-items:center; justify-content:space-between; min-height:56px; gap:10px; }
            .metric .label{ font-weight:600; display:flex; align-items:center; gap:8px; opacity:.95; }
            .metric .value{ font-variant-numeric: tabular-nums; font-weight:600; }

            /* Sync status */
            .section-title{ font-weight:800; font-size:1.15rem; letter-spacing:.2px; margin: 0 0 10px; color: var(--title-color); }
            .sync-card{ background: var(--card); border-radius:12px; box-shadow: 0 8px 24px rgba(0,0,0,.18); padding:16px; margin: 6px 0 36px; border-left: 6px solid var(--text-dim); }
            .sync-card.lvl-ok{ border-left-color: var(--ok); } .sync-card.lvl-warn{ border-left-color: var(--warn); } .sync-card.lvl-bad{ border-left-color: var(--bad); }
            .sync-head{ display:flex; align-items:center; gap:14px; margin-bottom:14px; }
            .sync-dot{ flex:none; width:18px; height:18px; border-radius:50%; background: var(--text-dim); }
            .lvl-ok .sync-dot{ background: var(--ok); box-shadow: 0 0 0 5px rgba(25,135,84,.22); }
            .lvl-warn .sync-dot{ background: var(--warn); box-shadow: 0 0 0 5px rgba(255,193,7,.22); }
            .lvl-bad .sync-dot{ background: var(--bad); box-shadow: 0 0 0 5px rgba(220,53,69,.22); }
            .sync-title{ font-weight:800; font-size:1.25rem; line-height:1.3; }
            .sync-detail{ color: var(--text-dim); font-size:.95rem; }
            .sync-metrics{ grid-template-columns: repeat(4, minmax(0, 1fr)); margin-bottom:14px; }
            @media (max-width: 900px){ .sync-metrics{ grid-template-columns: repeat(2, minmax(0,1fr)); } }
            .sync-metrics .metric{ flex-direction:column; align-items:flex-start; justify-content:center; gap:2px; }
            .sync-metrics .label{ font-size:.82rem; color: var(--text-dim); font-weight:600; }
            .sync-metrics .value{ font-size:1.05rem; }
            .src-wrap{ overflow-x:auto; margin-bottom:14px; }
            .src-table{ font-size:.92rem; margin:0; }
            .src-table th{ font-weight:700; border-bottom:1px solid var(--border); white-space:nowrap; }
            .src-table td{ border-top:1px solid var(--row-sep); white-space:nowrap; }
            .src-table th, .src-table td{ padding:.4rem .55rem; background: transparent; }
            .src-state{ display:inline-block; min-width:92px; font-weight:700; }
            .st-sel{ color: var(--ok); } .st-ok{ color: inherit; } .st-bad{ color: var(--bad); } .st-warn{ color:#b88400; }
            .reach{ display:inline-flex; gap:3px; vertical-align:middle; }
            .reach i{ width:8px; height:8px; border-radius:50%; background: var(--tile-bg); border:1px solid var(--border); }
            .reach i.on{ background: var(--ok); border-color: var(--ok); }
            .chart-bar{ display:flex; justify-content:space-between; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:8px; }
            .chart-bar .note{ color: var(--text-dim); font-size:.85rem; }
            .charts{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:12px; }
            @media (max-width: 980px){ .charts{ grid-template-columns: 1fr; } }
            .chart-box{ background: var(--tile-bg); border-radius:10px; padding:10px 12px; }
            .chart-box h6{ font-weight:700; font-size:.9rem; margin:0 0 6px; }
            .chart-box .canvas-wrap{ position:relative; height:200px; }
        </style>
    </head>
    <body>
        <!-- Toolbar -->
        <div class="toolbar" id="toolbar">
            <label class="switch expand" title="Expand/Collapse all">
                <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="3" y="3" width="7" height="7" rx="1"></rect>
                    <rect x="14" y="3" width="7" height="7" rx="1"></rect>
                    <rect x="3" y="14" width="7" height="7" rx="1"></rect>
                </svg>
                <input type="checkbox" id="expand-toggle"><span class="slider"></span>
            </label>
            <label class="switch theme" title="Light/Dark theme">
                <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M12 3v2M12 19v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M3 12h2M19 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path>
                    <circle cx="12" cy="12" r="5"></circle>
                </svg>
                <input type="checkbox" id="theme-toggle"><span class="slider"></span>
            </label>
            <div class="info-wrap">
                <div class="info-btn" id="infoBtn" title="Project info">i</div>
                <div class="info-pop" id="infoPop">
                    <strong>TICC-DASH</strong><br/>
                    More information: <a href="https://ticc-dash.org" target="_blank" rel="noopener">ticc-dash.org</a>
                </div>
            </div>
        </div>

        <div class="page">
            <!-- Brand header (logo + title remain centered together) -->
            <div class="brand-wrap">
                <img class="brand-logo" src="/static/img/ticc-dash-logo.png" alt="TICC-DASH Logo" />
                <div class="title">TICC-DASH</div>
            </div>
            <div class="subtitle">Time Information of Chrony Clients – Dashboard</div>

            <div class="datetime-block">
                <div>Date: <span id="date-part">--</span></div>
                <span class="sep"></span>
                <div>Time: <span id="time-part">--</span></div>
            </div>

            <div style="height:18px"></div>
            <div class="section-title">Time sync</div>
            <div class="sync-card" id="sync-card">
                <div class="sync-head">
                    <span class="sync-dot"></span>
                    <div>
                        <div class="sync-title" id="sync-title">Loading…</div>
                        <div class="sync-detail" id="sync-detail"></div>
                    </div>
                </div>
                <div class="metrics sync-metrics">
                    <div class="metric"><div class="label">Reference</div><div class="value" id="sm-ref">-</div></div>
                    <div class="metric"><div class="label">Stratum</div><div class="value" id="sm-stratum">-</div></div>
                    <div class="metric"><div class="label">System clock offset</div><div class="value" id="sm-offset">-</div></div>
                    <div class="metric"><div class="label">RMS offset</div><div class="value" id="sm-rms">-</div></div>
                    <div class="metric"><div class="label">Frequency error</div><div class="value" id="sm-freq">-</div></div>
                    <div class="metric"><div class="label">Root dispersion</div><div class="value" id="sm-disp">-</div></div>
                    <div class="metric"><div class="label">Last clock update</div><div class="value" id="sm-updated">-</div></div>
                    <div class="metric"><div class="label">Leap status · CPU temp</div><div class="value" id="sm-leap">-</div></div>
                    <div class="metric"><div class="label">GPS fix (gpsd)</div><div class="value" id="sm-gpsfix">-</div></div>
                    <div class="metric"><div class="label">Satellites used / visible</div><div class="value" id="sm-sats">-</div></div>
                    <div class="metric"><div class="label">HDOP</div><div class="value" id="sm-hdop">-</div></div>
                    <div class="metric"><div class="label">GPS UTC</div><div class="value" id="sm-gpstime">-</div></div>
                </div>
                <div class="src-wrap">
                    <table class="table src-table">
                        <thead><tr>
                            <th>Source</th><th>State</th><th>Type</th><th class="text-end">Stratum</th>
                            <th>Reach (last 8 polls)</th><th class="text-end">Last sample</th><th class="text-end">Offset</th><th class="text-end">± Error</th>
                        </tr></thead>
                        <tbody id="src-tbody"></tbody>
                    </table>
                </div>
                <div class="chart-bar">
                    <div class="btn-group btn-group-sm" role="group" id="range-group">
                        <button type="button" class="btn btn-outline-secondary" data-range="900">15 min</button>
                        <button type="button" class="btn btn-outline-secondary active" data-range="3600">1 h</button>
                        <button type="button" class="btn btn-outline-secondary" data-range="21600">6 h</button>
                        <button type="button" class="btn btn-outline-secondary" data-range="86400">24 h</button>
                    </div>
                    <span class="note" id="history-note"></span>
                </div>
                <div class="charts">
                    <div class="chart-box"><h6>System clock offset &amp; RMS (µs)</h6><div class="canvas-wrap"><canvas id="ch-offset"></canvas></div></div>
                    <div class="chart-box"><h6>Frequency error (ppm) vs CPU temperature (°C)</h6><div class="canvas-wrap"><canvas id="ch-freq"></canvas></div></div>
                    <div class="chart-box"><h6>Source offsets (µs)</h6><div class="canvas-wrap"><canvas id="ch-src"></canvas></div></div>
                </div>
            </div>

            <div class="section-title">Clients</div>
            <div class="subinfo">
                <span class="badge bg-primary badge-clients">Clients: <span id="clients-count">0</span></span>
            </div>

            <div class="summary-bar">
                <span class="summary-pill pill-ok">OK: <span id="count-ok">0</span></span>
                <span class="summary-pill pill-warn">Warning: <span id="count-warn">0</span></span>
                <span class="summary-pill pill-bad">Critical: <span id="count-bad">0</span></span>
            </div>

            <div class="controls">
                <select id="sort-select" class="form-select" title="Sort clients">
                    <option value="ip_order">Sort by: IP Address (IPv4 numeric, then others)</option>
                    <option value="drop_desc">Sort by: Drop Count (high → low)</option>
                    <option value="last_recent">Sort by: Last Seen (recent → old)</option>
                </select>
                <input id="search" type="text" class="form-control" placeholder="Search clients..."/>
            </div>

            <div class="table-wrap">
                <table class="table table-hover align-middle client-table">
                    <colgroup>
                        <col style="width:32px" />
                        <col />
                        <col style="width:120px" />
                        <col style="width:84px" />
                        <col style="width:84px" />
                        <col style="width:84px" />
                        <col style="width:96px" />
                        <col style="width:160px" />
                    </colgroup>
                    <thead>
                        <tr>
                            <th class="caret-cell"></th>
                            <th>Address</th>
                            <th class="text-center">Status</th>
                            <th class="text-end">NTP</th>
                            <th class="text-end">Drop</th>
                            <th class="text-end">Cmd</th>
                            <th class="text-end">Interval</th>
                            <th>Last Seen</th>
                        </tr>
                    </thead>
                    <tbody id="client-tbody"></tbody>
                </table>
            </div>
        </div>

        <script>
            function applyTheme(theme){
                $("html").attr("data-bs-theme", theme);
                $("#theme-toggle").prop("checked", theme === "dark");
            }
            const THEME_KEY="ticc_dash_theme", OPEN_KEY="ticc_dash_open_rows";
            const savedTheme=localStorage.getItem(THEME_KEY)||"dark"; applyTheme(savedTheme);
            $("#theme-toggle").on("change", function(){ const t=this.checked?"dark":"light"; localStorage.setItem(THEME_KEY,t); applyTheme(t); });

            const infoBtn=document.getElementById("infoBtn"), infoPop=document.getElementById("infoPop");
            infoBtn.addEventListener("click",(e)=>{e.stopPropagation(); infoPop.style.display = infoPop.style.display==="block"?"none":"block";});
            document.addEventListener("click",(e)=>{ if(!infoPop.contains(e.target) && e.target!==infoBtn){ infoPop.style.display="none"; } });

            function toInt(v){ const n=parseInt(v,10); return isNaN(n)?0:n; }
            function lastToSeconds(raw){
                if(!raw) return null; const s=String(raw).trim().toLowerCase();
                if (s==="-"||s==="?"||s==="") return null;
                if (/^\\d+$/.test(s)) return parseInt(s,10);
                const m=s.match(/^(\\d+(?:\\.\\d+)?)\\s*([a-z]+)$/i); if(!m) return null;
                const num=parseFloat(m[1]), unit=m[2];
                if(["s","sec","secs","second","seconds"].includes(unit)) return num;
                if(["m","min","mins","minute","minutes"].includes(unit)) return num*60;
                if(["h","hr","hrs","hour","hours"].includes(unit)) return num*3600;
                if(["d","day","days"].includes(unit)) return num*86400;
                return null;
            }
            function humanLast(raw){ const sec=lastToSeconds(raw); if(sec===null) return raw||"-"; if(sec<60) return Math.floor(sec)+" sec ago"; const m=Math.floor(sec/60); if(m<60) return m+" min ago"; const h=Math.floor(m/60); if(h<24) return h+" hr ago"; return Math.floor(h/24)+" d ago"; }
            function severity(r){ const d=toInt(r.Drop); if(d>=10) return 2; if(d>0) return 1; return 0; }
            function sevLabel(s){ return s===2?"Critical":(s===1?"Warning":"OK"); }
            function iconForAddr(a){ if(/^\\d+\\.\\d+\\.\\d+\\.\\d+$/.test(a)) return "🌐"; if(/^[0-9a-fA-F:]+$/.test(a)) return "🔗"; return "💻"; }

            function loadOpenSet(){ try{ return new Set(JSON.parse(localStorage.getItem(OPEN_KEY)||"[]")); }catch(e){ return new Set(); } }
            function saveOpenSet(s){ try{ localStorage.setItem(OPEN_KEY, JSON.stringify([...s])); }catch(e){} }
            let openSet=loadOpenSet();

            function ipTuple(a){ if(!/^\\d+\\.\\d+\\.\\d+\\.\\d+$/.test(a)) return null; return a.split(".").map(n=>parseInt(n,10)); }
            function sortRows(rows, mode){
                if(mode==="ip_order"){
                    rows.sort((a,b)=>{ const A=ipTuple(a.addr||""),B=ipTuple(b.addr||""); if(A&&B){ for(let i=0;i<4;i++){ if(A[i]!=B[i]) return A[i]-B[i]; } return 0; } if(A&&!B) return -1; if(!A&&B) return 1; return (a.addr||"").toLowerCase().localeCompare((b.addr||"").toLowerCase()); });
                } else if(mode==="drop_desc"){ rows.sort((a,b)=> toInt(b.Drop)-toInt(a.Drop)); }
                else if(mode==="last_recent"){ rows.sort((a,b)=>{ const as=lastToSeconds(a.Last), bs=lastToSeconds(b.Last); if(as===null&&bs===null) return 0; if(as===null) return 1; if(bs===null) return -1; return as-bs; }); }
                return rows;
            }

            function updateSummary(rows){ let ok=0,w=0,b=0; rows.forEach(r=>{ const s=severity(r); if(s===0) ok++; else if(s===1) w++; else b++; }); $("#count-ok").text(ok); $("#count-warn").text(w); $("#count-bad").text(b); }

            function detailHTML(r){ return `
                <tr class="detail-row" data-detail-for="${r.addr}"><td></td><td colspan="7">
                    <div class="metrics">
                        <div class="metric"><div class="label">🕙 NTP Packets</div><div class="value">${r.NTP||"-"}</div></div>
                        <div class="metric"><div class="label">📉 Dropped Packets</div><div class="value">${r.Drop||"-"}</div></div>
                        <div class="metric"><div class="label">📨 Command Packets</div><div class="value">${r.Cmd||"-"}</div></div>
                        <div class="metric"><div class="label">🔄 Interval</div><div class="value">${r.Int||"-"}</div></div>
                        <div class="metric"><div class="label">👁️ Last Seen</div><div class="value">${humanLast(r.Last)}</div></div>
                    </div>
                </td></tr>`; }
            function rowHTML(r){ const sev=severity(r), addr=r.addr||"", open=openSet.has(addr), caret=open?"▲":"▼"; return `
                <tr class="client-row sev-${sev}" data-addr="${addr}">
                    <td class="caret-cell"><span class="caret">${caret}</span></td>
                    <td class="addr-cell" title="${addr}">${iconForAddr(addr)}&nbsp; ${addr}</td>
                    <td class="text-center">${sevLabel(sev)}</td>
                    <td class="td-num">${r.NTP||"-"}</td><td class="td-num">${r.Drop||"-"}</td><td class="td-num">${r.Cmd||"-"}</td><td class="td-num">${r.Int||"-"}</td>
                    <td class="last-cell">${humanLast(r.Last)}</td>
                </tr>${open?detailHTML(r):""}`; }

            function updateExpandToggleVisual(){ const total=$("#client-tbody .client-row").length; const openCnt=openSet.size; $("#expand-toggle").prop("checked", total>0 && openCnt===total); }

            function bindHandlers(){
                $("#client-tbody .client-row").off("click").on("click", function(e){
                    if($(e.target).closest("a,button,select,input,label").length) return;
                    const $row=$(this), addr=$row.data("addr"), isOpen=openSet.has(addr);
                    if(isOpen){ $row.find(".caret").text("▼"); $(`tr.detail-row[data-detail-for="${addr}"]`).remove(); openSet.delete(addr); }
                    else { $row.find(".caret").text("▲"); $(detailHTML(cache.find(r=>r.addr===addr))).insertAfter($row); openSet.add(addr); }
                    saveOpenSet(openSet); updateExpandToggleVisual();
                });

                $("#expand-toggle").off("change").on("change", function(){
                    if(this.checked){
                        $("#client-tbody .client-row").each(function(){ const $r=$(this), a=$r.data("addr"); if(!openSet.has(a)){ $r.find(".caret").text("▲"); $(detailHTML(cache.find(r=>r.addr===a))).insertAfter($r); openSet.add(a); }});
                    }else{
                        $("#client-tbody .client-row .caret").text("▼"); $("#client-tbody tr.detail-row").remove(); openSet=new Set();
                    }
                    saveOpenSet(openSet); updateExpandToggleVisual();
                });
            }

            let cache=[], lastHash="";
            function computeHash(rows){ return rows.map(r=>[r.addr,r.NTP,r.Drop,r.Int,r.Last,r.Cmd].join("|")).join("~"); }

            function render(){
                const q=$("#search").val().trim().toLowerCase(), mode=$("#sort-select").val();
                let rows=cache.filter(r=> !q || JSON.stringify(r).toLowerCase().includes(q));
                rows=sortRows(rows,mode); updateSummary(rows);
                $("#client-tbody").html(rows.map(rowHTML).join("")); bindHandlers(); updateExpandToggleVisual();
            }

            function refresh(){
                $.getJSON("/data", function(payload){
                    const y=window.scrollY;
                    const [d,t]=(payload.local_time||"").split(", "); $("#date-part").text(d||"--"); $("#time-part").text(t||"--");
                    $("#clients-count").text(payload.count||0);
                    const rows=payload.clients_parsed||[], h=computeHash(rows);
                    if(h!==lastHash){ cache=rows; render(); lastHash=h; } else { updateSummary(cache); }
                    if(window.scrollY!==y) window.scrollTo(0,y);
                });
            }
            // ---- Time sync status ----
            function fmtSec(v, signed){
                if(v===null||v===undefined) return "-";
                const a=Math.abs(v), s=signed?(v<0?"−":"+"):"";
                if(a<1e-6) return s+(a*1e9).toFixed(a<1e-8?1:0)+" ns";
                if(a<1e-3) return s+(a*1e6).toFixed(2)+" µs";
                if(a<1) return s+(a*1e3).toFixed(3)+" ms";
                return s+a.toFixed(3)+" s";
            }
            function fmtAgo(sec){
                if(sec===null||sec===undefined) return "never";
                sec=Math.max(0,Math.round(sec));
                if(sec<120) return sec+" s ago"; const m=Math.round(sec/60);
                if(m<120) return m+" min ago"; const h=Math.round(m/60);
                if(h<48) return h+" h ago"; return Math.round(h/24)+" d ago";
            }
            const MODE_TYPE={"#":"Reference clock","^":"Server","=":"Peer"};
            const SRC_STATE={"*":["● Selected","st-sel"],"+":["Combined","st-ok"],"-":["Not combined","st-ok"],"?":["Unusable","st-bad"],"x":["Falseticker","st-bad"],"~":["Too variable","st-warn"]};
            function reachDots(r){
                let h=""; for(let i=7;i>=0;i--){ h+=`<i class="${(r>>i)&1?"on":""}"></i>`; }
                return `<span class="reach" title="reach ${r.toString(8)} (octal), oldest poll on the left">${h}</span>`;
            }
            function srcRowHTML(s){
                const st=SRC_STATE[s.state]||[s.state,"st-ok"], none=s.last_rx===null;
                return `<tr><td class="addr-cell">${s.name}</td><td><span class="src-state ${st[1]}">${st[0]}</span></td>
                    <td>${MODE_TYPE[s.mode]||s.mode}</td><td class="td-num">${s.mode==="#"?"-":s.stratum}</td>
                    <td>${reachDots(s.reach)}</td><td class="td-num">${fmtAgo(s.last_rx)}</td>
                    <td class="td-num">${none?"-":fmtSec(s.offset,true)}</td><td class="td-num">${none?"-":fmtSec(s.error,false)}</td></tr>`;
            }
            function refreshSync(){
                $.getJSON("/sync").done(applySync).fail(function(x){ applySync(x.responseJSON||{level:"bad",title:"Sync status unavailable",detail:"Dashboard could not reach /sync"}); });
            }
            function applySync(p){
                $("#sync-card").removeClass("lvl-ok lvl-warn lvl-bad").addClass("lvl-"+(p.level||"bad"));
                $("#sync-title").text(p.title||"-"); $("#sync-detail").text(p.detail||"");
                const t=p.tracking; if(!t) return;
                $("#sm-ref").text(t.ref_name&&t.ref_name!==t.ref_id ? t.ref_name : (t.ref_name||"-"));
                $("#sm-stratum").text(t.stratum);
                $("#sm-offset").text(fmtSec(t.system_offset,true)+(t.system_offset>=0?" ahead":" behind"));
                $("#sm-rms").text(fmtSec(t.rms_offset,false));
                $("#sm-freq").text((t.frequency>=0?"+":"")+t.frequency.toFixed(3)+" ppm ("+(t.frequency>=0?"fast":"slow")+")");
                $("#sm-disp").text(fmtSec(t.root_dispersion,false));
                $("#sm-updated").text(t.ref_time ? fmtAgo(p.now-t.ref_time)+" · every "+Math.round(t.update_interval)+" s" : "never");
                $("#sm-leap").text(t.leap+(p.cpu_temp!==null&&p.cpu_temp!==undefined?" · "+p.cpu_temp.toFixed(1)+" °C":""));
                $("#src-tbody").html((p.sources||[]).map(srcRowHTML).join(""));
                const g=p.gps;
                $("#sm-gpsfix").text(g ? (["No fix","No fix","2D fix","3D fix"][g.mode]||"No fix") : "gpsd not reachable");
                $("#sm-sats").text(g ? g.used+" / "+g.seen : "-");
                $("#sm-hdop").text(g&&g.hdop!==null&&g.hdop!==undefined ? g.hdop.toFixed(2) : "-");
                $("#sm-gpstime").text(g&&g.time ? g.time.replace("T"," ").replace(/\\.\\d+Z$/,"Z") : "-");
            }

            // ---- Sync history charts ----
            const PALETTE=["#22c55e","#0ea5e9","#f59e0b","#a855f7","#ef4444","#14b8a6","#ec4899","#84cc16"];
            const srcColor={}; let charts={}, histRange=3600;
            function colorFor(name){ if(!srcColor[name]) srcColor[name]=PALETTE[Object.keys(srcColor).length%PALETTE.length]; return srcColor[name]; }
            function cssVar(n){ return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
            function timeTick(v){ return new Date(v).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}); }
            function baseOptions(extraScales){
                const txt=cssVar("--text-dim"), grid=cssVar("--row-sep");
                const scales={ x:{type:"linear", ticks:{color:txt, maxTicksLimit:6, callback:timeTick}, grid:{color:grid}},
                               y:{ticks:{color:txt}, grid:{color:grid}} };
                Object.assign(scales, extraScales||{});
                for(const k in scales){ scales[k].ticks=Object.assign({color:txt}, scales[k].ticks); scales[k].grid=Object.assign({color:grid}, scales[k].grid); }
                return { responsive:true, maintainAspectRatio:false, animation:false, normalized:true, spanGaps:true,
                    interaction:{mode:"index", intersect:false},
                    elements:{point:{radius:0, hitRadius:6}, line:{borderWidth:1.6, tension:0.15}},
                    plugins:{ legend:{labels:{color:txt, boxWidth:10, boxHeight:10}},
                              tooltip:{callbacks:{title:(items)=> items.length? new Date(items[0].parsed.x).toLocaleString():""}} },
                    scales };
            }
            function ds(label, color, data, extra){ return Object.assign({label, data, borderColor:color, backgroundColor:color}, extra||{}); }
            function buildCharts(h){
                const pts=h.points||[], us=v=> v===null?null:v*1e6;
                const offset=pts.map(p=>({x:p.t*1000,y:us(p.offset)})), rms=pts.map(p=>({x:p.t*1000,y:us(p.rms)}));
                const freq=pts.map(p=>({x:p.t*1000,y:p.freq})), temp=pts.map(p=>({x:p.t*1000,y:p.temp}));
                const names=[...new Set(pts.flatMap(p=>Object.keys(p.src)))].sort();
                const srcSets=names.map(n=>{
                    const data=pts.map(p=>({x:p.t*1000, y: n in p.src ? us(p.src[n]) : null}));
                    const vals=data.map(d=>Math.abs(d.y)).filter(v=>!isNaN(v)).sort((a,b)=>a-b);
                    const median=vals.length?vals[Math.floor(vals.length/2)]:0;
                    // coarse sources (e.g. NMEA, ms-level) would flatten the rest; start them hidden
                    return ds(n, colorFor(n), data, {hidden: median>1000});
                });
                const specs={
                    "ch-offset":{ datasets:[ds("Offset",PALETTE[1],offset), ds("RMS offset",PALETTE[2],rms)] },
                    "ch-freq":{ datasets:[ds("Frequency (ppm)",PALETTE[3],freq,{yAxisID:"y"}), ds("CPU °C",PALETTE[4],temp,{yAxisID:"y1"})],
                                scales:{ y1:{position:"right", grid:{drawOnChartArea:false}} } },
                    "ch-src":{ datasets:srcSets },
                };
                for(const id in specs){
                    const hidden={}; if(charts[id]) charts[id].data.datasets.forEach((d,i)=>{ hidden[d.label]=!charts[id].isDatasetVisible(i); });
                    specs[id].datasets.forEach(d=>{ if(d.label in hidden) d.hidden=hidden[d.label]; });
                    if(charts[id]) charts[id].destroy();
                    charts[id]=new Chart(document.getElementById(id), {type:"line", data:{datasets:specs[id].datasets}, options:baseOptions(specs[id].scales)});
                }
                const since=h.since? "history since "+new Date(h.since*1000).toLocaleString()+" (kept in memory, resets on service restart)" : "collecting first samples…";
                $("#history-note").text(since+" · "+pts.length+" points, "+h.interval+" s each");
            }
            let lastHist=null;
            function refreshHistory(){ if(typeof Chart==="undefined") return; $.getJSON("/sync/history",{range:histRange},function(h){ lastHist=h; buildCharts(h); }); }
            $("#range-group").on("click","button",function(){ $("#range-group button").removeClass("active"); $(this).addClass("active"); histRange=parseInt($(this).data("range"),10); refreshHistory(); });
            $("#theme-toggle").on("change", function(){ if(lastHist) buildCharts(lastHist); });

            $(function(){ $("#sort-select").on("change",render); $("#search").on("input",render); refresh(); setInterval(refresh,1000);
                refreshSync(); setInterval(refreshSync,5000); refreshHistory(); setInterval(refreshHistory,30000); });
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
