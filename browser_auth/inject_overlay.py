"""
inject_overlay.py
=================
Injects the C3 rich action overlay + clipboard bridge into noVNC's index.html.
Called once by entrypoint.sh after the noVNC web root is prepared.

Features injected:
  - Rich status panel: M365 session, cookie extraction progress, pool stats
  - Macro buttons: Auto-Login M365, Auto-Login Consumer, Clear Cache, Screenshot, Pool Reload
  - Bidirectional clipboard bridge: host→VNC via /api/clipboard/push, VNC→host polling /api/clipboard/pull
"""
import re
import sys
from pathlib import Path

INDEX = Path("/tmp/novnc-web/index.html")
SENTINEL = "MACRO-OVERLAY-V2"

if not INDEX.exists():
    print("[inject_overlay] WARNING: index.html not found — skipping overlay inject")
    sys.exit(0)

text = INDEX.read_text(encoding="utf-8")

# Remove old v1 overlay if present
if "MACRO-OVERLAY-->" in text and SENTINEL not in text:
    text = re.sub(
        r"<!-- MACRO-OVERLAY -->.*?</script>\s*</body>",
        "</body>",
        text,
        flags=re.DOTALL,
    )

if SENTINEL in text:
    print("[inject_overlay] Rich overlay already present — skipping")
    sys.exit(0)

if "</body>" not in text:
    print("[inject_overlay] WARNING: no </body> in index.html — skipping")
    sys.exit(0)

