/* Jefferson ARC StepIR Antenna Control -- client */
"use strict";

const OPERATOR_STORAGE_KEY = "steprtool.operator.v1";

/* ------------------------------------------------------------------ DOM */

const $ = (id) => document.getElementById(id);

const els = {
  opModal:     $("operator-modal"),
  opName:      $("op-name"),
  opCall:      $("op-callsign"),
  opErr:       $("op-error"),
  opSave:      $("op-save"),
  opDisplay:   $("op-display"),
  opChange:    $("op-change"),

  step100Freq: $("step100-freq"),
  step100Freq_btn: $("btn-step100-freq"),
  step100Home_btn: $("btn-step100-home"),
  step100Cal_btn:  $("btn-step100-cal"),
  step100Port: $("step100-port"),
  step100Dot:  $("step100-dot"),
  step100Text: $("step100-text"),
  step100Cd:   $("step100-countdown"),

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

  connStatus:  $("conn-status"),
};

/* ----------------------------------------------------------- operator */

function getOperator() {
  try {
    const raw = localStorage.getItem(OPERATOR_STORAGE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || !obj.name || !obj.callsign) return null;
    return { name: String(obj.name).trim(), callsign: String(obj.callsign).trim().toUpperCase() };
  } catch (_) { return null; }
}

function setOperator(op) {
  localStorage.setItem(OPERATOR_STORAGE_KEY, JSON.stringify(op));
  renderOperator();
}

function clearOperator() {
  localStorage.removeItem(OPERATOR_STORAGE_KEY);
  renderOperator();
}

function renderOperator() {
  const op = getOperator();
  if (op) {
    els.opDisplay.textContent = `${op.callsign} (${op.name})`;
    els.opModal.classList.add("hidden");
  } else {
    els.opDisplay.textContent = "—";
    els.opModal.classList.remove("hidden");
    els.opName.focus();
  }
}

function showOperatorModal() {
  const op = getOperator();
  if (op) {
    els.opName.value = op.name;
    els.opCall.value = op.callsign;
  }
  els.opErr.hidden = true;
  els.opModal.classList.remove("hidden");
  els.opName.focus();
}

els.opSave.addEventListener("click", () => {
  const name = els.opName.value.trim();
  const callsign = els.opCall.value.trim().toUpperCase();
  if (!name) { els.opErr.textContent = "Name is required."; els.opErr.hidden = false; return; }
  if (!/^[A-Z0-9]{3,10}$/.test(callsign)) {
    els.opErr.textContent = "Callsign must be 3–10 letters or digits.";
    els.opErr.hidden = false;
    return;
  }
  setOperator({ name, callsign });
});

els.opChange.addEventListener("click", showOperatorModal);

/* ----------------------------------------------------------- helpers */

function setDeviceState(device, busy, mock, secondsRemaining) {
  const dot  = device === "step100" ? els.step100Dot  : els.dcu2Dot;
  const text = device === "step100" ? els.step100Text : els.dcu2Text;
  const cd   = device === "step100" ? els.step100Cd   : els.dcu2Cd;

  dot.classList.remove("busy", "mock");
  if (busy) {
    dot.classList.add("busy");
    text.textContent = "busy";
    cd.textContent = secondsRemaining > 0 ? `${secondsRemaining}s` : "";
  } else if (mock) {
    dot.classList.add("mock");
    text.textContent = "idle (mock)";
    cd.textContent = "";
  } else {
    text.textContent = "idle";
    cd.textContent = "";
  }
  // Disable/enable command buttons based on device busy state.
  const busyMap = {
    step100: busy,
    dcu2:    busy,
  };
  els.step100Freq_btn.disabled = busyMap.step100;
  els.step100Home_btn.disabled = busyMap.step100;
  els.step100Cal_btn.disabled  = busyMap.step100;
  els.dcu2Go.disabled = busyMap.dcu2;
}

// Per-device tracking so countdown emits from the server animate cleanly.
const deviceState = {
  step100: { busy: false, mock: false, port: "—" },
  dcu2:    { busy: false, mock: false, port: "—" },
};

function applyDeviceSnapshot(snap) {
  const d = deviceState[snap.device];
  d.busy = snap.busy;
  d.mock = snap.mock;
  d.port = snap.port;
  const portLabel = snap.mock ? "MOCK" : snap.port;
  if (snap.device === "step100") {
    els.step100Port.textContent = `port ${portLabel}`;
  } else {
    els.dcu2Port.textContent = `port ${portLabel}`;
  }
  setDeviceState(snap.device, snap.busy, snap.mock, snap.seconds_remaining || 0);
}

