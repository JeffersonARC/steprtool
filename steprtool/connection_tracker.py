"""Tracks which operators are currently connected and emits friendly
'joined' and 'left' events into the ActivityFeed.

Socket.IO 'disconnect' fires on intentional tab-close *and* on transient
network blips, page reloads, Tailscale hiccups, etc. We don't want one
of those to produce 'left/joined' churn in the activity feed. So we
debounce: when a callsign's last socket goes away, schedule a 'left'
event for DEBOUNCE_SECONDS later. If a new socket identifies as the
same callsign within that window, we cancel the pending leave instead
of emitting joined+left.

Multi-tab handling: one operator with two browser tabs has two sids
under the same callsign. Closing one tab doesn't trigger 'left' because
the callsign still has one active sid.

Operator changes mid-session: if a sid was Brownell KJ5BYZ and then
identifies as Alice W5XYZ, we silently move it. Per design decision Q2:
no farewell for the old callsign, just a 'joined' event for the new
callsign (if not already present).
"""

from __future__ import annotations

import logging
import threading
from typing import Optional


logger = logging.getLogger(__name__)


DEFAULT_DEBOUNCE_SECONDS = 10


class ConnectionTracker:
    def __init__(self, activity_feed, debounce_seconds: int = DEFAULT_DEBOUNCE_SECONDS):
        self._lock = threading.Lock()
        # sid -> (name, callsign)
        self._sids: dict[str, tuple[str, str]] = {}
        # callsign -> {"sids": set[str], "name": str}
        self._callsigns: dict[str, dict] = {}
        # callsign -> Timer (pending leave)
        self._pending_leaves: dict[str, threading.Timer] = {}
        self._activity = activity_feed
        self._debounce = debounce_seconds

    def on_identify(self, sid: str, name: str, callsign: str) -> None:
        """Called after socket identifies. Emits 'joined' for new callsigns
        only; reconnects within the debounce window are silent."""
        joined_emit_for: Optional[str] = None

        with self._lock:
            # If sid was previously bound to a different callsign, detach
            # silently from the old one.
            old = self._sids.get(sid)
            if old and old[1] != callsign:
                self._detach_sid_locked(sid)

            self._sids[sid] = (name, callsign)
            entry = self._callsigns.setdefault(callsign, {"sids": set(), "name": name})
            was_empty = len(entry["sids"]) == 0
            entry["sids"].add(sid)
            entry["name"] = name

            pending = self._pending_leaves.pop(callsign, None)
            if pending is not None:
                pending.cancel()
                # Quiet reconnect — no joined event.
                return

            if was_empty:
                joined_emit_for = f"{name} {callsign}"

        if joined_emit_for is not None:
            self._activity.record(f"{joined_emit_for} joined")

    def on_disconnect(self, sid: str) -> None:
        with self._lock:
            self._detach_sid_locked(sid, schedule_leave=True)

    def _detach_sid_locked(self, sid: str, schedule_leave: bool = False) -> None:
        info = self._sids.pop(sid, None)
        if info is None:
            return
        name, callsign = info
        entry = self._callsigns.get(callsign)
        if entry is None:
            return
        entry["sids"].discard(sid)
        if entry["sids"]:
            return  # other tabs/sessions still open under this callsign
        if not schedule_leave:
            self._callsigns.pop(callsign, None)
            return

        # Schedule a debounced leave for this callsign.
        def _fire():
            should_emit = False
            with self._lock:
                self._pending_leaves.pop(callsign, None)
                entry_now = self._callsigns.get(callsign)
                if entry_now and entry_now["sids"]:
                    return  # reconnected during debounce
                self._callsigns.pop(callsign, None)
                should_emit = True
            if should_emit:
                self._activity.record(f"{name} {callsign} left the system")

        timer = threading.Timer(self._debounce, _fire)
        timer.daemon = True
        self._pending_leaves[callsign] = timer
        timer.start()

    def public_users(self) -> list[dict]:
        """List of currently-online operators (after debounce), for the
        online-users pill strip. Different from on_disconnect debouncing
        in that this snapshot reflects the *immediate* state — the strip
        shouldn't show people who closed their tab a second ago."""
        with self._lock:
            return [
                {"callsign": cs, "name": info.get("name", "")}
                for cs, info in sorted(self._callsigns.items())
            ]
