# ticc-dash.py
from flask import Flask, jsonify, render_template_string
import subprocess
import os
from datetime import datetime
import socket

use_sudo = os.getenv("CHRONY_USE_SUDO", "true").lower() in ("true", "1")
chrony_socket = os.getenv("CHRONY_SOCKET")

command = ["chronyc"]
if use_sudo:
    command.insert(0, "sudo")
if chrony_socket:
    command.extend(["-n", "-h", chrony_socket])
command.append("clients")

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
            .summary-bar { display:flex; gap:10px; justify-content:center; align-items:center; flex-wrap:wrap; margin-top: 18px; margin-bottom: 48px; }
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

            <div style="height:14px"></div>
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
            $(function(){ $("#sort-select").on("change",render); $("#search").on("input",render); refresh(); setInterval(refresh,1000); });
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
