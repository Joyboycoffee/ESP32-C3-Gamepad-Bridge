"""
EvoFox One S Gamepad Web Visualizer (ESP32-C3 Wireless Bridge)
Serves a high-performance 60 FPS interactive HTML5/SVG gamepad on http://localhost:8080
"""

import sys
import time
import json
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import serial

PORT = "COM8"
BAUD = 115200
HTTP_PORT = 8080

class GamepadState:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = {
            "connected": False,
            "lx": 128, "ly": 128,
            "rx": 128, "ry": 128,
            "lt_ana": 0, "rt_ana": 0,
            "a": False, "b": False, "x": False, "y": False,
            "lb": False, "rb": False,
            "lt": False, "rt": False,
            "ls": False, "rs": False,
            "up": False, "down": False, "left": False, "right": False,
            "select": False, "start": False, "home": False,
            "raw": "80 80 80 80 FF 00 00 00 00 1E",
            "pkts": 0
        }

state = GamepadState()

def serial_reader_thread():
    """Background thread reading from ESP32-C3 on COM8."""
    while True:
        s = None
        try:
            s = serial.Serial()
            s.port = PORT
            s.baudrate = BAUD
            s.timeout = 0.2
            s.dtr = True
            s.rts = False
            s.open()
            print(f"[SERIAL] Connected to {PORT} at {BAUD} baud.", flush=True)
            with state.lock:
                state.data["connected"] = True

            while True:
                line = s.readline()
                if not line:
                    continue
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue

                if "[REPORT 10-byte]:" in text or "[NOTIF h=27 len=10]:" in text:
                    parts = text.split("]:")
                    if len(parts) >= 2:
                        bytes_str = parts[1].strip().split()
                        if len(bytes_str) == 10:
                            try:
                                b = [int(x, 16) for x in bytes_str]
                                hat = b[4]
                                with state.lock:
                                    state.data["connected"] = True
                                    state.data["raw"] = " ".join(f"{x:02X}" for x in b)
                                    state.data["pkts"] += 1
                                    # Sticks
                                    state.data["lx"] = b[0]
                                    state.data["ly"] = b[1]
                                    state.data["rx"] = b[2]
                                    state.data["ry"] = b[3]
                                    # D-Pad
                                    state.data["up"]    = (hat == 0 or hat == 1 or hat == 7)
                                    state.data["right"] = (hat == 1 or hat == 2 or hat == 3)
                                    state.data["down"]  = (hat == 3 or hat == 4 or hat == 5)
                                    state.data["left"]  = (hat == 5 or hat == 6 or hat == 7)
                                    # Buttons 1
                                    state.data["a"]  = bool(b[5] & 0x01)
                                    state.data["b"]  = bool(b[5] & 0x02)
                                    state.data["x"]  = bool(b[5] & 0x08)
                                    state.data["y"]  = bool(b[5] & 0x10)
                                    state.data["lb"] = bool(b[5] & 0x40)
                                    state.data["rb"] = bool(b[5] & 0x80)
                                    # Buttons 2
                                    state.data["lt"]     = bool(b[6] & 0x01) or (b[8] > 40)
                                    state.data["rt"]     = bool(b[6] & 0x02) or (b[7] > 40)
                                    state.data["select"] = bool(b[6] & 0x04)
                                    state.data["start"]  = bool(b[6] & 0x08)
                                    state.data["home"]   = bool(b[6] & 0x10)
                                    state.data["ls"]     = bool(b[6] & 0x20)
                                    state.data["rs"]     = bool(b[6] & 0x40)
                                    # Triggers
                                    state.data["rt_ana"] = b[7]
                                    state.data["lt_ana"] = b[8]
                            except Exception:
                                pass
        except serial.SerialException as se:
            print(f"[SERIAL] Exception: {se}", flush=True)
            with state.lock:
                state.data["connected"] = False
            if s:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(1.0)
        except Exception as ex:
            print(f"[SERIAL] Unexpected error: {ex}", flush=True)
            with state.lock:
                state.data["connected"] = False
            if s:
                try:
                    s.close()
                except Exception:
                    pass
            time.sleep(1.0)

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EvoFox One S - Gamepad Visualizer</title>
<style>
  :root {
    --bg: #0e111a;
    --card: #161b26;
    --card-border: #283144;
    --chassis: #202636;
    --chassis-border: #36425c;
    --cyan: #00e5ff;
    --green: #00e676;
    --red: #ff1744;
    --blue: #2979ff;
    --yellow: #ffd600;
    --purple: #e040fb;
    --text: #f0f4fc;
    --text-muted: #8391a8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 24px;
    min-height: 100vh;
  }
  .header {
    width: 100%;
    max-width: 960px;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 16px 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }
  .status-badge {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 600;
    font-size: 15px;
  }
  .dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: #555;
    transition: all 0.2s ease;
  }
  .dot.online {
    background: var(--green);
    box-shadow: 0 0 14px var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.6; transform: scale(1.15); }
  }
  .title-group h1 { font-size: 20px; color: var(--cyan); letter-spacing: 0.5px; }
  .title-group p { font-size: 12px; color: var(--text-muted); }

  .main-stage {
    width: 100%;
    max-width: 960px;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 20px;
    padding: 30px;
    display: flex;
    justify-content: center;
    align-items: center;
    position: relative;
    box-shadow: 0 12px 32px rgba(0,0,0,0.4);
  }
  svg {
    width: 100%;
    max-width: 860px;
    height: auto;
    filter: drop-shadow(0 10px 20px rgba(0,0,0,0.5));
  }

  /* Gamepad SVG styling */
  .btn-base {
    transition: fill 0.05s ease, filter 0.05s ease;
  }
  .btn-active-a { fill: var(--green) !important; filter: drop-shadow(0 0 10px var(--green)); }
  .btn-active-b { fill: var(--red) !important; filter: drop-shadow(0 0 10px var(--red)); }
  .btn-active-x { fill: var(--blue) !important; filter: drop-shadow(0 0 10px var(--blue)); }
  .btn-active-y { fill: var(--yellow) !important; filter: drop-shadow(0 0 10px var(--yellow)); }
  .btn-active-cyan { fill: var(--cyan) !important; filter: drop-shadow(0 0 10px var(--cyan)); }
  .btn-active-purple { fill: var(--purple) !important; filter: drop-shadow(0 0 10px var(--purple)); }
  .btn-active-white { fill: #fff !important; filter: drop-shadow(0 0 8px #fff); }

  .stick-puck {
    transition: transform 0.03s linear, fill 0.05s ease;
    cursor: grab;
  }
  .stick-puck.pressed {
    fill: var(--cyan) !important;
    filter: drop-shadow(0 0 12px var(--cyan));
  }

  .hud {
    width: 100%;
    max-width: 960px;
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 16px;
    padding: 16px 24px;
    margin-top: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.3);
  }
  .hud-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 14px;
  }
  .hud-label { color: var(--text-muted); font-size: 13px; font-weight: 500; }
  .hud-value { font-family: "Consolas", monospace; font-weight: bold; color: var(--cyan); }
  .active-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 4px;
  }
  .tag {
    background: rgba(0, 229, 255, 0.15);
    border: 1px solid var(--cyan);
    color: var(--cyan);
    padding: 4px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: bold;
    letter-spacing: 0.5px;
  }
