/* steprtool page: device controls. */
"use strict";

const els = {
  sda100Freq: $("sda100-freq"),
  sda100Freq_btn: $("btn-sda100-freq"),
  sda100Direction: $("radio-sda100-direction"),
  sda100Home_btn: $("btn-sda100-home"),
  sda100Cal_btn:  $("btn-sda100-cal"),
  sda100Qry_btn:  $("btn-sda100-query"),
  sda100Port: $("sda100-port"),
  sda100Dot:  $("sda100-dot"),
  sda100Text: $("sda100-text"),
  sda100Cd:   $("sda100-countdown"),

  dcu2Az:      $("dcu2-az"),
  dcu2Go:      $("btn-dcu2-go"),
  dcu2Port:    $("dcu2-port"),
  dcu2Dot:     $("dcu2-dot"),
  dcu2Text:    $("dcu2-text"),
  dcu2Cd:      $("dcu2-countdown"),

  laTimestamp: $("la-timestamp"),
  laDevice:    $("la-device"),
  laAction:    $("la-action"),
  laOperator:  $("la-operator"),
  laStatus:    $("la-status"),
  laDetail:    $("la-detail"),
  laBytes:     $("la-bytes"),
  bigCountdown:$("big-countdown"),
};

/* ------------------------------------------------------- direction helpers */

function getSelectedDirection() {
  const checked = document.querySelector('input[name="sda100-direction"]:checked');
  return checked ? checked.value : "normal";
}
function setSelectedDirection(val) {
  const radio = document.querySelector(`input[name="sda100-direction"][value="${val}"]`);
  if (radio) radio.checked = true;
}

/* ----------------------------------------------------------- device state */

const deviceState = {
  sda100: { busy: false, mock: false, port: "—", seconds_remaining: 0, seconds_total: 0 },
  dcu2:    { busy: false, mock: false, port: "—", seconds_remaining: 0, seconds_total: 0 },
};

function formatMMSS(seconds) {
  seconds = Math.max(0, Math.floor(seconds));
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function renderDeviceRow(device) {
  const d = deviceState[device];
  const dot  = device === "sda100" ? els.sda100Dot  : els.dcu2Dot;
  const text = device === "sda100" ? els.sda100Text : els.dcu2Text;
  const cd   = device === "sda100" ? els.sda100Cd   : els.dcu2Cd;

  dot.classList.remove("busy", "mock");
  if (d.busy) {
    dot.classList.add("busy");
    text.textContent = "busy";
    cd.textContent = d.seconds_remaining > 0 ? `${d.seconds_remaining}s` : "";
  } else if (d.mock) {
    dot.classList.add("mock");
    text.textContent = "idle (mock)";
    cd.textContent = "";
  } else {
    text.textContent = "idle";
    cd.textContent = "";
  }

  if (device === "sda100") {
    els.sda100Qry_btn.disabled = d.busy;
    els.sda100Freq_btn.disabled = d.busy;
    els.sda100Home_btn.disabled = d.busy;
    els.sda100Cal_btn.disabled  = d.busy;
  } else {
    els.dcu2Go.disabled = d.busy;
  }
}

function renderBigCountdown() {
  const items = [];
  for (const dev of ["sda100", "dcu2"]) {
    const d = deviceState[dev];
    if (!d.busy || d.seconds_remaining <= 0) continue;
    const label = dev === "sda100" ? "SDA 100" : "DCU-2";
    items.push(
      `<div class="big-cd-item">
         <span class="big-cd-label">${label}</span>
         <span class="big-cd-time">${formatMMSS(d.seconds_remaining)}</span>
       </div>`
    );
  }
  els.bigCountdown.innerHTML = items.join("");
}

function applyDeviceSnapshot(snap) {
  const d = deviceState[snap.device];
  d.busy = !!snap.busy; d.mock = !!snap.mock; d.port = snap.port;
  d.seconds_remaining = snap.seconds_remaining || 0;
  d.seconds_total = snap.seconds_total || 0;
  const portLabel = snap.mock ? "MOCK" : snap.port;
  if (snap.device === "sda100") {
    els.sda100Port.textContent = `port ${portLabel}`;
    if (snap.direction) setSelectedDirection(snap.direction);
  } else {
    els.dcu2Port.textContent = `port ${portLabel}`;
  }
  renderDeviceRow(snap.device);
  renderBigCountdown();
}

/* -------------------------------------------------- inputs sync via socket */

const INPUT_HANDLERS = {
  sda100_freq:      (v) => { els.sda100Freq.value = v; },
  dcu2_az:           (v) => { els.dcu2Az.value = v; },
  sda100_direction: (v) => { setSelectedDirection(v); },
};

function applyLastAction(la) {
  if (!la) return;
  els.laTimestamp.textContent = la.timestamp || "—";
  els.laDevice.textContent    = la.device === "sda100" ? "SDA 100" : "DCU-2";
  els.laAction.textContent    = la.action || "—";
  els.laOperator.textContent  = la.operator || "—";
  els.laDetail.textContent    = la.detail || "—";
  els.laBytes.textContent     = la.bytes_hex || "—";

  els.laStatus.classList.remove("status-sent", "status-mock", "status-noimp", "status-err");
  const s = la.status || "";
  els.laStatus.textContent = s || "—";
  if (s === "SENT") els.laStatus.classList.add("status-sent");
  else if (s === "MOCK") els.laStatus.classList.add("status-mock");
  else if (s === "NOT IMPLEMENTED") els.laStatus.classList.add("status-noimp");
  else if (s === "ERROR") els.laStatus.classList.add("status-err");

  const inputs = la.inputs || {};
  for (const key in inputs) {
    const handler = INPUT_HANDLERS[key];
    if (handler) handler(inputs[key]);
  }
}

/* ----------------------------------------- socket events specific to this page */

window.onStateMessage = function(s) {
  if (s.sda100) applyDeviceSnapshot(s.sda100);
  if (s.dcu2)    applyDeviceSnapshot(s.dcu2);
  if (s.last_action) applyLastAction(s.last_action);
};

socket.on("device_locked", ({ device, seconds_remaining, seconds_total }) => {
  const d = deviceState[device];
  d.busy = true; d.seconds_remaining = seconds_remaining; d.seconds_total = seconds_total;
  renderDeviceRow(device); renderBigCountdown();
});
socket.on("device_countdown", ({ device, seconds_remaining }) => {
  deviceState[device].seconds_remaining = seconds_remaining;
  renderDeviceRow(device); renderBigCountdown();
});
socket.on("device_unlocked", ({ device }) => {
  const d = deviceState[device]; d.busy = false; d.seconds_remaining = 0;
  renderDeviceRow(device); renderBigCountdown();
});
socket.on("last_action", (la) => applyLastAction(la));

/* ----------------------------------------------------------- API calls */

function getOperator() {
  try {
    const raw = localStorage.getItem("steprtool.operator.v1");
    if (!raw) return null;
    const o = JSON.parse(raw);
    if (!o || !o.name || !o.callsign) return null;
    return { name: String(o.name).trim(), callsign: String(o.callsign).trim().toUpperCase() };
  } catch (_) { return null; }
}

async function postJSON(url, body) {
  const op = getOperator();
  if (!op) {
    // Common.js handles showing the modal; just bail.
    throw new Error("operator not set");
  }
  const payload = Object.assign({ operator: op }, body);
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  let data = {};
  try { data = await res.json(); } catch (_) {}
  return { ok: res.ok, status: res.status, data };
}

function showErrorAsLastAction(device, action, message) {
  applyLastAction({
    timestamp: new Date().toISOString(),
    device, action,
    operator: (getOperator() && (getOperator().callsign + " " + getOperator().name)) || "—",
    status: "ERROR", detail: message, bytes_hex: "", inputs: {},
  });
}

els.sda100Qry_btn.addEventListener("click", async () => {
  const origText = els.sda100Qry_btn.textContent;
  els.sda100Qry_btn.textContent = "Querying...";
  try {
    const { ok, status, data } = await postJSON("/api/sda100/query", {});
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("sda100", "Change SDA100 Direction", msg + extra);
    }
  } catch (e) { showErrorAsLastAction("sda100", "Query SDA100 Frequency + Direction", e.message || String(e)); }

  
});