OVERLAY = r"""
<!-- MACRO-OVERLAY-V2 -->
<style>
#c3-overlay{position:fixed;top:12px;right:12px;z-index:9999;width:270px;
  background:rgba(10,10,14,0.93);border:1px solid rgba(255,255,255,0.12);
  border-radius:12px;color:#f5f5f7;font-family:system-ui,sans-serif;font-size:13px;
  backdrop-filter:blur(16px);box-shadow:0 8px 32px rgba(0,0,0,0.6);
  transition:opacity .2s ease;user-select:none;}
#c3-overlay.collapsed #c3-ov-body{display:none;}
#c3-ov-header{display:flex;align-items:center;justify-content:space-between;
  padding:9px 12px;cursor:pointer;border-bottom:1px solid rgba(255,255,255,0.07);
  border-radius:12px 12px 0 0;}
#c3-ov-title{font-weight:700;font-size:11px;letter-spacing:.05em;
  text-transform:uppercase;color:rgba(255,255,255,0.55);}
#c3-ov-dot{width:9px;height:9px;border-radius:50%;background:#8e8e93;flex-shrink:0;margin-left:6px;}
#c3-ov-dot.active{background:#30d158;}
#c3-ov-dot.expired{background:#ff453a;animation:ov-pulse 1.2s ease-in-out infinite;}
#c3-ov-dot.unknown{background:#ffa000;}
@keyframes ov-pulse{0%,100%{opacity:1}50%{opacity:.3}}
#c3-ov-collapse{background:none;border:none;color:rgba(255,255,255,0.35);
  cursor:pointer;font-size:15px;line-height:1;padding:0 2px;}
#c3-ov-body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:7px;}
.ov-sep{border:none;border-top:1px solid rgba(255,255,255,0.07);margin:2px 0;}
.ov-lbl{font-size:10px;text-transform:uppercase;letter-spacing:.06em;
  color:rgba(255,255,255,0.38);margin-bottom:3px;}
.ov-val{font-size:12px;color:#f5f5f7;word-break:break-word;line-height:1.45;}
.ov-val.ok{color:#30d158;}.ov-val.warn{color:#ffa000;}.ov-val.fail{color:#ff453a;}
.ov-grid2{display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ov-btn{padding:6px 8px;border-radius:7px;border:1px solid rgba(255,255,255,0.12);
  background:rgba(255,255,255,0.07);color:#f5f5f7;font-size:11px;font-family:inherit;
  cursor:pointer;font-weight:500;text-align:center;transition:all .15s ease;white-space:nowrap;}
.ov-btn:hover{background:rgba(255,255,255,0.13);border-color:rgba(255,255,255,0.25);}
.ov-btn.primary{background:rgba(10,132,255,0.22);border-color:rgba(10,132,255,0.45);color:#64b5ff;}
.ov-btn.primary:hover{background:rgba(10,132,255,0.38);}
.ov-btn.danger{background:rgba(255,69,58,0.14);border-color:rgba(255,69,58,0.38);color:#ff6b6b;}
.ov-btn.danger:hover{background:rgba(255,69,58,0.26);}
.ov-btn:disabled{opacity:.4;cursor:not-allowed;}
#ov-msg{font-size:11px;color:#ffa000;min-height:13px;text-align:center;padding-top:4px;}
#ov-ss-wrap{display:none;margin-top:3px;}
#ov-ss-img{width:100%;border-radius:6px;border:1px solid rgba(255,255,255,0.1);cursor:pointer;}
.ov-clip-row{display:flex;gap:5px;align-items:stretch;}
.ov-clip-input{flex:1;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.12);
  border-radius:6px;color:#f5f5f7;font-size:11px;padding:5px 7px;font-family:monospace;
  resize:none;min-height:34px;}
.ov-clip-input:focus{outline:none;border-color:rgba(10,132,255,0.5);}
.ov-clip-btns{display:flex;flex-direction:column;gap:4px;}
</style>

<div id="c3-overlay">
  <div id="c3-ov-header" onclick="ovToggle()">
    <span id="c3-ov-title">&#129302; C3 Browser Auth</span>
    <span style="display:flex;align-items:center;gap:4px;">
      <span id="c3-ov-dot"></span>
      <button id="c3-ov-collapse" onclick="event.stopPropagation();ovToggle()" title="Collapse">&#8211;</button>
    </span>
  </div>
  <div id="c3-ov-body">

    <!-- Session + extraction status -->
    <div>
      <div class="ov-lbl">M365 Session</div>
      <div class="ov-val" id="ov-session">Checking&#8230;</div>
    </div>
    <div>
      <div class="ov-lbl">Cookie Extraction</div>
      <div class="ov-val" id="ov-extract">Checking&#8230;</div>
    </div>
    <div class="ov-grid2">
      <div>
        <div class="ov-lbl">Pool</div>
        <div class="ov-val" id="ov-pool">—</div>
      </div>
      <div>
        <div class="ov-lbl">Last checked</div>
        <div class="ov-val" id="ov-ts">—</div>
      </div>
    </div>

    <hr class="ov-sep">

    <!-- Macro buttons -->
    <div>
      <div class="ov-lbl">Quick Actions</div>
      <div class="ov-grid2" style="margin-top:4px;">
        <button class="ov-btn primary" onclick="runMacro('auto-login-m365')">&#128640; M365 Login</button>
        <button class="ov-btn primary" onclick="runMacro('auto-login-consumer')">&#128640; Consumer</button>
        <button class="ov-btn danger"  onclick="runMacro('clear-cache')">&#128465; Clear Cache</button>
        <button class="ov-btn"         onclick="runMacro('pool-reload')">&#8635; Reload Pool</button>
      </div>
      <div style="margin-top:6px;display:flex;gap:6px;">
        <button class="ov-btn" style="flex:1" onclick="runExtract()">&#128273; Extract Cookies</button>
        <button class="ov-btn" style="flex:1" onclick="runScreenshot()">&#128247; Screenshot</button>
      </div>
    </div>

    <!-- Screenshot preview -->
    <div id="ov-ss-wrap">
      <div class="ov-lbl">Last Screenshot <span style="float:right;cursor:pointer;color:#0a84ff" onclick="document.getElementById('ov-ss-wrap').style.display='none'">&#10005;</span></div>
      <img id="ov-ss-img" src="" alt="screenshot" title="Click to open full size" onclick="window.open(this.src)">
    </div>

    <hr class="ov-sep">

    <!-- Clipboard bridge -->
    <div>
      <div class="ov-lbl">Clipboard Bridge (host &#8596; VNC)</div>
      <div class="ov-clip-row" style="margin-top:4px;">
        <textarea class="ov-clip-input" id="ov-clip-text" placeholder="Paste text here to push into VNC&#8230;" rows="2"></textarea>
        <div class="ov-clip-btns">
          <button class="ov-btn primary" style="font-size:10px;padding:4px 7px;" onclick="clipPush()" title="Push host text into VNC clipboard">&#8593; Push</button>
          <button class="ov-btn" style="font-size:10px;padding:4px 7px;" onclick="clipPull()" title="Pull VNC clipboard to host">&#8595; Pull</button>
        </div>
      </div>
    </div>

    <div id="ov-msg"></div>
  </div>
</div>

<script>
(function(){
  var C3 = 'http://localhost:8001';
  var _collapsed = false;

  function ovToggle() {
    _collapsed = !_collapsed;
    var el = document.getElementById('c3-overlay');
    var btn = document.getElementById('c3-ov-collapse');
    if (_collapsed) { el.classList.add('collapsed'); btn.textContent = '+'; }
    else { el.classList.remove('collapsed'); btn.textContent = '\u2013'; }
  }
  window.ovToggle = ovToggle;

  function ovMsg(txt, ok) {
    var el = document.getElementById('ov-msg');
    if (!el) return;
    el.textContent = txt;
    el.style.color = ok === true ? '#30d158' : ok === false ? '#ff453a' : '#ffa000';
    clearTimeout(el._t);
    el._t = setTimeout(function(){ el.textContent = ''; }, 5000);
  }

  function setDot(state) {
    var dot = document.getElementById('c3-ov-dot');
    if (!dot) return;
    dot.className = state;
  }

  function refreshStatus() {
    // Session + pool status
    fetch(C3 + '/status').then(function(r){ return r.json(); }).then(function(d) {
      var poolEl = document.getElementById('ov-pool');
      if (poolEl) {
        if (d.pool_initialized) {
          poolEl.textContent = (d.pool_available||0) + '/' + (d.pool_size||0) + ' free';
          poolEl.className = 'ov-val ok';
        } else {
          poolEl.textContent = 'Not initialized';
          poolEl.className = 'ov-val warn';
        }
      }
    }).catch(function(){});

    // Session health
    fetch(C3 + '/session-health').then(function(r){ return r.json(); }).then(function(d) {
      var el = document.getElementById('ov-session');
      if (!el) return;
      var s = d.session || 'unknown';
      if (s === 'active') {
        el.textContent = 'Active \u2022 ' + (d.profile || '');
        el.className = 'ov-val ok';
        setDot('active');
      } else if (s === 'expired') {
        el.textContent = 'Expired \u2014 sign in via noVNC';
        el.className = 'ov-val fail';
        setDot('expired');
      } else {
        el.textContent = 'Unknown \u2014 ' + (d.reason || '');
        el.className = 'ov-val warn';
        setDot('unknown');
      }
      var ts = document.getElementById('ov-ts');
      if (ts) ts.textContent = new Date().toLocaleTimeString();
    }).catch(function(){
      var el = document.getElementById('ov-session');
      if (el) { el.textContent = 'C3 unreachable'; el.className = 'ov-val fail'; }
      setDot('expired');
    });

    // Auth progress (extraction state)
    fetch(C3 + '/auth-progress').then(function(r){ return r.json(); }).then(function(d) {
      var el = document.getElementById('ov-extract');
      if (!el) return;
      var phase = d.phase || 'idle';
      var msg = d.message || phase;
      if (phase === 'ok') { el.textContent = 'Done \u2714 ' + msg; el.className = 'ov-val ok'; }
      else if (phase === 'error') { el.textContent = 'Error: ' + msg; el.className = 'ov-val fail'; }
      else if (phase === 'idle') { el.textContent = 'Idle \u2014 click Extract Cookies'; el.className = 'ov-val'; }
      else { el.textContent = msg; el.className = 'ov-val warn'; }
    }).catch(function(){});
  }

  // Run a macro via C3 API
  function runMacro(action) {
    ovMsg('Running ' + action + '\u2026');
    fetch(C3 + '/api/macro', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: action})
    }).then(function(r){ return r.json(); }).then(function(d) {
      ovMsg(d.message || d.status || 'Done', d.status === 'ok');
      setTimeout(refreshStatus, 1500);
    }).catch(function(e){ ovMsg('Error: ' + e.message, false); });
  }
  window.runMacro = runMacro;

  // Extract cookies
  function runExtract() {
    ovMsg('Extracting cookies\u2026');
    var el = document.getElementById('ov-extract');
    if (el) { el.textContent = 'Extracting\u2026'; el.className = 'ov-val warn'; }
    fetch(C3 + '/extract', {method: 'POST'}).then(function(r){ return r.json(); }).then(function(d) {
      if (d.status === 'ok') {
        ovMsg('Cookies extracted \u2714', true);
      } else {
        ovMsg('Extract failed: ' + (d.message || d.status), false);
      }
      setTimeout(refreshStatus, 1000);
    }).catch(function(e){ ovMsg('Extract error: ' + e.message, false); });
  }
  window.runExtract = runExtract;

  // Screenshot
  function runScreenshot() {
    ovMsg('Capturing screenshot\u2026');
    fetch(C3 + '/api/macro', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({action: 'screenshot'})
    }).then(function(r){ return r.json(); }).then(function(d) {
      if (d.status === 'ok' && d.image) {
        var img = document.getElementById('ov-ss-img');
        var wrap = document.getElementById('ov-ss-wrap');
        if (img && wrap) { img.src = d.image; wrap.style.display = 'block'; }
        ovMsg('Screenshot captured', true);
      } else {
        ovMsg('Screenshot failed: ' + (d.message || ''), false);
      }
    }).catch(function(e){ ovMsg('Screenshot error: ' + e.message, false); });
  }
  window.runScreenshot = runScreenshot;

  // Clipboard push (host -> VNC)
  function clipPush() {
    var txt = (document.getElementById('ov-clip-text') || {}).value || '';
    if (!txt) { ovMsg('Nothing to push \u2014 type text first'); return; }
    fetch(C3 + '/api/clipboard/push', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: txt})
    }).then(function(r){ return r.json(); }).then(function(d) {
      ovMsg(d.status === 'ok' ? 'Pushed ' + d.bytes_written + ' bytes to VNC' : 'Push failed: ' + d.message,
            d.status === 'ok');
    }).catch(function(e){ ovMsg('Push error: ' + e.message, false); });
  }
  window.clipPush = clipPush;

  // Clipboard pull (VNC -> host)
  function clipPull() {
    fetch(C3 + '/api/clipboard/pull').then(function(r){ return r.json(); }).then(function(d) {
      if (d.status === 'ok') {
        var inp = document.getElementById('ov-clip-text');
        if (inp) inp.value = d.text || '';
        if (d.text && navigator.clipboard) {
          navigator.clipboard.writeText(d.text).catch(function(){});
        }
        ovMsg(d.text ? 'Pulled ' + d.text.length + ' chars from VNC' : 'VNC clipboard is empty', !!d.text);
      } else {
        ovMsg('Pull failed: ' + d.message, false);
      }
    }).catch(function(e){ ovMsg('Pull error: ' + e.message, false); });
  }
  window.clipPull = clipPull;

  // Auto-push host clipboard to VNC on window focus (enhanced bridge)
  window.addEventListener('focus', function() {
    if (navigator.clipboard && navigator.clipboard.readText) {
      navigator.clipboard.readText().then(function(t) {
        if (!t) return;
        fetch(C3 + '/api/clipboard/push', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({text: t})
        }).catch(function(){});
      }).catch(function(){});
    }
  });

  // Poll status every 5s
  refreshStatus();
  setInterval(refreshStatus, 5000);
})();
</script>
</body>"""

text = text.replace("</body>", OVERLAY, 1)
INDEX.write_text(text, encoding="utf-8")
print("[inject_overlay] Rich overlay v2 injected into noVNC index.html")