</style>
</head>
<body>

<div class="header">
  <div class="status-badge">
    <div id="dot" class="dot"></div>
    <span id="conn-text">Connecting to ESP32-C3...</span>
  </div>
  <div class="title-group" style="text-align: right;">
    <h1>EvoFox One S</h1>
    <p>Zero-Latency ESP32-C3 BLE-to-UART Bridge</p>
  </div>
</div>

<div class="main-stage">
  <svg viewBox="0 0 800 520" xmlns="http://www.w3.org/2000/svg">
    <!-- Trigger Gauges -->
    <!-- LT Gauge -->
    <rect x="50" y="80" width="46" height="130" rx="8" fill="#1b202c" stroke="#2c3447" stroke-width="2"/>
    <rect id="lt-bar" x="53" y="177" width="40" height="30" rx="6" fill="var(--cyan)"/>
    <text x="73" y="65" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">LT</text>
    <text id="lt-val" x="73" y="230" fill="var(--cyan)" font-size="13" font-family="monospace" text-anchor="middle">0%</text>

    <!-- RT Gauge -->
    <rect x="704" y="80" width="46" height="130" rx="8" fill="#1b202c" stroke="#2c3447" stroke-width="2"/>
    <rect id="rt-bar" x="707" y="177" width="40" height="30" rx="6" fill="var(--red)"/>
    <text x="727" y="65" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">RT</text>
    <text id="rt-val" x="727" y="230" fill="var(--red)" font-size="13" font-family="monospace" text-anchor="middle">0%</text>

    <!-- Gamepad Outer Body -->
    <path d="M 230 110 C 320 95, 480 95, 570 110 C 640 120, 710 180, 680 370 C 660 490, 560 480, 500 420 C 450 370, 350 370, 300 420 C 240 480, 140 490, 120 370 C 90 180, 160 120, 230 110 Z"
          fill="var(--chassis)" stroke="var(--chassis-border)" stroke-width="4"/>

    <!-- Left Bumper (LB) -->
    <rect id="btn-lb" x="210" y="90" width="95" height="32" rx="8" fill="#2d354a" stroke="#414c68" stroke-width="2"/>
    <text x="257" y="112" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">LB</text>

    <!-- Right Bumper (RB) -->
    <rect id="btn-rb" x="495" y="90" width="95" height="32" rx="8" fill="#2d354a" stroke="#414c68" stroke-width="2"/>
    <text x="542" y="112" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">RB</text>

    <!-- Left Stick Radar -->
    <g transform="translate(260, 230)">
      <circle r="46" fill="#171b26" stroke="#313b52" stroke-width="2"/>
      <line x1="-46" y1="0" x2="46" y2="0" stroke="#252d40" stroke-width="1"/>
      <line x1="0" y1="-46" x2="0" y2="46" stroke="#252d40" stroke-width="1"/>
      <circle id="stick-l" class="stick-puck" cx="0" cy="0" r="24" fill="#3b4661" stroke="var(--cyan)" stroke-width="2"/>
      <circle id="stick-l-nub" cx="0" cy="0" r="6" fill="#8898b8"/>
      <text id="lbl-lstick" x="0" y="65" fill="var(--text-muted)" font-size="11" text-anchor="middle">L: (128, 128)</text>
    </g>

    <!-- Right Stick Radar -->
    <g transform="translate(480, 320)">
      <circle r="46" fill="#171b26" stroke="#313b52" stroke-width="2"/>
      <line x1="-46" y1="0" x2="46" y2="0" stroke="#252d40" stroke-width="1"/>
      <line x1="0" y1="-46" x2="0" y2="46" stroke="#252d40" stroke-width="1"/>
      <circle id="stick-r" class="stick-puck" cx="0" cy="0" r="24" fill="#3b4661" stroke="var(--cyan)" stroke-width="2"/>
      <circle id="stick-r-nub" cx="0" cy="0" r="6" fill="#8898b8"/>
      <text id="lbl-rstick" x="0" y="65" fill="var(--text-muted)" font-size="11" text-anchor="middle">R: (128, 128)</text>
    </g>

    <!-- D-Pad -->
    <g transform="translate(320, 320)">
      <!-- Center -->
      <rect x="-14" y="-14" width="28" height="28" fill="#2d354a"/>
      <!-- UP -->
      <path id="btn-up" class="btn-base" d="M -13 -14 L 13 -14 L 13 -40 Q 13 -44 9 -44 L -9 -44 Q -13 -44 -13 -40 Z" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
      <text x="0" y="-24" fill="#8898b8" font-size="12" text-anchor="middle">▲</text>
      <!-- DOWN -->
      <path id="btn-down" class="btn-base" d="M -13 14 L 13 14 L 13 40 Q 13 44 9 44 L -9 44 Q -13 44 -13 40 Z" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
      <text x="0" y="32" fill="#8898b8" font-size="12" text-anchor="middle">▼</text>
      <!-- LEFT -->
      <path id="btn-left" class="btn-base" d="M -14 -13 L -14 13 L -40 13 Q -44 13 -44 9 L -44 -9 Q -44 -13 -40 -13 Z" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
      <text x="-26" y="4" fill="#8898b8" font-size="12" text-anchor="middle">◀</text>
      <!-- RIGHT -->
      <path id="btn-right" class="btn-base" d="M 14 -13 L 14 13 L 40 13 Q 44 13 44 9 L 44 -9 Q 44 -13 40 -13 Z" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
      <text x="28" y="4" fill="#8898b8" font-size="12" text-anchor="middle">▶</text>
    </g>

    <!-- ABXY Face Buttons -->
    <g transform="translate(540, 230)">
      <!-- Y (Top) -->
      <circle id="btn-y" class="btn-base" cx="0" cy="-36" r="18" fill="#3a3410" stroke="#524a18" stroke-width="2"/>
      <text x="0" y="-30" fill="#ffd600" font-size="15" font-weight="bold" text-anchor="middle">Y</text>
      <!-- A (Bottom) -->
      <circle id="btn-a" class="btn-base" cx="0" cy="36" r="18" fill="#103a1f" stroke="#18522e" stroke-width="2"/>
      <text x="0" y="42" fill="#00e676" font-size="15" font-weight="bold" text-anchor="middle">A</text>
      <!-- X (Left) -->
      <circle id="btn-x" class="btn-base" cx="-36" cy="0" r="18" fill="#12243d" stroke="#18365c" stroke-width="2"/>
      <text x="-36" y="6" fill="#2979ff" font-size="15" font-weight="bold" text-anchor="middle">X</text>
      <!-- B (Right) -->
      <circle id="btn-b" class="btn-base" cx="36" cy="0" r="18" fill="#3d1419" stroke="#5c1a21" stroke-width="2"/>
      <text x="36" y="6" fill="#ff1744" font-size="15" font-weight="bold" text-anchor="middle">B</text>
    </g>

    <!-- Center Navigation Buttons -->
    <!-- SELECT / BACK -->
    <rect id="btn-select" x="350" y="210" width="30" height="16" rx="6" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
    <text x="365" y="200" fill="var(--text-muted)" font-size="10" font-weight="bold" text-anchor="middle">BACK</text>

    <!-- HOME / GUIDE -->
    <circle id="btn-home" cx="400" cy="180" r="18" fill="#263044" stroke="var(--green)" stroke-width="2.5"/>
    <text x="400" y="186" fill="#fff" font-size="14" font-weight="bold" text-anchor="middle">X</text>

    <!-- START -->
    <rect id="btn-start" x="420" y="210" width="30" height="16" rx="6" fill="#2d354a" stroke="#414c68" stroke-width="1.5"/>
    <text x="435" y="200" fill="var(--text-muted)" font-size="10" font-weight="bold" text-anchor="middle">START</text>
  </svg>
