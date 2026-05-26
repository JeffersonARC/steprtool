"""Activity feed: the canonical place to record user-visible events.

Why a central object instead of just logging?
  1. The home page shows a "Recent activity" panel with live updates.
  2. The user_activity.log needs friendlier verbiage than the technical
     log lines we used before.
  3. Some events (joined, left, visited IC-7300) don't fit naturally into
     a device controller — they're page/socket-level.

Every event goes through ActivityFeed.record():
  - prepended to an in-memory ring buffer (newest first, max 30)
  - written to user_activity.log via the existing user_activity logger
  - broadcast to all connected browsers via Socket.IO 'activity_event'

A separate handler at startup loads the file's tail into the ring buffer
so the home page shows recent history even after a process restart.
"""

from __future__ import annotations

import collections
import logging
import threading
from datetime import datetime, timezone
from typing import Optional


DEFAULT_CAPACITY = 30


class ActivityFeed:
    def __init__(self, socketio, capacity: int = DEFAULT_CAPACITY):
        self._lock = threading.Lock()
        self._events: collections.deque = collections.deque(maxlen=capacity)
        self._socketio = socketio
        self._user_logger = logging.getLogger("steprtool.user_activity")

    def record(self, message: str, *, broadcast: bool = True) -> dict:
        """Record an event. Returns the event dict so callers can inspect it."""
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
        }
        with self._lock:
            self._events.appendleft(event)
        self._user_logger.info(message)
        if broadcast and self._socketio is not None:
            self._socketio.emit("activity_event", event)
        return event

    def snapshot(self) -> list[dict]:
        """Return events newest-first. Safe to send straight to the client."""
        with self._lock:
            return list(self._events)

    def seed_from_log(self, log_path, max_events: Optional[int] = None) -> int:
        """Populate the ring buffer with the tail of user_activity.log.

        Returns the number of events loaded. Called once during startup so
        the home page shows history even after a process restart. We parse
        the existing log lines (timestamp + message); anything we can't
        parse is skipped silently.
        """
        from pathlib import Path
        import re

        if max_events is None:
            max_events = self._events.maxlen or DEFAULT_CAPACITY
        p = Path(log_path)
        if not p.exists():
            return 0

        # Log format: "YYYY-MM-DD HH:MM:SS INFO  message...".
        pat = re.compile(
            r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\w+\s+(.+)$"
        )
        loaded: list[dict] = []
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return 0

        for line in lines[-(max_events * 4):]:  # tail with slack for multi-line
            m = pat.match(line.rstrip("\n"))
            if not m:
                continue
            ts_str, msg = m.groups()
            try:
                # Logger emits in local time; we store an ISO with a 'Z' so
                # the UI can parse, but flag the local-time origin in the
                # 'source' attribute below if we ever care to.
                local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                local_dt = local_dt.astimezone()  # attach local tz
                iso = local_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                continue
            loaded.append({"timestamp": iso, "message": msg})

        # Keep only the last max_events; deque.appendleft so newest first.
        loaded = loaded[-max_events:]
        with self._lock:
            self._events.clear()
            for e in loaded:
                self._events.appendleft(e)
        return len(loaded)
