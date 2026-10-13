#!/usr/bin/env python3
"""Read-only Genesis dual NMR fit-stream dashboard.

Observes Genesis's protected fitter publisher on 127.0.0.1:8778 and the
legacy LabVIEW fitter publisher through the authenticated SSH reverse tunnel
on 127.0.0.1:18778. NEVER sends packets to the fitters, writes EPICS PVs,
or touches hardware. Serves HTTP on loopback only.

Usage: python scripts/genesis_nmr_dual_monitor.py --port 18880
Access from Mac using: ssh -N -L 18880:127.0.0.1:18880 utsav@192.168.1.6
"""
import argparse
import json
import math
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHANNELS = {
    "pynmr": ("Genesis / PyNMR (selected fitter)", 8778),
    "labview": ("Legacy LabVIEW (independent fitter)", 18778),
}
SERIES = ("raw", "data", "total_fit", "I_plus_primary", "I_minus_primary", "I_plus_secondary", "I_minus_secondary")
MAX_POINTS = 8192
MAX_BYTES = 8_000_000


def finite_array(value, length=None):
    if not isinstance(value, list) or not (2 <= len(value) <= MAX_POINTS):
        raise ValueError("Missing or invalid spectrum array")
    if length is not None and len(value) != length:
        raise ValueError("Inconsistent spectrum lengths")
    out = []
    for v in value:
        if v is None:
            out.append(None)
        elif type(v) in (int, float) and math.isfinite(v):
            out.append(float(v))
        else:
            raise ValueError("Invalid non-finite spectrum value")
    return out


def normalize(message):
    if not isinstance(message, dict) or message.get("type") != "fit":
        raise ValueError("Not a fit-publisher message")
    axis = finite_array(message.get("freqs_mhz"))
    if any(v is None for v in axis):
        raise ValueError("Invalid frequency axis")
    count = len(axis)
    declared = message.get("npts")
    if declared is not None and (type(declared) is not int or declared != count):
        raise ValueError("npts mismatch")
    lines = {}
    for name in SERIES:
        value = message.get(name)
        lines[name] = finite_array(value, count) if value is not None else None
    if lines["raw"] is None or lines["data"] is None:
        raise ValueError("Missing raw or processed NMR spectrum")

    def scalar(key):
        value = message.get(key)
        return float(value) if type(value) in (int, float) and math.isfinite(value) else None

    return {
        "ts": str(message.get("ts", ""))[:100],
        "fit_type": str(message.get("fit_type", ""))[:50],
        "material": str(message.get("material", ""))[:50],
        "P_fit": scalar("P_fit"),
        "Q_fit": scalar("Q_fit"),
        "npts": count,
        "freqs_mhz": axis,
        "lines": lines,
    }


class MonitorState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            key: {"name": name, "connected": False, "received": 0,
                  "last_received": None, "error": "Waiting for stream", "spectrum": None}
            for key, (name, _) in CHANNELS.items()
        }

    def update(self, channel, **changes):
        with self.lock:
            self.data[channel].update(changes)

    def snapshot(self):
        with self.lock:
            # Encoding after the lock releases prevents reader/HTTP contention.
            records = json.loads(json.dumps(self.data, allow_nan=False))
        now = time.time()
        for item in records.values():
            last = item["last_received"]
            item["age_seconds"] = round(now - last, 1) if last is not None else None
            item["fresh"] = bool(item["connected"] and last is not None and now - last < 60)
        return records