// Map of input keys (sent by the server) to DOM elements they should populate.
const INPUT_FIELD_MAP = {
  step100_freq: () => els.step100Freq,
  dcu2_az:      () => els.dcu2Az,
};

function applyLastAction(la) {
  if (!la) return;
  els.laTimestamp.textContent = la.timestamp || "—";
  els.laDevice.textContent    = la.device === "step100" ? "Step 100" : "DCU-2";
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

  // Push committed input values into the form fields so every connected
  // browser reflects what the most recent operator sent. We update
  // unconditionally; the per-device lock prevents two clients from racing
  // a command, and other clients can re-edit once the lock clears.
  const inputs = la.inputs || {};
  for (const key in inputs) {
    const lookup = INPUT_FIELD_MAP[key];
    if (!lookup) continue;
    const el = lookup();
    if (el) el.value = inputs[key];
  }
}

/* ----------------------------------------------------------- socket.io */

const socket = io({ transports: ["websocket", "polling"] });

socket.on("connect", () => {
  els.connStatus.textContent = "connected";
  els.connStatus.classList.remove("conn-down");
  els.connStatus.classList.add("conn-up");
});
socket.on("disconnect", () => {
  els.connStatus.textContent = "disconnected";
  els.connStatus.classList.remove("conn-up");
  els.connStatus.classList.add("conn-down");
});

socket.on("state", (s) => {
  applyDeviceSnapshot(s.step100);
  applyDeviceSnapshot(s.dcu2);
  if (s.last_action) applyLastAction(s.last_action);
});

socket.on("device_locked", ({ device, seconds_remaining }) => {
  deviceState[device].busy = true;
  setDeviceState(device, true, deviceState[device].mock, seconds_remaining);
});
socket.on("device_countdown", ({ device, seconds_remaining }) => {
  setDeviceState(device, true, deviceState[device].mock, seconds_remaining);
});
socket.on("device_unlocked", ({ device }) => {
  deviceState[device].busy = false;
  setDeviceState(device, false, deviceState[device].mock, 0);
});
socket.on("last_action", (la) => applyLastAction(la));

/* ----------------------------------------------------------- API calls */

async function postJSON(url, body) {
  const op = getOperator();
  if (!op) { showOperatorModal(); throw new Error("operator not set"); }
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
    status: "ERROR",
    detail: message,
    bytes_hex: "",
  });
}

/* Step 100: Change Frequency */
els.step100Freq_btn.addEventListener("click", async () => {
  const v = els.step100Freq.value.trim();
  if (!v) { showErrorAsLastAction("step100", "Change Frequency", "frequency required"); return; }
  const freq_khz = parseInt(v, 10);
  if (!Number.isFinite(freq_khz)) {
    showErrorAsLastAction("step100", "Change Frequency", "frequency must be an integer");
    return;
  }
  try {
    const { ok, status, data } = await postJSON("/api/step100/frequency", { frequency_khz: freq_khz });
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("step100", "Change Frequency", msg + extra);
    }
    // success: server broadcasts last_action via SocketIO, no need to update here
  } catch (e) {
    showErrorAsLastAction("step100", "Change Frequency", e.message || String(e));
  }
});

/* Step 100: Home */
els.step100Home_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/step100/home", {});
    if (!ok) showErrorAsLastAction("step100", "Home", data.error || `HTTP ${status}`);
  } catch (e) { showErrorAsLastAction("step100", "Home", e.message || String(e)); }
});

/* Step 100: Calibrate */
els.step100Cal_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/step100/calibrate", {});
    if (!ok) showErrorAsLastAction("step100", "Calibrate", data.error || `HTTP ${status}`);
  } catch (e) { showErrorAsLastAction("step100", "Calibrate", e.message || String(e)); }
});

/* DCU-2: compass quick-fills */
document.querySelectorAll(".compass-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    els.dcu2Az.value = btn.dataset.az;
    els.dcu2Az.focus();
  });
});

/* DCU-2: Change Direction */
els.dcu2Go.addEventListener("click", async () => {
  const v = els.dcu2Az.value.trim();
  if (!v) { showErrorAsLastAction("dcu2", "Change Direction", "azimuth required"); return; }
  const azimuth = parseInt(v, 10);
  if (!Number.isFinite(azimuth)) {
    showErrorAsLastAction("dcu2", "Change Direction", "azimuth must be an integer");
    return;
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

/* ----------------------------------------------------------- bootstrap */

renderOperator();
// Initial state will arrive via the 'state' SocketIO event on connect.