els.sda100Freq_btn.addEventListener("click", async () => {
  const v = els.sda100Freq.value.trim();
  if (!v) { showErrorAsLastAction("sda100", "Change Frequency", "frequency required"); return; }
  const freq_khz = parseInt(v, 10);
  if (!Number.isFinite(freq_khz)) {
    showErrorAsLastAction("sda100", "Change Frequency", "frequency must be an integer"); return;
  }
  try {
    const { ok, status, data } = await postJSON("/api/sda100/frequency", {
      frequency_khz: freq_khz, direction: getSelectedDirection(),
    });
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("sda100", "Change Frequency", msg + extra);
    }
  } catch (e) { showErrorAsLastAction("sda100", "Change Frequency", e.message || String(e)); }
});

els.sda100Direction.addEventListener("click", async () => {
  const v = els.sda100Freq.value.trim();
  if (!v) { showErrorAsLastAction("sda100", "Change SDA100 Direction", "frequency required"); return; }
  const freq_khz = parseInt(v, 10);
  if (!Number.isFinite(freq_khz)) {
    showErrorAsLastAction("sda100", "Change SDA100 Direction", "frequency must be an integer"); return;
  }
  try {
    const { ok, status, data } = await postJSON("/api/sda100/frequency", {
      frequency_khz: freq_khz, direction: getSelectedDirection(),
    });
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("sda100", "Change SDA100 Direction", msg + extra);
    }
  } catch (e) { showErrorAsLastAction("sda100", "Change SDA100 Direction", e.message || String(e)); }
});

els.sda100Home_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/sda100/home", {
      direction: getSelectedDirection(),
    });
    if (!ok) {
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("sda100", "Home", (data.error || `HTTP ${status}`) + extra);
    }
  } catch (e) { showErrorAsLastAction("sda100", "Home", e.message || String(e)); }
});

els.sda100Cal_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/sda100/calibrate", {
      direction: getSelectedDirection(),
    });
    if (!ok) {
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("sda100", "Calibrate", (data.error || `HTTP ${status}`) + extra);
    }
  } catch (e) { showErrorAsLastAction("sda100", "Calibrate", e.message || String(e)); }
});



document.querySelectorAll(".compass-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    els.dcu2Az.value = btn.dataset.az; els.dcu2Az.focus();
  });
});

els.dcu2Go.addEventListener("click", async () => {
  const v = els.dcu2Az.value.trim();
  if (!v) { showErrorAsLastAction("dcu2", "Change Direction", "azimuth required"); return; }
  const azimuth = parseInt(v, 10);
  if (!Number.isFinite(azimuth)) {
    showErrorAsLastAction("dcu2", "Change Direction", "azimuth must be an integer"); return;
  }
  try {
    const { ok, status, data } = await postJSON("/api/dcu2/azimuth", { azimuth });
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("dcu2", "Change Direction", msg + extra);
    }
  } catch (e) { showErrorAsLastAction("dcu2", "Change Direction", e.message || String(e)); }
});