def consume(channel, port, state, stop):
    while not stop.is_set():
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as sock:
                sock.settimeout(40)
                state.update(channel, connected=True, error=None)
                with sock.makefile("rb") as reader:
                    while not stop.is_set():
                        raw = reader.readline(MAX_BYTES + 1)
                        if not raw:
                            raise ConnectionError("Publisher closed connection")
                        if len(raw) > MAX_BYTES:
                            raise ValueError("Oversized publisher message")
                        try:
                            spectrum = normalize(json.loads(raw))
                        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
                            state.update(channel, error=f"Ignored invalid message: {exc}"[:160])
                            continue
                        with state.lock:
                            current = state.data[channel]
                            current.update(spectrum=spectrum, received=current["received"] + 1,
                                           last_received=time.time(), connected=True, error=None)
        except (OSError, ConnectionError, ValueError) as exc:
            state.update(channel, connected=False, error=str(exc)[:160])
        stop.wait(3)


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Genesis · Dual NMR Monitor</title>
<style>
:root {color-scheme:light; font:14px system-ui,Arial,sans-serif;color:#173451;background:#eef3f7}
body{margin:0 auto;max-width:1600px;padding:24px}
header{display:flex;gap:16px;justify-content:space-between;align-items:baseline;margin-bottom:18px}
h1{font-size:23px;margin:0}.sub{color:#5e7184;margin-top:5px}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:16px}
section{background:white;border:1px solid #dbe4ed;border-radius:12px;padding:16px;min-width:0}
.head{display:flex;justify-content:space-between;gap:10px;align-items:center}
h2{font-size:17px;margin:0}.status{padding:5px 9px;background:#e7e9ed;border-radius:20px;font-size:12px}
.ok{background:#d9f3e9;color:#155d46}.off{background:#ffe7e7;color:#9c3333}
.metrics{display:flex;flex-wrap:wrap;gap:12px;color:#38556e;padding:12px 0;font-variant-numeric:tabular-nums}
canvas{display:block;width:100%;height:360px;border:1px solid #e3ebf0;border-radius:7px;background:#fbfdff}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin-top:11px;font-size:12px}
.dot{width:9px;height:9px;display:inline-block;border-radius:50%;margin-right:5px}
footer{margin-top:17px;color:#60768b;font-size:12px}
@media(max-width:650px){body{padding:10px}header{display:block}}
</style></head><body>
<header><div><h1>Genesis · NMR Source Monitor</h1><div class="sub">Independent read-only fit publishers · no hardware commands or source switching</div></div><div id="poll">Connecting…</div></header>
<main>
<section><div class="head"><h2>Genesis / JLab PyNMR</h2><span id="pynmr-status" class="status">Waiting</span></div>
<div id="pynmr-info" class="metrics">Waiting for first fit…</div><canvas id="pynmr-plot"></canvas><div id="pynmr-legend" class="legend"></div></section>
<section><div class="head"><h2>Legacy LabVIEW</h2><span id="labview-status" class="status">Waiting</span></div>
<div id="labview-info" class="metrics">Waiting for first fit…</div><canvas id="labview-plot"></canvas><div id="labview-legend" class="legend"></div></section>
</main><footer>Inputs: local Genesis publisher :8778, SSH-tunneled legacy publisher :18778. Stale after 60 s without new data. Displayed P/Q are fitter outputs, not calibration acceptance. Source selection remains unchanged.</footer>
<script>
const names={raw:'Raw NMR',data:'Processed',total_fit:'Total fit',I_plus_primary:'I+ primary',I_minus_primary:'I− primary',I_plus_secondary:'I+ secondary',I_minus_secondary:'I− secondary'};
const colors={raw:'#8898a8',data:'#247ab6',total_fit:'#d04835',I_plus_primary:'#2b986c',I_minus_primary:'#b07621',I_plus_secondary:'#8c55b8',I_minus_secondary:'#b45d99'};
const order=Object.keys(names);const shown=new Set(['data','total_fit','raw']);
function draw(canvas,spectrum){
const dpr=window.devicePixelRatio||1, width=Math.max(250,canvas.clientWidth),height=360;
canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
const margin={left:58,right:16,top:22,bottom:38}, pw=width-margin.left-margin.right,ph=height-margin.top-margin.bottom;
ctx.fillStyle='#60778c';ctx.font='11px system-ui';
if(!spectrum){ctx.fillText('Waiting for fit-publisher data',margin.left,35);return;}
const xs=spectrum.freqs_mhz;const series=order.filter(k=>shown.has(k)&&spectrum.lines[k]);
let ymin=Infinity,ymax=-Infinity,xmin=Math.min(...xs),xmax=Math.max(...xs);
for(const k of series){for(const v of spectrum.lines[k]){if(v!==null){ymin=Math.min(ymin,v);ymax=Math.max(ymax,v);}}}
if(!Number.isFinite(ymin)||!Number.isFinite(ymax)||xmin===xmax){ctx.fillText('No finite data to plot',margin.left,35);return;}
let pad=(ymax-ymin)*.1||1;ymin-=pad;ymax+=pad;
const X=x=>margin.left+(x-xmin)/(xmax-xmin)*pw, Y=y=>margin.top+(ymax-y)/(ymax-ymin)*ph;
ctx.lineWidth=1;ctx.strokeStyle='#e0e8ef';
for(let i=0;i<=4;i++){let yy=margin.top+i*ph/4;ctx.beginPath();ctx.moveTo(margin.left,yy);ctx.lineTo(width-margin.right,yy);ctx.stroke();ctx.fillStyle='#64798e';ctx.textAlign='right';ctx.fillText((ymax-i*(ymax-ymin)/4).toExponential(1),margin.left-6,yy+4);}
ctx.fillStyle='#64798e';ctx.textAlign='center';
for(let i=0;i<=4;i++){const xx=margin.left+i*pw/4;ctx.fillText((xmin+i*(xmax-xmin)/4).toFixed(3),xx,height-21);}
ctx.fillText('Frequency (MHz)',margin.left+pw/2,height-5);
ctx.save();ctx.beginPath();ctx.rect(margin.left,margin.top,pw,ph);ctx.clip();
for(const k of series){const a=spectrum.lines[k];ctx.strokeStyle=colors[k];ctx.lineWidth=k==='data'||k==='total_fit'?1.8:1;ctx.beginPath();let started=false;
for(let i=0;i<a.length;i++){const y=a[i];if(y===null){started=false;continue;}const x=X(xs[i]),yy=Y(y);if(!started){ctx.moveTo(x,yy);started=true;}else ctx.lineTo(x,yy);}ctx.stroke();}
ctx.restore();}
function setChannel(id,state){const s=state.spectrum;const badge=document.getElementById(id+'-status');
badge.className='status '+(state.fresh?'ok':'off');badge.textContent=state.fresh?'LIVE':state.connected?'STALE':'DISCONNECTED';
const info=document.getElementById(id+'-info');info.replaceChildren();
let labels=s?[`Received: ${state.received}`,`Points: ${s.npts}`,`Mode: ${s.fit_type||'—'}`,`P: ${s.P_fit===null?'—':s.P_fit.toFixed(4)}`,`Q: ${s.Q_fit===null?'—':s.Q_fit.toFixed(4)}`,`Source ts: ${s.ts||'—'}`,`Age: ${state.age_seconds===null?'—':state.age_seconds+'s'}`]:[`No fitted message received`,state.error||''];
for(const l of labels){let el=document.createElement('span');el.textContent=l;info.appendChild(el);}
draw(document.getElementById(id+'-plot'),s);
}
function renderLegend(id){const root=document.getElementById(id+'-legend');for(const k of order){const item=document.createElement('label');const cb=document.createElement('input');cb.type='checkbox';cb.checked=shown.has(k);cb.addEventListener('change',()=>{if(cb.checked)shown.add(k);else shown.delete(k);});const dot=document.createElement('span');dot.className='dot';dot.style.background=colors[k];item.append(cb,dot,document.createTextNode(names[k]));root.append(item);}}
renderLegend('pynmr');renderLegend('labview');
async function refresh(){try{const r=await fetch('/api/state',{cache:'no-store'});if(!r.ok)throw Error('HTTP '+r.status);const data=await r.json();for(const id of ['pynmr','labview'])setChannel(id,data[id]);document.getElementById('poll').textContent='Monitoring · '+new Date().toLocaleTimeString();}catch(e){document.getElementById('poll').textContent='Monitor unreachable: '+e.message;}}
setInterval(refresh,2000);refresh();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    state = None

    def do_GET(self):
        if self.path == "/api/state":
            body = json.dumps(self.state.snapshot(), allow_nan=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif self.path == "/":
            body = HTML.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18880, help="loopback HTTP port (default: 18880)")
    args = parser.parse_args()
    state = MonitorState()
    stop = threading.Event()
    for key, (_, port) in CHANNELS.items():
        threading.Thread(target=consume, args=(key, port, state, stop), daemon=True, name=f"monitor-{key}").start()
    Handler.state = state
    try:
        with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as httpd:
            print(f"[READY] Dual NMR monitor http://127.0.0.1:{args.port}", flush=True)
            print("[READ ONLY] PyNMR publisher :8778 / LabVIEW SSH publisher :18778", flush=True)
            httpd.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
