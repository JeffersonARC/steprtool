"""Activity feed: the canonical place to record user-visible events.

Why a central object instead of just logging?
  1. The home page shows a "Recent activity" panel with live updates.
  2. user_activity.log needs friendlier verbiage than raw technical lines.
  3. Some events (joined, left, visited X) don't fit a device controller —
     they're page/socket-level.

Events go through ActivityFeed.record():
  - inserted into an in-memory buffer, kept sorted newest-first, capped
  - optionally written to user_activity.log (persist=True)
  - optionally broadcast to browsers via Socket.IO 'activity_event'

Persistence policy:
  * User actions (joined/left, frequency/azimuth/home/calibrate, visited X,
    system restart, manual antenna overrides): persist=True -> they live in
    user_activity.log and are re-seeded into the buffer on restart.
  * Email-driven antenna events: persist=False -> the IMAP mailbox is their
    permanent record. On restart the email walkback backfills the most recent
    few directly from the mailbox (with their real email timestamps), so they
    are never written to the log (which would double them up and stamp them
    with log-time instead of email-time).

Timestamps are stored as ISO-8601 UTC strings ("...+00:00"), which sort
lexicographically in chronological order — so the buffer can be kept sorted
by a plain string comparison.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional


DEFAULT_CAPACITY = 30


def _to_iso_utc(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


class ActivityFeed:
    def __init__(self, socketio, capacity: int = DEFAULT_CAPACITY):
        self._lock = threading.Lock()
        # newest-first list of {"timestamp": iso, "message": str, "_seq": int}
        self._events: list[dict] = []
        self._capacity = capacity
        self._seq = 0  # monotonic tiebreaker for same-second events
        self._socketio = socketio
        self._user_logger = logging.getLogger("steprtool.user_activity")

    def _sort_and_trim(self) -> None:
        # Sort by (timestamp, seq) descending: newest first, and within the
        # same second the most-recently-inserted wins. Assumes caller holds
        # the lock.
        self._events.sort(key=lambda e: (e["timestamp"], e["_seq"]), reverse=True)
        del self._events[self._capacity:]

    def record(
        self,
        message: str,
        *,
        timestamp: Optional[datetime] = None,
        persist: bool = True,
        broadcast: bool = True,
    ) -> dict:
        """Record an event.

        timestamp: when the event actually happened (e.g. the email's own
            time). Defaults to now() for live user actions.
        persist:   write to user_activity.log. False for email antenna events
            (the mailbox is their record).
        broadcast: emit to connected browsers. False for startup backfill.
        """
        iso = _to_iso_utc(timestamp or datetime.now(timezone.utc))
        with self._lock:
            event = {"timestamp": iso, "message": message, "_seq": self._seq}
            self._seq += 1
            self._events.append(event)
            self._sort_and_trim()
        if persist:
            self._user_logger.info(message)
        public = {"timestamp": iso, "message": message}
        if broadcast and self._socketio is not None:
            self._socketio.emit("activity_event", public)
        return public

    def snapshot(self) -> list[dict]:
        """Return events newest-first. Safe to send straight to the client."""
        with self._lock:
            return [{"timestamp": e["timestamp"], "message": e["message"]}
                    for e in self._events]

    def seed_from_log(self, log_path, max_events: Optional[int] = None) -> int:
        """Merge the tail of user_activity.log into the buffer at startup.

        Returns the number of events loaded. Parses the log lines
        (timestamp + message); anything unparseable is skipped. The buffer
        is then re-sorted and trimmed, so this can be called alongside other
        record() calls in any order.
        """
        from pathlib import Path
        import re

        if max_events is None:
            max_events = self._capacity
        p = Path(log_path)
        if not p.exists():
            return 0

        # Log format: "YYYY-MM-DD HH:MM:SS INFO  message...".
        pat = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\w+\s+(.+)$")
        loaded: list[dict] = []
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return 0

        for line in lines[-(max_events * 4):]:  # tail with slack
            m = pat.match(line.rstrip("\n"))
            if not m:
                continue
            ts_str, msg = m.groups()
            try:
                # Logger emits local time; convert to ISO UTC for sorting.
                local_dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").astimezone()
                iso = local_dt.astimezone(timezone.utc).isoformat(timespec="seconds")
            except ValueError:
                continue
            loaded.append({"timestamp": iso, "message": msg})

        loaded = loaded[-max_events:]
        with self._lock:
            for e in loaded:
                e["_seq"] = self._seq
                self._seq += 1
                self._events.append(e)
            self._sort_and_trim()
        return len(loaded)
