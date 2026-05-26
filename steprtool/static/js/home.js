/* Home page: Recent Activity feed live updates + tile-click recording. */
"use strict";

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
    if (target !== "ic7300" && target !== "calendar") return;
    tile.addEventListener("click", () => {
      try {
        // Fire and forget; default link click then opens the new tab.
        window.socket.emit("activity_visit", { target: target });
      } catch (_) {}
    });
  });
})();