</div>

<div class="hud">
  <div class="hud-row">
    <span class="hud-label">ACTIVE CONTROLS:</span>
    <div id="tags" class="active-tags">
      <span class="tag" style="border-color:#555; color:#888;">NEUTRAL / RELEASED</span>
    </div>
  </div>
  <div class="hud-row" style="margin-top: 8px;">
    <span class="hud-label">10-BYTE HID PACKET:</span>
    <span id="raw-hex" class="hud-value" style="color: var(--yellow);">80 80 80 80 FF 00 00 00 00 1E</span>
  </div>
</div>

<script>
  const dot = document.getElementById('dot');
  const connText = document.getElementById('conn-text');
  const rawHex = document.getElementById('raw-hex');
  const tagsContainer = document.getElementById('tags');

  // Trigger Bars
  const ltBar = document.getElementById('lt-bar');
  const ltVal = document.getElementById('lt-val');
  const rtBar = document.getElementById('rt-bar');
  const rtVal = document.getElementById('rt-val');

  // Sticks
  const stickL = document.getElementById('stick-l');
  const stickLNub = document.getElementById('stick-l-nub');
  const stickR = document.getElementById('stick-r');
  const stickRNub = document.getElementById('stick-r-nub');
  const lblLStick = document.getElementById('lbl-lstick');
  const lblRStick = document.getElementById('lbl-rstick');

  // Buttons
  const btnA = document.getElementById('btn-a');
  const btnB = document.getElementById('btn-b');
  const btnX = document.getElementById('btn-x');
  const btnY = document.getElementById('btn-y');
  const btnLb = document.getElementById('btn-lb');
  const btnRb = document.getElementById('btn-rb');
  const btnUp = document.getElementById('btn-up');
  const btnDown = document.getElementById('btn-down');
  const btnLeft = document.getElementById('btn-left');
  const btnRight = document.getElementById('btn-right');
  const btnSelect = document.getElementById('btn-select');
  const btnStart = document.getElementById('btn-start');
  const btnHome = document.getElementById('btn-home');

  function updateUI(s) {
    if (s.connected) {
      dot.className = "dot online";
      connText.innerText = "ONLINE: Connected to ESP32-C3 (COM8)";
      connText.style.color = "var(--green)";
    } else {
      dot.className = "dot";
      connText.innerText = "DISCONNECTED: Connecting to COM8...";
      connText.style.color = "var(--red)";
    }

    rawHex.innerText = s.raw || "--";

    // Sticks
    const normLx = (s.lx - 128) / 128.0;
    const normLy = (s.ly - 128) / 128.0;
    const pxL = normLx * 26;
    const pyL = normLy * 26;
    stickL.setAttribute('cx', pxL);
    stickL.setAttribute('cy', pyL);
    stickLNub.setAttribute('cx', pxL);
    stickLNub.setAttribute('cy', pyL);
    stickL.className.baseVal = s.ls ? "stick-puck pressed" : "stick-puck";
    lblLStick.innerText = `L: (${s.lx}, ${s.ly})`;

    const normRx = (s.rx - 128) / 128.0;
    const normRy = (s.ry - 128) / 128.0;
    const pxR = normRx * 26;
    const pyR = normRy * 26;
    stickR.setAttribute('cx', pxR);
    stickR.setAttribute('cy', pyR);
    stickRNub.setAttribute('cx', pxR);
    stickRNub.setAttribute('cy', pyR);
    stickR.className.baseVal = s.rs ? "stick-puck pressed" : "stick-puck";
    lblRStick.innerText = `R: (${s.rx}, ${s.ry})`;

    // Triggers (Analog gauges: max height 118, y goes from 86 down to 204)
    const ltPct = s.lt_ana / 255.0;
    const ltH = Math.max(0, Math.min(118, ltPct * 118));
    ltBar.setAttribute('y', 204 - ltH);
    ltBar.setAttribute('height', ltH);
    ltVal.innerText = `${Math.round(ltPct * 100)}%`;

    const rtPct = s.rt_ana / 255.0;
    const rtH = Math.max(0, Math.min(118, rtPct * 118));
    rtBar.setAttribute('y', 204 - rtH);
    rtBar.setAttribute('height', rtH);
    rtVal.innerText = `${Math.round(rtPct * 100)}%`;

    // ABXY
    btnA.className.baseVal = s.a ? "btn-base btn-active-a" : "btn-base";
    btnB.className.baseVal = s.b ? "btn-base btn-active-b" : "btn-base";
    btnX.className.baseVal = s.x ? "btn-base btn-active-x" : "btn-base";
    btnY.className.baseVal = s.y ? "btn-base btn-active-y" : "btn-base";

    // Shoulders
    btnLb.className.baseVal = s.lb ? "btn-base btn-active-cyan" : "btn-base";
    btnRb.className.baseVal = s.rb ? "btn-base btn-active-purple" : "btn-base";

    // D-Pad
    btnUp.className.baseVal = s.up ? "btn-base btn-active-a" : "btn-base";
    btnDown.className.baseVal = s.down ? "btn-base btn-active-a" : "btn-base";
    btnLeft.className.baseVal = s.left ? "btn-base btn-active-a" : "btn-base";
    btnRight.className.baseVal = s.right ? "btn-base btn-active-a" : "btn-base";

    // Menu
    btnSelect.className.baseVal = s.select ? "btn-base btn-active-white" : "btn-base";
    btnStart.className.baseVal = s.start ? "btn-base btn-active-white" : "btn-base";
    btnHome.className.baseVal = s.home ? "btn-base btn-active-cyan" : "btn-base";

    // Active Tags
    const active = [];
    if (s.a) active.push("A");
    if (s.b) active.push("B");
    if (s.x) active.push("X");
    if (s.y) active.push("Y");
    if (s.lb) active.push("LB");
    if (s.rb) active.push("RB");
    if (s.lt || s.lt_ana > 30) active.push(`LT (${Math.round(ltPct*100)}%)`);
    if (s.rt || s.rt_ana > 30) active.push(`RT (${Math.round(rtPct*100)}%)`);
    if (s.ls) active.push("L-CLICK");
    if (s.rs) active.push("R-CLICK");
    if (s.up) active.push("UP");
    if (s.down) active.push("DOWN");
    if (s.left) active.push("LEFT");
    if (s.right) active.push("RIGHT");
    if (s.select) active.push("BACK");
    if (s.start) active.push("START");
    if (s.home) active.push("HOME");

    if (active.length > 0) {
      tagsContainer.innerHTML = active.map(t => `<span class="tag">${t}</span>`).join('');
    } else {
      tagsContainer.innerHTML = `<span class="tag" style="border-color:#555; color:#888;">NEUTRAL / RELEASED</span>`;
    }
  }

  // Polling stream via Server-Sent Events or Fetch loop
  const evtSource = new EventSource('/stream');
  evtSource.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      updateUI(data);
    } catch(err) {}
  };
  evtSource.onerror = () => {
    // Fallback to fetch loop if SSE disconnects
    setTimeout(pollState, 100);
  };

  async function pollState() {
    try {
      const res = await fetch('/api/state');
      if (res.ok) {
        const data = await res.json();
        updateUI(data);
      }
    } catch(e) {}
    setTimeout(pollState, 33); // ~30 FPS fallback
  }
</script>
</body>
</html>
"""

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_CONTENT.encode("utf-8"))
        elif self.path == "/api/state":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with state.lock:
                payload = json.dumps(state.data)
            self.wfile.write(payload.encode("utf-8"))
        elif self.path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            last_pkts = -1
            try:
                while True:
                    with state.lock:
                        cur_pkts = state.data["pkts"]
                        cur_data = dict(state.data)
                    # Push on change or heartbeat every 200ms
                    payload = json.dumps(cur_data)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    time.sleep(0.016) # ~60 Hz
            except Exception:
                pass
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Silence default HTTP access logs
        return

def main():
    print(f"=== Starting EvoFox Web Visualizer on http://localhost:{HTTP_PORT} ===")
    t = threading.Thread(target=serial_reader_thread, daemon=True)
    t.start()

    server = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), RequestHandler)
    print(f"=== Web Visualizer running! Open http://localhost:{HTTP_PORT} in your browser ===")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

if __name__ == "__main__":
    main()
