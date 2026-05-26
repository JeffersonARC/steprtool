/* steprtool common client code. Loaded on every page.
   Owns: socket setup, operator identification, online-users strip,
   antenna-state alert, connection status. */
"use strict";

const OPERATOR_STORAGE_KEY = "steprtool.operator.v1";

const $ = (id) => document.getElementById(id);

// Elements present on every page (defined in base.html).
const commonEls = {
  opModal:     $("operator-modal"),
  opName:      $("op-name"),
  opCall:      $("op-callsign"),
  opErr:       $("op-error"),
  opSave:      $("op-save"),
  opDisplay:   $("op-display"),
  opChange:    $("op-change"),

  onlinePills: $("online-pills"),

  antennaAlert:     $("antenna-alert"),
  antennaAlertText: $("antenna-alert-text"),
  mainGrid:         $("main-grid"),

  connStatus:  $("conn-status"),
};

/* ----------------------------------------------------- operator */

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
  if (window.socket && window.socket.connected) window.socket.emit("identify", op);
}

function renderOperator() {
  const op = getOperator();
  if (op) {
    commonEls.opDisplay.textContent = `${op.callsign} (${op.name})`;
    commonEls.opModal.classList.add("hidden");
  } else {
    commonEls.opDisplay.textContent = "—";
    commonEls.opModal.classList.remove("hidden");
    commonEls.opName.focus();
  }
}

function showOperatorModal() {
  const op = getOperator();
  if (op) { commonEls.opName.value = op.name; commonEls.opCall.value = op.callsign; }
  commonEls.opErr.hidden = true;
  commonEls.opModal.classList.remove("hidden");
  commonEls.opName.focus();
}

commonEls.opSave.addEventListener("click", () => {
  const name = commonEls.opName.value.trim();
  const callsign = commonEls.opCall.value.trim().toUpperCase();
  if (!name) { commonEls.opErr.textContent = "Name is required."; commonEls.opErr.hidden = false; return; }
  if (!/^[A-Z0-9]{3,10}$/.test(callsign)) {
    commonEls.opErr.textContent = "Callsign must be 3–10 letters or digits.";
    commonEls.opErr.hidden = false; return;
  }
  setOperator({ name, callsign });
});
commonEls.opChange.addEventListener("click", showOperatorModal);

/* --------------------------------------- online users + antenna state */

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[c]));
}

function renderOnlineUsers(list) {
  if (!Array.isArray(list) || list.length === 0) {
    commonEls.onlinePills.innerHTML = '<span class="online-empty">no one online yet</span>';
    return;
  }
  commonEls.onlinePills.innerHTML = list.map((u) => {
    const call = String(u.callsign || "").trim();
    const name = String(u.name || "").trim();
    return `<span class="online-pill"><span class="pill-call">${escapeHtml(call)}</span>` +
           (name ? `<span class="pill-name">${escapeHtml(name)}</span>` : "") + `</span>`;
  }).join("");
}

function formatLocalTimestamp(isoString) {
  if (!isoString) return "";
  const d = new Date(isoString);
  if (isNaN(d.getTime())) return isoString;
  try {
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", second: "2-digit",
      timeZoneName: "short",
    });
  } catch (_) { return d.toString(); }
}

/* Antenna-state notification dispatch.
   Tracks the last status we've seen so we only fire on real transitions
   (not on initial page-load state). The Notification 'tag' lets the OS
   replace any earlier antenna notification rather than stacking them. */
let _lastAntennaStatus = null;

function maybeFireAntennaNotification(snap) {
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "granted") return;
  // No transition: same status as last time. Update tracker and bail.
  if (snap.status === _lastAntennaStatus) {
    _lastAntennaStatus = snap.status;
    return;
  }
  // First observation since page load: just record it, don't fire — the
  // user already sees the banner on the page itself.
  if (_lastAntennaStatus === null) {
    _lastAntennaStatus = snap.status;
    return;
  }
  // Real transition. Fire.
  _lastAntennaStatus = snap.status;
  const title = snap.status === "disconnected"
    ? "⚠ Antennas Disconnected"
    : "✓ Antennas Reconnected";
  const when = formatLocalTimestamp(snap.timestamp);
  let body = `As of ${when}`;
  if (snap.source === "override" && snap.operator) {
    body += ` (manual override by ${snap.operator})`;
  }
  try {
    new Notification(title, {
      body: body,
      tag: "steprtool-antenna",   // dedupe replacement, not stack
      renotify: true,             // re-alert if a previous one was dismissed
    });
  } catch (_) { /* some browsers throw if permission lapses */ }
}

function applyAntennaState(snap) {
  if (!snap) return;
  const disconnected = snap.status === "disconnected";
  if (disconnected) {
    const when = formatLocalTimestamp(snap.timestamp);
    let msg = `Antennas were disconnected at ${when}`;
    if (snap.source === "override" && snap.operator) {
      msg += ` (manual override by ${snap.operator})`;
    } else if (snap.source === "default") {
      msg += ` (default — no email walkback)`;
    }
    commonEls.antennaAlertText.textContent = msg;
    commonEls.antennaAlert.hidden = false;
    commonEls.antennaAlert.classList.add("visible");
    if (commonEls.mainGrid) commonEls.mainGrid.classList.add("locked");
    document.body.classList.add("antennas-disconnected");
  } else {
    commonEls.antennaAlert.hidden = true;
    commonEls.antennaAlert.classList.remove("visible");
    if (commonEls.mainGrid) commonEls.mainGrid.classList.remove("locked");
    document.body.classList.remove("antennas-disconnected");
  }
  maybeFireAntennaNotification(snap);
}

/* ----------------------------------------------------- socket.io */

const socket = io({ transports: ["websocket", "polling"] });
window.socket = socket; // expose for page-specific scripts

socket.on("connect", () => {
  commonEls.connStatus.textContent = "connected";
  commonEls.connStatus.classList.remove("conn-down");
  commonEls.connStatus.classList.add("conn-up");
  const op = getOperator();
  if (op) socket.emit("identify", op);
});

socket.on("disconnect", () => {
  commonEls.connStatus.textContent = "disconnected";
  commonEls.connStatus.classList.remove("conn-up");
  commonEls.connStatus.classList.add("conn-down");
});

socket.on("state", (s) => {
  if (s.online_users) renderOnlineUsers(s.online_users);
  if (s.antenna_state) applyAntennaState(s.antenna_state);
  // Forward state to page-specific handler so it can hook in.
  if (typeof window.onStateMessage === "function") window.onStateMessage(s);
});

socket.on("online_users", (list) => renderOnlineUsers(list));
socket.on("antenna_state", (snap) => applyAntennaState(snap));

renderOperator();
renderOnlineUsers([]);
