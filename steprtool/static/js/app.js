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

  onlinePills: $("online-pills"),

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
  bigCountdown:$("big-countdown"),

  connStatus:  $("conn-status"),
};

/* -------------------------------------------------- operator (localStorage) */

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
  // Tell the server immediately so the online-users list updates.
  if (socket && socket.connected) socket.emit("identify", op);
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

/* ------------------------------------------------------- direction helpers */

function getSelectedDirection() {
  const checked = document.querySelector('input[name="step100-direction"]:checked');
  return checked ? checked.value : "normal";
}

function setSelectedDirection(val) {
  const radio = document.querySelector(
    `input[name="step100-direction"][value="${val}"]`
  );
  if (radio) radio.checked = true;
}

/* ----------------------------------------------------------- device state */

const deviceState = {
  step100: { busy: false, mock: false, port: "—", seconds_remaining: 0, seconds_total: 0 },
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
  const dot  = device === "step100" ? els.step100Dot  : els.dcu2Dot;
  const text = device === "step100" ? els.step100Text : els.dcu2Text;
  const cd   = device === "step100" ? els.step100Cd   : els.dcu2Cd;

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
  // Disable buttons of the busy device.
  if (device === "step100") {
    els.step100Freq_btn.disabled = d.busy;
    els.step100Home_btn.disabled = d.busy;
    els.step100Cal_btn.disabled  = d.busy;
  } else {
    els.dcu2Go.disabled = d.busy;
  }
}

function renderBigCountdown() {
  const items = [];
  for (const dev of ["step100", "dcu2"]) {
    const d = deviceState[dev];
    if (!d.busy || d.seconds_remaining <= 0) continue;
    const label = dev === "step100" ? "STEP 100" : "DCU-2";
    items.push(`
      <div class="big-cd-item">
        <span class="big-cd-label">${label}</span>
        <span class="big-cd-time">${formatMMSS(d.seconds_remaining)}</span>
      </div>
    `);
  }
  els.bigCountdown.innerHTML = items.join("");
}

function applyDeviceSnapshot(snap) {
  const d = deviceState[snap.device];
  d.busy = !!snap.busy;
  d.mock = !!snap.mock;
  d.port = snap.port;
  d.seconds_remaining = snap.seconds_remaining || 0;
  d.seconds_total = snap.seconds_total || 0;
  const portLabel = snap.mock ? "MOCK" : snap.port;
  if (snap.device === "step100") {
    els.step100Port.textContent = `port ${portLabel}`;
    // Sync direction radio if server included it (state push, not last_action)
    if (snap.direction) setSelectedDirection(snap.direction);
  } else {
    els.dcu2Port.textContent = `port ${portLabel}`;
  }
  renderDeviceRow(snap.device);
  renderBigCountdown();
}

/* -------------------------------------------------- inputs sync via socket */

// Each handler updates a UI element from a structured value the server sent.
const INPUT_HANDLERS = {
  step100_freq:      (v) => { els.step100Freq.value = v; },
  dcu2_az:           (v) => { els.dcu2Az.value = v; },
  step100_direction: (v) => { setSelectedDirection(v); },
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

  // Sync inputs from server (so all browsers reflect the committed values).
  const inputs = la.inputs || {};
  for (const key in inputs) {
    const handler = INPUT_HANDLERS[key];
    if (handler) handler(inputs[key]);
  }
}

/* --------------------------------------------------------- online users */

function renderOnlineUsers(list) {
  if (!Array.isArray(list) || list.length === 0) {
    els.onlinePills.innerHTML = '<span class="online-empty">no one online yet</span>';
    return;
  }
  els.onlinePills.innerHTML = list.map((u) => {
    const call = String(u.callsign || "").trim();
    const name = String(u.name || "").trim();
    return `<span class="online-pill"><span class="pill-call">${escapeHtml(call)}</span>` +
           (name ? `<span class="pill-name">${escapeHtml(name)}</span>` : "") +
           `</span>`;
  }).join("");
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

/* ------------------------------------------------------------- socket.io */

const socket = io({ transports: ["websocket", "polling"] });

socket.on("connect", () => {
  els.connStatus.textContent = "connected";
  els.connStatus.classList.remove("conn-down");
  els.connStatus.classList.add("conn-up");
  // Identify ourselves to the server so we appear in the online-users list.
  const op = getOperator();
  if (op) socket.emit("identify", op);
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
  if (s.online_users) renderOnlineUsers(s.online_users);
});

socket.on("online_users", (list) => renderOnlineUsers(list));

socket.on("device_locked", ({ device, seconds_remaining, seconds_total }) => {
  const d = deviceState[device];
  d.busy = true;
  d.seconds_remaining = seconds_remaining;
  d.seconds_total = seconds_total;
  renderDeviceRow(device);
  renderBigCountdown();
});
socket.on("device_countdown", ({ device, seconds_remaining }) => {
  const d = deviceState[device];
  d.seconds_remaining = seconds_remaining;
  renderDeviceRow(device);
  renderBigCountdown();
});
socket.on("device_unlocked", ({ device }) => {
  const d = deviceState[device];
  d.busy = false;
  d.seconds_remaining = 0;
  renderDeviceRow(device);
  renderBigCountdown();
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
    inputs: {},
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
    const { ok, status, data } = await postJSON("/api/step100/frequency", {
      frequency_khz: freq_khz,
      direction: getSelectedDirection(),
    });
    if (!ok) {
      const msg = data.error || `HTTP ${status}`;
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("step100", "Change Frequency", msg + extra);
    }
  } catch (e) {
    showErrorAsLastAction("step100", "Change Frequency", e.message || String(e));
  }
});

/* Step 100: Home */
els.step100Home_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/step100/home", {
      direction: getSelectedDirection(),
    });
    if (!ok) {
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("step100", "Home", (data.error || `HTTP ${status}`) + extra);
    }
  } catch (e) { showErrorAsLastAction("step100", "Home", e.message || String(e)); }
});

/* Step 100: Calibrate */
els.step100Cal_btn.addEventListener("click", async () => {
  try {
    const { ok, status, data } = await postJSON("/api/step100/calibrate", {
      direction: getSelectedDirection(),
    });
    if (!ok) {
      const extra = data.seconds_remaining ? ` (${data.seconds_remaining}s remaining)` : "";
      showErrorAsLastAction("step100", "Calibrate", (data.error || `HTTP ${status}`) + extra);
    }
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
renderOnlineUsers([]);
// Initial state arrives via the 'state' Socket.IO event on connect.
