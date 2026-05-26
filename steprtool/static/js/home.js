/* Home page: Recent Activity feed live updates + tile-click recording +
   browser-notification permission banner. */
"use strict";

/* ---------------- Notification permission banner ------------------
   Shown once on the home page if Notification.permission === "default"
   and the user hasn't explicitly dismissed it. Click "Enable alerts"
   triggers the browser's permission prompt; the actual notification
   firing on antenna_state events lives in common.js so both pages can
   surface alerts once permission is granted. */
(function maybeShowNotificationBanner() {
  if (typeof Notification === "undefined") return;
  if (Notification.permission !== "default") return;
  if (localStorage.getItem("steprtool.notifications.dismissed") === "1") return;

  const banner = document.createElement("aside");
  banner.className = "notification-banner";
  banner.setAttribute("role", "region");
  banner.setAttribute("aria-label", "Enable browser notifications");
  banner.innerHTML =
    '<div class="nb-inner">' +
      '<span class="nb-icon" aria-hidden="true">🔔</span>' +
      '<span class="nb-text">Get a desktop alert whenever antennas are connected or disconnected, even when this tab is in the background.</span>' +
      '<button type="button" class="nb-enable">Enable alerts</button>' +
      '<button type="button" class="nb-dismiss" aria-label="Dismiss">×</button>' +
    '</div>';

  const main = document.querySelector(".home-main");
  if (main) main.insertBefore(banner, main.firstChild);

  banner.querySelector(".nb-enable").addEventListener("click", () => {
    const finish = () => banner.remove();
    try {
      const result = Notification.requestPermission();
      // requestPermission returns a Promise in modern browsers, a callback
      // in older ones. Handle both. Either way we hide the banner once the
      // user has answered the prompt.
      if (result && typeof result.then === "function") {
        result.then(finish, finish);
      } else {
        finish();
      }
    } catch (_) { finish(); }
  });
  banner.querySelector(".nb-dismiss").addEventListener("click", () => {
    localStorage.setItem("steprtool.notifications.dismissed", "1");
    banner.remove();
  });
})();

(function() {
  const list = document.getElementById("activity-list");

  function fmtLocal(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameDay = d.getFullYear() === now.getFullYear()
                 && d.getMonth() === now.getMonth()
                 && d.getDate() === now.getDate();
    try {
      if (sameDay) {
        return "today " + d.toLocaleTimeString(undefined,
          { hour: "numeric", minute: "2-digit" });
      }
      return d.toLocaleString(undefined,
        { month: "short", day: "numeric",
          hour: "numeric", minute: "2-digit" });
    } catch (_) { return d.toString(); }
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[c]));
  }

  function renderList(events) {
    if (!Array.isArray(events) || events.length === 0) {
      list.innerHTML = '<li class="activity-empty">No activity yet.</li>';
      return;
    }
    list.innerHTML = events.map((ev) =>
      `<li class="activity-row" data-ts="${escapeHtml(ev.timestamp)}">
         <span class="activity-ts">${escapeHtml(fmtLocal(ev.timestamp))}</span>
         <span class="activity-msg">${escapeHtml(ev.message)}</span>
       </li>`
    ).join("");
  }

  // Rewrite any server-rendered timestamps into friendlier local-time strings.
  function decorateExisting() {
    list.querySelectorAll(".activity-ts[data-ts]").forEach((el) => {
      el.textContent = fmtLocal(el.dataset.ts);
    });
  }
  decorateExisting();

  // Bridge from common.js's onStateMessage hook so we replace the SSR list
  // with whatever the server believes is current at connect-time.
  window.onStateMessage = function(state) {
    if (state && Array.isArray(state.activity_events)) {
      renderList(state.activity_events);
    }
  };

  // Live updates: prepend new events as they arrive.
  const MAX = 30;
  window.socket.on("activity_event", (ev) => {
    if (!ev || !ev.message) return;
    // If list shows the empty-state placeholder, clear it first.
    const empty = list.querySelector(".activity-empty");
    if (empty) list.innerHTML = "";
    const row = document.createElement("li");
    row.className = "activity-row";
    row.dataset.ts = ev.timestamp || "";
    row.innerHTML =
      `<span class="activity-ts">${escapeHtml(fmtLocal(ev.timestamp))}</span>
       <span class="activity-msg">${escapeHtml(ev.message)}</span>`;
    list.insertBefore(row, list.firstChild);
    // Trim to last MAX.
    while (list.children.length > MAX) list.removeChild(list.lastChild);
  });

  // Tile-click instrumentation: tell the server before navigating away.
  document.querySelectorAll(".home-tile[data-tile]").forEach((tile) => {
    const target = tile.dataset.tile;
    if (target !== "ic7300" && target !== "calendar" && target !== "chat") return;
    tile.addEventListener("click", () => {
      try {
        // Fire and forget; default link click then opens the new tab.
        window.socket.emit("activity_visit", { target: target });
      } catch (_) {}
    });
  });
})();
