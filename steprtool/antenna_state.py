"""Antenna connect/disconnect state, driven by lightning-detector emails
or by URL-parameter overrides.

The state is held in a single AntennaState instance. Updates follow a
strict most-recent-event-wins rule (any update older than the current
timestamp is rejected). All state transitions are broadcast to connected
browsers via the 'antenna_state' Socket.IO event.

Possible state shapes (the dict produced by snapshot()):

  {
    "status":    "connected" | "disconnected",
    "timestamp": "2026-05-22T14:14:19+00:00",     # ISO-8601 UTC
    "source":    "email" | "override" | "default",
    "operator":  "KJ5BYZ" | None,                 # set only for overrides
  }

Initial state on construction defaults to "connected" (so dev/testing
without the email integration is usable). On startup, the email listener
will overwrite this with the result of the IMAP walkback; if walkback
finds nothing, the listener flips the default to "disconnected" per the
agreed safety policy.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional


logger = logging.getLogger(__name__)


VALID_STATUSES = ("connected", "disconnected")
VALID_SOURCES = ("email", "override", "default")


def _utc_iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


class AntennaState:
    def __init__(self, socketio, *, default_status: str = "connected"):
        if default_status not in VALID_STATUSES:
            raise ValueError(f"default_status must be one of {VALID_STATUSES}")
        self._lock = threading.Lock()
        self._status: str = default_status
        self._timestamp: datetime = datetime.now(timezone.utc)
        self._source: str = "default"
        self._operator: Optional[str] = None
        self._socketio = socketio

    # ----------------------------------------------------------------- query

    def is_disconnected(self) -> bool:
        with self._lock:
            return self._status == "disconnected"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "timestamp": _utc_iso(self._timestamp),
                "source": self._source,
                "operator": self._operator,
            }

    # ---------------------------------------------------------------- update

    def update(
        self,
        status: str,
        timestamp: datetime,
        source: str,
        operator: Optional[str] = None,
        broadcast: bool = True,
    ) -> bool:
        """Apply a new event if its timestamp is newer than current.

        Returns True if applied, False if rejected as stale. A change of
        status is logged at INFO; a refresh with the same status but newer
        timestamp is logged at DEBUG.
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        with self._lock:
            if timestamp <= self._timestamp and source != "default":
                # Stale event: most-recent-event-wins says drop it.
                # We still allow 'default' updates to overwrite an initial
                # placeholder of the same time (set in __init__).
                return False

            old_status = self._status
            old_source = self._source
            self._status = status
            self._timestamp = timestamp
            self._source = source
            self._operator = operator

            snap = {
                "status": self._status,
                "timestamp": _utc_iso(self._timestamp),
                "source": self._source,
                "operator": self._operator,
            }

        if old_status != status:
            logger.info(
                "antenna state %s -> %s (source=%s, operator=%s, at=%s)",
                old_status, status, source, operator or "-",
                snap["timestamp"],
            )
        else:
            logger.debug(
                "antenna state refresh: %s unchanged (source %s -> %s, at=%s)",
                status, old_source, source, snap["timestamp"],
            )

        if broadcast and self._socketio is not None:
            self._socketio.emit("antenna_state", snap)
        return True

    # --------------------------------------------------- replace default-init

    def replace_default(
        self, status: str, source: str = "default", timestamp: Optional[datetime] = None
    ) -> None:
        """Used by the email listener after walkback to set the initial
        state. Unlike update(), this is *unconditional* — it's only meant
        to overwrite the placeholder set in __init__."""
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        with self._lock:
            self._status = status
            self._source = source
            self._operator = None
            self._timestamp = timestamp
            snap = {
                "status": self._status,
                "timestamp": _utc_iso(self._timestamp),
                "source": self._source,
                "operator": self._operator,
            }
        logger.info(
            "antenna state initialized: %s (source=%s, at=%s)",
            status, source, snap["timestamp"],
        )
        if self._socketio is not None:
            self._socketio.emit("antenna_state", snap)
